# codex-ble-buddy

BLE approval bridge for OpenAI Codex, Claude Code, and M5StickS3 Buddy hardware.

`codex-ble-buddy` connects Codex or Claude Code `PermissionRequest` hooks to a BLE Buddy device that speaks a Nordic UART Service style protocol. Approval requests appear on the Buddy hardware, and the hook only returns `allow` or `deny` after the device sends an explicit decision.

The current implementation is focused on Windows 10/11 with Python and `bleak`.

## What Works

- Codex CLI and Codex App/Desktop through `~/.codex/config.toml`.
- Claude Code through `~/.claude/settings.json`.
- Persistent local BLE service on `127.0.0.1:8765`.
- Optional hook auto-start through a Windows scheduled task named `codex-ble-buddy`.
- BLE devices advertising as `Codex-*`, `CodeBuddy*`, or `Buddy*`.
- Safe failure behavior: no device response means no decision, never default allow.

Windows UI automation for non-hook approval dialogs is intentionally out of scope.

## Requirements

- Python 3.10+
- Windows 10/11 with Bluetooth enabled
- Python dependency: `bleak`
- A BLE device advertising one of these name prefixes:
  - `Codex-`
  - `CodeBuddy`
  - `Buddy`

## Quick Start

For the guided Windows setup, double-click one of these scripts from the repository folder:

- `首次配置.bat`: Chinese prompts.
- `first-time-setup.bat`: English prompts.

The first-time setup script installs dependencies, runs `doctor`, configures Codex, optionally configures Claude Code, and enables local service auto-start for the configured hooks.

Manual setup from PowerShell:

```powershell
cd C:\path\to\codex-ble-buddy
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
codex-ble-buddy doctor
codex-ble-buddy setup-codex --auto-start-service
codex-ble-buddy setup-claude --auto-start-service
```

The setup commands use the current Windows user automatically:

- Codex default config: `%USERPROFILE%\.codex\config.toml`
- Claude Code default settings: `%USERPROFILE%\.claude\settings.json`

They also generate hook commands with the current Python executable, so users do not need to copy a machine-specific path.

## Recommended Test Flow

1. Confirm the Buddy is discoverable:

   ```powershell
   codex-ble-buddy scan --timeout 10
   ```

2. Start or auto-start the persistent service:

   ```powershell
   codex-ble-buddy start-service-task
   codex-ble-buddy doctor
   ```

   A healthy service reports `online` and ideally `(connected)`.

3. Send a test approval through the service:

   ```powershell
   codex-ble-buddy send-test --service --timeout 30
   ```

   Press `allow` on the Buddy. Expected output:

   ```json
   {"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}
   ```

4. Restart Codex or Claude Code after changing hook settings so the app reloads its config.

If `doctor`, `scan`, or `send-test` reports that `bleak` is unavailable, install dependencies with `python -m pip install -e .` or `python -m pip install -r requirements.txt`. Hooks never install packages at runtime.

## StickS3 Firmware

The matching StickS3 firmware source is included in this repository:

