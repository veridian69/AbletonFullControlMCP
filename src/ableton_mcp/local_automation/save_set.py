"""Narrow Windows Save As adapter. Never accept overwrite; verify disk and Live.

Only classic Windows common Save dialogs are supported. Unknown/custom dialogs
return unsupported, with no guessed keystrokes. No automation occurs on import.
"""
from __future__ import annotations
import asyncio
import gzip
import os
from pathlib import Path
import time
from typing import Any, Awaitable, Callable
import xml.etree.ElementTree as ET


class SaveUIUnsupported(RuntimeError):
    pass


class WindowsSaveDialog:
    def __init__(self, process_id: int):
        if os.name != "nt":
            raise SaveUIUnsupported("Save As UI is only supported on Windows")
        try:
            import win32api
            import win32con
            import win32gui
            import win32process
        except ImportError as exc:
            raise SaveUIUnsupported("Windows UI adapter needs pywin32") from exc
        self.api, self.con, self.gui, self.process = win32api, win32con, win32gui, win32process
        self.pid = int(process_id)
        handle = win32api.OpenProcess(0x1000, False, self.pid)
        try:
            import ctypes
            from ctypes import wintypes
            name = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(name))
            query = ctypes.windll.kernel32.QueryFullProcessImageNameW
            query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL
            if not query(wintypes.HANDLE(int(handle)), 0, name, ctypes.byref(size)):
                raise SaveUIUnsupported("cannot verify the Live process executable")
            if not Path(name.value).name.lower().startswith("ableton live"):
                raise SaveUIUnsupported("bridge PID does not identify Ableton Live")
        finally:
            handle.Close()
        self.dialog = None
        self.edit = None
        self.button = None
        self.initial = set()

    def _windows(self):
        found = []
        def visit(hwnd, _):
            if self.gui.IsWindowVisible(hwnd) and self.process.GetWindowThreadProcessId(hwnd)[1] == self.pid:
                found.append(hwnd)
        self.gui.EnumWindows(visit, None)
        return found

    def _send(self, hwnd, message, wparam=0, lparam=0):
        return self.gui.SendMessageTimeout(hwnd, message, wparam, lparam,
                                           self.con.SMTO_ABORTIFHUNG, 2000)

    def open_save_as(self):
        windows = self._windows()
        self.initial = set(windows)
        if any(self.gui.GetClassName(w) == "#32770" for w in windows):
            raise SaveUIUnsupported("Live already has a modal dialog; close it first")
        mains = [w for w in windows if "Ableton Live" in self.gui.GetWindowText(w)
                 and not self.gui.GetWindow(w, self.con.GW_OWNER)]
        if len(mains) != 1:
            raise SaveUIUnsupported("cannot identify exactly one Live main window")
        main = mains[0]
        self.gui.SetForegroundWindow(main)
        if self.gui.GetForegroundWindow() != main:
            raise SaveUIUnsupported("Live did not acquire keyboard focus")
        self.initial = set(windows)
        # Key releases are guaranteed even if input injection fails midway.
        keys = [self.con.VK_CONTROL, self.con.VK_SHIFT, ord("S")]
        try:
            for key in keys:
                self.api.keybd_event(key, 0, 0, 0)
        finally:
            for key in reversed(keys):
                self.api.keybd_event(key, 0, self.con.KEYEVENTF_KEYUP, 0)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            dialogs = [w for w in self._windows() if w not in self.initial
                       and self.gui.GetClassName(w) == "#32770"]
            if len(dialogs) == 1:
                self.dialog = dialogs[0]
                break
            time.sleep(0.05)
        if self.dialog is None:
            raise SaveUIUnsupported("standard Windows Save As dialog unavailable")
        children = []
        self.gui.EnumChildWindows(self.dialog, lambda w, _: children.append(w), None)
        edits = [w for w in children if self.gui.GetClassName(w) == "Edit"
                 and (self.gui.GetDlgCtrlID(w) in (1001, 1152)
                      or self.gui.GetDlgCtrlID(self.gui.GetParent(w)) == 1148)]
        self.button = self.gui.GetDlgItem(self.dialog, 1)
        if len(edits) != 1 or not self.button or self.gui.GetClassName(self.button) != "Button":
            raise SaveUIUnsupported("Save As filename/Save controls are unsupported")
        self.edit = edits[0]

    def _read_control_text(self, hwnd):
        # GetWindowText deliberately cannot read another process's edit
        # controls. WM_GETTEXT is the documented cross-process route.
        import ctypes
        from ctypes import wintypes
        send = ctypes.windll.user32.SendMessageTimeoutW
        send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                         wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                         ctypes.POINTER(ctypes.c_size_t)]
        send.restype = wintypes.LPARAM
        buffer = ctypes.create_unicode_buffer(32768)
        result = ctypes.c_size_t()
        ok = send(hwnd, self.con.WM_GETTEXT, len(buffer),
                  ctypes.cast(buffer, ctypes.c_void_p).value,
                  self.con.SMTO_ABORTIFHUNG, 2000, ctypes.byref(result))
        if not ok:
            raise SaveUIUnsupported("could not read Save As filename control")
        return buffer.value

    def submit(self, path):
        if Path(path).exists():
            raise FileExistsError("destination appeared before Save; refusing overwrite")
        if not self.dialog or not self.gui.IsWindow(self.dialog):
            raise SaveUIUnsupported("Save As dialog was cancelled")
        self._send(self.edit, self.con.WM_SETTEXT, 0, str(path))
        if self._read_control_text(self.edit) != str(path):
            raise SaveUIUnsupported("Save As filename field did not accept the exact path")
        if Path(path).exists():
            raise FileExistsError("destination appeared before Save; refusing overwrite")
        self._send(self.button, self.con.BM_CLICK)

    def poll(self):
        windows = self._windows()
        # Any additional modal after submission is never approved: overwrite,
        # missing folder, invalid filename, trial-mode warning, etc.
        if any(w not in self.initial and w != self.dialog
               and self.gui.GetClassName(w) == "#32770" for w in windows):
            return "overwrite"
        if self.dialog and self.gui.IsWindow(self.dialog):
            return "pending"
        return "complete"

    def cancel(self):
        # Only dismiss windows created by this invocation, in this Live process.
        for hwnd in self._windows():
            if hwnd not in self.initial and self.gui.GetClassName(hwnd) == "#32770":
                cancel = self.gui.GetDlgItem(hwnd, 2)
                if cancel:
                    self._send(cancel, self.con.BM_CLICK)
                else:
                    self._send(hwnd, self.con.WM_CLOSE)


