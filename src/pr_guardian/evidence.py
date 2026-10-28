"""Evidence provenance: what the model actually received, and what it may cite.

The distinction this module enforces:

* **source verification** — the cited file/commit/line range really came back to
  the model during this review, from a real tool call whose id matches;
* **claim quality** — whether the finding is *correct*. That is not decided here,
  and nothing in this module may set it.

A finding whose source is verified is not a confirmed defect. Its claim stays
`claim_unjudged`; only deterministic tools (ruff, pytest) can produce `confirmed`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from .models import EvidenceRef, ReviewFinding

# statuses for a finding's evidence
EVIDENCE_DETERMINISTIC = "deterministic"
EVIDENCE_VERIFIED = "source_verified"
EVIDENCE_UNVERIFIED = "source_unverified"
EVIDENCE_NO_REF = "no_evidence_ref"
EVIDENCE_NOT_EVALUATED = "not_evaluated"

CLAIM_UNJUDGED = "claim_unjudged"
CLAIM_DETERMINISTIC = "deterministic"

_SNIPPET_LINE = re.compile(r"^\s*(\d+)\s*\|", re.MULTILINE)
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class EvidenceEntry:
    """One unit of evidence the model actually received."""

    tool_call_id: str
    tool_name: str
    ref: str
    file_path: str
    start_line: int
    end_line: int
    hit: bool
    truncated: bool
    snippet_sha256: str = ""

    def covers(self, file_path: str, start_line: int, end_line: int) -> bool:
        return (self.file_path == file_path
                and self.start_line <= start_line
                and end_line <= self.end_line)

    def as_dict(self) -> dict:
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "ref": self.ref,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "hit": self.hit,
            "truncated": self.truncated,
            "snippet_sha256": self.snippet_sha256,
        }


@dataclass
class EvidenceRegistry:
    """Everything the model was shown, keyed by the tool call that produced it."""

    ref: str
    entries: list[EvidenceEntry] = field(default_factory=list)

    # ---- recording -------------------------------------------------------
    def record(self, record, payload: dict) -> None:
        """Record the payload as delivered (after any truncation).

        Only a hit creates citable evidence. Misses, errors and budget denials are
        recorded as non-evidence so they can never back a citation.
        """
        if record.outcome != "hit":
            return
        if not isinstance(payload, dict) or payload.get("ok") is False:
            return
        truncated = bool(payload.get("truncated"))
        name = record.name
        call_id = getattr(record, "call_id", "") or ""
        sha = str(payload.get("ref") or self.ref)

        for file_path, start, end, snippet in self._ranges(name, payload):
            self.entries.append(EvidenceEntry(
                tool_call_id=call_id, tool_name=name, ref=sha,
                file_path=file_path, start_line=start, end_line=end,
                hit=True, truncated=truncated, snippet_sha256=_sha256(snippet),
            ))

    @staticmethod
    def _ranges(name: str, payload: dict) -> list[tuple[str, int, int, str]]:
        out: list[tuple[str, int, int, str]] = []
        if name == "read_code_context":
            file_path = payload.get("file_path")
            start = payload.get("start_line")
            end = payload.get("end_line")
            if isinstance(file_path, str) and isinstance(start, int) and isinstance(end, int):
                out.append((file_path, start, end, str(payload.get("content", ""))))
        elif name == "find_symbol_definition":
            for match in payload.get("matches") or []:
                file_path = match.get("file_path")
                snippet = str(match.get("snippet", ""))
                numbers = [int(n) for n in _SNIPPET_LINE.findall(snippet)]
                line = match.get("line")
                if not isinstance(file_path, str):
                    continue
                start = min(numbers) if numbers else (line if isinstance(line, int) else 0)
                end = max(numbers) if numbers else start
                if start >= 1:
                    out.append((file_path, start, end, snippet))
        elif name == "search_code":
            for hit in payload.get("hits") or []:
                file_path = hit.get("file_path")
                start = hit.get("start_line")
                end = hit.get("end_line")
                if isinstance(file_path, str) and isinstance(start, int) and isinstance(end, int):
                    out.append((file_path, start, end, str(hit.get("snippet", ""))))
        return out

    # ---- lookup ----------------------------------------------------------
    def find(self, ref: EvidenceRef) -> EvidenceEntry | None:
        """Resolve which delivered evidence covers this range.

        Verification is by file and line range, not by the id the model echoed:
        provider tool call ids are opaque strings the model cannot reliably
        reproduce. When several entries cover the range, prefer the one whose
        tool name matches, then the earliest.
        """
        covering = [e for e in self.entries
                    if e.covers(ref.file_path, ref.start_line, ref.end_line)]
        if not covering:
            return None
        if ref.tool_name:
            named = [e for e in covering if e.tool_name == ref.tool_name]
            if named:
                return named[0]
        return covering[0]

    def covers(self, file_path: str, start_line: int, end_line: int) -> bool:
        return any(e.covers(file_path, start_line, end_line) for e in self.entries)

    def summary(self) -> list[dict]:
        return [entry.as_dict() for entry in self.entries]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate_ref(ref: EvidenceRef, registry: EvidenceRegistry,
                 snapshot_sha: str, allowed_paths: set[str]) -> tuple[str, str]:
    """Return (status, reason). Deterministic; never inspects the claim text."""
    if not isinstance(ref, EvidenceRef):
        return EVIDENCE_UNVERIFIED, "not_a_ref"
    # The model cannot choose the commit: it is pinned by the review. A value that
    # is not a commit SHA is a formatting slip, not a citation of another commit;
    # a real 40-hex SHA that differs is treated as a claim about another commit.
    if ref.ref and _FULL_SHA.match(ref.ref.strip().lower()) and ref.ref != snapshot_sha:
        return EVIDENCE_UNVERIFIED, "sha_mismatch"
    if not ref.ref or not _FULL_SHA.match(ref.ref.strip().lower()):
        reasons = "commit_ref_not_a_sha"
    else:
        reasons = ""
    if ref.file_path not in allowed_paths:
        return EVIDENCE_UNVERIFIED, "path_not_allowed"
    if ref.start_line < 1 or ref.end_line < ref.start_line:
        return EVIDENCE_UNVERIFIED, "bad_range"
    if registry.ref != snapshot_sha:
        return EVIDENCE_UNVERIFIED, "registry_sha_mismatch"
    entry = registry.find(ref)
    if entry is None:
        return EVIDENCE_UNVERIFIED, "not_delivered_to_model"
    if not entry.tool_call_id:
        return EVIDENCE_UNVERIFIED, "evidence_has_no_call_id"
    return EVIDENCE_VERIFIED, (reasons or "ok")


def verify_finding(finding: ReviewFinding, registry: EvidenceRegistry | None,
                   snapshot_sha: str, allowed_paths: set[str]) -> ReviewFinding:
    """Return a copy with evidence fields filled in. Never changes the claim."""
    if finding.source != "llm":
        return finding.model_copy(update={
            "evidence_status": EVIDENCE_DETERMINISTIC,
            "claim_status": CLAIM_DETERMINISTIC,
            "evidence_refs": [],
        })
    if registry is None:
        return finding.model_copy(update={
            "evidence_status": EVIDENCE_NOT_EVALUATED,
            "claim_status": CLAIM_UNJUDGED,
        })
    if not finding.evidence_refs:
        return finding.model_copy(update={
            "evidence_status": EVIDENCE_NO_REF,
            "claim_status": CLAIM_UNJUDGED,
        })

    reasons: list[str] = []
    verified_count = 0
    annotated: list[EvidenceRef] = []
    for ref in finding.evidence_refs:
        status, reason = validate_ref(ref, registry, snapshot_sha, allowed_paths)
        entry = registry.find(ref) if status == EVIDENCE_VERIFIED else None
        supplied_id = ref.tool_call_id
        # the registry decides which call delivered this code; a model-supplied id
        # is kept only if it matches, and noted when it does not
        real_call_id = entry.tool_call_id if entry else supplied_id
        if supplied_id and entry and supplied_id != entry.tool_call_id:
            reasons.append("model_tool_call_id_unrecognised")
        if status == EVIDENCE_VERIFIED:
            verified_count += 1
        else:
            reasons.append(reason)
        annotated.append(ref.model_copy(update={
            "evidence_status": status,
            "ref": snapshot_sha,          # pinned by the review, never by the model
            "tool_call_id": real_call_id,
            "tool_name": (entry.tool_name if entry else ref.tool_name),
        }))
    # every cited ref must be verified; one bad citation taints the finding's
    # provenance, because the model may be relying on it
    status = (EVIDENCE_VERIFIED
              if finding.evidence_refs and verified_count == len(finding.evidence_refs)
              else EVIDENCE_UNVERIFIED)
    return finding.model_copy(update={
        "evidence_refs": annotated,
        "evidence_status": status,
        "claim_status": CLAIM_UNJUDGED,
        "evidence_validation_notes": reasons[:6],
    })


def verify_findings(findings: list[ReviewFinding], registry: EvidenceRegistry | None,
                    snapshot_sha: str, allowed_paths: set[str]) -> list[ReviewFinding]:
    return [verify_finding(f, registry, snapshot_sha, allowed_paths) for f in findings]


def registry_fingerprint(registry: EvidenceRegistry) -> str:
    """Stable digest of what was delivered, for reports and tests."""
    blob = json.dumps([e.as_dict() for e in registry.entries], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
