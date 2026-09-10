"""Permanent write blockade: no bridge access, Live access or job advancement."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def forbidden(*args, **kwargs):
    pytest.fail("Disabled film writes must not access Live, connect or advance a job")


@pytest.mark.parametrize("confirm", [False, True])
def test_bridge_write_blocked_before_live_or_job_access(monkeypatch, confirm):
    path = Path(__file__).resolve().parents[1] / "live_remote_script/AbletonFullControlBridge/handlers/project.py"
    spec = importlib.util.spec_from_file_location("disabled_film_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "Live", SimpleNamespace(
        Application=SimpleNamespace(get_application=forbidden)))
    result = module.import_film_cues(None, preview=False, confirm_tempo=confirm,
                                     job_id="override", allow_write=True)
    assert result["status"] == "refused"
    assert result["write_outcome"] == "not_written"
    assert result["writes_enabled"] is False
    assert "job_id" not in result
    assert not getattr(module, "_FILM_CUE_JOBS", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [False, True])
async def test_mcp_write_blocked_before_planning_or_connection(monkeypatch, confirm):
    from ableton_mcp.server import build_server
    from ableton_mcp.tools import film_cues
    monkeypatch.setattr(film_cues, "get_bridge_client", forbidden)
    monkeypatch.setattr(film_cues, "plan_film_cues", forbidden)
    _, result = await build_server().call_tool("film_cues_import", {
        "cue_sheet": {}, "preview": False, "confirm_tempo": confirm,
    })
    assert result["status"] == "refused"
    assert result["write_outcome"] == "not_written"
    assert result["writes_enabled"] is False
    assert "job_id" not in result


@pytest.mark.parametrize("cancel", [False, True])
def test_retained_job_cannot_be_advanced(monkeypatch, cancel):
    path = Path(__file__).resolve().parents[1] / "live_remote_script/AbletonFullControlBridge/handlers/project.py"
    spec = importlib.util.spec_from_file_location("disabled_film_step", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class NeverAdvance:
        __next__ = forbidden
        throw = forbidden
        close = forbidden
    module._FILM_CUE_JOBS = {"old": {"steps": NeverAdvance(), "done": False}}
    monkeypatch.setitem(sys.modules, "Live", SimpleNamespace(
        Application=SimpleNamespace(get_application=forbidden)))
    result = module.film_cues_step(None, job_id="old", cancel=cancel)
    assert result["status"] == "refused"
    assert result["write_outcome"] == "unknown"
    assert result["transport_restored"] is None
    assert result["job_id"] == "old"
