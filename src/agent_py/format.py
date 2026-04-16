"""Formatting helpers: repr truncation, pagination, source-line context."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

REPR_MAX = 50
PAGE_SIZE = 10
SOURCE_CONTEXT = 3


def truncate(s: str, limit: int = REPR_MAX) -> str:
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def paginate(items: list[Any], page: int = 1, page_size: int = PAGE_SIZE) -> dict[str, Any]:
    total = len(items)
    if page < 1:
        page = 1
    start = (page - 1) * page_size
    end = start + page_size
    pages = max(1, (total + page_size - 1) // page_size)
    return {
        "page": page,
        "pages": pages,
        "total": total,
        "page_size": page_size,
        "items": items[start:end],
    }


def filter_dunders(variables: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [v for v in variables if not is_dunder(v.get("name", ""))]


def format_variable(var: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw DAP variable dict to agent-py output shape."""
    name = var.get("name", "")
    type_ = var.get("type", "")
    value = var.get("value", "")
    ref = var.get("variablesReference", 0) or 0
    out: dict[str, Any] = {"name": name, "type": type_}
    if ref:
        out["ref"] = ref
        out["preview"] = truncate(str(value))
    else:
        out["value"] = truncate(str(value))
    return out


def source_context(file: str, line: int, window: int = SOURCE_CONTEXT) -> list[dict[str, Any]]:
    p = Path(file)
    if not p.exists() or not p.is_file():
        return []
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return []
    start = max(1, line - window)
    end = min(len(lines), line + window)
    out = []
    for ln in range(start, end + 1):
        out.append({"line": ln, "text": lines[ln - 1], "current": ln == line})
    return out
