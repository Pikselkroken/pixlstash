@echo off
set PIXLSTASH_APP_DIR=%~dp0
set PIXLSTASH_PORT=9537

if exist "%PIXLSTASH_APP_DIR%venv\Scripts\pixlstash-server.exe" (
    start "" "%PIXLSTASH_APP_DIR%venv\Scripts\pixlstash-server.exe" %*
) else (
    start "" "%PIXLSTASH_APP_DIR%venv\Scripts\python.exe" -m pixlstash.app %*
)

powershell -NoProfile -Command ^
  "$healthUrl = 'http://localhost:%PIXLSTASH_PORT%/version'; $appUrl = 'http://localhost:%PIXLSTASH_PORT%/'; $deadline = [DateTime]::UtcNow.AddSeconds(60); while ([DateTime]::UtcNow -lt $deadline) { try { Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop | Out-Null; break } catch { Start-Sleep -Seconds 1 } }; Start-Process $appUrl"
