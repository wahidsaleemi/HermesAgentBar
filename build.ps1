<#
  Build HermesAgentBar.exe — standalone Windows executable
  ---------------------------------------------------------
  MUST be run on Windows with Python 3.10+ (PyInstaller cannot
  cross-compile a win32 exe from Linux/macOS without wine).

  What it does:
    1. Creates a clean local venv
    2. Installs runtime deps + PyInstaller from requirements.txt
    3. Bundles hermes_agentbar.py into a single windowed .exe
       using the shared assets/hermes_agentbar.ico icon
    4. Reports the built exe size

  Usage:
    powershell -ExecutionPolicy Bypass -File build.ps1
#>

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║      Build HermesAgentBar.exe (Windows)       ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# --- Python check ---
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "[!] Python not found. Install Python 3.10+ from python.org" -ForegroundColor Red
    exit 1
}
Write-Host "[*] Python found: $($python.Source)" -ForegroundColor Green

# --- Clean venv ---
if (Test-Path "build_venv") {
    Write-Host "[*] Removing existing build_venv..." -ForegroundColor DarkGray
    Remove-Item -Recurse -Force "build_venv"
}
Write-Host "[*] Creating build venv..." -ForegroundColor Cyan
python -m venv build_venv
& "build_venv\Scripts\Activate.ps1"

# --- Install deps + PyInstaller ---
Write-Host "[*] Installing dependencies + PyInstaller..." -ForegroundColor Cyan
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

# --- Build ---
Write-Host "[*] Running PyInstaller..." -ForegroundColor Cyan
pyinstaller --onefile `
    --windowed `
    --name "HermesAgentBar" `
    --icon "assets/hermes_agentbar.ico" `
    --add-data "assets;assets" `
    --hidden-import pystray._win32 `
    --hidden-import customtkinter `
    --collect-data customtkinter `
    --hidden-import win32api `
    --hidden-import win32gui `
    hermes_agentbar.py

# --- Report ---
if (Test-Path "dist/HermesAgentBar.exe") {
    $size = [math]::Round((Get-Item "dist/HermesAgentBar.exe").Length / 1MB, 1)
    Write-Host ""
    Write-Host "[OK] Built: dist/HermesAgentBar.exe ($size MB)" -ForegroundColor Green
    Write-Host "     Copy it anywhere on Windows and run!" -ForegroundColor DarkGray
} else {
    Write-Host "[FAIL] Build failed - dist/HermesAgentBar.exe not found" -ForegroundColor Red
    exit 1
}
