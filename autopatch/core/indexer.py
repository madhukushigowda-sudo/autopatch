"""Static project index (Stage 1).

Parses Python files with ``ast`` and never imports or runs them. Produces:
files / functions / classes, imports classified as internal, standard-library
or third-party, an import graph (NetworkX), entry points, statically discovered
tests, and config/requirement hints.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import os
import re
import sys
import tokenize
from collections import defaultdict
from pathlib import Path, PurePosixPath

import networkx as nx

STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))  # Python 3.10+
ENTRY_FILE_NAMES = {"main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "__main__.py", "cli.py"}
CONFIG_FILE_NAMES = {"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "setup.py", "conftest.py"}
TEST_FILE_RE = re.compile(r"^(test_.*|.*_test)\.py$", re.IGNORECASE)  # pytest default patterns
REQ_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
MAX_TEST_IDS = 500


# ---------------------------------------------------------------- helpers
def module_name_for(rel_path: str) -> str:
    parts = list(PurePosixPath(rel_path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _norm_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _is_installed(top_level: str) -> bool:
    """True if a top-level module can be found in THIS environment (does not import it)."""
    try:
        return importlib.util.find_spec(top_level) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _read_source(path: Path):
    """Return (text, error). Honours PEP 263 encoding declarations."""
    raw = path.read_bytes()
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        return raw.decode(encoding), None
    except (SyntaxError, UnicodeDecodeError, LookupError) as exc:
        return None, {"line": 1, "msg": f"File could not be decoded ({type(exc).__name__})"}


def _count_loc(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def _is_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    cmp_ = node.test
    if len(cmp_.ops) != 1 or not isinstance(cmp_.ops[0], ast.Eq):
        return False
    sides = [cmp_.left, *cmp_.comparators]
    has_name = any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
    has_main = any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides)
    return has_name and has_main


def _unparse(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return "?"


class _Collector(ast.NodeVisitor):
    """Collects functions, classes and import statements."""

    def __init__(self):
        self.functions: list[dict] = []
        self.classes: list[dict] = []
        self.imports: list[ast.AST] = []
        self._stack: list[tuple[str, str]] = []

    def visit_Import(self, node):
        self.imports.append(node)

    def visit_ImportFrom(self, node):
        self.imports.append(node)

    def visit_ClassDef(self, node):
        methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.classes.append(
            {
                "name": node.name,
                "lineno": node.lineno,
                "end_lineno": getattr(node, "end_lineno", node.lineno),
                "bases": [_unparse(b) for b in node.bases],
                "methods": methods,
            }
        )
        self._stack.append(("class", node.name))
        self.generic_visit(node)
        self._stack.pop()

    def _visit_func(self, node, is_async: bool):
        a = node.args
        arg_names = [x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]]
        if a.vararg:
            arg_names.append("*" + a.vararg.arg)
        if a.kwarg:
            arg_names.append("**" + a.kwarg.arg)
        qualname = ".".join([n for _, n in self._stack] + [node.name])
        self.functions.append(
            {
                "name": node.name,
                "qualname": qualname,
                "lineno": node.lineno,
                "end_lineno": getattr(node, "end_lineno", node.lineno),
                "args": arg_names,
                "is_method": bool(self._stack) and self._stack[-1][0] == "class",
                "is_async": is_async,
                "has_docstring": ast.get_docstring(node) is not None,
            }
        )
        self._stack.append(("func", node.name))
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node):
        self._visit_func(node, False)

    def visit_AsyncFunctionDef(self, node):
        self._visit_func(node, True)


def _static_tests(rel_path: str, tree: ast.Module) -> list[str]:
    """pytest/unittest-style test ids discovered without running anything."""
    ids: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            ids.append(f"{rel_path}::{node.name}")
        elif isinstance(node, ast.ClassDef):
            is_unittest = any(_unparse(b).endswith("TestCase") for b in node.bases)
            has_init = any(isinstance(n, ast.FunctionDef) and n.name == "__init__" for n in node.body)
            if is_unittest or (node.name.startswith("Test") and not has_init):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                        ids.append(f"{rel_path}::{node.name}::{item.name}")
    return ids


def _resolve_imports(nodes, module: str, is_package: bool, modules: dict, aliases: dict):
    """Classify import statements; returns (records, internal_targets)."""

    def canon(name: str):
        if name in modules:
            return name
        return aliases.get(name)

    records: list[dict] = []
    targets: set[str] = set()
    pkg_parts = module.split(".") if module else []
    if not is_package:
        pkg_parts = pkg_parts[:-1]

    def external(name: str, line: int, written: str):
        top = name.split(".")[0]
        kind = "stdlib" if top in STDLIB else "third_party"
        records.append({"module": written, "line": line, "kind": kind, "target": top})

    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                target = next((canon(".".join(parts[:i])) for i in range(len(parts), 0, -1) if canon(".".join(parts[:i]))), None)
                if target:
                    targets.add(target)
                    records.append({"module": alias.name, "line": node.lineno, "kind": "internal", "target": target})
                else:
                    external(alias.name, node.lineno, alias.name)
        else:  # ImportFrom
            level = node.level or 0
            mod = node.module or ""
            written = "." * level + mod
            if level:
                up = level - 1
                if up > len(pkg_parts):
                    records.append({"module": written, "line": node.lineno, "kind": "unresolved", "target": written})
                    continue
                base_parts = pkg_parts[: len(pkg_parts) - up] + (mod.split(".") if mod else [])
                base = ".".join(base_parts)
            else:
                base = mod
            found = set()
            if base and canon(base):
                found.add(canon(base))
            for alias in node.names:
                if alias.name == "*":
                    continue
                sub = f"{base}.{alias.name}" if base else alias.name
                if canon(sub):
                    found.add(canon(sub))
            if found:
                for t in sorted(found):
                    targets.add(t)
                    records.append({"module": written, "line": node.lineno, "kind": "internal", "target": t})
            elif level:
                records.append({"module": written, "line": node.lineno, "kind": "unresolved", "target": written})
            else:
                external(base, node.lineno, written)
    # Deduplicate identical (module, kind, target) records on the same line.
    unique = {(r["module"], r["line"], r["kind"], r["target"]): r for r in records}
    return list(unique.values()), targets


def _parse_requirements(root: Path, rel_files: list[str]) -> list[str]:
    names: set[str] = set()
    candidates = [
        rel for rel in rel_files
        if PurePosixPath(rel).name.lower().startswith("requirements") and PurePosixPath(rel).suffix == ".txt"
    ][:10]
    for rel in candidates:
        try:
            lines = (root / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            match = REQ_NAME_RE.match(line)
            if match:
                names.add(_norm_dist(match.group(0)))
    return sorted(names)


# ---------------------------------------------------------------- main API
def index_project(root, check_installed: bool = True) -> dict:
    root = Path(root)
    rel_files = sorted(
        Path(dp, f).relative_to(root).as_posix()
        for dp, _dirs, fs in os.walk(root)
        for f in fs
        if not Path(dp, f).is_symlink()
    )
    py_files = [f for f in rel_files if f.endswith(".py")]

    modules: dict[str, str] = {}
    for f in py_files:
        name = module_name_for(f)
        if name:
            modules[name] = f
    # src/ layout: allow "pkg" to resolve to "src.pkg".
    aliases = {n[4:]: n for n in modules if n.startswith("src.") and n[4:] not in modules}

    graph = nx.DiGraph()
    for name, path in modules.items():
        graph.add_node(name, path=path)

    file_entries: list[dict] = []
    syntax_errors: list[dict] = []
    test_ids: list[str] = []
    test_files: list[str] = []
    entry_points: list[str] = []
    third_party_files: dict[str, set[str]] = defaultdict(set)
    unresolved: list[dict] = []
    totals = {"loc": 0, "functions": 0, "classes": 0}

    for rel in py_files:
        text, err = _read_source(root / rel)
        module = module_name_for(rel)
        is_test = bool(TEST_FILE_RE.match(PurePosixPath(rel).name))
        entry = {
            "path": rel, "module": module, "loc": 0, "is_test": is_test, "has_main": False,
            "error": err, "functions": [], "classes": [], "imports": [],
        }
        if text is not None:
            entry["loc"] = _count_loc(text)
            try:
                tree = ast.parse(text, filename=rel)  # parse only; never executed
            except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
                entry["error"] = {
                    "line": getattr(exc, "lineno", None) or 1,
                    "msg": (getattr(exc, "msg", None) or str(exc) or type(exc).__name__)[:200],
                }
            else:
                col = _Collector()
                col.visit(tree)
                entry["functions"], entry["classes"] = col.functions, col.classes
                entry["has_main"] = any(_is_main_guard(n) for n in tree.body)
                is_pkg = PurePosixPath(rel).name == "__init__.py"
                records, targets = _resolve_imports(col.imports, module, is_pkg, modules, aliases)
                entry["imports"] = records
                if module:
                    for t in targets:
                        if t != module:
                            graph.add_edge(module, t)
                for r in records:
                    if r["kind"] == "third_party":
                        third_party_files[r["target"]].add(rel)
                    elif r["kind"] == "unresolved":
                        unresolved.append({"path": rel, "line": r["line"], "module": r["module"]})
                if is_test:
                    ids = _static_tests(rel, tree)
                    test_ids.extend(ids)
        if entry["error"]:
            syntax_errors.append({"path": rel, "line": entry["error"]["line"], "msg": entry["error"]["msg"]})
        if is_test:
            test_files.append(rel)
        if entry["has_main"] or PurePosixPath(rel).name in ENTRY_FILE_NAMES:
            entry_points.append(rel)
        totals["loc"] += entry["loc"]
        totals["functions"] += len(entry["functions"])
        totals["classes"] += len(entry["classes"])
        file_entries.append(entry)

    declared = set(_parse_requirements(root, rel_files))
    third_party = [
        {
            "name": name,
            "used_in": sorted(paths),
            "installed_here": _is_installed(name) if check_installed else None,
            "declared_in_requirements": _norm_dist(name) in declared,
        }
        for name, paths in sorted(third_party_files.items())
    ]

    most_imported = sorted(
        ({"module": n, "imported_by": graph.in_degree(n)} for n in graph.nodes if graph.in_degree(n) > 0),
        key=lambda d: (-d["imported_by"], d["module"]),
    )[:5]
    cycles = sorted(sorted(c) for c in nx.strongly_connected_components(graph) if len(c) > 1)

    config_files = sorted(
        f for f in rel_files
        if PurePosixPath(f).name in CONFIG_FILE_NAMES
        or (PurePosixPath(f).name.lower().startswith("requirements") and f.endswith(".txt"))
    )
    return {
        "all_files": rel_files,
        "files": file_entries,
        "totals": {**totals, "python_files": len(py_files), "all_files": len(rel_files)},
        "third_party": third_party,
        "unresolved_imports": unresolved,
        "graph": {
            "nodes": [{"id": n, "path": d.get("path"), "imports": graph.out_degree(n), "imported_by": graph.in_degree(n)} for n, d in graph.nodes(data=True)],
            "edges": [{"source": a, "target": b} for a, b in graph.edges],
            "most_imported": most_imported,
            "cycles": cycles,
        },
        "entry_points": sorted(set(entry_points)),
        "tests": {
            "files": sorted(test_files),
            "static_test_count": len(test_ids),
            "ids": test_ids[:MAX_TEST_IDS],
            "has_conftest": any(PurePosixPath(f).name == "conftest.py" for f in rel_files),
        },
        "config_files": config_files,
        "declared_requirements": sorted(declared),
        "syntax_errors": syntax_errors,
    }
