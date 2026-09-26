"""Rename a copied package's literal identity without executing or reformatting it."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

from refactory_dsh.nn.package import PackageError


def _source(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    encoding, _lines = tokenize.detect_encoding(io.BytesIO(data).readline)
    return data.decode(encoding), encoding


def _parse(path: Path) -> tuple[str, ast.Module]:
    text, _encoding = _source(path)
    try:
        return text, ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        raise PackageError(f"cannot read package source {path}: {exc.msg}") from exc


def _identity(tree: ast.Module) -> ast.Constant | None:
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(isinstance(target, ast.Name) and target.id == "PACKAGE"
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))
    ]
    if len(assignments) != 1 or assignments[0] not in tree.body:
        return None
    value = assignments[0].value
    if not isinstance(value, ast.Call):
        return None
    names = [keyword.value for keyword in value.keywords if keyword.arg == "name"]
    name = names[0] if len(names) == 1 else (value.args[0] if not names and value.args else None)
    return name if isinstance(name, ast.Constant) and isinstance(name.value, str) else None


def declared_name(root: Path) -> str:
    """Read a direct literal ``PACKAGE = Package(name=...)``; return empty if dynamic."""
    _text, tree = _parse(root / "__init__.py")
    identity = _identity(tree)
    return str(identity.value) if identity is not None else ""


def _rewritten(path: Path, old: str, new: str, *, manifest: bool) -> str:
    text, tree = _parse(path)
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    def offset(position: tuple[int, int]) -> int:
        row, column = position
        return offsets[row - 1] + column

    def span(node: ast.AST) -> tuple[int, int]:
        # AST columns count UTF-8 bytes; tokenize columns count characters.
        def position(row: int, column: int) -> int:
            return offsets[row - 1] + len(lines[row - 1].encode("utf-8")[:column].decode("utf-8"))
        return position(node.lineno, node.col_offset), position(node.end_lineno, node.end_col_offset)

    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    changes: dict[tuple[int, int], str] = {}

    def within(node: ast.AST) -> list[tokenize.TokenInfo]:
        start, end = span(node)
        return [token for token in tokens if start <= offset(token.start) and offset(token.end) <= end]

    def replace_token(token: tokenize.TokenInfo, value: str) -> None:
        changes[offset(token.start), offset(token.end)] = value

    def literal(node: ast.Constant, value: str) -> None:
        pieces = [token for token in within(node) if token.type == tokenize.STRING]
        if len(pieces) != 1:
            raise PackageError(f"cannot rename {path}: use one literal for {node.value!r}, not joined literals")
        replace_token(pieces[0], repr(value))

    prefix = f"refactory_packages.{old}"
    replacement = f"refactory_packages.{new}"
    self_prefix = re.compile(re.escape(prefix) + r"(?=[.:]|$)")
    reference = re.compile(re.escape(prefix) + r"(?:\.[A-Za-z_]\w*)*(?::[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)?\Z")
    mode_id = re.compile(re.escape(old) + r"/[a-z][a-z0-9_]*\Z")
    identity = _identity(tree) if manifest else None
    docstrings = {
        node.body[0].value for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    fstring_parts = {part for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values}
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            if any(isinstance(part, ast.Constant) and isinstance(part.value, str) and self_prefix.search(part.value)
                   for part in node.values):
                raise PackageError(f"cannot rename {path}: dynamic self-qualified f-string; use a literal module constant")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node in docstrings or node in fstring_parts:
                continue
            if node is identity:
                literal(node, new)
            elif reference.fullmatch(node.value):
                literal(node, replacement + node.value[len(prefix):])
            elif mode_id.fullmatch(node.value):
                literal(node, new + node.value[len(old):])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == prefix or alias.name.startswith(prefix + "."):
                    names = [token for token in within(alias) if token.type == tokenize.NAME]
                    replace_token(names[1], new)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and (node.module == prefix or node.module.startswith(prefix + ".")):
                names = [token for token in within(node) if token.type == tokenize.NAME]
                replace_token(names[2], new)  # from, refactory_packages, <package>
            elif node.level == 0 and node.module == "refactory_packages":
                for alias in node.names:
                    if alias.name == old:
                        token = next(token for token in within(alias) if token.type == tokenize.NAME)
                        # Keep the local binding, e.g. `old.ops`, while importing the copy.
                        replace_token(token, new if alias.asname else f"{new} as {old}")
        elif (isinstance(node, ast.Attribute) and node.attr == old
              and isinstance(node.value, ast.Name) and node.value.id == "refactory_packages"):
            names = [token for token in within(node) if token.type == tokenize.NAME]
            replace_token(names[-1], new)
    for (start, end), value in sorted(changes.items(), reverse=True):
        text = text[:start] + value + text[end:]
    return text


def rename(root: Path, old: str, new: str) -> None:
    """Rewrite literal self references in a copy, retaining prose and external imports.

    Plan every file first: an unsupported source form refuses the import before
    any rewritten file is installed. Relative imports need no change.
    """
    planned = {path: _rewritten(path, old, new, manifest=path == root / "__init__.py")
               for path in root.rglob("*.py")}
    for path, text in planned.items():
        original, encoding = _source(path)
        if text != original:
            path.write_bytes(text.encode(encoding))
