"""Swagger (OpenAPI 3.0) loading and JSON Schema validation for BSS Microservice v1.

OpenAPI 3.0 schemas are not plain JSON Schema, so they are converted first:
  * $ref to #/components/schemas/... is inlined
  * nullable: true          -> type: [<type>, "null"]
  * exclusiveMinimum: true  -> exclusiveMinimum: <minimum> (draft 7 form)
  * example                 -> dropped
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime
from pathlib import Path

import jsonschema

SPEC_PATH = Path(os.environ.get("BSS_SWAGGER", Path(__file__).with_name("swagger_bss_v1.json")))
SPEC = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

# Opt-in strict mode: undocumented response fields become failures.
STRICT = os.environ.get("BSS_STRICT_SCHEMA", "0").lower() in ("1", "true", "yes")

FORMATS = jsonschema.FormatChecker(formats=())


@FORMATS.checks("uuid", raises=ValueError)
def _is_uuid(value) -> bool:
    return not isinstance(value, str) or bool(uuid.UUID(value))


@FORMATS.checks("date", raises=ValueError)
def _is_date(value) -> bool:
    return not isinstance(value, str) or bool(date.fromisoformat(value)) and len(value) == 10


@FORMATS.checks("date-time", raises=ValueError)
def _is_date_time(value) -> bool:
    if not isinstance(value, str):
        return True
    if "T" not in value and " " not in value:
        raise ValueError("no time part")
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return True


def _deref(node: dict) -> dict:
    while isinstance(node, dict) and "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        node = SPEC["components"]["schemas"][name]
    return node


def to_json_schema(node: dict, strict: bool = STRICT) -> dict:
    """Convert one OpenAPI 3.0 schema object (recursively) to draft-7 JSON Schema."""
    node = _deref(node)
    out: dict = {}
    for key, value in node.items():
        if key == "properties":
            out[key] = {name: to_json_schema(sub, strict) for name, sub in value.items()}
        elif key == "items":
            out[key] = to_json_schema(value, strict)
        elif key in ("example", "nullable", "exclusiveMinimum", "exclusiveMaximum"):
            continue
        else:
            out[key] = value
    if "properties" in out and "type" not in out:
        out["type"] = "object"
    if node.get("nullable") and "type" in out:
        out["type"] = [out["type"], "null"]
    if node.get("exclusiveMinimum") is True and "minimum" in out:
        out["exclusiveMinimum"] = out.pop("minimum")
    if node.get("exclusiveMaximum") is True and "maximum" in out:
        out["exclusiveMaximum"] = out.pop("maximum")
    if strict and out.get("type") == "object" and "properties" in out:
        out.setdefault("additionalProperties", False)
    return out


def operation(path: str, method: str) -> dict:
    try:
        return SPEC["paths"][path][method.lower()]
    except KeyError as exc:
        raise KeyError(f"{method.upper()} {path} is not in the swagger") from exc


def operations() -> list[tuple[str, str]]:
    return [(p, m.upper()) for p, item in SPEC["paths"].items() for m in item]


def _json_schema_of(content: dict | None) -> dict | None:
    if not content:
        return None
    media = content.get("application/json") or next(iter(content.values()), {})
    return to_json_schema(media["schema"]) if "schema" in media else None


def response_schema(path: str, method: str, status: int) -> dict | None:
    responses = operation(path, method).get("responses", {})
    resp = responses.get(str(status))
    if resp is not None:
        return _json_schema_of(resp.get("content"))
    return None


def request_schema(path: str, method: str) -> dict | None:
    body = operation(path, method).get("requestBody")
    return _json_schema_of(body.get("content")) if body else None


def schema_errors(instance, schema: dict, limit: int = 10) -> list[str]:
    validator = jsonschema.Draft7Validator(schema, format_checker=FORMATS)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    out = []
    for err in errors[:limit]:
        where = "$" + "".join(f"[{p!r}]" if isinstance(p, str) else f"[{p}]" for p in err.absolute_path)
        out.append(f"{where}: {err.message}")
    return out


ERROR_SCHEMA = to_json_schema({"$ref": "#/components/schemas/ErrorResponse"})


def validate_response(path: str, method: str, resp) -> list[str]:
    """Return contract violations of an HTTP response (empty list = OK).

    Documented status -> its schema (JSON required).
    Undocumented 4xx/5xx with a JSON body -> ErrorResponse schema.
    """
    documented = str(resp.status_code) in operation(path, method).get("responses", {})
    schema = response_schema(path, method, resp.status_code)
    ctype = resp.headers.get("Content-Type", "")
    is_json = "json" in ctype.lower()
    if schema is None and resp.status_code >= 400 and is_json:
        schema = ERROR_SCHEMA
    if schema is None:
        return []
    if not is_json:
        if documented:
            return [f"Content-Type is '{ctype}', expected application/json"]
        return []
    try:
        body = resp.json()
    except ValueError:
        return [f"Body is not valid JSON: {resp.text[:200]!r}"]
    return schema_errors(body, schema)
