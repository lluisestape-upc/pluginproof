param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name PluginProof `
    --icon "assets\logo.ico" `
    --add-data "assets\gui.html;assets" `
    --add-data "assets\report_shell.html;assets" `
    --collect-all pedalboard `
    --collect-all matplotlib `
    --collect-all scipy `
    --collect-all webview `
    gui.py
