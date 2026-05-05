# Firmware

This directory contains the StickS3 firmware source used with `codex-ble-buddy`.

## Included Source

- `code-buddy-sticks3/`: StickS3 firmware source copied from `CharlexH/CodeBuddy`.
- `code-buddy-sticks3-source.zip`: zip archive generated from the same source directory for convenient download.

## M5Burner Image

- `m5burner/054cc8a7b876c402854d72aa809b6401.bin`: prebuilt firmware image from the local M5Burner package.
- SHA256: `4E245CAB8343E4C4ABCE0BE32790028AFC04F5700D33DC4DC5B13CE7E824F202`

This image is included so users can keep the matching M5Burner binary together with the host bridge and firmware source.

## Upstream

- Repository: https://github.com/CharlexH/CodeBuddy
- Upstream commit: `8ef537a4c66131575d951b838bb42b13f8a2da31`
- Upstream firmware path: `firmware/`

## License Notes

The firmware source includes its upstream `LICENSE`. Code is MIT licensed as stated there.

The `code-buddy-sticks3/characters/bufo/` GIF assets have separate third-party artwork terms documented in upstream `LICENSE` and `characters/bufo/README.md`.

## Build

Install PlatformIO, then:

```powershell
cd firmware\code-buddy-sticks3
pio run
```

Flash directly:

```powershell
pio run -t upload
```