- Source: `firmware/code-buddy-sticks3/`
- Source zip: `firmware/code-buddy-sticks3-source.zip`
- M5Burner binary: `firmware/m5burner/054cc8a7b876c402854d72aa809b6401.bin`
- Upstream: [CharlexH/CodeBuddy](https://github.com/CharlexH/CodeBuddy), firmware copied from commit `8ef537a4c66131575d951b838bb42b13f8a2da31`

Build with PlatformIO:

```powershell
cd firmware\code-buddy-sticks3
pio run
```

Flash directly:

```powershell
pio run -t upload
```

The firmware source keeps the upstream `LICENSE`. The bundled `characters/bufo/` GIF assets have separate third-party artwork terms documented in `firmware/code-buddy-sticks3/LICENSE` and `firmware/code-buddy-sticks3/characters/bufo/README.md`.

## Install For Development

From Windows PowerShell:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
```

If editable install is not needed:

```powershell
python -m pip install -r requirements.txt
```

## CLI Reference

Diagnostics:

```powershell
codex-ble-buddy doctor
```

Scan for Buddy devices:

```powershell
codex-ble-buddy scan --timeout 10
```

Keep a persistent BLE connection warm:

```powershell
codex-ble-buddy serve
```

Configure hooks:

```powershell
codex-ble-buddy setup-codex --auto-start-service
codex-ble-buddy setup-claude --auto-start-service
```

Manage the Windows scheduled task:

```powershell
codex-ble-buddy install-service-task
codex-ble-buddy start-service-task
codex-ble-buddy uninstall-service-task
```

Send a test approval prompt through the local service:

```powershell
codex-ble-buddy send-test --service --timeout 30
```

Run the hook flow manually with sample input:

```powershell
'{"hookEventName":"PermissionRequest","tool":"shell","command":"dir","reason":"test"}' | codex-ble-buddy approve-request --timeout 30 --auto-start-service
```

When started through the scheduled task, service logs are written to `%TEMP%\codex-ble-buddy\service.log`.

`doctor` also reports whether the Codex hook managed by this project is configured in your default Codex config file.
It also reports whether the local persistent service is online at `http://127.0.0.1:8765` and whether its BLE connection is currently connected.

## Hook Configuration Details

Use the setup commands first:

```powershell
codex-ble-buddy setup-codex --auto-start-service
codex-ble-buddy setup-claude --auto-start-service
```

Codex setup sets `approval_policy = "untrusted"`, enables `codex_hooks`, and installs a managed `PermissionRequest` hook block. Claude Code setup writes the equivalent managed hook into `settings.json`. Later setup runs replace this project's managed hook without replacing unrelated user settings.

Examples are provided in:

- `examples/hooks.json`
- `examples/config.toml`

Prefer the module entry point shown below after installing the project into the active Python environment. The setup commands generate a command with the current Python executable automatically.

Example `~/.codex/hooks.json`:

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "python -m codex_ble_buddy.cli approve-request --timeout 30 --auto-start-service",
            "timeout": 30,
            "statusMessage": "Checking approval request"
          }
        ]
      }
    ]
  }
}
```

Example `~/.codex/config.toml`:

```toml
approval_policy = "untrusted"

[features]
codex_hooks = true

[[hooks.PermissionRequest]]
matcher = ".*"

[[hooks.PermissionRequest.hooks]]
type = "command"
command = "python -m codex_ble_buddy.cli approve-request --timeout 30 --auto-start-service"
timeout = 30
statusMessage = "Checking approval request"
```

## BLE Protocol

Computer to device:

```json
{
  "total": 1,
  "running": 0,
  "waiting": 1,
  "msg": "approve: shell",
  "entries": ["npm install"],
  "tokens": 0,
  "tokens_today": 0,
  "prompt": {
    "id": "request-id",
    "tool": "shell",
    "hint": "npm install"
  }
}
```

Device to computer:

```json
{
  "cmd": "permission",
  "id": "request-id",
  "decision": "once"
}
```

or:

```json
{
  "cmd": "permission",
  "id": "request-id",
  "decision": "deny"
}
```

Messages are UTF-8 JSON with a newline terminator. The bridge maps CodeBuddy `decision: "once"` to Codex `allow`.

## Safety Behavior

The hook never approves by default.

- Local service unavailable: falls back to one-shot BLE.
- Local service busy: returns no decision.
- BLE unavailable: returns no decision.
- Scan timeout: returns no decision.
- Connection failure: returns no decision.
- Device timeout: returns no decision.
- Malformed device response: returns no decision.
- Explicit `deny`: returns Codex deny output.
- Explicit `allow`: returns Codex allow output for the current request only.

## Windows BLE Troubleshooting

- Make sure Bluetooth is enabled in Windows Settings.
- Make sure the device is powered on and advertising.
- If the device was previously paired and behaves oddly, remove it from Windows Bluetooth settings and retry.
- Run `codex-ble-buddy scan --timeout 10` to confirm advertisement visibility.
- Keep the device close during the MVP connection tests.
- `No active codex turn` on the Buddy means the service is linked but idle. It is expected after an approval completes.
- If `send-test --service` returns `{}`, check `%TEMP%\codex-ble-buddy\service.log`. `Forwarded permission request ...` followed by `Timed out waiting for BLE Buddy notification` means the PC wrote the request to BLE but did not receive an allow/deny notification.
- If hook settings changed but Codex or Claude Code does not prompt, restart the app so it reloads its config file.

## Tests

```powershell
python -m unittest discover -s tests
```
