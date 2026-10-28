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
import logging
from typing import Any

from pydantic import BaseModel, Field

from .agent_tools import ToolError
from .evidence import EVIDENCE_VERIFIED, EvidenceRegistry, validate_ref
from .models import EvidenceRef

LOGGER = logging.getLogger("pr_guardian.sufficiency")

ASSESSMENT_VERSION = "v4.5.2"

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
    # empty | analysable | unanalysable: an empty diff is not a failed analysis
    diff_state: str = "analysable"
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
    # Default values are part of the contract for callers that omit the argument.
    defaults = [ast.unparse(d) for d in list(args.defaults) + [d for d in args.kw_defaults if d]]
    # The return *shape* stands in for a contract change that has no annotation,
    # but only when a container appears or disappears: str -> list is a contract
    # change, int -> int * 3 is not.
    shapes = sorted({type(sub.value).__name__
                     for sub in ast.walk(node) if isinstance(sub, ast.Return)
                     and sub.value is not None})
    return {"args": names, "kwonly": kwonly, "vararg": bool(args.vararg),
            "kwarg": bool(args.kwarg), "raises": sorted(raises), "returns": returns,
            "return_shapes": shapes, "defaults": defaults}


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


_CONTAINERS = {"ListComp", "List", "Dict", "DictComp", "Tuple", "Set", "SetComp"}


def _container_changed(before: dict, after: dict) -> bool:
    old = set(before.get("return_shapes") or [])
    new = set(after.get("return_shapes") or [])
    if old == new:
        return False
    return bool((old | new) & _CONTAINERS)


def _executable(node: ast.AST) -> str:
    """The AST text of a body without docstrings; comments never survive parsing."""
    body = list(getattr(node, "body", []) or [])
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


def _module_executable(tree: ast.AST) -> str:
    """Module-level statements only: definition removal is handled separately."""
    body = [node for node in getattr(tree, "body", [])
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                     and isinstance(node.value.value, str))]
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


def _aliases(tree: ast.AST) -> dict[str, str]:
    """local name -> original name for `from x import y as z`."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname:
                    out[alias.asname] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    out[alias.asname] = alias.name.split(".")[-1]
    return out


def _import_origins(tree: ast.AST) -> dict[str, tuple[str, str]]:
    """local name -> (module stem, original name), for import-aware matching."""
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            stem = node.module.split(".")[-1]
            for alias in node.names:
                out[alias.asname or alias.name] = (stem, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                stem = alias.name.split(".")[-1]
                out[alias.asname or stem] = (stem, stem)
    return out


def _module_stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".py")


def _decorated(node: ast.AST) -> bool:
    return bool(getattr(node, "decorator_list", []))


def _registry_referenced(name: str, source: str) -> bool:
    """Is the name stored in a module-level mapping (registry dispatch)?"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for value in node.values:
                if isinstance(value, ast.Name) and value.id == name:
                    return True
                if isinstance(value, ast.Attribute) and value.attr == name:
                    return True
    return False


