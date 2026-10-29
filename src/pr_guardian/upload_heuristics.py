"""A short, explicit list of heuristic checks. Suspicious by construction.

None of these is presented as a proven defect: every finding is `suspected` and names
the pattern it matched. The list is deliberately small and explainable rather than a
pretend vulnerability scanner.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

RULE_EVAL = "UP001"
RULE_SHELL = "UP002"
RULE_OS_SYSTEM = "UP003"
RULE_PICKLE = "UP004"
RULE_BARE_EXCEPT = "UP005"
RULE_MUTABLE_DEFAULT = "UP006"
RULE_HARDCODED_SECRET = "UP007"

SECRET_HINTS = ("password", "passwd", "secret", "token", "api_key", "apikey",
                "access_key", "private_key")


@dataclass(frozen=True)
class Heuristic:
    rule: str
    severity: str
    message: str
    suggestion: str


EVAL = Heuristic(RULE_EVAL, "high",
                 "eval()/exec() executes a string as code",
                 "Prefer explicit parsing (ast.literal_eval, json, a dispatch table) "
                 "over evaluating text.")
SHELL = Heuristic(RULE_SHELL, "high",
                  "subprocess is called with shell=True",
                  "Pass an argument list and leave shell=False; never interpolate "
                  "untrusted values into a shell string.")
OS_SYSTEM = Heuristic(RULE_OS_SYSTEM, "high",
                      "os.system()/os.popen() runs a shell command",
                      "Use subprocess with an argument list instead.")
PICKLE = Heuristic(RULE_PICKLE, "high",
                   "pickle.loads()/yaml.load() can construct arbitrary objects",
                   "Use json, or yaml.safe_load(); never unpickle untrusted data.")
BARE_EXCEPT = Heuristic(RULE_BARE_EXCEPT, "medium",
                        "a bare `except:` also catches KeyboardInterrupt and SystemExit",
                        "Catch the specific exception types you can handle.")
MUTABLE_DEFAULT = Heuristic(RULE_MUTABLE_DEFAULT, "medium",
                            "a mutable default argument is shared between calls",
                            "Default to None and build the container inside the function.")
HARDCODED_SECRET = Heuristic(RULE_HARDCODED_SECRET, "high",
                             "a secret-looking name is assigned a string literal",
                             "Read secrets from the environment or a secret store; "
                             "rotate anything committed.")


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        while isinstance(func, ast.Attribute):
            parts.append(func.attr)
            func = func.value
        if isinstance(func, ast.Name):
            parts.append(func.id)
        return ".".join(reversed(parts))
    return ""


def scan_tree(tree: ast.AST) -> list[tuple[Heuristic, int, int]]:
    found: list[tuple[Heuristic, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in {"eval", "exec"}:
                found.append((EVAL, node.lineno, node.end_lineno or node.lineno))
            elif name in {"os.system", "os.popen"}:
                found.append((OS_SYSTEM, node.lineno, node.end_lineno or node.lineno))
            elif name.startswith("subprocess."):
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) \
                            and kw.value.value is True:
                        found.append((SHELL, node.lineno, node.end_lineno or node.lineno))
            elif name in {"pickle.loads", "pickle.load"}:
                found.append((PICKLE, node.lineno, node.end_lineno or node.lineno))
            elif name in {"yaml.load", "yaml.unsafe_load"}:
                safe = {kw.arg for kw in node.keywords}
                if "Loader" not in safe:
                    found.append((PICKLE, node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            found.append((BARE_EXCEPT, node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = list(node.args.defaults) + [
                d for d in node.args.kw_defaults if d is not None]
            for default in defaults:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    found.append((MUTABLE_DEFAULT, default.lineno,
                                  default.end_lineno or default.lineno))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            for target in targets:
                name = target.id if isinstance(target, ast.Name) else (
                    target.attr if isinstance(target, ast.Attribute) else "")
                if name and any(hint in name.lower() for hint in SECRET_HINTS) \
                        and len(value.value) >= 8:
                    found.append((HARDCODED_SECRET, node.lineno,
                                  node.end_lineno or node.lineno))
    return found
