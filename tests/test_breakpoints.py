from __future__ import annotations

from pathlib import Path

from agent_py import state as st


def test_add_and_remove_breakpoint(tmp_cwd: Path):
    (tmp_cwd / "foo.py").write_text("x = 1\n")
    bps = st.add_breakpoint("foo.py", 1, cwd=tmp_cwd)
    assert len(bps) == 1
    assert bps[0]["file"].endswith("foo.py")
    assert bps[0]["line"] == 1
    assert "condition" not in bps[0]

    bps = st.add_breakpoint("foo.py", 1, condition="x > 0", cwd=tmp_cwd)
    # adding same location replaces
    assert len(bps) == 1
    assert bps[0]["condition"] == "x > 0"

    bps = st.add_breakpoint("foo.py", 2, cwd=tmp_cwd)
    assert len(bps) == 2

    bps = st.remove_breakpoint("foo.py", 1, cwd=tmp_cwd)
    assert len(bps) == 1
    assert bps[0]["line"] == 2


def test_breakpoints_cli(cli, tmp_cwd: Path):
    (tmp_cwd / "foo.py").write_text("x = 1\n")
    out = cli("break", "foo.py:1")
    assert out["ok"] is True
    assert len(out["breakpoints"]) == 1

    out = cli("breakpoints")
    assert len(out["breakpoints"]) == 1

    out = cli("unbreak", "foo.py:1")
    assert out["breakpoints"] == []
