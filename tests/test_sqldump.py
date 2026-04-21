"""Tests for the hand-rolled mysqldump parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from wp2static.sqldump import _parse_string, _parse_values, iter_rows


def test_parse_string_basic_escapes():
    # \\n, \\', \\\\ and doubled '' all collapse correctly.
    src = r"'hello\nworld\'s \\ end''ing'"
    value, end = _parse_string(src, 0)
    assert value == "hello\nworld's \\ end'ing"
    assert end == len(src)


def test_parse_string_null_byte_and_tab():
    src = r"'a\0b\tc'"
    value, _ = _parse_string(src, 0)
    assert value == "a\x00b\tc"


def test_parse_values_mixed_types():
    rows = list(_parse_values("(1,'a',NULL,3.14),(2,'b\\'c',NULL,0);"))
    assert rows == [(1, "a", None, 3.14), (2, "b'c", None, 0)]


def test_iter_rows_filters_by_table(tmp_path: Path):
    sql = tmp_path / "d.sql"
    sql.write_text(
        "INSERT INTO `keep` VALUES (1,'x'),(2,'y');\n"
        "INSERT INTO `drop` VALUES (99,'skip');\n",
        encoding="utf-8",
    )
    rows = list(iter_rows(sql, tables={"keep"}))
    assert rows == [("keep", (1, "x")), ("keep", (2, "y"))]


def test_iter_rows_unterminated_raises(tmp_path: Path):
    sql = tmp_path / "d.sql"
    sql.write_text("INSERT INTO `t` VALUES (1,'broken\n", encoding="utf-8")
    with pytest.raises(ValueError):
        list(iter_rows(sql))
