# codex-ble-buddy

Cross-platform BLE bridge for OpenAI Codex approvals and M5StickS3 Buddy hardware.

This project connects Codex `PermissionRequest` hooks to a BLE device that exposes a Nordic UART Service style interface. The first MVP targets Windows with Python and `bleak`.

## Support Status

- Codex CLI: supported through the official `PermissionRequest` hook path.
- Codex App/Desktop: supported through the same `~/.codex/config.toml` hook configuration used by Codex CLI.
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

On Windows, double-click `首次配置.bat` in this folder for Chinese prompts. It installs dependencies, runs `doctor`, and opens the Codex hook configuration prompt. An English alias, `first-time-setup.bat`, is also provided.

From Windows PowerShell:

```powershell
cd C:\Users\23479\Documents\GitHub\codex-ble-buddy
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

For Chinese prompts, run:

```powershell
codex-ble-buddy setup-codex --language zh
```

The setup command shows the system default Codex config path, lets you accept it or type a custom `config.toml` path, then asks for confirmation before writing. It adds a managed `PermissionRequest` hook block and can update that block later without replacing the rest of your Codex config.

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

Run the Codex hook flow manually with sample input:

```powershell
'{"hookEventName":"PermissionRequest","tool":"shell","command":"dir","reason":"test"}' | codex-ble-buddy approve-request --timeout 30
```

Doctor check:

```powershell
codex-ble-buddy doctor
```

`doctor` also reports whether the Codex hook managed by this project is configured in your default Codex config file.

## Codex Hook Configuration

Use the interactive setup command first:

```powershell
codex-ble-buddy setup-codex
```

Examples are also provided in:

- `examples/hooks.json`
- `examples/config.toml`

Use the absolute path to `scripts/codex_permission_hook.py` in your local checkout.

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
            "command": "python C:\\Users\\23479\\Documents\\GitHub\\codex-ble-buddy\\scripts\\codex_permission_hook.py --timeout 30",
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
[features]
codex_hooks = true

[[hooks.PermissionRequest]]
matcher = ".*"

[[hooks.PermissionRequest.hooks]]
type = "command"
command = "python C:\\Users\\23479\\Documents\\GitHub\\codex-ble-buddy\\scripts\\codex_permission_hook.py --timeout 30"
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
