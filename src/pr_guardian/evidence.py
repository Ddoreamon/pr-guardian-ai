"""Evidence provenance: what the model actually received, and what it may cite.

Two things are kept strictly apart:

* **source verification** — the cited file/commit/line range really came back to
  the model, *in full*, during this review, from a real tool call;
* **claim quality** — whether the finding is *correct*. That is not decided here,
  and nothing in this module may set it.

Source verification is deliberately narrow: only lines delivered with a line number
that were not cut by truncation are citable. A search hit proves the retriever
returned a candidate, not that the model read the function.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from .models import EvidenceRef, ReviewFinding

EVIDENCE_DETERMINISTIC = "deterministic"
EVIDENCE_VERIFIED = "source_verified"
EVIDENCE_UNVERIFIED = "source_unverified"
EVIDENCE_NO_REF = "no_evidence_ref"
EVIDENCE_NOT_EVALUATED = "not_evaluated"

CLAIM_UNJUDGED = "claim_unjudged"
CLAIM_DETERMINISTIC = "deterministic"

# a delivered numbered source line, as the tools render it: "   12 | code"
_NUMBERED_LINE = re.compile(r"^\s*(\d+)\s*\|\s?(.*)$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _snippet_hash(text: str) -> str:
    """The short digest persisted with an evidence entry."""
    return _sha256(text)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def numbered_lines(text: object, *, truncated: bool) -> list[int]:
    """Line numbers whose entire text is present in the delivered string.

    When the payload was truncated the final physical line may be cut mid-way, so
    it is dropped rather than treated as read.
    """
    if not isinstance(text, str) or not text:
        return []
    physical = text.splitlines()
    if truncated and physical:
        physical = physical[:-1]
    numbers = []
    for line in physical:
        match = _NUMBERED_LINE.match(line)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


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
    # "read" and "symbol" are citable; "candidate" (a search hit) is not
    kind: str = "read"
    complete_lines: list[int] = field(default_factory=list)
    # the call that produced this evidence, so a saved report can be replayed
    arguments: dict = field(default_factory=dict)

    @property
    def citable(self) -> bool:
        return self.kind in {"read", "symbol"}

    def covers(self, file_path: str, start_line: int, end_line: int) -> bool:
        if not self.citable or self.file_path != file_path:
            return False
        if not (self.start_line <= start_line and end_line <= self.end_line):
            return False
        if not self.complete_lines:
            return False
        return all(line in self.complete_lines for line in range(start_line, end_line + 1))

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
            "kind": self.kind,
            # the count is kept for older readers; the list is what replay verifies
            "complete_lines": len(self.complete_lines),
            "complete_lines_list": sorted(self.complete_lines),
            "arguments": dict(self.arguments),
        }


@dataclass
class EvidenceRegistry:
    """Everything the model was shown, keyed by the tool call that produced it."""

    ref: str
    entries: list[EvidenceEntry] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    # ---- recording -------------------------------------------------------
    def record(self, record, payload: dict) -> None:
        """Record the payload as delivered (after any truncation).

        Only a hit whose payload is bound to *this* commit can create citable
        evidence. Misses, errors, budget denials and cross-SHA payloads are
        refused and counted, never turned into evidence.
        """
        if record.outcome != "hit":
            return
        if not isinstance(payload, dict) or payload.get("ok") is False:
            return
        payload_ref = payload.get("ref")
        call_id = getattr(record, "call_id", "") or ""
        if not isinstance(payload_ref, str) or not payload_ref:
            self.rejected.append({"tool_call_id": call_id, "tool": record.name,
                                  "reason": "payload_ref_missing"})
            return
        if payload_ref != self.ref:
            self.rejected.append({"tool_call_id": call_id, "tool": record.name,
                                  "reason": "payload_ref_mismatch",
                                  "payload_ref": payload_ref})
            return

        truncated = bool(payload.get("truncated"))
        for file_path, start, end, numbers, kind, snippet in self._ranges(
                record.name, payload, truncated):
            self.entries.append(EvidenceEntry(
                tool_call_id=call_id, tool_name=record.name, ref=payload_ref,
                file_path=file_path, start_line=start, end_line=end, hit=True,
                truncated=truncated, snippet_sha256=_snippet_hash(snippet), kind=kind,
                complete_lines=numbers,
                arguments=dict(getattr(record, "arguments", {}) or {}),
            ))

    @staticmethod
    def _ranges(name: str, payload: dict, truncated: bool
                ) -> list[tuple[str, int, int, list[int], str, str]]:
        out: list[tuple[str, int, int, list[int], str, str]] = []
        if name == "read_code_context":
            file_path = payload.get("file_path")
            content = payload.get("content")
            numbers = numbered_lines(content, truncated=truncated)
            if isinstance(file_path, str) and numbers:
                out.append((file_path, min(numbers), max(numbers), numbers,
                            "read", str(content)))
        elif name == "find_symbol_definition":
            for match in payload.get("matches") or []:
                file_path = match.get("file_path")
                snippet = str(match.get("snippet", ""))
                numbers = numbered_lines(snippet, truncated=truncated)
                if isinstance(file_path, str) and numbers:
                    out.append((file_path, min(numbers), max(numbers), numbers,
                                "symbol", snippet))
        elif name == "search_code":
            # A hit proves the retriever returned a candidate. Its snippet carries
            # no line numbers, so it cannot prove any line was read.
            for hit in payload.get("hits") or []:
                file_path = hit.get("file_path")
                start = hit.get("start_line")
                end = hit.get("end_line")
                if isinstance(file_path, str) and isinstance(start, int) and isinstance(end, int):
                    out.append((file_path, start, end, [], "candidate",
                                str(hit.get("snippet", ""))))
        return out

    # ---- lookup ----------------------------------------------------------
    def find(self, ref: EvidenceRef) -> EvidenceEntry | None:
        """Resolve which delivered evidence covers this range, for citation.

        Verification is by file and fully delivered lines, not by the id the model
        echoed: provider call ids are opaque strings it cannot reliably reproduce.
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

    def candidate_covers(self, file_path: str, start_line: int, end_line: int) -> bool:
        """Was this range returned only as a retrieval candidate?"""
        return any(not e.citable and e.file_path == file_path
                   and e.start_line <= start_line and end_line <= e.end_line
                   for e in self.entries)

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
    # A value that is not a commit SHA is a formatting slip, not a citation of
    # another commit: the commit is pinned by the review, not chosen by the model.
    supplied = (ref.ref or "").strip().lower()
    if supplied and _FULL_SHA.match(supplied) and supplied != snapshot_sha:
        return EVIDENCE_UNVERIFIED, "sha_mismatch"
    if ref.file_path not in allowed_paths:
        return EVIDENCE_UNVERIFIED, "path_not_allowed"
    if ref.start_line < 1 or ref.end_line < ref.start_line:
        return EVIDENCE_UNVERIFIED, "bad_range"
    if registry.ref != snapshot_sha:
        return EVIDENCE_UNVERIFIED, "registry_sha_mismatch"
    entry = registry.find(ref)
    if entry is None:
        if registry.candidate_covers(ref.file_path, ref.start_line, ref.end_line):
            return EVIDENCE_UNVERIFIED, "candidate_only"
        return EVIDENCE_UNVERIFIED, "not_delivered_to_model"
    if entry.ref != snapshot_sha:
        return EVIDENCE_UNVERIFIED, "entry_sha_mismatch"
    if not entry.tool_call_id:
        return EVIDENCE_UNVERIFIED, "evidence_has_no_call_id"
    return EVIDENCE_VERIFIED, "ok"


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

    # every cited range must be fully delivered; one bad citation taints the
    # finding's provenance, because the model may be relying on it
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
    blob = json.dumps({"entries": [e.as_dict() for e in registry.entries],
                       "rejected": registry.rejected}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