def _valid_als(path: Path) -> bool:
    try:
        with gzip.open(path, "rb") as stream:
            data = stream.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            return False
        root = ET.fromstring(data)
        return root.tag == "Ableton" and root.find("LiveSet") is not None
    except (OSError, EOFError, ET.ParseError):
        return False


class WindowsSaveCurrent(WindowsSaveDialog):
    """Ctrl+S only, in the verified bridge process; never approve a dialog."""

    def save_current(self):
        windows = self._windows()
        self.initial = set(windows)
        mains = [w for w in windows if "Ableton Live" in self.gui.GetWindowText(w)
                 and not self.gui.GetWindow(w, self.con.GW_OWNER)]
        if len(mains) != 1:
            raise SaveUIUnsupported("cannot identify exactly one Live main window")
        self.main = mains[0]
        # Refuse all other visible windows, including custom modals and plug-in
        # editors. Keyboard focus must never redirect Save into another editor.
        if len(windows) != 1 or not self.gui.IsWindowEnabled(self.main):
            raise SaveUIUnsupported("Live has another visible window or modal; close it first")
        try:
            self.gui.SetForegroundWindow(self.main)
        except Exception:
            # A background MCP process may lack foreground permission. Borrow
            # the current foreground input queue only for focus acquisition;
            # always detach it before injecting the verified Live shortcut.
            foreground = self.gui.GetForegroundWindow()
            source_thread = self.api.GetCurrentThreadId()
            foreground_thread = self.process.GetWindowThreadProcessId(foreground)[0]
            if source_thread == foreground_thread:
                raise SaveUIUnsupported("Live did not acquire keyboard focus")
            self.process.AttachThreadInput(source_thread, foreground_thread, True)
            try:
                self.gui.SetForegroundWindow(self.main)
            finally:
                self.process.AttachThreadInput(source_thread, foreground_thread, False)
        if self.gui.GetForegroundWindow() != self.main:
            raise SaveUIUnsupported("Live did not acquire keyboard focus")
        keys = [self.con.VK_CONTROL, ord("S")]
        try:
            for key in keys:
                self.api.keybd_event(key, 0, 0, 0)
        finally:
            for key in reversed(keys):
                self.api.keybd_event(key, 0, self.con.KEYEVENTF_KEYUP, 0)

    def poll(self):
        windows = self._windows()
        if any(w not in self.initial for w in windows):
            return "modal"
        if self.main not in windows or not self.gui.IsWindowEnabled(self.main):
            return "pending"
        return "complete"


