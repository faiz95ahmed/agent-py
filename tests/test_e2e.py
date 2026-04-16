"""End-to-end: launch a real debuggee, attach, hit breakpoint, list vars, continue."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix-socket daemon")


def _wait_until(predicate, timeout=30.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_full_flow(cli, sample_script: Path, tmp_cwd: Path):
    # line 7 is the `total += value` statement inside the loop
    out = cli("break", f"{sample_script}:7")
    assert out["ok"] is True
    assert len(out["breakpoints"]) == 1

    out = cli("launch", str(sample_script))
    assert out["ok"] is True
    assert out["port"] > 0

    out = cli("connect", "--break-on", "uncaught", timeout=45)
    assert out["ok"] is True
    assert out["status"] == "paused", out
    pause = out["pause"]
    assert pause["line"] == 7
    assert pause["file"].endswith("sample.py")
    # source context includes the current line
    assert any(s["current"] and s["line"] == 7 for s in pause["source"])
    # stack should include at least compute + main + module
    funcs = [f["function"] for f in pause["stack"]]
    assert "compute" in funcs

    # list locals in the innermost frame (compute)
    out = cli("listvars")
    assert out["ok"] is True
    scopes = out["scopes"]
    local_scope = next(s for s in scopes if s["scope"].lower().startswith("local"))
    names = [v["name"] for v in local_scope["items"]]
    assert "data" in names
    assert "total" in names
    # `data` is a dict so it should be a ref
    data_var = next(v for v in local_scope["items"] if v["name"] == "data")
    assert "ref" in data_var

    # expand the list with pagination (debugpy adds a few synthesized entries
    # like `len()`, so total is >= the 25 real elements).
    page1 = cli("variable", str(data_var["ref"]))
    assert page1["ok"] is True
    assert page1["total"] >= 25
    assert page1["page_size"] == 10
    assert len(page1["items"]) == 10

    last_page = cli("variable", str(data_var["ref"]), "--page", str(page1["pages"]))
    assert len(last_page["items"]) >= 1

    # all indices 0..24 should be reachable across pages
    all_names: list[str] = []
    for p in range(1, page1["pages"] + 1):
        out = cli("variable", str(data_var["ref"]), "--page", str(p))
        all_names.extend(v["name"] for v in out["items"])
    # debugpy zero-pads list indices to the width of the largest index.
    assert "00" in all_names
    assert "24" in all_names

    # switch to the main frame (index 1) and check we see `numbers` + `label`
    out = cli("frame", "1")
    assert out["ok"] is True
    assert out["pause"]["function"] == "main"

    out = cli("listvars")
    main_locals = next(s for s in out["scopes"] if s["scope"].lower().startswith("local"))
    main_names = [v["name"] for v in main_locals["items"]]
    assert "numbers" in main_names
    assert "label" in main_names
    label = next(v for v in main_locals["items"] if v["name"] == "label")
    assert "value" in label  # scalar — no ref
    assert "sum of squares" in label["value"]

    # step over once — should still be paused (next iteration of loop) or on another line
    # Actually frame switched; jump back to inner frame first by setting it via listvars path.
    # Simpler: just continue until termination.
    out = cli("continue", timeout=30)
    # after continue, may hit breakpoint again (24 more times) or terminate.
    # Loop until terminated.
    tries = 0
    while out.get("status") == "paused" and tries < 50:
        out = cli("continue", timeout=30)
        tries += 1
    assert out.get("status") == "terminated", out

    # kill cleans up
    k = cli("kill")
    assert k["ok"] is True
