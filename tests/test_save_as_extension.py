import asyncio
import gzip
from pathlib import Path
import pytest
async def save_set_as(*args, **kwargs):
    import importlib.util
    assert importlib.util.find_spec("ableton_mcp.local_automation.save_set") is not None, "Save As extension missing"
    from ableton_mcp.local_automation.save_set import save_set_as as operation
    return await operation(*args, **kwargs)


class Driver:
    def __init__(self, result="complete"):
        self.result = result
        self.opened = False
        self.cancelled = False
        self.target = None
    def open_save_as(self):
        self.opened = True
    def submit(self, path):
        self.target = path
        if self.result == "complete":
            Path(path).write_bytes(gzip.compress(b'<Ableton><LiveSet /></Ableton>'))
    def poll(self):
        return self.result
    def cancel(self):
        self.cancelled = True


async def active(path):
    return {"file_path": str(path), "process_id": 123}


@pytest.mark.asyncio
async def test_save_as_handles_spaces_and_verifies_actual_als_and_active_path(tmp_path):
    target = tmp_path / "Jon's new score.als"
    driver = Driver()
    result = await save_set_as(str(target), lambda: active(target), driver=driver)
    assert result["saved"] is True
    assert result["verified"] is True
    assert result["path"] == str(target.resolve())
    assert gzip.decompress(target.read_bytes()).startswith(b'<Ableton>')


@pytest.mark.asyncio
async def test_save_as_refuses_existing_destination_without_opening_dialog(tmp_path):
    target = tmp_path / "existing.als"
    target.write_bytes(b"original")
    driver = Driver()
    result = await save_set_as(str(target), lambda: active(target), driver=driver)
    assert result["saved"] is False
    assert result["status"] == "conflict"
    assert target.read_bytes() == b"original"
    assert not driver.opened


@pytest.mark.asyncio
async def test_save_as_cancel_is_never_reported_as_success(tmp_path):
    target = tmp_path / "cancelled.als"
    result = await save_set_as(str(target), lambda: active(target), driver=Driver("cancelled"))
    assert result["saved"] is False
    assert result["status"] == "cancelled"
    assert not target.exists()


@pytest.mark.asyncio
async def test_save_as_refuses_overwrite_confirmation(tmp_path):
    target = tmp_path / "race.als"
    driver = Driver("overwrite")
    result = await save_set_as(str(target), lambda: active(target), driver=driver)
    assert result["saved"] is False
    assert result["status"] == "conflict"
    assert driver.cancelled


@pytest.mark.asyncio
async def test_save_as_wrong_active_path_is_unverified(tmp_path):
    target = tmp_path / "new.als"
    result = await save_set_as(str(target), lambda: active(tmp_path / "other.als"), driver=Driver())
    assert result["saved"] is False
    assert result["status"] == "unverified"
    assert result["file_written"] is True


@pytest.mark.asyncio
async def test_save_as_invalid_als_is_never_reported_saved(tmp_path):
    target = tmp_path / "invalid.als"
    driver = Driver()
    def invalid_submit(path):
        Path(path).write_bytes(b"not an Ableton Set")
    driver.submit = invalid_submit
    result = await save_set_as(str(target), lambda: active(target), driver=driver)
    assert result["saved"] is False
    assert result["status"] == "unverified"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["relative.als", "relative.txt"])
async def test_save_as_invalid_path_does_not_touch_ui(path, tmp_path):
    driver = Driver()
    result = await save_set_as(path, lambda: active(tmp_path), driver=driver)
    assert result["saved"] is False
    assert not driver.opened


def test_unsupported_preexisting_dialog_is_never_cancelled():
    from types import SimpleNamespace
    from ableton_mcp.local_automation.save_set import WindowsSaveDialog, SaveUIUnsupported
    driver = WindowsSaveDialog.__new__(WindowsSaveDialog)
    driver.initial = set()
    driver.pid = 123
    driver._windows = lambda: [99]
    touched = []
    driver.con = SimpleNamespace(WM_CLOSE=16)
    driver._send = lambda *args: touched.append(args[0])
    driver.gui = SimpleNamespace(GetClassName=lambda h: "#32770",
                                GetDlgItem=lambda h, i: touched.append(h))
    with pytest.raises(SaveUIUnsupported):
        driver.open_save_as()
    driver.cancel()
    assert touched == []


def test_save_filename_verification_reads_control_text_across_processes(tmp_path):
    from types import SimpleNamespace
    from ableton_mcp.local_automation.save_set import WindowsSaveDialog
    target = str(tmp_path / "new.als")
    driver = WindowsSaveDialog.__new__(WindowsSaveDialog)
    driver.dialog, driver.edit, driver.button = 9, 10, 11
    driver.gui = SimpleNamespace(IsWindow=lambda h: True, GetWindowText=lambda h: "")
    driver.con = SimpleNamespace(WM_SETTEXT=12, BM_CLICK=13)
    driver._read_control_text = lambda h: target
    sent = []
    driver._send = lambda h, msg, *args: sent.append((h, msg))
    driver.submit(target)
    assert (11, 13) in sent
