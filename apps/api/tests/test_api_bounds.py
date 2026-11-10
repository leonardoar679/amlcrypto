"""Everything a request may carry has a size.

A number with no upper bound reaches the database or date arithmetic as whatever the client wrote
(SQLite integers are 64-bit, a timedelta holds 999,999,999 days); text with no length is held in
memory and written to a log whatever its size. So the API's own description of itself (its OpenAPI
schema, built from the routes) is read, and every integer, number and piece of text a request can
carry must say how large it may be: the bounds are in app.api.limits and in each field.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def resolve(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[1]]
    return schema


def leaves(
    schema: dict[str, Any], components: dict[str, Any], where: str, seen: tuple[str, ...] = ()
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every scalar (or list) a schema can hold, with where it is, looking through unions, nested
    models and lists."""
    ref = schema.get("$ref")
    if ref is not None:
        if ref in seen:
            return
        seen = (*seen, ref)
    schema = resolve(schema, components)
    for key in ("anyOf", "oneOf", "allOf"):
        for option in schema.get(key, []):
            yield from leaves(option, components, where, seen)
    kind = schema.get("type")
    if kind == "object":
        for name, field in schema.get("properties", {}).items():
            yield from leaves(field, components, f"{where}.{name}", seen)
        if isinstance(schema.get("additionalProperties"), dict):
            yield from leaves(schema["additionalProperties"], components, f"{where}.*", seen)
        elif schema.get("additionalProperties") is True:
            yield where, schema
    elif kind == "array":
        yield where, schema
        yield from leaves(schema.get("items", {}), components, f"{where}[]", seen)
    elif kind in ("integer", "number", "string"):
        yield where, schema


def request_inputs(spec: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """(where, what it is, schema) for every parameter and body field of every route."""
    components = spec.get("components", {}).get("schemas", {})
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            for parameter in operation.get("parameters", []):
                where = f"{method.upper()} {path} {parameter['in']} {parameter['name']}"
                for found, schema in leaves(parameter["schema"], components, where):
                    yield found, parameter["in"], schema
            body = operation.get("requestBody", {}).get("content", {})
            for media in body.values():
                where = f"{method.upper()} {path} body"
                for found, schema in leaves(media.get("schema", {}), components, where):
                    yield found, "body", schema


def test_every_number_and_every_piece_of_text_a_request_carries_is_bounded(app):
    spec = app.openapi()
    unbounded = []
    seen = 0
    for where, place, schema in request_inputs(spec):
        seen += 1
        kind = schema.get("type")
        if kind in ("integer", "number"):
            low = "minimum" in schema or "exclusiveMinimum" in schema
            high = "maximum" in schema or "exclusiveMaximum" in schema
            if not (low and high):
                unbounded.append((where, "number without both bounds"))
        elif kind == "string":
            # a fixed set of values, or a date the parser reads, is as bounded as it needs to be;
            # a path segment is bounded by the length the server accepts for a URL
            if "enum" in schema or "const" in schema or schema.get("format") == "date-time":
                continue
            if place != "path" and "maxLength" not in schema and "pattern" not in schema:
                unbounded.append((where, "text without a length"))
        elif kind == "array":
            if "maxItems" not in schema:
                unbounded.append((where, "list without a size"))
        else:
            unbounded.append((where, "free-form object"))
    assert seen > 100, f"only {seen} inputs found"
    assert not unbounded, unbounded
