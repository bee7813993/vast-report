from __future__ import annotations

from pathlib import Path
from typing import Any


def load_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        data = _parse_simple_yaml(text)
    else:
        data = yaml.safe_load(text)

    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    if "machines" not in data or not isinstance(data["machines"], dict):
        raise ValueError("Config must contain a machines mapping")
    return data


def resolve_config_path(value: str | Path, config_path: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    tokens: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        tokens.append((indent, raw_line.strip()))

    if not tokens:
        return {}

    value, index = _parse_block(tokens, 0, tokens[0][0])
    if index != len(tokens):
        raise ValueError("Could not parse complete config.yaml")
    if not isinstance(value, dict):
        raise ValueError("Config root must be a mapping")
    return value


def _parse_block(
    tokens: list[tuple[int, str]], index: int, indent: int
) -> tuple[Any, int]:
    if index >= len(tokens):
        return {}, index
    current_indent, content = tokens[index]
    if current_indent < indent:
        return {}, index
    if current_indent != indent:
        raise ValueError(f"Unexpected indentation near: {content}")
    if content.startswith("- "):
        return _parse_list(tokens, index, indent)
    return _parse_mapping(tokens, index, indent)


def _parse_list(
    tokens: list[tuple[int, str]], index: int, indent: int
) -> tuple[list[Any], int]:
    values: list[Any] = []
    while index < len(tokens):
        current_indent, content = tokens[index]
        if current_indent < indent:
            break
        if current_indent != indent or not content.startswith("- "):
            break
        item = content[2:].strip()
        index += 1
        if item:
            values.append(_parse_scalar(item))
        elif index < len(tokens) and tokens[index][0] > indent:
            nested, index = _parse_block(tokens, index, tokens[index][0])
            values.append(nested)
        else:
            values.append(None)
    return values, index


def _parse_mapping(
    tokens: list[tuple[int, str]], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    values: dict[str, Any] = {}
    while index < len(tokens):
        current_indent, content = tokens[index]
        if current_indent < indent:
            break
        if current_indent != indent or content.startswith("- "):
            break
        key, sep, raw_value = content.partition(":")
        if not sep:
            raise ValueError(f"Expected key/value pair near: {content}")
        key = _unquote(key.strip())
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            values[key] = _parse_scalar(raw_value)
        elif index < len(tokens) and tokens[index][0] > indent:
            values[key], index = _parse_block(tokens, index, tokens[index][0])
        else:
            values[key] = {}
    return values, index


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return _unquote(value)
    try:
        if any(char in value for char in ".eE"):
            return float(value)
        return int(value)
    except ValueError:
        return value


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
