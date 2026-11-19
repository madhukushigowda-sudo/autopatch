"""A few AutoPatch AST checks for bug patterns Ruff does not report by default.

Static only: the code is parsed, never executed. Each item is {code, message, file, line, function}.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_TERMINATORS = (ast.Return, ast.Raise, ast.Break, ast.Continue)
_DIV_OPS = (ast.Div, ast.FloorDiv, ast.Mod)
_BROAD = {"Exception", "BaseException"}
# str methods that return a new string; calling them as a bare statement throws the result away
_STR_METHODS = {"strip", "lstrip", "rstrip", "lower", "upper", "title", "capitalize", "casefold", "swapcase",
                "ljust", "rjust", "center", "zfill", "expandtabs", "removeprefix", "removesuffix"}
_IGNORE_RE = re.compile(r"#\s*autopatch:\s*ignore(?:\[([A-Za-z0-9_, ]+)\])?", re.I)


def ignored_codes(line_text: str) -> set | None:
    """None: no marker. Empty set: ignore every rule on this line. Otherwise the listed codes."""
    m = _IGNORE_RE.search(line_text or "")
    if not m:
        return None
    return {c.strip().upper() for c in m.group(1).split(",") if c.strip()} if m.group(1) else set()


def is_ignored(lines: list, line: int | None, code: str | None) -> bool:
    if not line or line < 1 or line > len(lines):
        return False
    codes = ignored_codes(lines[line - 1])
    return codes is not None and (not codes or (code or "").upper() in codes)


def _handler_is_broad(handler) -> bool:
    t = handler.type
    if t is None:
        return True
    names = t.elts if isinstance(t, ast.Tuple) else [t]
    return any((isinstance(n, ast.Name) and n.id in _BROAD) or (isinstance(n, ast.Attribute) and n.attr in _BROAD) for n in names)


def _is_zero(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and not isinstance(node.value, bool) and node.value in (0, 0.0)


class _Visitor(ast.NodeVisitor):
    def __init__(self, rel: str):
        self.rel, self.items, self._funcs = rel, [], []

    def _add(self, code, message, line, **extra):
        self.items.append({"code": code, "message": message, "file": self.rel, "line": line,
                           "function": self._funcs[-1] if self._funcs else None, **extra})

    def _func(self, node):
        self._funcs.append(node.name)
        self.generic_visit(node)
        self._funcs.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _func

    def visit_Try(self, node):
        for handler in node.handlers:
            body = handler.body
            if all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                                               and s.value.value is Ellipsis) for s in body):
                caught = ast.unparse(handler.type) if handler.type is not None else "everything"
                if _handler_is_broad(handler):
                    self._add("AP001", f"except block for {caught} does nothing (pass); the error is silently ignored.", handler.lineno)
                else:
                    # A narrow handler with 'pass' is usually deliberate (EAFP, StopIteration, optional files).
                    # Still reported, but as a low-severity review note rather than a Medium finding.
                    self._add("AP001", f"except block for {caught} does nothing (pass). This is often intentional for a specific "
                                       "exception; check that ignoring it is what you want.", handler.lineno,
                              severity="Low", confidence="Low")
        self.generic_visit(node)

    visit_TryStar = visit_Try

    def visit_BinOp(self, node):
        if isinstance(node.op, _DIV_OPS) and _is_zero(node.right):
            self._add("AP002", f"Division by the constant {node.right.value!r} always raises ZeroDivisionError.", node.lineno)
        self.generic_visit(node)

    def visit_Expr(self, node):
        v = node.value
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in _STR_METHODS and not v.keywords \
                and isinstance(v.func.value, (ast.Name, ast.Attribute, ast.Constant, ast.Subscript)):
            self._add("AP004", f"The result of .{v.func.attr}() is discarded. Strings are immutable, so this line changes nothing.",
                      node.lineno)
        self.generic_visit(node)

    def visit_Assign(self, node):
        v = node.value
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in ("sort", "reverse") \
                and not v.args and all(isinstance(t, ast.Name) for t in node.targets):
            self._add("AP005", f"list.{v.func.attr}() sorts in place and returns None; the assigned name will be None.", node.lineno)
        self.generic_visit(node)

    def visit_Return(self, node):
        v = node.value
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in ("sort", "reverse") \
                and not v.args and isinstance(v.func.value, ast.Name):
            self._add("AP005", f"list.{v.func.attr}() sorts in place and returns None; this returns None.", node.lineno)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        if isinstance(node.op, _DIV_OPS) and _is_zero(node.value):
            self._add("AP002", f"Division by the constant {node.value.value!r} always raises ZeroDivisionError.", node.lineno)
        self.generic_visit(node)

    def generic_visit(self, node):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                self._check_unreachable(block)
        super().generic_visit(node)

    def _check_unreachable(self, block: list):
        for i, stmt in enumerate(block[:-1]):
            if isinstance(stmt, _TERMINATORS):
                nxt = block[i + 1]
                # A docstring-like constant after return is not interesting; real statements are.
                if not (isinstance(nxt, ast.Expr) and isinstance(nxt.value, ast.Constant)):
                    kind = type(stmt).__name__.lower()
                    self._add("AP003", f"Code after '{kind}' on line {stmt.lineno} can never run.", nxt.lineno)
                break


def scan_file(path: Path, rel: str) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text, filename=rel)
    except (SyntaxError, ValueError, RecursionError, MemoryError, OSError):
        return []  # syntax errors are reported separately
    visitor = _Visitor(rel)
    visitor.visit(tree)
    lines = text.splitlines()
    seen, unique = set(), []
    for it in visitor.items:  # generic_visit can reach a block twice
        key = (it["code"], it["line"])
        if is_ignored(lines, it["line"], it["code"]):
            continue  # the author marked this line '# autopatch: ignore'
        if key not in seen:
            seen.add(key)
            unique.append(it)
    return unique


def scan_project(root: Path, rel_files: list[str]) -> list[dict]:
    out: list[dict] = []
    for rel in rel_files:
        out.extend(scan_file(Path(root) / rel, rel))
    return out


def drop_ignored(root: Path, items: list[dict]) -> list[dict]:
    """Remove tool findings on lines the author marked '# autopatch: ignore' (optionally '[CODE,...]'). Used for Ruff items."""
    cache: dict[str, list] = {}
    kept = []
    for it in items:
        f = it.get("file")
        if f not in cache:
            try:
                cache[f] = (Path(root) / f).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                cache[f] = []
        if not is_ignored(cache[f], it.get("line"), it.get("code")):
            kept.append(it)
    return kept
