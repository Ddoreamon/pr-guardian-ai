"""Evidence sufficiency: are the *predefined evidence obligations* covered?

Three ideas are kept strictly apart:

* `source_verified` (V4.1) — the cited lines were really delivered to the model;
* `evidence_sufficient` (V4.5) — the obligations derived from the diff are covered;
* `claim_correct` — whether the finding is right. Nothing here judges it.

Obligations are derived **only** from trusted inputs: the pinned GitSnapshot, the
diff, ASTs and the trusted index. Hidden tests, gold labels, evaluation CSVs and
model self-reports are never inputs, and no case identifier appears in this module.
"""

from __future__ import annotations

import ast
import hashlib
from typing import Any

from pydantic import BaseModel, Field

from .evidence import EVIDENCE_VERIFIED, EvidenceRegistry, validate_ref
from .models import EvidenceRef

ASSESSMENT_VERSION = "v4.5.1"

MODIFIED_DEFINITION = "modified_definition"
CALLEE_CONTRACT = "callee_contract"
CALLER_USAGE = "caller_usage"
CONFIGURATION_DEPENDENCY = "configuration_dependency"
BOUNDARY_CONDITION = "boundary_condition"
UNKNOWN_DEPENDENCY = "unknown_dependency"

KINDS = (MODIFIED_DEFINITION, CALLEE_CONTRACT, CALLER_USAGE,
         CONFIGURATION_DEPENDENCY, BOUNDARY_CONDITION, UNKNOWN_DEPENDENCY)

STATUS_FULFILLED = "fulfilled"
STATUS_MISSING = "missing"
STATUS_UNKNOWN = "unknown"
STATUS_OPTIONAL = "optional_unmet"

SUFFICIENT = "sufficient"
INSUFFICIENT = "insufficient"
UNKNOWN = "unknown"

_DYNAMIC_CALLS = {"eval", "exec", "getattr", "setattr", "compile", "globals", "locals"}


class EvidenceObligation(BaseModel):
    """One evidence duty derived from the diff, never from the answer."""

    obligation_id: str
    kind: str
    file_path: str
    qualname: str = ""
    required_span: tuple[int, int] | None = None
    rationale: str = ""
    derivation_method: str = ""
    # high: directly visible in the diff; medium: static inference; low: guess
    confidence: str = "high"
    required: bool = True
    status: str = "pending"


class EvidenceSufficiencyResult(BaseModel):
    status: str
    snapshot_sha: str
    assessment_version: str = ASSESSMENT_VERSION
    obligations: list[EvidenceObligation] = Field(default_factory=list)
    fulfilled_obligations: list[str] = Field(default_factory=list)
    missing_obligations: list[str] = Field(default_factory=list)
    unknown_obligations: list[str] = Field(default_factory=list)
    evidence_refs: list[dict] = Field(default_factory=list)
    explanation: str = ""

    def by_id(self, obligation_id: str) -> EvidenceObligation | None:
        return next((o for o in self.obligations if o.obligation_id == obligation_id), None)


def _oid(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return "ob-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def _function_nodes(tree: ast.AST) -> list[tuple[str, ast.AST]]:
    out: list[tuple[str, ast.AST]] = []

    def visit(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + node.name
                out.append((qual, node))
            elif isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + ".")

    visit(getattr(tree, "body", []), "")
    return out


def _signature(node: ast.AST) -> dict[str, Any]:
    args = getattr(node, "args", None)
    if args is None:
        return {}
    names = [a.arg for a in list(args.posonlyargs) + list(args.args)]
    kwonly = [a.arg for a in args.kwonlyargs]
    raises = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Raise) and isinstance(sub.exc, ast.Call):
            func = sub.exc.func
            if isinstance(func, ast.Name):
                raises.add(func.id)
            elif isinstance(func, ast.Attribute):
                raises.add(func.attr)
    returns = ast.unparse(node.returns) if getattr(node, "returns", None) else ""
    # The return *shape* stands in for a contract change that has no annotation:
    # a call returning a string and a list comprehension returning a list are
    # different contracts even though both are plain `return` statements.
    shapes = sorted({type(sub.value).__name__
                     for sub in ast.walk(node) if isinstance(sub, ast.Return)
                     and sub.value is not None})
    return {"args": names, "kwonly": kwonly, "vararg": bool(args.vararg),
            "kwarg": bool(args.kwarg), "raises": sorted(raises), "returns": returns,
            "return_shapes": shapes}


