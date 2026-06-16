# Detect (and optionally stop) any PixlStash processes that would lock files
# during an install/upgrade: the server (pixlstash-server.exe / venv python),
# the pip desktop launcher (pixlstash-desktop.exe), and the console window that
# runs Start-PixlStash-Server.bat (cmd.exe holds the .bat open while paused).
#
# Matched by running from the install dir OR by command line so we never touch
# unrelated python/cmd processes elsewhere on the machine.
#
# Exit code 1 = a PixlStash process is (still) running; 0 = none.
param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [switch]$Kill
)

$ErrorActionPreference = 'SilentlyContinue'

function Get-PixlProcs {
    Get-CimInstance Win32_Process | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath -like "$AppDir\*") -or
        ($_.CommandLine    -and $_.CommandLine    -like "*$AppDir\Start-PixlStash-Server.bat*")
    }
}

$procs = Get-PixlProcs
if ($Kill -and $procs) {
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Milliseconds 700
    $procs = Get-PixlProcs
}

if ($procs) { exit 1 } else { exit 0 }
