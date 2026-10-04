# iGUI 3 (Python)

Python port of iGUI 2.x (VB.NET, `..\iGui`): a tray application that drives
SAP GUI through SAP GUI Scripting, controlled by a local HTTP API and hotkeys.

## Run / test / build

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest              # unit + API security tests (no SAP needed)
.venv\Scripts\pythonw -m igui               # run from source
.venv\Scripts\pyinstaller packaging\igui.spec --noconfirm   # -> dist\iGui\iGui.exe
```

Starting the application (source or exe) also:

* creates or removes `iGUI.lnk` in the user's Startup folder, depending on the
  Autostart setting (default: on). This replaces the iGUI 2.x shortcut.
* sets `WarnOnAttach=0` in the SAP GUI registry key, unless
  `sap.suppress_attach_warning = false`.

Stop iGUI 2.x before starting iGUI 3. Both use port 50501 and the same hotkeys.

## Layout

| Module | Replaces (VB.NET) | Purpose |
|---|---|---|
| `config.py` | `My.Settings`, `App.config` | TOML config (defaults → `<install dir>\igui.toml` → `%APPDATA%\iGui\igui.toml`), user state |
| `commands.py` | parsing in `Form1.callSap` | command language parser and validation |
| `macros.py` | `Context.vb` | macro definitions, `/ctx` rendering |
| `sap/session.py` | session loop in `callSap` | scripting engine access, session selection |
| `sap/executor.py` | `callSap("Command")` | runs commands |
| `sap/dump.py` | `GuiTraverse.vb` | screen dump ("Zapisz kontekst") |
| `sap/recorder.py`, `sap/com_events.py` | `RecSess_*` handlers | session recorder → files |
| `sap/worker.py` | `goSAP`, `Go_Gui` threads | single COM (STA) worker thread with a bounded queue |
| `server.py` | `Nancy.vb`, `Nancy_host.vb` | HTTP API and security |
| `ui/tray.py` | `Form1` (NotifyIcon, menu) | tray icon, menu, balloon notifications |
| `ui/overlay.py` | `TransparentForm.vb`, `Timer_GUI` | splash and SAP window badge |
| `ui/hotkeys.py` | `RegisterHotKey`/`WndProc` | Ctrl+Alt+I / G / H |
| `autostart.py`, `runtime.py`, `winutil.py` | `Autostart.vb`, `ApplicationEvents.vb`, `win32.vb` | Windows integration |

Logs: `%LOCALAPPDATA%\iGui\logs\igui.log`.

## HTTP API (http://localhost:50501)

| Endpoint | Method | |
|---|---|---|
| `/go` | POST, PUT (GET only in legacy mode) | run commands, body `a=b&c=d` or JSON `{"commands": [["_tr","SE16"],["_vkey","0"]]}` |
| `/ctx/<macro>` | POST, PUT (GET only in legacy mode) | run a macro, body = placeholder values (`TABLE=MARA`) |
| `/iGUI/context.json` | GET | macro definitions |
| `/version`, `/help`, `/get` | GET | info / diagnostics (`/get` echoes the query as JSON) |

Action responses: `202 {"status":"queued"}`. Errors: `400` (invalid command, with
the reason), `401` (token), `403` (origin/host/client), `404` (unknown macro),
`405` (GET disabled), `413` (body too large), `503` (SAP queue full). The
outcome in SAP is shown as a tray notification.

The command language is the same as in iGUI 2.x (see `src/igui/resources/help.txt`).

## Security

The API acts in SAP with the user's authorisations, so it is locked down:

1. It listens on 127.0.0.1 / ::1 only. The client address and the `Host` header are
   checked, which blocks remote access and DNS rebinding. iGUI 2.x checked only the
   Host header.
2. No wildcard CORS. Web pages must be listed in `security.allowed_origins`.
   Cross-site browser requests are rejected using `Origin`, `Referer` and
   `Sec-Fetch-Site`. In 2.x any web page the user opened could drive SAP.
3. Actions use POST/PUT. GET links only work with `security.allow_get_actions = true`.
4. Optional shared secret `security.api_token`, sent as the `X-iGui-Token` header,
   as `Authorization: Bearer`, or as a `_token` form field. It is compared in
   constant time.
5. Every command is validated before it reaches SAP: transaction codes, element ids,
   vkey and screen numbers, value length, control characters and command count. The
   body size is limited and the SAP queue is bounded.
6. Responses send `no-store`, `nosniff`, a restrictive CSP and `X-Frame-Options`.
   `/get` returns JSON instead of HTML.
7. Macro definitions are loaded from https or a local file. Plain http to a
   non-local host only logs a warning, and the size is limited.

Example of an internal web page allowed to call iGUI:

```toml
# %APPDATA%\iGui\igui.toml or <install dir>\igui.toml
[security]
allowed_origins = ["https://portal.example.com"]
api_token = "change-me-to-a-long-random-value"
```

```js
fetch("http://localhost:50501/ctx/TABLE", {
  method: "POST",
  headers: {"Content-Type": "application/x-www-form-urlencoded", "X-iGui-Token": TOKEN},
  body: new URLSearchParams({TABLE: "MARA"}),
});
```

## Recorder

Ctrl+Alt+G (or the tray menu) starts recording the SAP session. Ctrl+Alt+H stops it.
Files go to `recorder.directory`, by default `Documents\iGui\recordings`:

* `igui_<time>.txt` contains the event log (focus changes, field changes, requests,
  screens), followed by the recorded steps as iGUI commands. The steps are given
  both as a ready-to-POST `/go` body and as a link. `_screen` checks are added after
  each screen change. The file name is copied to the clipboard.
* `igui_<time>.vbs` contains the same steps as a standard SAP GUI script. iGUI
  generates it from the events, because this SAP GUI rejects `session.RecordFile`
  ("The method got an invalid argument").

Commands that iGUI itself runs during a recording (API calls, macros) are recorded
exactly as sent. SAP's own events for them are incomplete (a `StartTransaction`
shows up only as a bare Enter), so those events are not recorded a second time.

pywin32's `makepy` cannot process SAP's type library. Events are therefore connected
directly through `IConnectionPoint`, using the DISPIDs read from the registered type
library. The sink answers SAP's `QueryInterface` for the event IID with an `IDispatch`
wrapper; see `sap/com_events.py`.

Verified live against a production SAP system: typed steps and API calls were recorded, the `.vbs` was
generated, and the recorded `/go` body was replayed successfully.

## Deployment

```powershell
packaging\build.ps1      # tests, then dist\iGui\ with iGui.exe (+ igui.toml, macros.json)
```

Copy `dist\iGui\` to the target machine and run `iGui.exe` once. That replaces
the iGUI 2.x autostart shortcut. Stop iGUI 2.x first: it holds port 50501 through
http.sys and the same hotkeys.

Site-specific settings are not committed. Create `igui.toml` (from
`igui.example.toml`: SAP system id, `allow_get_actions` for `wget …/go?_tr=…`
callers) and `macros.json` (your macro definitions) in the project root.
`build.ps1` copies both next to the exe. Without them the neutral defaults in
`src/igui/resources/` are used: system `DEV` and SE16/SE38 example macros.

## Bugs fixed compared with 2.x

* HTTP requests ran on VB's per-thread default `Form1` instance, so they never saw
  the tray app's last-used SAP window. Now one worker owns that state.
* `.press` called `.select`.
* Macro values containing `&` or `=` broke the command list. Values are now
  URL-encoded, and a missing placeholder returns 400.
* `Context.getLink`: the query was doubled, numbering started at `p0`, and the
  method was unused. It now produces a working `/ctx` link, shown in the dump.
* The "all sessions busy" status could never be reported. Windows are now compared
  top-level to top-level.
* The session UI could stay locked after an exception. `_pause` now unlocks the UI
  while it waits.
* Screen numbers are compared as numbers. Unknown element actions are now an error.
* The sequence stops only on SAP error or abort messages (E/A). Success, warning
  and info messages are reported.
* Dump: no crash on the root element, the title is captured correctly, elements
  without a value are listed, and `dump` over HTTP saves a file.
* Crash handler: no null-reference crash, and the log goes to `%LOCALAPPDATA%`.
* Recorder: no crash when no session is found. A failed start leaves nothing
  half-open. The output is saved to files.
* Window handles are 64-bit safe. Dead code (`Nancy_host.vb`, unused Win32
  declarations) was dropped.
