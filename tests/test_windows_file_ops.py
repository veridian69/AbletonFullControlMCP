import pytest
from ableton_mcp.local_automation.windows_file_ops import run_file_operation


class FakeUI:
    def __init__(self, *, fail=False, dialog=True, write=False):
        self.fail, self.dialog, self.write = fail, dialog, write
        self.calls = []

    def require_main(self, pid):
        if self.fail:
            raise RuntimeError('Live is not foreground')
        return 42

    def shortcut(self, hwnd, pid, key):
        self.calls.append(key)

    def save_dialog(self, pid, timeout):
        return 43 if self.dialog else None

    def submit_save(self, hwnd, pid, path):
        self.calls.append(str(path))
        if self.write:
            import gzip
            path.write_bytes(gzip.compress(b'<Ableton><LiveSet/></Ableton>'))


def test_foreground_failure_sends_nothing(tmp_path):
    ui = FakeUI(fail=True)
    result = run_file_operation('save_as', 7, str(tmp_path / 'x.als'), ui=ui, timeout=0)
    assert not result['ok']
    assert ui.calls == []


def test_existing_file_refused_before_ui(tmp_path):
    path = tmp_path / 'x.als'
    path.write_bytes(b'original')
    ui = FakeUI()
    result = run_file_operation('save_as', 7, str(path), ui=ui, timeout=0)
    assert not result['ok']
    assert ui.calls == []
    assert path.read_bytes() == b'original'


def test_save_requires_file_evidence(tmp_path):
    result = run_file_operation('save_as', 7, str(tmp_path / 'x.als'), ui=FakeUI(), timeout=0)
    assert result['status'] == 'user_action_required'
    assert result['saved'] is False


def test_save_success_with_als_evidence(tmp_path):
    result = run_file_operation('save_as', 7, str(tmp_path / 'x.als'), ui=FakeUI(write=True), timeout=0)
    assert result['saved'] is True
    assert result['status'] == 'completed'


def test_unknown_dialog_never_submitted(tmp_path):
    ui = FakeUI(dialog=False)
    result = run_file_operation('save_as', 7, str(tmp_path / 'x.als'), ui=ui, timeout=0)
    assert result['status'] == 'user_action_required'
    assert ui.calls == ['save_as']


@pytest.mark.parametrize('operation', ['new_set', 'export_audio'])
def test_ui_requests_do_not_claim_completion_or_answer_dialogs(operation):
    ui = FakeUI()
    result = run_file_operation(operation, 7, ui=ui, timeout=0)
    assert result['status'] == 'user_action_required'
    assert result['stage'] == 'shortcut_sent'
    assert result['ok'] is False
    assert ui.calls == [operation]


def test_invalid_operation_sends_nothing():
    ui = FakeUI()
    result = run_file_operation('discard', 7, ui=ui)
    assert not result['ok']
    assert ui.calls == []


def test_low_level_guard_rejects_wrong_process():
    from ableton_mcp.local_automation.windows_file_ops import WindowsLiveUI
    from types import SimpleNamespace
    ui = WindowsLiveUI.__new__(WindowsLiveUI)
    ui.u = SimpleNamespace(GetForegroundWindow=lambda: 42)
    ui.identity = lambda hwnd: {'process_id': 99}
    with pytest.raises(RuntimeError, match='no longer foreground'):
        ui.require_foreground(42, 7)


def test_low_level_guard_rejects_owned_dialog():
    from ableton_mcp.local_automation.windows_file_ops import WindowsLiveUI
    from types import SimpleNamespace
    ui = WindowsLiveUI.__new__(WindowsLiveUI)
    ui.u = SimpleNamespace(GetForegroundWindow=lambda: 42, GetWindow=lambda hwnd, flag: 41)
    ui.require_foreground = lambda hwnd, pid: None
    with pytest.raises(RuntimeError, match='main window'):
        ui.require_main(7)


def test_non_als_file_not_success(tmp_path):
    class InvalidUI(FakeUI):
        def submit_save(self, hwnd, pid, path):
            path.write_bytes(b'not an ALS')
    result = run_file_operation('save_as', 7, str(tmp_path / 'x.als'), ui=InvalidUI(), timeout=0)
    assert result['saved'] is False


def test_save_race_does_not_submit(tmp_path):
    path = tmp_path / 'x.als'
    class RacingUI(FakeUI):
        def save_dialog(self, pid, timeout):
            path.write_bytes(b'other writer')
            return 43
    ui = RacingUI()
    result = run_file_operation('save_as', 7, str(path), ui=ui, timeout=0)
    assert not result['ok']
    assert ui.calls == ['save_as']
    assert path.read_bytes() == b'other writer'


@pytest.mark.asyncio
async def test_all_windows_tools_registered():
    from mcp.server.fastmcp import FastMCP
    from ableton_mcp.tools.windows_file_ops import register
    mcp = FastMCP('test')
    register(mcp)
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {
        'windows_live_ui_status', 'windows_save_set_as', 'windows_new_set',
        'windows_open_native_export',
    }


@pytest.mark.asyncio
async def test_save_as_path_never_silently_saves_current_set(monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from ableton_mcp.tools import high_level
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(args)
        return {'ok': True}
    monkeypatch.setattr(high_level, '_call', forbidden)
    mcp = FastMCP('test')
    high_level.register(mcp)
    await mcp.call_tool('op_save_set', {'path': 'C:/test/new.als'})
    assert calls == []


def test_truncated_gzip_never_counts_as_saved(tmp_path):
    import gzip
    from ableton_mcp.local_automation.windows_file_ops import _als_evidence
    path = tmp_path / 'partial.als'
    path.write_bytes(gzip.compress(b'<Ableton><LiveSet>' + b'<X/>' * 4000 + b'</LiveSet></Ableton>')[:-5])
    assert not _als_evidence(path)


def test_malformed_xml_never_counts_as_saved(tmp_path):
    import gzip
    from ableton_mcp.local_automation.windows_file_ops import _als_evidence
    path = tmp_path / 'partial.als'
    path.write_bytes(gzip.compress(b'<Ableton><LiveSet>'))
    assert not _als_evidence(path)
