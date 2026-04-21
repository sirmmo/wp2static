"""Minimal streaming parser for mysqldump output.

We do not implement a full SQL parser. We extract rows from the
``INSERT INTO `tablename` VALUES (...), (...), ...;`` statements, which
mysqldump emits on a single (possibly very long) line per statement.

Supported MySQL scalar literal forms:
    - NULL
    - integers and decimals
    - 'single-quoted strings' with C-style escapes (\\', \\", \\\\, \\n,
      \\r, \\t, \\0, \\b, \\Z) and standard SQL doubled-quote (``''``)
    - binary blobs / hex literals are returned as raw bytes of their hex

Anything we don't recognise falls back to the raw token string.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

# Only scan INSERTs for tables we care about; everything else streams past.
_INSERT_RE = re.compile(
    r"^INSERT INTO `(?P<table>[^`]+)` VALUES ",
)

_ESCAPES = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "0": "\x00",
    "b": "\b",
    "Z": "\x1a",
    "\\": "\\",
    "'": "'",
    '"': '"',
}


def _parse_string(src: str, i: int) -> tuple[str, int]:
    """Parse a single-quoted MySQL string starting at src[i] == "'"."""
    assert src[i] == "'"
    out: list[str] = []
    i += 1
    n = len(src)
    while i < n:
        c = src[i]
        if c == "\\" and i + 1 < n:
            nxt = src[i + 1]
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if c == "'":
            # doubled single-quote -> literal '
            if i + 1 < n and src[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(c)
        i += 1
    raise ValueError("unterminated string literal")


def _parse_scalar(src: str, i: int) -> tuple[object, int]:
    """Parse one scalar value starting at src[i], return (value, next_i)."""
    n = len(src)
    # skip whitespace
    while i < n and src[i] in " \t\r\n":
        i += 1
    if i >= n:
        raise ValueError("unexpected end of input")
    c = src[i]
    if c == "'":
        return _parse_string(src, i)
    if c == "N" and src.startswith("NULL", i):
        return None, i + 4
    if c == "t" and src.startswith("true", i):
        return True, i + 4
    if c == "f" and src.startswith("false", i):
        return False, i + 5
    # number / bareword up to , or )
    j = i
    while j < n and src[j] not in ",)":
        j += 1
    token = src[i:j].strip()
    if token == "":
        raise ValueError(f"empty token at offset {i}")
    # try int, then float, else return token as-is
    try:
        return int(token), j
    except ValueError:
        pass
    try:
        return float(token), j
    except ValueError:
        pass
    return token, j


def _parse_row(src: str, i: int) -> tuple[tuple, int]:
    """Parse a single ``(v1, v2, ...)`` tuple, return (row, next_i)."""
    n = len(src)
    while i < n and src[i] in " \t\r\n":
        i += 1
    if i >= n or src[i] != "(":
        raise ValueError(f"expected '(' at offset {i}")
    i += 1
    values: list[object] = []
    while i < n:
        value, i = _parse_scalar(src, i)
        values.append(value)
        while i < n and src[i] in " \t\r\n":
            i += 1
        if i >= n:
            raise ValueError("unterminated row")
        if src[i] == ",":
            i += 1
            continue
        if src[i] == ")":
            return tuple(values), i + 1
        raise ValueError(f"expected ',' or ')' at offset {i}, got {src[i]!r}")
    raise ValueError("unterminated row")


def _parse_values(values_src: str) -> Iterator[tuple]:
    """Iterate rows from the VALUES clause body (everything after 'VALUES ')."""
    i = 0
    n = len(values_src)
    while i < n:
        while i < n and values_src[i] in " \t\r\n":
            i += 1
        if i >= n:
            return
        if values_src[i] == ";":
            return
        row, i = _parse_row(values_src, i)
        yield row
        while i < n and values_src[i] in " \t\r\n,":
            i += 1


def iter_rows(
    dump_path: Path,
    tables: set[str] | None = None,
    encoding: str = "utf-8",
) -> Iterator[tuple[str, tuple]]:
    """Yield (table_name, row_tuple) for every INSERT row in the dump.

    If ``tables`` is given, only those table names are yielded (others are
    skipped without being parsed). Reads the dump line by line — mysqldump
    emits one INSERT statement per line, so this streams even multi-GB dumps.
    """
    with open(dump_path, "r", encoding=encoding, errors="replace") as fh:
        for line in fh:
            m = _INSERT_RE.match(line)
            if not m:
                continue
            table = m.group("table")
            if tables is not None and table not in tables:
                continue
            body = line[m.end():]
            # trailing ';\n' is harmless; _parse_values stops at ';'
            for row in _parse_values(body):
                yield table, row
