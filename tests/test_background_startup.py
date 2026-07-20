"""Background startup: handshake immediately, tools gate on index readiness."""
import time
import pytest
from codetree.server import create_server


def _tool_fn(mcp, name):
    return mcp.local_provider._components[f"tool:{name}@"].fn


def test_background_tools_block_until_ready(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    t0 = time.perf_counter()
    mcp = create_server(str(tmp_path), background=True)
    create_time = time.perf_counter() - t0
    # create_server must return without waiting for indexing
    assert create_time < 5.0
    # first tool call blocks until the index is ready, then answers correctly
    result = _tool_fn(mcp, "get_file_skeleton")("calc.py")
    assert "add" in result
    # dict-returning tools work too
    status = _tool_fn(mcp, "index_status")()
    assert status["graph_exists"] is True
    assert status["status"] == "ready"


def test_index_status_reports_indexing_without_blocking(tmp_path, monkeypatch):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    # slow the build down so we can observe the indexing state
    import codetree.server as srv
    import time as _time
    orig_build = srv.Indexer.build

    def slow_build(self, *args, **kwargs):
        _time.sleep(1.0)
        return orig_build(self, *args, **kwargs)

    monkeypatch.setattr(srv.Indexer, "build", slow_build)
    mcp = create_server(str(tmp_path), background=True)
    status = _tool_fn(mcp, "index_status")()
    assert status["status"] == "indexing"
    # once a gated tool returns, the probe must report ready
    _tool_fn(mcp, "get_file_skeleton")("calc.py")
    assert _tool_fn(mcp, "index_status")()["status"] == "ready"


def test_background_startup_failure_reported_not_hung(tmp_path):
    # a FILE as root: .codetree can't be created -> graph store open fails
    bad_root = tmp_path / "not_a_dir"
    bad_root.write_text("i am a file")
    mcp = create_server(str(bad_root), background=True)
    result = _tool_fn(mcp, "get_file_skeleton")("calc.py")
    assert "startup failed" in result
    status = _tool_fn(mcp, "index_status")()
    assert "startup failed" in status["error"]


def test_synchronous_startup_failure_still_raises(tmp_path):
    bad_root = tmp_path / "not_a_dir"
    bad_root.write_text("i am a file")
    with pytest.raises(Exception):
        create_server(str(bad_root))
