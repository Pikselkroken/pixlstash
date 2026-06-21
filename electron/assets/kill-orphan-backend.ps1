# Kill any orphaned PixlStash bundled-backend python.exe whose executable lives
# under the given install subtree, so the NSIS installer can overwrite the
# native .pyd / .dll files instead of hanging on a locked file (GitHub #486).
#
# This file is embedded verbatim into the NSIS installer (File /oname=...) and
# invoked from build/installer.nsh. Keeping it a standalone .ps1 means its many
# '$' characters are NOT subject to NSIS string escaping, which is the whole
# reason the kill logic does not live inline in the .nsh.
#
# Scope is deliberately narrow: ONLY python.exe processes whose ExecutablePath
# is under -Root. A system / venv / unrelated Python is never touched.

[CmdletBinding()]
param(
    # The bundled-runtime dir, e.g. C:\Users\me\AppData\Local\Programs\PixlStash\resources\python
    [Parameter(Mandatory = $true)]
    [string] $Root
)

$ErrorActionPreference = 'SilentlyContinue'

# Normalise so the prefix match is robust to trailing slashes / casing.
$rootFull = ([System.IO.Path]::GetFullPath($Root)).TrimEnd('\')
$prefix = ($rootFull + '\').ToLowerInvariant()

Write-Output "Looking for a running PixlStash backend under: $rootFull"

$killed = 0
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
    $path = $_.ExecutablePath
    if (-not $path) { return }
    # Normalise the candidate path the same way as $prefix so an 8.3 short name
    # or a junction/symlink route still matches (otherwise the orphan we are
    # hunting could survive and re-introduce the hang). Fails closed: if the path
    # cannot be resolved we leave the process alone.
    try { $full = [System.IO.Path]::GetFullPath($path) } catch { $full = $path }
    if ($full.ToLowerInvariant().StartsWith($prefix)) {
        Write-Output "Stopping orphaned backend PID $($_.ProcessId): $full"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
}

if ($killed -eq 0) {
    Write-Output "No running PixlStash backend found (nothing to stop)."
}
else {
    Write-Output "Stopped $killed orphaned backend process(es)."
    # Give the OS a moment to release the file handles before extraction.
    Start-Sleep -Milliseconds 750
}

exit 0