def _removed_definitions(changed_files, head_index, base) -> list[tuple[str, str, list[int]]]:
    """(file, qualname, deleted lines) for definitions the head no longer has."""
    removed: list[tuple[str, str, list[int]]] = []
    for changed in changed_files:
        if not changed.deleted_lines:
            continue
        try:
            base_source = base.read_source(changed.file_path)
        except ToolError:  # the file does not exist at base; nothing to compare
            LOGGER.debug("no base version for %s", changed.file_path)
            continue
        try:
            base_nodes = _function_nodes(ast.parse(base_source))
        except SyntaxError:
            continue
        head_chunks = [c for c in head_index.chunks if c.file_path == changed.file_path]
        head_names = {c.qualname for c in head_chunks}
        for qualname, node in base_nodes:
            span = (getattr(node, "lineno", 0), getattr(node, "end_lineno", 0) or 0)
            touched = any(span[0] <= line <= span[1] for line in changed.deleted_lines)
            if touched and qualname not in head_names:
                removed.append((changed.file_path, qualname, list(changed.deleted_lines)))
    return removed


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
            if old is not None:
                before, after = _signature(old), _signature(node)
                for key in ("args", "kwonly", "vararg", "kwarg", "raises",
                            "returns", "defaults"):
                    if before.get(key) != after.get(key):
                        contract_changed = True
                        break
                if not contract_changed and _container_changed(before, after):
                    contract_changed = True

        # callees reached from the changed definition; aliases are resolved first
        aliases = _aliases(head_tree)
        for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
            func = call.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else "")
            if not name:
                continue
            name = aliases.get(name, name)
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

        # a decorator or a registry mapping hides the call sites
        if _decorated(node) or _registry_referenced(chunk.name, source):
            obligations.append(EvidenceObligation(
                obligation_id=_oid("dispatch", chunk.qualname),
                kind=UNKNOWN_DEPENDENCY, file_path=chunk.file_path,
                qualname=chunk.qualname,
                required_span=(chunk.start_line, chunk.end_line),
                rationale=("the definition is registered or decorated, so its callers "
                           "cannot be enumerated statically"),
                derivation_method="AST decorator / module-level mapping scan",
                confidence="low", required=False))

        # callers of a definition whose contract changed
        if contract_changed:
            for other in index.chunks:
                # a caller in the same file breaks just as hard as a remote one
                if (other.file_path == chunk.file_path
                        and other.start_line == chunk.start_line):
                    continue
                # a caller must actually import this definition, not just share a name
                try:
                    other_tree = ast.parse(head.read_source(other.file_path))
                except (SyntaxError, ToolError):
                    continue
                origins = _import_origins(other_tree)
                target_stem = _module_stem(chunk.file_path)
                local_names = {local for local, origin in origins.items()
                               if origin == (target_stem, chunk.name)}
                # only a same-file bare call may fall back to the plain name;
                # a cross-file caller that imports the same name from elsewhere is
                # a different function
                if (not local_names and other.file_path == chunk.file_path
                        and chunk.name not in origins):
                    local_names = {chunk.name}
                if not (local_names & set(other.calls or [])):
                    continue
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
                # the changed contract may travel further than one hop
                if any(other.qualname.split(".")[-1] in (deeper.calls or [])
                       for deeper in index.chunks
                       if deeper.qualname != other.qualname):
                    obligations.append(EvidenceObligation(
                        obligation_id=_oid("transitive", chunk.qualname,
                                          other.qualname),
                        kind=UNKNOWN_DEPENDENCY, file_path=other.file_path,
                        qualname=other.qualname,
                        required_span=(other.start_line, other.end_line),
                        rationale=("the changed contract propagates through this "
                                   "caller to at least one more caller"),
                        derivation_method="two-hop call edge from the index",
                        confidence="low", required=False))

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

    deleted_obligations, deletion_states = _deleted_obligations(
        head, base, changed_files, index)
    obligations.extend(deleted_obligations)

    removed = _removed_definitions(changed_files, index, base)
    removed_files = {file_path for file_path, _q, _l in removed}
    attributed = {(o.file_path, o.kind) for o in obligations}
    for changed in changed_files:
        if not (changed.added_lines or changed.deleted_lines):
            continue
        if deletion_states.get(changed.file_path) == "comment_only":
            continue
        if changed.file_path in removed_files:
            continue
        if (changed.file_path, MODIFIED_DEFINITION) in attributed:
            continue
        if (changed.file_path, CALLEE_CONTRACT) in attributed:
            continue
        obligations.append(EvidenceObligation(
            obligation_id=_oid("unanalysable", changed.file_path),
            kind=UNKNOWN_DEPENDENCY, file_path=changed.file_path, qualname="",
            rationale=("the change lies outside the indexed definitions, so no "
                       "evidence duty could be attributed to it"),
            derivation_method="diff present, no enclosing definition matched",
            confidence="low", required=False))

    for file_path, qualname, _deleted in removed:
        stem = _module_stem(file_path)
        short = qualname.split(".")[-1]
        callers = []
        for candidate in index.chunks:
            if candidate.file_path == file_path:
                continue
            try:
                origins = _import_origins(ast.parse(head.read_source(candidate.file_path)))
            except (SyntaxError, ToolError):
                continue
            locals_for_target = {local for local, origin in origins.items()
                                 if origin == (stem, short)}
            if locals_for_target & set(candidate.calls or []):
                callers.append(candidate)
        if callers:
            for caller in callers:
                obligations.append(EvidenceObligation(
                    obligation_id=_oid("removed-caller", qualname, caller.file_path,
                                      caller.qualname),
                    kind=CALLER_USAGE, file_path=caller.file_path,
                    qualname=caller.qualname,
                    required_span=(caller.start_line, caller.end_line),
                    rationale=(f"{qualname} is removed and {caller.qualname} calls it"),
                    derivation_method="base AST diff + index call edge",
                    confidence="medium", required=True))
        else:
            obligations.append(EvidenceObligation(
                obligation_id=_oid("removed-unknown", file_path, qualname),
                kind=UNKNOWN_DEPENDENCY, file_path=file_path, qualname=qualname,
                rationale=(f"{qualname} is removed and no static caller was found; "
                           f"its users cannot be enumerated"),
                derivation_method="base AST diff + index search",
                confidence="low", required=False))

    return _dedupe(obligations)


