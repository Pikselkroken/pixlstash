@echo off
set PIXLVAULT_APP_DIR=%~dp0
set PIXLVAULT_PORT=9537

start "" "%PIXLVAULT_APP_DIR%venv\Scripts\pixlvault-server.exe" %*

timeout /t 4 /nobreak >nul
start "" "http://localhost:%PIXLVAULT_PORT%"
