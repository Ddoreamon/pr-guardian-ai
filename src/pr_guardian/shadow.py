"""Shadow replay: can the evidence a review received be reconstructed exactly?

A persisted report stores one `evidence_sources` entry per delivered range. Replay
re-executes the same tool call against the pinned snapshot and compares the result
with what the report claims was delivered:

* the numbered lines that were complete,
* the snippet hash of the delivered text,
* the commit the payload was bound to.

Only an `exact` replay may produce a verdict other than `unknown`: re-reading a file
is not the same as the model having received it, so a replay that cannot prove
equality must never be reported as sufficient.
"""

from __future__ import annotations

from .evidence import EvidenceEntry, EvidenceRegistry, _snippet_hash

REPLAY_EXACT = "exact"
REPLAY_MISMATCH = "mismatch"
REPLAY_UNVERIFIABLE = "unverifiable"
REPLAY_SHA_MISMATCH = "sha_mismatch"
REPLAY_SOURCE_MISSING = "source_missing"

_TOOL_ARGS = {
    "read_code_context": ("file_path", "start_line", "end_line"),
    "find_symbol_definition": ("symbol",),
    "search_code": ("query", "top_k"),
}


def _replay_call(codebase, entry: dict):
    """Re-run the recorded tool call against the pinned snapshot."""
    name = entry.get("tool_name") or entry.get("tool") or ""
    args = entry.get("arguments")
    if not isinstance(args, dict):
        return None, REPLAY_UNVERIFIABLE
    if name == "read_code_context":
        path = args.get("file_path")
        start = args.get("start_line")
        end = args.get("end_line")
        if not isinstance(path, str) or not isinstance(start, int) or not isinstance(end, int):
            return None, REPLAY_UNVERIFIABLE
        return codebase.read_code_context(path, start, end), REPLAY_EXACT
    if name == "find_symbol_definition":
        symbol = args.get("symbol")
        if not isinstance(symbol, str):
            return None, REPLAY_UNVERIFIABLE
        return codebase.find_symbol_definition(symbol), REPLAY_EXACT
    if name == "search_code":
        query = args.get("query")
        if not isinstance(query, str):
            return None, REPLAY_UNVERIFIABLE
        return codebase.search_code(query, int(args.get("top_k") or 5)), REPLAY_EXACT
    return None, REPLAY_UNVERIFIABLE


def replay_registry(codebase, evidence_sources: list[dict]) -> tuple[EvidenceRegistry, str, list[str]]:
    """Rebuild the registry from the report and verify it against the snapshot.

    Returns (registry, status, notes). The registry contains only what the report
    claims was delivered; the re-read payload is used for verification, not as a
    substitute for the model's input.
    """
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    notes: list[str] = []
    status = REPLAY_EXACT

    for index, entry in enumerate(evidence_sources or []):
        if not isinstance(entry, dict):
            return registry, REPLAY_MISMATCH, [f"entry {index}: not an object"]
        ref = entry.get("ref")
        if ref != codebase.snapshot_sha:
            notes.append(f"entry {index}: bound to {ref!r}, snapshot is "
                         f"{codebase.snapshot_sha!r}")
            return registry, REPLAY_SHA_MISMATCH, notes

        payload, state = _replay_call(codebase, entry)
        if state != REPLAY_EXACT:
            notes.append(f"entry {index}: {state} (no usable tool arguments)")
            status = REPLAY_UNVERIFIABLE
            continue
        if not isinstance(payload, dict) or payload.get("ok") is False:
            notes.append(f"entry {index}: the snapshot no longer serves this call")
            return registry, REPLAY_SOURCE_MISSING, notes

        try:
            ranges = EvidenceRegistry._ranges(  # same parser the registry used
                str(entry.get("tool_name", "")), payload, bool(payload.get("truncated")))
        except Exception:  # noqa: BLE001 - a payload we cannot parse is a failure
            return registry, REPLAY_MISMATCH, [f"entry {index}: unparsable payload"]

        match = None
        for file_path, start, end, numbers, kind, snippet in ranges:
            if (file_path == entry.get("file_path")
                    and start == entry.get("start_line")
                    and end == entry.get("end_line")
                    and kind == entry.get("kind", "read")):
                match = (numbers, _snippet_hash(snippet))
                break
        if match is None:
            notes.append(f"entry {index}: the snapshot yields no range matching "
                         f"{entry.get('file_path')}:{entry.get('start_line')}-"
                         f"{entry.get('end_line')}")
            return registry, REPLAY_MISMATCH, notes

        numbers, digest = match
        if entry.get("snippet_sha256") and entry["snippet_sha256"] != digest:
            notes.append(f"entry {index}: snippet hash differs from the snapshot")
            return registry, REPLAY_MISMATCH, notes
        claimed = entry.get("complete_lines_list")
        if claimed is None:
            notes.append(f"entry {index}: report has no complete-line list")
            status = REPLAY_UNVERIFIABLE
            continue
        if sorted(claimed) != sorted(numbers):
            notes.append(f"entry {index}: complete lines differ "
                         f"({len(claimed)} vs {len(numbers)})")
            return registry, REPLAY_MISMATCH, notes

        registry.entries.append(EvidenceEntry(
            tool_call_id=str(entry.get("tool_call_id", "")),
            tool_name=str(entry.get("tool_name", "")),
            ref=str(ref), file_path=entry["file_path"],
            start_line=int(entry["start_line"]), end_line=int(entry["end_line"]),
            hit=True, truncated=bool(entry.get("truncated", False)),
            snippet_sha256=digest, kind=entry.get("kind", "read"),
            complete_lines=sorted(numbers),
            arguments=entry.get("arguments") or {},
        ))

    if status == REPLAY_EXACT and len(registry.entries) != len(evidence_sources or []):
        status = REPLAY_UNVERIFIABLE
        notes.append("some entries could not be verified")
    return registry, status, notes


def assess_report(report: dict, codebase, base, changed_files, *,
                  obligations, classifier) -> dict:
    """Replay a saved report and assess it, refusing to over-claim on bad replay."""
    from .sufficiency import SUFFICIENT, UNKNOWN, assess

    evidence_sources = report.get("evidence_sources") or []
    registry, replay_status, notes = replay_registry(codebase, evidence_sources)
    diff_state = classifier(codebase, base, changed_files)
    result = assess(registry, obligations, codebase.snapshot_sha,
                    set(codebase.source_files()), diff_state=diff_state)

    if replay_status != REPLAY_EXACT:
        result = result.model_copy(update={
            "status": UNKNOWN,
            "explanation": (f"replay is {replay_status}, so the evidence the model "
                            f"received cannot be proven; the snapshot re-read is not a "
                            f"substitute for it"),
        })
    elif replay_status == REPLAY_EXACT and result.status == SUFFICIENT:
        result = result.model_copy(update={
            "explanation": ("every derived obligation is covered by evidence that "
                            "replays exactly from the saved report"),
        })

    return {
        "sufficiency": result.model_dump(mode="json"),
        "replay_status": replay_status,
        "replay_notes": notes,
        "snapshot_sha": codebase.snapshot_sha,
    }