def _deleted_obligations(head, base, changed_files, index) -> tuple[
        list[EvidenceObligation], dict[str, str]]:
    """Obligations created by removed lines, including guard/return/handler removal."""
    from .repo_index import build_index  # noqa: F401 - index is passed in

    head_chunks = {(c.file_path, c.qualname): c for c in index.chunks}
    out: list[EvidenceObligation] = []
    states: dict[str, str] = {}
    for changed in changed_files:
        if not changed.deleted_lines:
            continue
        try:
            base_tree = ast.parse(base.read_source(changed.file_path))
        except (SyntaxError, ToolError):
            continue
        base_nodes = dict(_function_nodes(base_tree))
        try:
            head_tree = ast.parse(head.read_source(changed.file_path))
            head_nodes = dict(_function_nodes(head_tree))
        except (SyntaxError, ToolError):
            head_tree, head_nodes = None, {}

        covered: set[int] = set()
        for qualname, node in base_nodes.items():
            span = (getattr(node, "lineno", 0), getattr(node, "end_lineno", 0) or 0)
            touched = [line for line in changed.deleted_lines
                       if span[0] <= line <= span[1]]
            if not touched:
                continue
            covered.update(touched)
            current = head_nodes.get(qualname)
            if current is None:
                continue  # removal is handled by _removed_definitions
            if _executable(node) == _executable(current):
                states[changed.file_path] = "comment_only"
                continue  # comment or docstring only: no behaviour obligation
            span_head = (getattr(current, "lineno", 0), getattr(current, "end_lineno", 0) or 0)
            chunk = head_chunks.get((changed.file_path, qualname))
            required_span = span_head if span_head[0] else (
                (chunk.start_line, chunk.end_line) if chunk else (0, 0))
            states[changed.file_path] = "analysed"
            out.append(EvidenceObligation(
                obligation_id=_oid("deleted-lines", changed.file_path, qualname),
                kind=MODIFIED_DEFINITION, file_path=changed.file_path,
                qualname=qualname,
                required_span=required_span,
                rationale="lines were removed from this definition and its behaviour changed",
                derivation_method="base/head AST body comparison over deleted lines",
                confidence="high", required=True))

        if not covered and not changed.added_lines:
            states.setdefault(changed.file_path, "comment_only")
        for line in changed.deleted_lines:
            for chunk in index.chunks:
                if chunk.file_path == changed.file_path and \
                        chunk.start_line <= line <= chunk.end_line:
                    covered.add(line)
                    break
        leftover = [line for line in changed.deleted_lines if line not in covered]
        if leftover and head_tree is not None:
            try:
                base_module = _module_executable(base_tree)
                head_module = _module_executable(head_tree)
            except SyntaxError:
                base_module = head_module = ""
            if base_module != head_module:
                out.append(EvidenceObligation(
                    obligation_id=_oid("module-deletion", changed.file_path),
                    kind=UNKNOWN_DEPENDENCY, file_path=changed.file_path,
                    qualname="",
                    rationale=("module-level code was removed outside any indexed "
                               "definition; its effect cannot be attributed"),
                    derivation_method="module AST comparison over deleted lines",
                    confidence="low", required=False))
    return out, states


