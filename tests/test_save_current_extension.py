"""Current Save must prove a fresh disk write, never just key injection."""
import gzip
import os
from pathlib import Path
import pytest


async def save_current(*args, **kwargs):
    from ableton_mcp.local_automation import save_set
    assert hasattr(save_set, "save_existing_set"), "verified current Save missing"
    return await save_set.save_existing_set(*args, **kwargs)


def existing(path):
    path.write_bytes(gzip.compress(b'<Ableton><LiveSet /></Ableton>'))
    os.utime(path, ns=(1_600_000_000_000_000_000,) * 2)
    return path


async def active(path):
    return {"file_path": str(path) if path else None, "process_id": 123}


class Driver:
    def __init__(self, path, state="complete", write=True):
        self.path, self.state, self.write = path, state, write
        self.opened = self.cancelled = False

    def save_current(self):
        self.opened = True
        if self.write:
            self.path.write_bytes(gzip.compress(b'<Ableton><LiveSet><Saved /></LiveSet></Ableton>'))

    def poll(self):
        return self.state

    def cancel(self):
        self.cancelled = True


@pytest.mark.asyncio
async def test_current_save_requires_fresh_valid_als_and_matching_path(tmp_path):
    path = existing(tmp_path / "Jon's current score.als")
    result = await save_current(lambda: active(path), driver=Driver(path))
    assert result["saved"] and result["verified"]
    assert result["method"] == "windows_save"
    assert result["write_observed"] is True
    assert b"Saved" in gzip.decompress(path.read_bytes())


@pytest.mark.asyncio
async def test_current_save_without_disk_write_never_claims_success(tmp_path):
    path = existing(tmp_path / "score.als")
    result = await save_current(lambda: active(path), driver=Driver(path, write=False), timeout_seconds=0.02)
    assert not result["saved"] and not result["verified"]
    assert result["status"] == "unverified"
    assert result["write_observed"] is False


@pytest.mark.asyncio
async def test_current_save_unsaved_set_requires_save_as_without_keys(tmp_path):
    driver = Driver(tmp_path / "unused.als")
    result = await save_current(lambda: active(None), driver=driver)
    assert result["status"] == "needs_save_as"
    assert not driver.opened


@pytest.mark.asyncio
async def test_current_save_missing_als_requires_save_as_without_keys(tmp_path):
    path = tmp_path / "missing.als"
    driver = Driver(path)
    result = await save_current(lambda: active(path), driver=driver)
    assert result["status"] == "needs_save_as"
    assert not driver.opened


@pytest.mark.asyncio
async def test_current_save_invalid_existing_file_refuses_overwrite(tmp_path):
    path = tmp_path / "wrong.als"
    path.write_bytes(b"not a Live Set")
    driver = Driver(path)
    result = await save_current(lambda: active(path), driver=driver)
    assert result["status"] == "unverified"
    assert path.read_bytes() == b"not a Live Set"
    assert not driver.opened


@pytest.mark.asyncio
async def test_current_save_changed_active_path_is_never_verified(tmp_path):
    path = existing(tmp_path / "score.als")
    calls = 0
    async def changed():
        nonlocal calls
        calls += 1
        return await active(path if calls <= 2 else tmp_path / "other.als")
    result = await save_current(changed, driver=Driver(path))
    assert not result["saved"] and result["status"] == "unverified"


@pytest.mark.asyncio
async def test_current_save_unknown_dialog_refused_even_if_file_written(tmp_path):
    path = existing(tmp_path / "score.als")
    driver = Driver(path, state="modal")
    result = await save_current(lambda: active(path), driver=driver)
    assert not result["saved"] and result["status"] == "unsupported"
    assert driver.cancelled


@pytest.mark.asyncio
async def test_current_save_invalid_written_als_is_never_verified(tmp_path):
    path = existing(tmp_path / "score.als")
    driver = Driver(path)
    def invalid():
        path.write_bytes(b"broken save")
    driver.save_current = invalid
    result = await save_current(lambda: active(path), driver=driver, timeout_seconds=0.02)
    assert not result["saved"] and result["status"] == "unverified"


def test_current_ui_refuses_preexisting_owned_dialog_without_touching_it():
    from types import SimpleNamespace
    from ableton_mcp.local_automation import save_set
    assert hasattr(save_set, "WindowsSaveCurrent"), "Windows current Save adapter missing"
    driver = save_set.WindowsSaveCurrent.__new__(save_set.WindowsSaveCurrent)
    driver.initial = set()
    driver._windows = lambda: [1, 2]
    touched = []
    driver.con = SimpleNamespace(GW_OWNER=4)
    driver.gui = SimpleNamespace(GetWindowText=lambda h: "Score - Ableton Live 12 Suite",
                                GetWindow=lambda h, flag: 1 if h == 2 else 0,
                                IsWindowEnabled=lambda h: True,
                                GetClassName=lambda h: "CustomDialog")
    driver._send = lambda *args: touched.append(args)
    with pytest.raises(save_set.SaveUIUnsupported):
        driver.save_current()
    driver.cancel()
    assert touched == []


def test_current_ui_focus_retry_detaches_threads_and_releases_shortcut():
    from types import SimpleNamespace
    from ableton_mcp.local_automation.save_set import WindowsSaveCurrent
    driver = WindowsSaveCurrent.__new__(WindowsSaveCurrent)
    driver.initial = set()
    driver._windows = lambda: [1]
    calls, keys, state = [], [], {"focused": 2}
    def focus(hwnd):
        if not calls:
            raise RuntimeError("Windows foreground lock")
        state["focused"] = hwnd
    driver.gui = SimpleNamespace(GetWindowText=lambda h: "Score - Ableton Live 12 Suite",
                                GetWindow=lambda h, flag: 0,
                                IsWindowEnabled=lambda h: True,
                                GetForegroundWindow=lambda: state["focused"],
                                SetForegroundWindow=focus)
    driver.api = SimpleNamespace(GetCurrentThreadId=lambda: 10,
                                keybd_event=lambda *args: keys.append(args[:3]))
    driver.process = SimpleNamespace(GetWindowThreadProcessId=lambda h: (20, 321),
                                     AttachThreadInput=lambda *args: calls.append(args))
    driver.con = SimpleNamespace(GW_OWNER=4, VK_CONTROL=17, KEYEVENTF_KEYUP=2)
    driver.save_current()
    assert state["focused"] == 1
    assert calls == [(10, 20, True), (10, 20, False)]
    assert keys == [(17, 0, 0), (83, 0, 0), (83, 0, 2), (17, 0, 2)]
