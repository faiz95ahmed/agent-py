from agent_py.format import (
    truncate, paginate, filter_dunders, format_variable, source_context,
)


def test_truncate():
    assert truncate("abc", 50) == "abc"
    assert truncate("x" * 100, 50).endswith("…")
    assert len(truncate("x" * 100, 50)) == 50


def test_paginate():
    items = list(range(25))
    page1 = paginate(items, page=1, page_size=10)
    assert page1["items"] == list(range(10))
    assert page1["total"] == 25
    assert page1["pages"] == 3
    page3 = paginate(items, page=3, page_size=10)
    assert page3["items"] == [20, 21, 22, 23, 24]


def test_filter_dunders():
    vs = [{"name": "x"}, {"name": "__init__"}, {"name": "_y"}]
    out = filter_dunders(vs)
    assert [v["name"] for v in out] == ["x", "_y"]


def test_format_variable_composite_vs_scalar():
    scalar = format_variable({"name": "n", "type": "int", "value": "5", "variablesReference": 0})
    assert scalar == {"name": "n", "type": "int", "value": "5"}

    composite = format_variable({"name": "d", "type": "dict", "value": "{...}", "variablesReference": 17})
    assert composite["ref"] == 17
    assert composite["preview"] == "{...}"


def test_source_context(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("\n".join(f"line{i}" for i in range(1, 11)))
    ctx = source_context(str(f), 5, window=2)
    lines = [c["line"] for c in ctx]
    assert lines == [3, 4, 5, 6, 7]
    assert next(c for c in ctx if c["current"])["line"] == 5
