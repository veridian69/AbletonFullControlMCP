# Windows GUI file helpers

These optional tools run in the MCP server's interactive Windows desktop.
They are separate from the Python Live Object Model and the bridge. They use
only Python's standard library; no AppActivate, title-only activation, clipboard,
mouse coordinates or additional automation packages.

1. Bring Ableton Live's main window to the foreground, with no modal dialog.
2. Call `windows_live_ui_status()` to read the foreground executable, window and PID.
3. Pass that `process_id` to the relevant operation. Keep keyboard and mouse idle
   while the helper runs.

```text
windows_save_set_as(process_id=1234, path="D:/Music/My Project/New Version.als")
windows_new_set(process_id=1234)
windows_open_native_export(process_id=1234)
```

The PID is illustrative: obtain the actual value from the status tool.

- **Save As:** sends Ctrl-Shift-S, recognises an English native Save As dialog,
  fills its uniquely identified filename edit control, checks the text back and
  submits once. The destination must be an absolute `.als` path in an existing
  directory and must not exist. The helper never answers overwrite prompts.
  `saved: true` requires a complete gzip CRC/EOF and streaming XML validation with an
  Ableton root and LiveSet child, with unchanged file size/mtime during the read.
  This is file evidence, not proof of which process wrote the file or a Live
  reload test.
- **New Set:** sends Ctrl-N once. Any Save Changes prompt remains for the user.
  The helper never selects Discard. Live uses its configured default template.
  Response is `user_action_required`, `stage: shortcut_sent`; creation is not
  verified, even if Live immediately creates a Set without a prompt.
- **Native export:** sends Ctrl-Shift-R once. Configure Export Audio/Video and
  finish the native export in Live. Response is `user_action_required`,
  `stage: shortcut_sent`; neither dialog appearance, render settings nor output
  is verified. Use the bounce tools for programmatic capture where suitable.

Every shortcut checks foreground HWND, executable identity, supplied PID,
main-window class and enabled state. User-held keyboard modifiers cause a refusal.
These checks do not make global keyboard injection atomic with foreground changes:
keep the desktop idle. Concurrent helper calls in one MCP process are refused.
Running multiple MCP server processes is not serialised.

Unknown/custom/localised dialogs are left untouched for manual completion.
Recognised executable names are Ableton Live N Suite/Standard/Intro/Lite.exe;
other editions/builds fail closed. Locked desktops, elevation differences and
Windows input restrictions may prevent operation. The helper does not steal
focus or retry a shortcut. If an operation times out, inspect Live before retrying.
A race with another writer can still show an overwrite prompt, which remains
unanswered. Use a destination unique to this operation.

`op_save_set(path=...)` now returns a GUI-helper hint without calling the bridge;
it no longer silently ignores the requested path and saves over the current Set.
`op_save_set()` retains the existing bridge behaviour.

Validation: fake-UI tests exercise foreground refusal, existing-file/race refusal,
missing/invalid file evidence, native-dialog absence, truthful stage reporting,
and tool registration. No Save As, New or Export command was run against Live
while developing these helpers. Native dialog compatibility remains unverified
on the user's Live 11.3.43 installation.
