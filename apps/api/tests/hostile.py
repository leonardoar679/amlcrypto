"""Hostile values for every place a request carries input, and the requests made from them.

The sweep in test_hostile_input.py sends each of these, one at a time, in place of the working
value of a request from tests/catalog.py. They are the kinds of thing that break code that assumed a
well-behaved client: numbers past 64 bits, text a database or a log cannot hold, characters that
look like something else, shapes the route does not expect, and sheer size.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel


def _nested(depth: int) -> Any:
    value: Any = []
    for _ in range(depth):
        value = [value]
    return value


# What a JSON body may carry in place of a field's value. Sent as JSON text (so a lone surrogate
# and NaN, which Python's parser accepts, arrive as the server would see them).
JSON_VALUES: list[Any] = [
    None,
    True,
    False,
    0,
    -1,
    2**31,
    2**63 - 1,
    2**63,
    2**64,
    10**30,
    -(10**30),
    1.5,
    -0.0,
    1e308,
    float("inf"),
    float("nan"),
    "",
    " ",
    "a",
    "x" * 5000,
    "é" * 3000,
    "\x00",
    "a\x00b",
    "\ud800",
    "‮",
    "​",
    "🙂",
    "é",
    "İ",
    "\n",
    "\r\n",
    "'",
    '"',
    "\\",
    "' OR '1'='1",
    "'; DROP TABLE users; --",
    "<script>alert(1)</script>",
    "{{7*7}}",
    "${7*7}",
    "%s%s%s%n",
    "../../etc/passwd",
    "0x" + "f" * 64,
    "9" * 5000,
    "scr_1",
    "scr_-1",
    "scr_" + "9" * 30,
    [],
    [1],
    [[]],
    [None] * 500,
    {},
    {"a": 1},
    {"$gt": ""},
    {"__proto__": {"x": 1}},
    _nested(80),
]

# What may stand in a path segment (percent-encoded when sent), and what is put in raw.
PATH_TEXTS: list[str] = [
    "0",
    "-1",
    "99999999999999999999999",
    "9" * 5000,
    "1.5",
    "1e3",
    "٣",  # Arabic-Indic digit: Python's int() reads it as 3
    "１２",  # full-width digits
    "abc",
    "",
    " ",
    "null",
    "NaN",
    "\x00",
    "'",
    "' OR 1=1 --",
    "<script>",
    "{{7*7}}",
    "..",
    "../../etc/passwd",
    "a/b",
    "x" * 5000,
    "é",
    "🙂",
    "‮",
    "scr_",
    "scr_0",
    "scr_-1",
    "scr_99999999999999999999999",
    "scr_１",
    "0x",
    "0x" + "g" * 40,
    "0x" + "0" * 40,
]
PATH_RAW: list[str] = [
    "%",
    "%zz",
    "%2",
    "%00",
    "%ff",
    "%c0%af",
    "..%2f..%2f",
    "%2e%2e",
    "%ED%A0%80",
]

# What may stand in a query value (percent-encoded when sent), and what is put in raw.
QUERY_TEXTS: list[str] = [
    "",
    " ",
    "0",
    "-1",
    "1",
    "200",
    "201",
    "1000000",
    "1000001",
    "99999999999999999999999",
    "9" * 5000,
    "1.5",
    "1e3",
    "1e999",
    "NaN",
    "inf",
    "٣",
    "true",
    "false",
    "null",
    "None",
    "x" * 5000,
    "\x00",
    "'",
    "' OR 1=1 --",
    "<script>",
    "%s",
    "é",
    "🙂",
    "2026-13-45",
    "2026-11-06T12:00:00Z",
    "0001-01-01T00:00:00Z",
    "9999-12-31T23:59:59Z",
    "2026-11-06T12:00:00+99:00",
    "ethereum",
    "eip155:1",
    "eip155:999999999",
    "0x" + "0" * 40,
]
QUERY_RAW: list[str] = ["%", "%zz", "%00", "%ff", "%ED%A0%80"]

# Whole bodies, as bytes: not JSON, JSON of the wrong shape, JSON that is hard to read.
BODIES: list[tuple[str, bytes]] = [
    ("empty", b""),
    ("open brace", b"{"),
    ("close brace", b"}"),
    ("null", b"null"),
    ("empty list", b"[]"),
    ("string", b'"x"'),
    ("number", b"123"),
    ("true", b"true"),
    ("empty object", b"{}"),
    ("cut off", b'{"a":'),
    ("bytes", b"\xff\xfe\x00\x01"),
    ("bom", b"\xef\xbb\xbf{}"),
    ("utf-16", "{}".encode("utf-16")),
    ("trailing nul", b"{}\x00"),
    ("nan", b'{"a": NaN}'),
    ("infinity", b'{"a": 1e999}'),
    ("duplicate keys", b'{"title": "x", "title": "y", "a": 1, "a": 2}'),
    ("very deep list", b"[" * 100_000 + b"]" * 100_000),
    ("very deep object", b'{"a":' * 50_000 + b"1" + b"}" * 50_000),
    ("many keys", b"{" + b",".join(b'"k%d":1' % n for n in range(20_000)) + b"}"),
    ("long number", b'{"a": ' + b"9" * 5000 + b"}"),
    ("form", b"a=1&b=2"),
    ("multipart", b"--x\r\nContent-Disposition: form-data; name=a\r\n\r\n1\r\n--x--\r\n"),
]
CONTENT_TYPES: list[str] = [
    "application/json",
    "text/plain",
    "application/x-www-form-urlencoded",
    "multipart/form-data; boundary=x",
    "multipart/form-data",
    "application/json; charset=utf-16",
    "application/json; charset=nope",
    "application/xml",
    "application/octet-stream",
    "",
]

# What may be sent in a header the API reads.
HEADER_VALUES: list[str] = [
    "",
    " ",
    "Bearer",
    "Bearer ",
    "Bearer x",
    "Bearer " + "x" * 10_000,
    "Bearer a.b.c",
    "Bearer ..",
    "Bearer eyJhbGciOiJub25lIn0.eyJzdWIiOiIxIn0.",
    "bearer x",
    "Basic YTpi",
    "Digest x",
    "x" * 10_000,
    "aml_",
    "aml_00000000_" + "x" * 43,
    "aml_zzzzzzzz_" + "é" * 43,
    "' OR 1=1 --",
    "\t",
    "é",
    "<script>",
]


@dataclass(frozen=True)
class Raw:
    """Text that goes into the URL exactly as written."""

    text: str


def quoted(value: str) -> str:
    return quote(value, safe="")


def query_string(params: dict[str, Any]) -> str:
    """A query string from name -> value, where a value is text (percent-encoded here), a Raw
    (put in as written) or a list of those (the name repeated)."""
    parts: list[str] = []
    for name, value in params.items():
        for item in value if isinstance(value, list) else [value]:
            text = item.text if isinstance(item, Raw) else quoted(str(item))
            parts.append(f"{quoted(name)}={text}")
    return "&".join(parts)


def with_query(path: str, params: dict[str, Any] | None) -> str:
    return path + ("?" + query_string(params) if params else "")


# --- the places in a request ---------------------------------------------------------------------


def path_variants(template: str, path: str) -> Iterator[tuple[str, str, str]]:
    """(placeholder, value, the path with that segment replaced) for each placeholder of the
    template and each hostile value."""
    pieces, parts = template.split("/"), path.split("/")
    assert len(pieces) == len(parts), (template, path)
    for index, piece in enumerate(pieces):
        if not piece.startswith("{"):
            continue
        for value in [*PATH_TEXTS, *(Raw(raw) for raw in PATH_RAW)]:
            text = value.text if isinstance(value, Raw) else quoted(value)
            yield (
                piece.strip("{}"),
                text,
                "/".join([*parts[:index], text, *parts[index + 1 :]]),
            )


def query_variants(
    names: list[str], base: dict[str, Any] | None
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """(name, value, the query with that one parameter replaced) for each declared parameter."""
    base = dict(base or {})
    for name in names:
        for value in QUERY_TEXTS:
            yield name, value, {**base, name: value}
        for raw in QUERY_RAW:
            yield name, raw, {**base, name: Raw(raw)}
        yield name, "twice", {**base, name: ["1", "2"]}


def fields_of(model: type[BaseModel], prefix: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    """Every field a body model accepts, nested models included, as paths of names."""
    for name, field in model.model_fields.items():
        yield (*prefix, name)
        annotation = field.annotation
        candidates = getattr(annotation, "__args__", None) or (annotation,)
        for candidate in candidates:
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                yield from fields_of(candidate, (*prefix, name))


def with_value(body: Any, path: tuple[str, ...], value: Any) -> Any:
    """A copy of the body with the value at `path` replaced (made if it is not there)."""
    result = dict(body)
    if len(path) == 1:
        result[path[0]] = value
    else:
        inner = result.get(path[0])
        result[path[0]] = with_value(inner if isinstance(inner, dict) else {}, path[1:], value)
    return result


def without(body: Any, path: tuple[str, ...]) -> Any:
    result = dict(body)
    if len(path) == 1:
        result.pop(path[0], None)
    elif isinstance(result.get(path[0]), dict):
        result[path[0]] = without(result[path[0]], path[1:])
    return result
