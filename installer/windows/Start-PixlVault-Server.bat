@echo off
set PIXLVAULT_APP_DIR=%~dp0
set PIXLVAULT_PORT=9537

start "" "%PIXLVAULT_APP_DIR%venv\Scripts\pixlvault-server.exe" %*

powershell -NoProfile -Command ^
  "$url = 'http://localhost:%PIXLVAULT_PORT%/version'; $deadline = [DateTime]::UtcNow.AddSeconds(60); while ([DateTime]::UtcNow -lt $deadline) { try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop | Out-Null; break } catch { Start-Sleep -Seconds 1 } }; Start-Process $url"