def _changed_definitions(head_index, changed_files) -> list[tuple[Any, list[int]]]:
    """Head chunks whose spans contain an added line."""
    out = []
    for changed in changed_files:
        if not changed.added_lines:
            continue
        for chunk in head_index.chunks:
            if chunk.file_path != changed.file_path:
                continue
            if any(chunk.start_line <= line <= chunk.end_line
                   for line in changed.added_lines):
                out.append((chunk, changed.added_lines))
    return out


def _is_dynamic(node: ast.AST) -> list[str]:
    found: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else "")
            if name in _DYNAMIC_CALLS:
                found.append(name)
            if any(isinstance(a, ast.Starred) for a in sub.args) or any(
                    kw.arg is None for kw in sub.keywords):
                found.append("**unpacking")
    return sorted(set(found))


def derive_obligations(head, base, changed_files) -> list[EvidenceObligation]:
    """Derive the evidence duties of this diff from trusted inputs only."""
    from .repo_index import build_index

    index = build_index(head)
    obligations: list[EvidenceObligation] = []
    by_qualname: dict[str, list] = {}
    for chunk in index.chunks:
        by_qualname.setdefault(chunk.qualname, []).append(chunk)

    for chunk, _added in _changed_definitions(index, changed_files):
        source = head.read_source(chunk.file_path)
        head_tree = ast.parse(source)
        head_nodes = dict(_function_nodes(head_tree))
        node = head_nodes.get(chunk.qualname)
        obligations.append(EvidenceObligation(
            obligation_id=_oid("modified", chunk.file_path, chunk.qualname),
            kind=MODIFIED_DEFINITION, file_path=chunk.file_path,
            qualname=chunk.qualname,
            required_span=(chunk.start_line, chunk.end_line),
            rationale="the diff changes this definition",
            derivation_method="diff line inside the AST span at head",
            confidence="high", required=True))

        if node is None:
            continue

        # contract change: compare with the same definition at the base commit
        contract_changed = False
        base_source = None
        try:
            base_source = base.read_source(chunk.file_path)
        except Exception:  # noqa: BLE001 - file may not exist at base
            base_source = None
        if base_source:
            try:
                base_nodes = dict(_function_nodes(ast.parse(base_source)))
            except SyntaxError:
                base_nodes = {}
            old = base_nodes.get(chunk.qualname)
            if old is not None and _signature(old) != _signature(node):
                contract_changed = True

        # callees reached from the changed definition
        for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
            func = call.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else "")
            if not name:
                continue
            for target in by_qualname.get(name, []):
                if target.file_path == chunk.file_path:
                    continue
                obligations.append(EvidenceObligation(
                    obligation_id=_oid("callee", chunk.qualname, target.file_path,
                                      target.qualname),
                    kind=CALLEE_CONTRACT, file_path=target.file_path,
                    qualname=target.qualname,
                    required_span=(target.start_line, target.end_line),
                    rationale=f"{chunk.qualname} calls {name}, defined in another file",
                    derivation_method="AST call name resolved through the index",
                    confidence="high", required=True))

        # callers of a definition whose contract changed
        if contract_changed:
            for other in index.chunks:
                # a caller in the same file breaks just as hard as a remote one
                if (other.file_path == chunk.file_path
                        and other.start_line == chunk.start_line):
                    continue
                if chunk.name in (other.calls or []):
                    obligations.append(EvidenceObligation(
                        obligation_id=_oid("caller", chunk.qualname, other.file_path,
                                          other.qualname),
                        kind=CALLER_USAGE, file_path=other.file_path,
                        qualname=other.qualname,
                        required_span=(other.start_line, other.end_line),
                        rationale=(f"{chunk.qualname} changes its signature or raised "
                                   f"exceptions, and {other.qualname} calls it"),
                        derivation_method="signature diff + index call edge",
                        confidence="medium", required=True))

        # configuration constants read from another file
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                for target in by_qualname.get(sub.id, []):
                    if target.kind == "constant" and target.file_path != chunk.file_path:
                        obligations.append(EvidenceObligation(
                            obligation_id=_oid("config", chunk.qualname, sub.id),
                            kind=CONFIGURATION_DEPENDENCY, file_path=target.file_path,
                            qualname=target.qualname,
                            required_span=(target.start_line, target.end_line),
                            rationale=f"{sub.id} is a module constant from another file",
                            derivation_method="Name load resolved to a constant chunk",
                            confidence="high", required=True))

        dynamic = _is_dynamic(node)
        if dynamic:
            obligations.append(EvidenceObligation(
                obligation_id=_oid("dynamic", chunk.qualname),
                kind=UNKNOWN_DEPENDENCY, file_path=chunk.file_path,
                qualname=chunk.qualname,
                required_span=(chunk.start_line, chunk.end_line),
                rationale=f"dynamic construct(s): {', '.join(dynamic)}",
                derivation_method="AST scan for dynamic calls or unpacking",
                confidence="low", required=False))

    return _dedupe(obligations)


