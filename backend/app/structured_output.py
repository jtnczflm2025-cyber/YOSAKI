"""Responses JSON Schema for validated, non-tool model decisions."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def _strict_shape(value: Any) -> Any:
    if isinstance(value, list):
        return [_strict_shape(item) for item in value]
    if not isinstance(value, dict):
        return value
    shaped = {key: _strict_shape(item) for key, item in value.items() if key not in {"title", "default"}}
    if "const" in shaped:
        shaped["enum"] = [shaped.pop("const")]
    if shaped.get("type") == "object":
        if shaped.get("additionalProperties") is True:
            raise ValueError("open object cannot use strict JSON Schema")
        shaped["additionalProperties"] = False
        if "properties" in shaped:
            shaped["required"] = list(shaped["properties"])
    return shaped


def response_format(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    try:
        schema = _strict_shape(schema)
        strict = True
    except ValueError:
        strict = False
    return {"format": {"type": "json_schema", "name": model.__name__, "strict": strict, "schema": schema}}
