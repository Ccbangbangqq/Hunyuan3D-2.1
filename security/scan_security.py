"""AST-level scan for shell / dynamic-execution patterns.

Usage:  python scan_security.py <repo_root>

Reports every *call* (not comments or docstrings) matching:
  - subprocess.<fn>(..., shell=True)
  - os.system(...) / os.popen(...)
  - eval(...) / exec(...)
as well as `import subprocess`-less usage, so a grep-based scanner and this agree.
"""
from __future__ import annotations

import ast
import os
import sys


def scan(root: str) -> list[tuple[str, int, str, str]]:
    findings: list[tuple[str, int, str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", "node_modules"}]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, root)
            try:
                tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
            except SyntaxError as exc:
                findings.append((rel, exc.lineno or 0, "PARSE_ERROR", str(exc)))
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    base = func.value.id if isinstance(func.value, ast.Name) else ""
                    name = f"{base}.{func.attr}" if base else func.attr
                    if base == "subprocess":
                        for kw in node.keywords:
                            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                findings.append((rel, node.lineno, "subprocess shell=True", name))
                    if name in {"os.system", "os.popen"}:
                        findings.append((rel, node.lineno, name, ast.unparse(func)))
                elif isinstance(func, ast.Name) and func.id in {"eval", "exec"}:
                    findings.append((rel, node.lineno, func.id, ast.unparse(func)))
    return sorted(findings)


if __name__ == "__main__":
    root = sys.argv[1]
    res = scan(root)
    print(f"# scan root: {root}")
    if not res:
        print("NO FINDINGS")
    for rel, line, kind, detail in res:
        print(f"{rel}:{line}: {kind}  [{detail[:60]}]")
    print(f"# total: {len(res)}")
