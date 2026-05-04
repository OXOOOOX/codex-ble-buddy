# codex-ble-buddy

Cross-platform BLE bridge for OpenAI Codex approvals and M5StickS3 Buddy hardware.

This project connects Codex `PermissionRequest` hooks to a BLE device that exposes a Nordic UART Service style interface. The first MVP targets Windows with Python and `bleak`.

## Support Status

- Codex CLI: supported through the official `PermissionRequest` hook path.
- Codex App/Desktop: supported through the same `~/.codex/config.toml` hook configuration used by Codex CLI.
- The setup command sets `approval_policy = "untrusted"` so trusted commands can run automatically while untrusted requests are sent through the Buddy approval hook.
- The installed matcher is `.*`, so every Codex request that produces a `PermissionRequest` is forwarded to Buddy for approval.
- Windows UI automation for non-hook approval dialogs is intentionally out of scope for the MVP.

## MVP Scope

- Scan for BLE devices named `Codex-*`, `CodeBuddy*`, or `Buddy*`.
- Connect with Nordic UART Service UUIDs.
- Send approval request JSON to the device.
- Wait for an explicit `allow` or `deny` response.
- Provide a Codex hook that never defaults to allow.
- Keep the first version focused on Codex hook approvals.

## Requirements

- Python 3.10+
- Windows 10/11 with Bluetooth enabled
- Python dependency: `bleak`
- A BLE device advertising one of these name prefixes:
  - `Codex-`
  - `CodeBuddy`
  - `Buddy`

## First-Time Setup

Install dependencies before configuring the Codex hook. The CLI and hook do not auto-install packages at runtime because permission prompts should stay fast and predictable.

On Windows, double-click `首次配置.bat` in this folder for Chinese prompts. It installs dependencies, runs `doctor`, opens the Codex and Claude Code hook configuration prompts, and enables local service auto-start for both hooks. An English alias, `first-time-setup.bat`, is also provided.

From Windows PowerShell:

```powershell
cd <path-to-codex-ble-buddy>
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
codex-ble-buddy doctor
```

Configure Codex to use the BLE approval hook:

```powershell
codex-ble-buddy setup-codex
```

Configure Claude Code to use the same BLE approval hook:

```powershell
codex-ble-buddy setup-claude
```

For Chinese prompts, run:

```powershell
codex-ble-buddy setup-codex --language zh
```

The setup command shows the system default Codex config path, lets you accept it or type a custom `config.toml` path, then asks for confirmation before writing. It sets `approval_policy = "untrusted"` and adds a managed `PermissionRequest` hook block. Later runs can update that managed block without replacing the rest of your Codex config.

For faster and more reliable approvals, start the persistent BLE service before opening Codex or Claude Code:

```powershell
codex-ble-buddy serve
```

If you want the hook to start that local service when it is offline, opt in during setup:

```powershell
codex-ble-buddy setup-codex --auto-start-service
codex-ble-buddy setup-claude --auto-start-service
```

The hook cannot ask an interactive question while Codex is waiting for JSON output, so auto-start is controlled by this setup-time consent. With this flag, setup installs a Windows scheduled task named `codex-ble-buddy` after the hook config is written, and the hook starts that task when the service is offline. Without this flag, the hook only uses an already-running service and falls back to the one-shot BLE path.

If you do not want an editable install:

```powershell
python -m pip install -r requirements.txt
```

If `doctor`, `scan`, or `send-test` reports that `bleak` is unavailable, run one of the install commands above. The Codex hook returns no decision when dependencies are missing; it never installs packages or approves automatically.

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

## CLI Usage

Scan for Buddy devices:

```powershell
codex-ble-buddy scan
```

Send a test approval prompt:

```powershell
codex-ble-buddy send-test --timeout 30
```

Keep a persistent BLE connection warm:

```powershell
codex-ble-buddy serve
```

With the service running, hook requests use the local service first and reuse the persistent BLE connection. If the service is not available, the hook falls back to the one-shot scan/connect flow.

Configure the hook to start the service automatically when it is offline:

```powershell
codex-ble-buddy setup-codex --auto-start-service
```

You can also manage the Windows scheduled task directly:

```powershell
codex-ble-buddy install-service-task
codex-ble-buddy start-service-task
codex-ble-buddy uninstall-service-task
```

When started through the scheduled task, service logs are written to `%TEMP%\codex-ble-buddy\service.log`.

Send a test approval prompt through the local service:

```powershell
codex-ble-buddy send-test --service --timeout 30
```

Run the Codex hook flow manually with sample input:

```powershell
'{"hookEventName":"PermissionRequest","tool":"shell","command":"dir","reason":"test"}' | codex-ble-buddy approve-request --timeout 30
```

Doctor check:

```powershell
codex-ble-buddy doctor
```

`doctor` also reports whether the Codex hook managed by this project is configured in your default Codex config file.
It also reports whether the local persistent service is online at `http://127.0.0.1:8765` and whether its BLE connection is currently connected.

## Codex Hook Configuration

Use the interactive setup command first:

```powershell
codex-ble-buddy setup-codex
```

Examples are also provided in:

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

## Tests

```powershell
python -m unittest discover -s tests
```
