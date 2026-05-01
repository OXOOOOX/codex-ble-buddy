@echo off
setlocal

cd /d "%~dp0"

if not exist ".tmp" mkdir ".tmp"
set "TEMP=%~dp0.tmp"
set "TMP=%~dp0.tmp"

if /i "%CODEX_BLE_BUDDY_LANGUAGE%"=="zh" (
  goto zh_setup
)

echo.
echo Codex BLE Buddy first-time setup
echo =================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on PATH.
  echo Install Python 3.10 or newer, then run this setup again.
  echo.
  pause
  exit /b 1
)

echo Installing or updating project dependencies in the current Python environment...
python -m pip install -e .
if errorlevel 1 (
  echo.
  echo Dependency installation failed.
  echo You can retry after checking your network, Python, or pip installation.
  echo.
  pause
  exit /b 1
)

echo.
echo Running environment check...
codex-ble-buddy doctor
if errorlevel 1 (
  echo.
  echo Doctor check failed. Fix the issue above, then run this setup again.
  echo.
  pause
  exit /b 1
)

echo.
echo Starting Codex hook configuration.
echo Press Enter to accept the default Codex config path, or type a custom config.toml path.
echo.
codex-ble-buddy setup-codex
if errorlevel 1 (
  echo.
  echo Codex hook configuration was cancelled or failed.
  echo.
  pause
  exit /b 1
)

echo.
echo Setup complete.
echo You can now test the device with:
echo   codex-ble-buddy scan --timeout 10
echo   codex-ble-buddy send-test --timeout 30
echo.
pause
exit /b 0

:zh_setup
echo.
echo Codex BLE Buddy 首次配置
echo =======================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo 未在 PATH 中找到 Python。
  echo 请安装 Python 3.10 或更高版本，然后重新运行此配置。
  echo.
  pause
  exit /b 1
)

echo 正在当前 Python 环境中安装或更新项目依赖...
python -m pip install -e .
if errorlevel 1 (
  echo.
  echo 依赖安装失败。
  echo 请检查网络、Python 或 pip 安装后重试。
  echo.
  pause
  exit /b 1
)

echo.
echo 正在检查运行环境...
codex-ble-buddy doctor
if errorlevel 1 (
  echo.
  echo 环境检查失败。请修复上方问题后重新运行此配置。
  echo.
  pause
  exit /b 1
)

echo.
echo 开始配置 Codex hook。
echo 按 Enter 使用默认 Codex 配置文件路径，或输入自定义 config.toml 路径。
echo.
codex-ble-buddy setup-codex --language zh
if errorlevel 1 (
  echo.
  echo Codex hook 配置已取消或失败。
  echo.
  pause
  exit /b 1
)

echo.
echo 配置完成。
echo 现在可以使用以下命令测试设备：
echo   codex-ble-buddy scan --timeout 10
echo   codex-ble-buddy send-test --timeout 30
echo.
pause
