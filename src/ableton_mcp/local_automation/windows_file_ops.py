"""Explicit Windows UI fallback. Never use this module as a Live LOM operation."""
from __future__ import annotations

import ctypes
import gzip
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

_LOCK = threading.Lock()


def _als_evidence(path: Path) -> bool:
    try:
        before = path.stat()
        with gzip.open(path, 'rb') as stream:
            root_ok = False
            live_set = False
            depth = 0
            for event, element in ET.iterparse(stream, events=('start', 'end')):
                if event == 'start':
                    depth += 1
                    if depth == 1:
                        root_ok = element.tag == 'Ableton'
                    if depth == 2 and element.tag == 'LiveSet':
                        live_set = True
                else:
                    depth -= 1
                    element.clear()
            # iterparse consumes EOF; gzip validates CRC and trailer on that read.
        after = path.stat()
        return (root_ok and live_set and before.st_size == after.st_size
                and before.st_mtime_ns == after.st_mtime_ns)
    except (OSError, EOFError, ET.ParseError):
        return False


def run_file_operation(operation, process_id, path=None, *, timeout=10.0, ui=None):
    """Send one verified shortcut; save only to a fresh absolute .als destination."""
    result = dict(ok=False, via='windows_gui', operation=operation, saved=False,
                  status='error', stage='validation')
    if not _LOCK.acquire(blocking=False):
        return dict(result, error='Another Windows file operation is running')
    try:
        if operation not in {'save_as', 'new_set', 'export_audio'}:
            raise ValueError('Unknown Windows file operation')
        if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
            raise ValueError('process_id must be a positive integer from windows_live_ui_status')
        if not 0 <= timeout <= 30:
            raise ValueError('timeout must be between 0 and 30 seconds')
        destination = None
        if operation == 'save_as':
            destination = Path(path or '')
            if not destination.is_absolute() or destination.suffix.lower() != '.als':
                raise ValueError('Save As requires an absolute .als path')
            if any(c in str(destination) for c in '\r\n\x00'):
                raise ValueError('Control characters are not allowed in paths')
            if not destination.parent.is_dir():
                raise ValueError('Destination directory must already exist')
            if destination.exists():
                raise ValueError('Refusing to overwrite an existing path')
            result['path'] = str(destination)
        ui = ui or WindowsLiveUI()
        hwnd = ui.require_main(process_id)
        ui.shortcut(hwnd, process_id, operation)
        result.update(status='user_action_required', stage='shortcut_sent')
        if operation != 'save_as':
            result['next_action'] = (
                'Inspect Live and resolve any Save Changes prompt without discarding work; '
                'verify the new Set in Live.' if operation == 'new_set' else
                'Configure and run Export Audio/Video in Live. No render settings or '
                'output files have been verified by this helper.'
            )
            return result
        dialog = ui.save_dialog(process_id, timeout)
        if not dialog:
            result['next_action'] = 'Inspect Live: no recognised native Save As dialog appeared.'
            return result
        # Check again immediately before submission; never answer overwrite prompts.
        if destination.exists():
            raise ValueError('Destination appeared during Save As; refusing submission')
        ui.submit_save(dialog, process_id, destination)
        result['stage'] = 'save_submitted'
        deadline = time.monotonic() + timeout
        while True:
            if _als_evidence(destination):
                result.update(ok=True, status='completed', saved=True, stage='file_verified',
                              evidence='Complete gzip CRC/EOF and Ableton/LiveSet XML verified; file stable during read')
                return result
            if time.monotonic() >= deadline:
                result['next_action'] = (
                    'Inspect Live for a pending dialog. No valid .als file was observed; '
                    'do not retry blindly or discard the current Set.'
                )
                return result
            time.sleep(0.1)
    except (OSError, RuntimeError, ValueError) as exc:
        result['error'] = str(exc)
        return result
    finally:
        _LOCK.release()


