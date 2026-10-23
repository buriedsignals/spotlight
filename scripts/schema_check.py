"""A small, fail-closed JSON Schema (draft-07) checker for Spotlight's own schemas.

Runtime scripts stay stdlib-only, so they cannot import jsonschema. This
checks the keywords our batch and proposal schemas use, including the
date, date-time and uri formats that jsonschema checks only on request. A
schema that uses any other keyword is refused rather than half-checked.
Tests compare it with jsonschema on the same documents.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ANNOTATIONS = {"$schema", "$id", "$comment", "title", "description", "definitions", "schema_version", "default"}
KEYWORDS = {
    "$ref", "type", "properties", "required", "additionalProperties", "enum", "const", "pattern",
    "minLength", "minItems", "maxItems", "minimum", "maximum", "exclusiveMinimum", "uniqueItems", "items", "allOf", "anyOf", "not", "if", "then",
    "else", "format",
}
TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaError(Exception):
    """The schema uses something this checker does not implement."""


def _format_ok(name: str, value: str) -> bool:
    try:
        if name == "date":
            return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)) and date.fromisoformat(value) is not None
        if name == "date-time":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})", value):
                return False
            datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
            return True
        if name == "uri":
            parsed = urlparse(value)
            return bool(parsed.scheme) and re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", parsed.scheme) is not None and " " not in value
    except ValueError:
        return False
    raise SchemaError(f"format {name!r} is not implemented")


class Checker:
    def __init__(self, schema: dict[str, Any]):
        self.root = schema
        self._audit(schema)

    def _audit(self, node: Any) -> None:
        if isinstance(node, dict):
            unknown = set(node) - KEYWORDS - ANNOTATIONS
            if unknown:
                raise SchemaError(f"unsupported keywords {sorted(unknown)}")
            for key, value in node.items():
                if key in ("properties", "definitions"):
                    for child in value.values():
                        self._audit(child)
                elif key in ("items", "not", "if", "then", "else", "additionalProperties") and isinstance(value, dict):
                    self._audit(value)
                elif key in ("allOf", "anyOf"):
                    for child in value:
                        self._audit(child)
        elif not isinstance(node, bool):
            raise SchemaError("a schema must be an object or a boolean")

    def _resolve(self, ref: str) -> dict[str, Any]:
        if not ref.startswith("#/"):
            raise SchemaError(f"only local references are implemented: {ref}")
        node: Any = self.root
        for part in ref[2:].split("/"):
            node = node[part]
        return node

    def errors(self, value: Any, schema: Any = None, path: str = "$") -> list[str]:
        schema = self.root if schema is None else schema
        if schema is True:
            return []
        if schema is False:
            return [f"{path}: not allowed"]
        out: list[str] = []
        if "$ref" in schema:
            out += self.errors(value, self._resolve(schema["$ref"]), path)
        if "type" in schema:
            kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(TYPES[k](value) for k in kinds):
                return out + [f"{path}: must be {'/'.join(kinds)}"]
        if "const" in schema and value != schema["const"]:
            out.append(f"{path}: must be {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            out.append(f"{path}: must be one of {schema['enum']}")
        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                out.append(f"{path}: too short")
            if "pattern" in schema and not re.search(schema["pattern"], value):
                out.append(f"{path}: does not match {schema['pattern']}")
            if "format" in schema and not _format_ok(schema["format"], value):
                out.append(f"{path}: not a valid {schema['format']}")
        if TYPES["number"](value):
            if "minimum" in schema and value < schema["minimum"]:
                out.append(f"{path}: below {schema['minimum']}")
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
                out.append(f"{path}: must be above {schema['exclusiveMinimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                out.append(f"{path}: above {schema['maximum']}")
        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                out.append(f"{path}: needs at least {schema['minItems']} items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                out.append(f"{path}: allows at most {schema['maxItems']} items")
            if schema.get("uniqueItems"):
                seen = [json.dumps(v, sort_keys=True) for v in value]
                if len(seen) != len(set(seen)):
                    out.append(f"{path}: items must be unique")
            if isinstance(schema.get("items"), (dict, bool)):
                for index, item in enumerate(value):
                    out += self.errors(item, schema["items"], f"{path}[{index}]")
        if isinstance(value, dict):
            for name in schema.get("required", []):
                if name not in value:
                    out.append(f"{path}: missing {name}")
            props = schema.get("properties", {})
            for name, child in value.items():
                if name in props:
                    out += self.errors(child, props[name], f"{path}.{name}")
                elif schema.get("additionalProperties") is False:
                    out.append(f"{path}: unknown field {name}")
                elif isinstance(schema.get("additionalProperties"), dict):
                    out += self.errors(child, schema["additionalProperties"], f"{path}.{name}")
        for sub in schema.get("allOf", []):
            out += self.errors(value, sub, path)
        if "anyOf" in schema and not any(not self.errors(value, sub, path) for sub in schema["anyOf"]):
            out.append(f"{path}: matches none of the allowed shapes")
        if "not" in schema and not self.errors(value, schema["not"], path):
            out.append(f"{path}: matches a forbidden shape")
        if "if" in schema:
            branch = schema.get("then") if not self.errors(value, schema["if"], path) else schema.get("else")
            if branch is not None:
                out += self.errors(value, branch, path)
        return out


def load(path: Path) -> Checker:
    return Checker(json.loads(path.read_text(encoding="utf-8")))