def _file_signature(path: Path):
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)


async def save_existing_set(active_set: Callable[[], Awaitable[dict[str, Any]]],
                            *, driver=None, timeout_seconds: float = 15.0) -> dict[str, Any]:
    """Save only the existing active ALS; success requires an observed new write.

    No active path/missing file returns needs_save_as without keyboard input.
    Unknown dialogs and unchanged/invalid disk files are never reported saved.
    """
    base = {"saved": False, "verified": False, "write_observed": False}
    target = None
    try:
        if not 0 < timeout_seconds <= 60:
            return {**base, "status": "error", "error": "timeout must be > 0 and <= 60 seconds"}
        info = await active_set()
        current = info.get("file_path")
        if not current:
            return {**base, "status": "needs_save_as", "error": "active Set has no saved path; supply a new .als path"}
        target = Path(current)
        if (not target.is_absolute() or target.suffix.lower() != ".als"
                or str(target).startswith("\\")):
            return {**base, "status": "unsupported", "error": "active path must be an absolute local .als file"}
        target = target.resolve()
        if not target.is_file():
            return {**base, "status": "needs_save_as", "path": str(target), "error": "active ALS is missing; use Save As"}
        if not _valid_als(target):
            return {**base, "status": "unverified", "path": str(target), "error": "existing file is not a valid Ableton Set; Save refused"}
        if driver is None:
            if not info.get("process_id"):
                raise SaveUIUnsupported("bridge does not expose the local Live process ID; update bridge")
            driver = WindowsSaveCurrent(info["process_id"])
        before = _file_signature(target)
        again = await active_set()
        if (not again.get("file_path") or Path(again["file_path"]).resolve() != target
                or again.get("process_id") != info.get("process_id")):
            return {**base, "status": "conflict", "path": str(target), "error": "active Set changed before Save; no key sent"}
        await asyncio.to_thread(driver.save_current)
        deadline = time.monotonic() + timeout_seconds
        write_observed = False
        while time.monotonic() < deadline:
            state = await asyncio.to_thread(driver.poll)
            if state == "modal":
                await asyncio.to_thread(driver.cancel)
                return {**base, "status": "unsupported", "path": str(target), "error": "additional Live window/dialog refused"}
            if state == "complete" and target.is_file():
                write_observed = _file_signature(target) != before
                if write_observed:
                    after = await active_set()
                    if (after.get("file_path") and Path(after["file_path"]).resolve() == target
                            and after.get("process_id") == info.get("process_id")
                            and _valid_als(target)):
                        return {"saved": True, "verified": True, "status": "ok",
                                "write_observed": True, "path": str(target),
                                "bytes": target.stat().st_size, "method": "windows_save"}
                    if after.get("file_path") != str(target):
                        return {**base, "status": "unverified", "write_observed": True,
                                "path": str(target), "active_file_path": after.get("file_path"),
                                "error": "active Set path changed after Save"}
            await asyncio.sleep(0.1)
        return {**base, "status": "unverified", "write_observed": write_observed,
                "path": str(target), "error": "fresh valid ALS write and active path could not be verified"}
    except (SaveUIUnsupported, ImportError) as exc:
        if driver is not None:
            await asyncio.to_thread(driver.cancel)
        return {**base, "status": "unsupported", "error": str(exc), "path": str(target) if target else None}
    except Exception as exc:
        if driver is not None:
            try:
                await asyncio.to_thread(driver.cancel)
            except Exception:
                pass
        return {**base, "status": "error", "error": str(exc), "path": str(target) if target else None}


