"""Minimal loader for config/default.yaml without an external YAML dependency.

Supports the subset the repository config uses: nested mappings by two-space
indent, scalar values (int, float, bool, quoted or plain strings), flow lists
like ``[1, 3]``, and full-line comments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "default.yaml"


def _parse_scalar(token: str) -> Any:
    text = token.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1]
        return [_parse_scalar(part) for part in inner.split(",") if part.strip()]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Parse the repository config file into a nested dictionary."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    root: dict[str, Any] = {}
    # Stack holds (indent, mapping) pairs; the base entry never matches.
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for line_number, raw_line in enumerate(
        config_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw_line.startswith("\t"):
            raise ValueError(f"Tabs are not supported (line {line_number})")
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if ":" not in stripped:
            raise ValueError(f"Expected 'key: value' (line {line_number})")
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(value)

    return root
