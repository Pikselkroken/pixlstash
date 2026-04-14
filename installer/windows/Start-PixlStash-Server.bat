@echo off
set PIXLSTASH_APP_DIR=%~dp0
set PIXLSTASH_PORT=9537

start "" /B powershell -NoProfile -WindowStyle Hidden -Command "$healthUrl = 'http://localhost:%PIXLSTASH_PORT%/version'; $appUrl = 'http://localhost:%PIXLSTASH_PORT%/'; $deadline = [DateTime]::UtcNow.AddSeconds(60); while ([DateTime]::UtcNow -lt $deadline) { try { Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop | Out-Null; Start-Process $appUrl; break } catch { Start-Sleep -Seconds 1 } }"

if exist "%PIXLSTASH_APP_DIR%venv\Scripts\pixlstash-server.exe" (
    "%PIXLSTASH_APP_DIR%venv\Scripts\pixlstash-server.exe" %*
) else (
    "%PIXLSTASH_APP_DIR%venv\Scripts\python.exe" -m pixlstash.app %*
)

echo.
if %ERRORLEVEL% NEQ 0 (
    echo PixlStash exited with an error ^(code %ERRORLEVEL%^).
) else (
    echo PixlStash has stopped.
)
echo Press any key to close this window.
pause >nul