def _dedupe(obligations: list[EvidenceObligation]) -> list[EvidenceObligation]:
    """One duty per (kind, file, symbol); different unknowns stay separate."""
    seen: set[tuple] = set()
    out = []
    for obligation in obligations:
        key: tuple = (obligation.kind, obligation.file_path, obligation.qualname)
        if obligation.kind == UNKNOWN_DEPENDENCY:
            key = (*key, obligation.obligation_id)
        if key in seen:
            continue
        seen.add(key)
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


def classify_diff(head, base, changed_files) -> str:
    """empty | comment_only | unanalysable | analysable.

    An empty diff is not a failed analysis, and a comment-only deletion is not an
    unattributable change. Keeping them apart is what lets `assess` explain itself.
    """
    touched = [c for c in changed_files if c.added_lines or c.deleted_lines]
    if not touched:
        return "empty"
    every_file_comment_only = True
    for changed in touched:
        if changed.added_lines:
            every_file_comment_only = False
            continue
        try:
            base_tree = ast.parse(base.read_source(changed.file_path))
            head_tree = ast.parse(head.read_source(changed.file_path))
        except (SyntaxError, ToolError):
            every_file_comment_only = False
            continue
        base_nodes = dict(_function_nodes(base_tree))
        head_nodes = dict(_function_nodes(head_tree))
        explained = False
        for qualname, node in base_nodes.items():
            span = (getattr(node, "lineno", 0), getattr(node, "end_lineno", 0) or 0)
            if not any(span[0] <= line <= span[1] for line in changed.deleted_lines):
                continue
            current = head_nodes.get(qualname)
            if current is not None and _executable(node) == _executable(current):
                explained = True
        if not explained:
            every_file_comment_only = False
    if every_file_comment_only:
        return "comment_only"
    obligations = derive_obligations(head, base, changed_files)
    return "analysable" if obligations else "unanalysable"


def assess(registry: EvidenceRegistry, obligations: list[EvidenceObligation],
           snapshot_sha: str, allowed_paths: set[str],
           diff_state: str = "analysable") -> EvidenceSufficiencyResult:
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

    if diff_state == "empty":
        return EvidenceSufficiencyResult(
            status=SUFFICIENT, snapshot_sha=snapshot_sha, diff_state="empty",
            obligations=[], fulfilled_obligations=[], missing_obligations=[],
            unknown_obligations=[], evidence_refs=[],
            explanation=("the diff is empty: there is nothing to review, and no claim "
                         "is made about the code"))

    if diff_state == "comment_only":
        return EvidenceSufficiencyResult(
            status=SUFFICIENT, snapshot_sha=snapshot_sha, diff_state="comment_only",
            obligations=[], fulfilled_obligations=[], missing_obligations=[],
            unknown_obligations=[], evidence_refs=[],
            explanation=("only comments or docstrings were removed; the parsed code of "
                         "every affected definition is unchanged, so there is no "
                         "evidence duty"))

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

    if not obligations:
        status = UNKNOWN
        explanation = ("the diff is not empty but no evidence duty could be derived; "
                       "treating it as sufficient would be unsafe")

    return EvidenceSufficiencyResult(
        status=status, snapshot_sha=snapshot_sha, diff_state=diff_state,
        obligations=assessed,
        fulfilled_obligations=fulfilled, missing_obligations=missing,
        unknown_obligations=unknown, evidence_refs=refs, explanation=explanation)