def _dedupe(obligations: list[EvidenceObligation]) -> list[EvidenceObligation]:
    seen: set[str] = set()
    out = []
    for obligation in obligations:
        if obligation.obligation_id in seen:
            continue
        seen.add(obligation.obligation_id)
        out.append(obligation)
    return out


def _covered(obligation: EvidenceObligation, registry: EvidenceRegistry,
             snapshot_sha: str, allowed_paths: set[str]) -> tuple[bool, str]:
    if obligation.required_span is None:
        return False, "no_span"
    start, end = obligation.required_span
    ref = EvidenceRef(ref=snapshot_sha, file_path=obligation.file_path,
                      start_line=start, end_line=end)
    status, reason = validate_ref(ref, registry, snapshot_sha, allowed_paths)
    if status != EVIDENCE_VERIFIED:
        return False, reason
    entry = registry.find(ref)
    return True, (entry.tool_call_id if entry else "ok")


def assess(registry: EvidenceRegistry, obligations: list[EvidenceObligation],
           snapshot_sha: str, allowed_paths: set[str]) -> EvidenceSufficiencyResult:
    """Decide whether the derived obligations are covered by delivered evidence."""
    fulfilled: list[str] = []
    missing: list[str] = []
    unknown: list[str] = []
    refs: list[dict] = []
    assessed: list[EvidenceObligation] = []

    for obligation in obligations:
        if obligation.kind == UNKNOWN_DEPENDENCY:
            unknown.append(obligation.obligation_id)
            assessed.append(obligation.model_copy(update={"status": STATUS_UNKNOWN}))
            continue
        ok, detail = _covered(obligation, registry, snapshot_sha, allowed_paths)
        if ok:
            fulfilled.append(obligation.obligation_id)
            refs.append({"obligation_id": obligation.obligation_id,
                         "file_path": obligation.file_path,
                         "span": list(obligation.required_span or (0, 0)),
                         "tool_call_id": detail})
            assessed.append(obligation.model_copy(update={"status": STATUS_FULFILLED}))
        elif obligation.required:
            missing.append(obligation.obligation_id)
            assessed.append(obligation.model_copy(update={"status": STATUS_MISSING}))
        else:
            assessed.append(obligation.model_copy(update={"status": STATUS_OPTIONAL}))

    if missing:
        status = INSUFFICIENT
        explanation = ("required evidence was not delivered for: " +
                       ", ".join(missing[:4]))
    elif unknown:
        status = UNKNOWN
        explanation = ("required evidence is covered, but dynamic constructs mean the "
                       "obligation set cannot be proven complete: " + ", ".join(unknown[:4]))
    else:
        status = SUFFICIENT
        explanation = ("every derived obligation was covered by delivered source lines; "
                       "this proves coverage of the duties, not correctness of the code")

    return EvidenceSufficiencyResult(
        status=status, snapshot_sha=snapshot_sha, obligations=assessed,
        fulfilled_obligations=fulfilled, missing_obligations=missing,
        unknown_obligations=unknown, evidence_refs=refs, explanation=explanation)
