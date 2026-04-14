@echo off
set PIXLSTASH_APP_DIR=%~dp0
set PIXLSTASH_PORT=9537

echo PixlStash is starting...
echo Open in your browser: http://localhost:%PIXLSTASH_PORT%/
echo.

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