class WindowsLiveUI:
    """Win32 only; requires Live already foreground and a caller-selected PID.

    Existing OLE utilities match title substrings and activate windows. File
    operations deliberately require executable identity and never steal focus.
    """

    def __init__(self):
        if sys.platform != 'win32':
            raise RuntimeError('Windows GUI helpers require Windows and an interactive desktop')
        from ctypes import wintypes as w
        self.w = w
        self.u = ctypes.WinDLL('user32', use_last_error=True)
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'GetForegroundWindow': ([], w.HWND),
            'GetWindowThreadProcessId': ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            'GetWindowTextW': ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetClassNameW': ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetWindow': ([w.HWND, w.UINT], w.HWND),
            'IsWindowEnabled': ([w.HWND], w.BOOL),
            'IsWindowVisible': ([w.HWND], w.BOOL),
            'GetDlgCtrlID': ([w.HWND], ctypes.c_int),
            'SendMessageTimeoutW': ([w.HWND, w.UINT, w.WPARAM, w.LPARAM,
                                     w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)], w.LPARAM),
        }
        for name, (args, restype) in signatures.items():
            fn = getattr(self.u, name)
            fn.argtypes, fn.restype = args, restype
        self.k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.k.OpenProcess.restype = w.HANDLE
        self.k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR,
                                                    ctypes.POINTER(w.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = w.BOOL
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.k.CloseHandle.restype = w.BOOL

    def text(self, hwnd, *, class_name=False):
        buf = ctypes.create_unicode_buffer(1024)
        fn = self.u.GetClassNameW if class_name else self.u.GetWindowTextW
        fn(hwnd, buf, len(buf))
        return buf.value

    def identity(self, hwnd):
        pid = self.w.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            raise RuntimeError('Cannot inspect foreground process')
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = self.w.DWORD(len(buf))
            if not self.k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                raise RuntimeError('Cannot resolve foreground executable')
            executable = buf.value
        finally:
            self.k.CloseHandle(handle)
        if not re.fullmatch(r'Ableton Live \d+ (Suite|Standard|Intro|Lite)\.exe',
                            Path(executable).name, re.IGNORECASE):
            raise RuntimeError('Foreground executable is not a recognised Ableton Live edition')
        return dict(process_id=pid.value, hwnd=int(hwnd), executable=executable,
                    title=self.text(hwnd), window_class=self.text(hwnd, class_name=True))

    def require_foreground(self, hwnd, pid):
        if self.u.GetForegroundWindow() != hwnd or self.identity(hwnd)['process_id'] != pid:
            raise RuntimeError('Expected Ableton process/window is no longer foreground')
        if not self.u.IsWindowEnabled(hwnd):
            raise RuntimeError('Live window is disabled by a modal dialog')

    def require_main(self, pid):
        hwnd = self.u.GetForegroundWindow()
        self.require_foreground(hwnd, pid)
        if self.u.GetWindow(hwnd, 4) or self.text(hwnd, class_name=True) != 'Ableton Live Window Class':
            raise RuntimeError('Bring the Ableton Live main window to the foreground first')
        return hwnd

    def shortcut(self, hwnd, pid, operation):
        w = self.w
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [('wVk', w.WORD), ('wScan', w.WORD), ('dwFlags', w.DWORD),
                        ('time', w.DWORD), ('dwExtraInfo', ctypes.c_size_t)]
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [('dx', w.LONG), ('dy', w.LONG), ('mouseData', w.DWORD),
                        ('dwFlags', w.DWORD), ('time', w.DWORD), ('dwExtraInfo', ctypes.c_size_t)]
        class UNION(ctypes.Union):
            _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]
        class INPUT(ctypes.Structure):
            _fields_ = [('type', w.DWORD), ('data', UNION)]
        keys = {'save_as': [0x11, 0x10, 0x53], 'new_set': [0x11, 0x4E],
                'export_audio': [0x11, 0x10, 0x52]}[operation]
        # Physical modifiers could change the command. Do not release user-held keys.
        self.u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.u.GetAsyncKeyState.restype = ctypes.c_short
        if any(self.u.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise RuntimeError('Release keyboard modifiers before invoking a GUI file operation')
        events = [INPUT(1, UNION(ki=KEYBDINPUT(key, 0, flag, 0, 0)))
                  for flag, sequence in [(0, keys), (2, reversed(keys))] for key in sequence]
        array = (INPUT * len(events))(*events)
        self.u.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self.u.SendInput.restype = w.UINT
        self.require_foreground(hwnd, pid)
        if self.u.SendInput(len(events), array, ctypes.sizeof(INPUT)) != len(events):
            raise RuntimeError('Windows rejected keyboard input; inspect Live and modifier state')

    def save_dialog(self, pid, timeout):
        deadline = time.monotonic() + timeout
        while True:
            hwnd = self.u.GetForegroundWindow()
            self.require_foreground(hwnd, pid)
            if (self.text(hwnd, class_name=True) == '#32770'
                    and self.text(hwnd).rstrip('.…') in {'Save Live Set As', 'Save As'}):
                return hwnd
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def message(self, hwnd, msg, wp=0, lp=0):
        result = ctypes.c_size_t()
        if not self.u.SendMessageTimeoutW(hwnd, msg, wp, lp, 2, 2000, ctypes.byref(result)):
            raise RuntimeError('Native dialog control did not respond')
        return result.value

    def submit_save(self, hwnd, pid, path):
        self.require_foreground(hwnd, pid)
        matches = []
        callback_type = ctypes.WINFUNCTYPE(self.w.BOOL, self.w.HWND, self.w.LPARAM)
        def visit(child, _):
            if (self.text(child, class_name=True) == 'Edit'
                    and self.u.GetDlgCtrlID(child) in {1001, 0x480}
                    and self.u.IsWindowVisible(child) and self.u.IsWindowEnabled(child)):
                matches.append(child)
            return True
        self.u.EnumChildWindows.argtypes = [self.w.HWND, callback_type, self.w.LPARAM]
        self.u.EnumChildWindows.restype = self.w.BOOL
        self.u.EnumChildWindows(hwnd, callback_type(visit), 0)
        if len(matches) != 1:
            raise RuntimeError('Native filename control is ambiguous or unavailable; finish Save As manually')
        value = ctypes.create_unicode_buffer(str(path))
        self.message(matches[0], 0x000C, 0, ctypes.addressof(value))  # WM_SETTEXT
        readback = ctypes.create_unicode_buffer(32768)
        self.message(matches[0], 0x000D, len(readback), ctypes.addressof(readback))
        if readback.value != str(path):
            raise RuntimeError('Save As filename readback mismatch; nothing submitted')
        self.require_foreground(hwnd, pid)
        if path.exists():
            raise RuntimeError('Destination now exists; nothing submitted')
        self.message(hwnd, 0x0111, 1, 0)  # WM_COMMAND / IDOK, only recognised save dialog
