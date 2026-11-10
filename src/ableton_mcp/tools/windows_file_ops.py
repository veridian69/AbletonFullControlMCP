"""Clearly labelled native Windows file operations, separate from bridge/LOM tools."""
from __future__ import annotations

import asyncio
from mcp.server.fastmcp import FastMCP
from ..local_automation.windows_file_ops import WindowsLiveUI, run_file_operation


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def windows_live_ui_status() -> dict:
        """Read foreground Ableton executable, PID and window; sends no keys.

        Bring Live's main window forward before using Windows file helpers.
        Pass the returned process_id explicitly to select that Live instance.
        """
        try:
            ui = WindowsLiveUI()
            info = ui.identity(ui.u.GetForegroundWindow())
            try:
                ui.require_main(info['process_id'])
                info['main_window_ready'] = True
            except RuntimeError:
                info['main_window_ready'] = False
            return dict(ok=True, via='windows_gui', **info)
        except (OSError, RuntimeError) as exc:
            return dict(ok=False, via='windows_gui', error=str(exc))

    @mcp.tool()
    async def windows_save_set_as(process_id: int, path: str, timeout: float = 10.0) -> dict:
        """Windows GUI Save As to a NEW absolute .als path; refuses existing files.

        Requires foreground Live main window and selected PID. Supports English
        native Save As dialogs; other dialogs need manual completion. Success
        requires a complete gzip and Ableton/LiveSet XML validation. Never answers overwrite or
        Save Changes prompts. Do not use keyboard/mouse during this operation.
        """
        return await asyncio.to_thread(run_file_operation, 'save_as', process_id,
                                       path, timeout=timeout)

    @mcp.tool()
    async def windows_new_set(process_id: int) -> dict:
        """Windows GUI: send Ctrl-N to verified foreground Live main window.

        Leaves Save Changes prompts for the user; never discards unsaved edits.
        Returns user_action_required because a new Set is not verified. Uses
        Live's default template, which need not be empty.
        """
        return await asyncio.to_thread(run_file_operation, 'new_set', process_id)

    @mcp.tool()
    async def windows_open_native_export(process_id: int) -> dict:
        """Windows GUI: send Ctrl-Shift-R to verified foreground Live main window.

        User must inspect Export Audio/Video, configure render settings and
        complete export manually. This helper does not render audio or verify
        the export dialog/output. Returns user_action_required at shortcut_sent.
        """
        return await asyncio.to_thread(run_file_operation, 'export_audio', process_id)
