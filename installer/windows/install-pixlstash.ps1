param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir
)

$ErrorActionPreference = "Stop"

function Resolve-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{ exe = "py"; args = @("-3.12") }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{ exe = "python"; args = @() }
    }
    throw "Python 3.10+ is required but was not found. Install Python and run installer again."
}

$pythonCmd = Resolve-PythonCommand
$venvDir = Join-Path $AppDir "venv"
$wheelDir = Join-Path $AppDir "dist"

if (-not (Test-Path $venvDir)) {
    Write-Host "Creating virtual environment in $venvDir"
    & $pythonCmd.exe @($pythonCmd.args) -m venv $venvDir
}

$pipExe = Join-Path $venvDir "Scripts\pip.exe"
if (-not (Test-Path $pipExe)) {
    throw "pip not found in virtual environment: $pipExe"
}

$wheel = Get-ChildItem -Path $wheelDir -Filter "pixlstash-*.whl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $wheel) {
    throw "No pixlstash wheel found in $wheelDir"
}

Write-Host "Installing wheel $($wheel.FullName)"
& $pipExe install --upgrade pip setuptools wheel
& $pipExe install --upgrade $wheel.FullName

$launcherPath = Join-Path $AppDir "Start-PixlStash-Server.bat"
$launcherContent = "@echo off`r`nset PIXLSTASH_APP_DIR=%~dp0`r`n\"%PIXLSTASH_APP_DIR%venv\Scripts\pixlstash-server.exe\" %*`r`n"
Set-Content -Path $launcherPath -Value $launcherContent -Encoding ASCII

Write-Host "PixlStash installation completed."
