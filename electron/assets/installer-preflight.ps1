# Pre-install checks run by the NSIS installer before an over-the-top update.
#
# 1. Kill any orphaned PixlStash bundled-backend python.exe whose executable
#    lives under -Root, so the installer can overwrite the native .pyd / .dll
#    files instead of hanging on a locked file (GitHub #486).
# 2. Detect files in the previous install whose paths would overflow the
#    Windows 260-character MAX_PATH limit when the OLD version's uninstaller
#    renames them into %TEMP%\ns?????.tmp\old-install\ during its "atomic"
#    removal step. That rename failure makes the old uninstaller abort with
#    exit code 2 and hard-fails the whole update (seen on every update away
#    from 1.7.0-rc.5, whose bundled torch ships ~190-char-deep license paths).
#    When such paths exist this script exits 3 and installer.nsh skips the old
#    uninstaller, updating over the existing files instead.
#
# This file is embedded verbatim into the NSIS installer (File /oname=...) and
# invoked from assets/installer.nsh. Keeping it a standalone .ps1 means its many
# '$' characters are NOT subject to NSIS string escaping, which is the whole
# reason this logic does not live inline in the .nsh.
#
# Exit codes: 0 = nothing special; 3 = over-long paths found (old uninstaller
# must be skipped). Any other value means PowerShell itself failed to run this
# script; installer.nsh then falls back to a narrow taskkill.
#
# Kill scope is deliberately narrow: ONLY python.exe processes whose
# ExecutablePath is under -Root. A system / venv / unrelated Python is never
# touched.

[CmdletBinding()]
param(
    # The bundled-runtime dir, e.g. C:\Users\me\AppData\Local\Programs\PixlStash\resources\python
    [Parameter(Mandatory = $true)]
    [string] $Root,

    # The whole install dir (parent of resources\). Enables the long-path
    # preflight; when omitted only the orphan kill runs.
    [string] $InstallRoot = ''
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

$exitCode = 0
if ($InstallRoot) {
    $instFull = ([System.IO.Path]::GetFullPath($InstallRoot)).TrimEnd('\')
    # Worst-case prefix the old uninstaller renames every file under:
    # %TEMP%\ + ns?????.tmp\ (NSIS plugin dir, "ns" + 5 chars + ".tmp") +
    # old-install. The file keeps its path relative to the install dir, so the
    # rename target length is that prefix plus (full length - install-dir
    # length). Classic MAX_PATH allows 259 usable characters; keep a 2-char
    # margin. cmd dir /s /b is used instead of Get-ChildItem -Recurse because
    # Windows PowerShell 5.1 can choke enumerating near-limit paths.
    $tempPrefixLen = ($env:TEMP.TrimEnd('\')).Length + 12 + 12
    $limit = 257
    $deep = @(cmd /c dir "$instFull" /s /b 2>$null | Where-Object {
            ($tempPrefixLen + ($_.Length - $instFull.Length)) -gt $limit
        })
    if ($deep.Count -gt 0) {
        Write-Output "$($deep.Count) file(s) in the previous install are nested too deeply for the Windows path-length limit, e.g.:"
        Write-Output "  $($deep[0])"
        Write-Output "The previous version's uninstaller would fail on these, so it will be skipped and the update will overwrite files in place."
        $exitCode = 3
    }
}

exit $exitCode