async def save_set_as(path: str, active_set: Callable[[], Awaitable[dict[str, Any]]],
                      *, driver=None, timeout_seconds: float = 15.0) -> dict[str, Any]:
    """Save to a new absolute .als path and verify active path + a real ALS file.

    Existing destinations are always refused. No return value from the UI is
    treated as proof. A file without verifiable active path is unverified.
    """
    base = {"saved": False, "verified": False}
    target = Path(path)
    if (not target.is_absolute() or target.suffix.lower() != ".als"
            or not target.parent.is_dir() or not target.name.strip()
            or any(ch in target.name for ch in '<>"|?*')
            or target.name.endswith((" ", ".")) or str(target).startswith("\\")):
        return {**base, "status": "error", "error": "use an absolute local .als path in an existing folder"}
    target = target.resolve()
    if target.exists():
        return {**base, "status": "conflict", "path": str(target), "error": "destination exists; overwrite refused"}
    if not 0 < timeout_seconds <= 60:
        return {**base, "status": "error", "error": "timeout must be > 0 and <= 60 seconds"}
    try:
        if driver is None:
            info = await active_set()
            if not info.get("process_id"):
                raise SaveUIUnsupported("bridge does not expose the local Live process ID; update bridge")
            driver = WindowsSaveDialog(info["process_id"])
        await asyncio.to_thread(driver.open_save_as)
        await asyncio.to_thread(driver.submit, str(target))
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            state = await asyncio.to_thread(driver.poll)
            if state == "overwrite":
                await asyncio.to_thread(driver.cancel)
                return {**base, "status": "conflict", "error": "additional modal dialog refused; overwrite never approved"}
            if state == "cancelled":
                return {**base, "status": "cancelled", "path": str(target)}
            if state == "complete":
                # UI closes before a large set finishes writing. Allow bounded
                # write latency but do not report a missing/invalid file saved.
                if target.exists():
                    info = await active_set()
                    current = info.get("file_path")
                    if _valid_als(target) and current and Path(current).resolve() == target:
                        return {"saved": True, "verified": True, "status": "ok",
                                "path": str(target), "bytes": target.stat().st_size,
                                "method": "windows_save_as"}
                    return {**base, "status": "unverified", "path": str(target),
                            "file_written": True, "active_file_path": current,
                            "error": "disk ALS validity or active Live Set path could not be verified"}
            await asyncio.sleep(0.1)
        await asyncio.to_thread(driver.cancel)
        return {**base, "status": "timeout", "path": str(target), "file_written": target.exists()}
    except (SaveUIUnsupported, ImportError) as exc:
        if driver is not None:
            await asyncio.to_thread(driver.cancel)
        return {**base, "status": "unsupported", "error": str(exc), "path": str(target)}
    except FileExistsError as exc:
        if driver is not None:
            await asyncio.to_thread(driver.cancel)
        return {**base, "status": "conflict", "error": str(exc), "path": str(target)}
    except Exception as exc:
        if driver is not None:
            try:
                await asyncio.to_thread(driver.cancel)
            except Exception:
                pass
        return {**base, "status": "error", "error": str(exc), "path": str(target),
                "file_written": target.exists()}
