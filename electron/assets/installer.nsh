# =============================================================================
# PixlStash desktop installer customisations (electron-builder NSIS include).
#
# Wired in via electron/package.json -> build.nsis.include = "build/installer.nsh".
# electron-builder !include's this file at the top of the generated installer
# header (NsisTarget.computeCommonInstallerScriptHeader), so the macros defined
# here are picked up by the template's !ifmacrodef / !insertmacro guards:
#
#   customHeader      -> installer.nsi (file scope, before .onInit). Defines our
#                        helper functions + callbacks and flips the details view on.
#   customInit        -> installer.nsi .onInit, AFTER initMultiUser ($INSTDIR known),
#                        BEFORE the install Section. Just seeds $PixlKillResult.
#   customCheckAppRunning -> allowOnlyOneInstallerInstance.nsh, inside the install
#                        Section, AFTER the user is prompted to close a running app
#                        and BEFORE installApplicationFiles. Closes the app, THEN
#                        sweeps the orphaned backend. Orphan-kill lives here now.
#   customInstall     -> installSection.nsh, AFTER installApplicationFiles
#                        (i.e. after the .7z is extracted into $INSTDIR). Markers + log.
#   customFinishPage  -> assistedInstaller.nsh, replaces the default finish page.
#
# Why this file exists (GitHub issue #486): on an over-the-top UPDATE the old
# version's bundled backend python.exe (running from
# <INSTDIR>\resources\python\python.exe, spawned non-detached on Windows with no
# Job Object) can survive after the app window closes. It holds the native
# .pyd / .dll files open, so when Nsis7z tries to overwrite them the install
# hangs on the "Installing" page with a frozen progress bar and no log.
# electron-builder's built-in app-close (PowerShell branch) does kill processes
# under $INSTDIR, but its taskkill fallback only kills PixlStash.exe by name and
# leaves python.exe orphaned, keeping the lock.
#
# ORDERING BUG (the "PixlStash died unexpectedly" report): the orphan-kill used
# to run in customInit (.onInit), which fires SECONDS BEFORE electron-builder's
# CHECK_APP_RUNNING prompt (that lives in the install Section, after .onInit). On
# an over-the-top update the OLD PixlStash.exe is still running, so killing its
# backend python.exe out from under it made the live app pop its own
# "PixlStash backend stopped / exited unexpectedly" modal (main.ts onExit, which
# fires whenever the backend dies while the app is not quitting) BEFORE the user
# was ever asked to close the app. Fix: do nothing to the backend until the
# standard prompt has CLOSED THE APP, then sweep the orphan. We therefore move
# the kill out of customInit and into customCheckAppRunning, which runs after the
# app is confirmed gone -- so the app's crash dialog can no longer appear.
#
# Fixes here:
#   1. Make the install details view visible (ShowInstDetails show) so progress
#      and any "Can't modify ...'s files" retry lines are visible and copyable.
#   2. Override customCheckAppRunning: reproduce electron-builder's stock prompt +
#      app-close loop verbatim, then (installer build only) sweep the orphaned
#      bundled backend, scoped strictly to python.exe under
#      $INSTDIR\resources\python so we never touch an unrelated Python. Running
#      AFTER the app is gone keeps the file-lock fix while killing the dialog.
#   3. Dump the details log to files (install dir + Desktop + %TEMP%) at the end
#      of install and on user abort, so a failed/cancelled install is reportable.
#   4. A finish-page link (and an abort message) that opens a pre-filled GitHub
#      new-issue URL with the app + Windows version baked in.
#
# NOTE: this file has NOT been built or run on Windows (authored on Linux). See
# the PR notes for the exact Windows build + over-the-top-update smoke test.
# =============================================================================

# ---------------------------------------------------------------------------
# customHeader: file-scope. Runs before .onInit. Flips the details view on and
# defines every helper function + the .onInstFailed callback we use. Functions
# are emitted here (single insertion point) so forward references from customInit
# / customInstall / .onInstFailed all resolve at compile time.
# ---------------------------------------------------------------------------
!macro customHeader
  # Show the details ListView during install. The user can watch live progress
  # and right-click -> "Copy Details To Clipboard" when something is stuck. This
  # is the primary copy source for a TRULY hung install (the file dump below
  # cannot run if we never reach the end of the section). Overrides common.nsh's
  # `ShowInstDetails nevershow` because this include is pulled in after it.
  ShowInstDetails show

  # Globals. PixlInstallLogPath is set inside PixlWriteLogs (defined below), so it
  # exists in both the installer and the uninstaller build. PixlKillResult is set
  # in customInit / customCheckAppRunning and read in customInstall; the latter two
  # ARE reachable in the uninstaller build (customCheckAppRunning is inserted there
  # too), but its writes there sit behind `!ifndef BUILD_UNINSTALLER`, so in the
  # uninstaller PixlKillResult would be unset and unreferenced. electron-builder
  # compiles NSIS with warnings-as-errors (warning 6001 "not referenced or never
  # set"), so guard the declaration to the installer build to match.
  Var /GLOBAL PixlInstallLogPath
  !ifndef BUILD_UNINSTALLER
    Var /GLOBAL PixlKillResult
  !endif

  # Defining customCheckAppRunning (below) makes the `!ifmacrondef
  # customCheckAppRunning` block in electron-builder's allowOnlyOneInstallerInstance.nsh
  # NOT run -- that block is where it would otherwise `!include getProcessInfo.nsh`
  # and declare `Var pid`. Our override reuses ${GetProcessInfo} and the stock
  # KILL_PROCESS macro (which filters on `PID ne $pid`), so we must provide both
  # ourselves or the build fails on the undefined macro / variable.
  !include "getProcessInfo.nsh"
  Var pid

  # LVM constants for DumpLog (read the details ListView item text). Guarded so
  # we never clash with a constant MUI / the template already defined.
  !ifndef LVM_GETITEMCOUNT
    !define LVM_GETITEMCOUNT 0x1004
  !endif
  !ifndef LVM_GETITEMTEXTA
    !define LVM_GETITEMTEXTA 0x102D
  !endif
  !ifndef LVM_GETITEMTEXTW
    !define LVM_GETITEMTEXTW 0x1073
  !endif
  !ifdef NSIS_UNICODE
    !define PIXL_LVM_GETITEMTEXT ${LVM_GETITEMTEXTW}
  !else
    !define PIXL_LVM_GETITEMTEXT ${LVM_GETITEMTEXTA}
  !endif

  # -------------------------------------------------------------------------
  # PixlGetWindowsVersion: push a human-readable Windows version string. Uses
  # WinVer.nsh (already included by common.nsh). For the bug-report body only.
  # -------------------------------------------------------------------------
  Function PixlGetWindowsVersion
    StrCpy $0 "Windows (unknown build)"
    # The electron-builder-bundled NSIS (3.0.4.x) ships a WinVer.nsh that predates
    # Windows 11, so ${IsWin11} is undefined. Referencing it makes the LogicLib
    # ${If} expand with the wrong token count ("macro _If requires 4 parameter(s),
    # passed 2") and aborts the whole build. Windows 11 reports as Windows 10 with
    # build >= 22000, so we detect it from the build number below instead.
    ${If} ${IsWin10}
      StrCpy $0 "Windows 10"
    ${ElseIf} ${IsWin8.1}
      StrCpy $0 "Windows 8.1"
    ${ElseIf} ${IsWin8}
      StrCpy $0 "Windows 8"
    ${ElseIf} ${IsWin7}
      StrCpy $0 "Windows 7"
    ${EndIf}
    ${If} ${AtLeastWin7}
      ReadRegStr $1 HKLM "SOFTWARE\Microsoft\Windows NT\CurrentVersion" "CurrentBuildNumber"
      ${If} $1 != ""
        ${If} $0 == "Windows 10"
        ${AndIf} $1 >= 22000
          StrCpy $0 "Windows 11"
        ${EndIf}
        StrCpy $0 "$0 (build $1)"
      ${EndIf}
    ${EndIf}
    Push $0
  FunctionEnd

  # -------------------------------------------------------------------------
  # PixlDumpLog: write every line in the details ListView to the file path in
  # $0. Standard NSIS "dump log to file" idiom (SysListView32 LVM_GETITEMTEXT).
  # Lets a failed / aborted install leave an attachable text log behind.
  # -------------------------------------------------------------------------
  Function PixlDumpLog
    Exch $5            # $5 = target file path (in)
    Push $0
    Push $1
    Push $2
    Push $3
    Push $4
    Push $6

    FindWindow $0 "#32770" "" $HWNDPARENT
    GetDlgItem $0 $0 1016        # the details ListView control id
    StrCmp $0 0 PixlDumpExit

    FileOpen $5 $5 "w"
    StrCmp $5 "" PixlDumpExit

    SendMessage $0 ${LVM_GETITEMCOUNT} 0 0 $6
    System::Alloc ${NSIS_MAX_STRLEN}
    Pop $3
    StrCpy $2 0
    System::Call "*(i, i, i, i, i, i, i, i, i) i \
      (0, 0, 0, 0, 0, r3, ${NSIS_MAX_STRLEN}) .r1"

    PixlDumpLoop:
      StrCmp $2 $6 PixlDumpDone
      System::Call "User32::SendMessage(i, i, i, i) i \
        ($0, ${PIXL_LVM_GETITEMTEXT}, $2, r1)"
      System::Call "*$3(&t${NSIS_MAX_STRLEN} .r4)"
      FileWrite $5 "$4$\r$\n"
      IntOp $2 $2 + 1
      Goto PixlDumpLoop

    PixlDumpDone:
      FileClose $5
      System::Free $1
      System::Free $3

    PixlDumpExit:
      Pop $6
      Pop $4
      Pop $3
      Pop $2
      Pop $1
      Pop $0
      Pop $5
  FunctionEnd

  # -------------------------------------------------------------------------
  # PixlWriteLogs: dump the details view to $INSTDIR (best effort) plus copies
  # to the Desktop and %TEMP% so the log is always findable even if $INSTDIR is
  # half-written. Sets $PixlInstallLogPath to the primary path.
  # -------------------------------------------------------------------------
  Function PixlWriteLogs
    SetDetailsPrint both
    StrCpy $PixlInstallLogPath "$INSTDIR\install-log.txt"
    Push "$PixlInstallLogPath"
    Call PixlDumpLog
    Push "$DESKTOP\PixlStash-install-log.txt"
    Call PixlDumpLog
    Push "$TEMP\PixlStash-install-log.txt"
    Call PixlDumpLog
    DetailPrint "Install log written to: $PixlInstallLogPath"
    DetailPrint "Copies on Desktop and in %TEMP% (PixlStash-install-log.txt)."
  FunctionEnd

  # -------------------------------------------------------------------------
  # PixlOpenIssue: open the pre-filled GitHub new-issue page. A URL cannot
  # attach a file, so the body tells the user where the log is and to paste the
  # visible details. App version + Windows build are baked into title and body.
  # NSIS '$' inside the URL string are NSIS vars ($INSTDIR); the '%' / encoded
  # bytes are literal. No PowerShell here, so no '$' escaping headaches.
  # -------------------------------------------------------------------------
  Function PixlOpenIssue
    Call PixlGetWindowsVersion
    Pop $1   # Windows version string
    StrCpy $2 "https://github.com/Pikselkroken/pixlstash/issues/new"
    StrCpy $2 "$2?labels=installer"
    StrCpy $2 "$2&title=Windows%20installer%20issue%20(v${VERSION})"
    StrCpy $2 "$2&body=PixlStash%20desktop%20version%3A%20${VERSION}%0A"
    StrCpy $2 "$2Windows%3A%20$1%0A"
    StrCpy $2 "$2Install%20directory%3A%20$INSTDIR%0A%0A"
    StrCpy $2 "$2What%20happened%3A%0A(describe%20the%20problem)%0A%0A"
    StrCpy $2 "$2Please%20paste%20the%20installer%20details%20view%20here%20"
    StrCpy $2 "$2(right-click%20the%20Installing%20log%2C%20Copy%20Details%20"
    StrCpy $2 "$2To%20Clipboard)%20and%20ATTACH%20the%20log%20file%3A%0A"
    StrCpy $2 "$2%20%20$INSTDIR%5Cinstall-log.txt%0A"
    StrCpy $2 "$2%20%20(also%20on%20your%20Desktop%20and%20in%20%25TEMP%25%20"
    StrCpy $2 "$2as%20PixlStash-install-log.txt)%0A"
    ExecShell "open" "$2"
  FunctionEnd

  # -------------------------------------------------------------------------
  # .onInstFailed: NSIS fires this when the install is aborted (Cancel / Quit
  # out of the section). Dump whatever is in the details view so an aborted
  # install still leaves an attachable log, then offer the report link.
  # -------------------------------------------------------------------------
  Function .onInstFailed
    Call PixlWriteLogs
    MessageBox MB_YESNO|MB_ICONQUESTION \
      "PixlStash setup did not finish.$\r$\n$\r$\nA log was saved to:$\r$\n$PixlInstallLogPath$\r$\n(and to your Desktop / %TEMP% as PixlStash-install-log.txt).$\r$\n$\r$\nReport this so we can fix it?" \
      IDNO PixlNoReport
      Call PixlOpenIssue
    PixlNoReport:
  FunctionEnd
!macroend

# ---------------------------------------------------------------------------
# customInit: in .onInit, AFTER initMultiUser (so $INSTDIR is the real, possibly
# previously-installed location) and BEFORE the install Section runs. Only seeds
# the default $PixlKillResult so customInstall always has something to print; the
# actual orphan-kill moved to customCheckAppRunning (see the header for why).
# ---------------------------------------------------------------------------
!macro customInit
  StrCpy $PixlKillResult "no previous backend checked (fresh install)"
!macroend

# ---------------------------------------------------------------------------
# customCheckAppRunning: REPLACES electron-builder's stock _CHECK_APP_RUNNING
# (allowOnlyOneInstallerInstance.nsh), which CHECK_APP_RUNNING inserts inside the
# install Section / uninstaller, i.e. AFTER the user has been prompted to close a
# running PixlStash and BEFORE installApplicationFiles extracts over $INSTDIR.
#
# Everything up to `pixlNotRunning:` is electron-builder's stock body reproduced
# verbatim (prompt -> graceful close -> force-kill -> elevated-app retry), so the
# user-facing close behaviour is unchanged. Only the scoped python orphan-kill
# AFTER it is ours. Because the app is confirmed gone before we touch python, the
# old "backend exited unexpectedly" dialog can no longer fire. The sweep is
# guarded to the installer build; the uninstaller keeps the stock behaviour (it
# also inserts this macro, but had no python-kill before, and PixlKillResult /
# BUILD_RESOURCES_DIR only exist in the installer build).
# ---------------------------------------------------------------------------
!macro customCheckAppRunning
  # electron-builder's stock CHECK_APP_RUNNING runs !insertmacro
  # IS_POWERSHELL_AVAILABLE *before* the body it would otherwise insert, but only
  # in its `!else` (no-customCheckAppRunning) branch. Since we define this macro,
  # that branch is skipped -- so the FIND_PROCESS / KILL_PROCESS macros below, which
  # (as of electron-builder 26) gate on `$IsPowerShellAvailable`, would reference a
  # variable that was never declared (NSIS warning 6000, fatal under
  # warnings-as-errors). Insert it ourselves to declare AND populate the var,
  # exactly as the stock path does. (CmdPath / PowerShellPath are still set by the
  # surrounding CHECK_APP_RUNNING macro, so only this insertion is missing.)
  # Guarded with !ifmacrodef so this stays a no-op on older electron-builder
  # releases that predate IS_POWERSHELL_AVAILABLE (there FIND_PROCESS / KILL_PROCESS
  # do not reference $IsPowerShellAvailable, so nothing is needed).
  !ifmacrodef IS_POWERSHELL_AVAILABLE
    !insertmacro IS_POWERSHELL_AVAILABLE
  !endif

  ${GetProcessInfo} 0 $pid $1 $2 $3 $4
  ${if} $3 != "${APP_EXECUTABLE_FILENAME}"
    ${if} ${isUpdated}
      # allow app to exit without explicit kill
      Sleep 300
    ${endIf}

    !insertmacro FIND_PROCESS "${APP_EXECUTABLE_FILENAME}" $R0
    ${if} $R0 == 0
      ${if} ${isUpdated}
        # allow app to exit without explicit kill
        Sleep 1000
        Goto pixlDoStopProcess
      ${endIf}
      MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "$(appRunning)" /SD IDOK IDOK pixlDoStopProcess
      Quit

      pixlDoStopProcess:

      DetailPrint "$(appClosing)"

      !insertmacro KILL_PROCESS "${APP_EXECUTABLE_FILENAME}" 0
      # to ensure that files are not "in-use"
      Sleep 300

      # Retry counter
      StrCpy $R1 0

      pixlLoop:
        IntOp $R1 $R1 + 1

        !insertmacro FIND_PROCESS "${APP_EXECUTABLE_FILENAME}" $R0
        ${if} $R0 == 0
          # wait to give a chance to exit gracefully
          Sleep 1000
          !insertmacro KILL_PROCESS "${APP_EXECUTABLE_FILENAME}" 1 # 1 = force kill
          !insertmacro FIND_PROCESS "${APP_EXECUTABLE_FILENAME}" $R0
          ${if} $R0 == 0
            DetailPrint `Waiting for "${PRODUCT_NAME}" to close.`
            Sleep 2000
          ${else}
            Goto pixlNotRunning
          ${endIf}
        ${else}
          Goto pixlNotRunning
        ${endIf}

        # App likely running with elevated permissions.
        # Ask user to close it manually
        ${if} $R1 > 1
          MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(appCannotBeClosed)" /SD IDCANCEL IDRETRY pixlLoop
          Quit
        ${else}
          Goto pixlLoop
        ${endIf}
      pixlNotRunning:
    ${endIf}
  ${endIf}

  # --- PixlStash addition: sweep the orphaned bundled backend (GitHub #486). ---
  # The app (if any) is confirmed gone above, so killing python now can no longer
  # trigger its "backend exited unexpectedly" modal (main.ts onExit). This breaks
  # the file lock that hangs an over-the-top update on machines where the stock
  # check leaves python.exe orphaned (its taskkill fallback only kills the app exe
  # by name). Installer build only -- the uninstaller keeps the stock behaviour.
  !ifndef BUILD_UNINSTALLER
    # Only meaningful on an update: when the target dir already holds a bundled
    # runtime. On a fresh install this path does not exist and the kill is skipped.
    ${If} ${FileExists} "$INSTDIR\resources\python\python.exe"
      # Guard against a single quote in the (user-chooseable) install path. A "'"
      # would terminate the single-quoted nsExec literal below early and splice the
      # rest of the path into raw command tokens. There is no -iex / Invoke-
      # Expression sink (the .ps1 only uses -Root for a string prefix compare), and
      # the installer runs as the same user who chose the path, so this is robustness
      # rather than a privilege boundary. We skip the auto-kill and tell the user to
      # close PixlStash manually instead of emitting a malformed command.
      ${StrContains} $9 "'" "$INSTDIR"
      ${If} $9 != ""
        StrCpy $PixlKillResult "skipped auto-kill: install path contains a quote; close PixlStash manually before updating"
      ${Else}
        # Embed and run our scoped kill helper. The .ps1 is embedded verbatim (File)
        # so its many PowerShell '$' are NOT subject to NSIS escaping. Extract into
        # $PLUGINSDIR (a freshly-created, randomised, auto-wiped per-install temp dir,
        # not a predictable $TEMP name, which closes the swap-the-script window),
        # and pass the bundled runtime dir as -Root so it only stops python.exe under
        # it. InitPluginsDir is idempotent.
        InitPluginsDir
        File "/oname=$PLUGINSDIR\pixlstash-kill-orphan.ps1" "${BUILD_RESOURCES_DIR}\kill-orphan-backend.ps1"
        nsExec::ExecToLog 'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\pixlstash-kill-orphan.ps1" -Root "$INSTDIR\resources\python"'
        Pop $0   # exit code (script always exits 0; non-zero => powershell missing)
        StrCpy $PixlKillResult "orphan-kill ran for $INSTDIR\resources\python (exit $0)"

        # Fallback only when PowerShell is unavailable / constrained. Narrow:
        # python.exe whose window title starts with PixlStash. Title-only, so it can
        # in principle hit an unrelated PixlStash-titled python the same user runs;
        # accepted as a best-effort last resort (CSO sign-off). Non-fatal.
        ${If} $0 != "0"
          nsExec::ExecToLog 'taskkill /F /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq PixlStash*"'
          Pop $1
          StrCpy $PixlKillResult "$PixlKillResult; powershell unavailable, used taskkill title fallback"
        ${EndIf}

        Delete "$PLUGINSDIR\pixlstash-kill-orphan.ps1"
      ${EndIf}
    ${EndIf}
  !endif
!macroend

# ---------------------------------------------------------------------------
# customInstall: in installSection.nsh, AFTER installApplicationFiles (the .7z
# has been extracted into $INSTDIR). The details view is live here, so this is
# where we print the human-readable markers and write the persistent log. If the
# install HUNG during extraction we never reach this point, which is exactly why
# the visible ShowInstDetails view (set in customHeader) is the primary copy
# source for a hang, and this file dump covers the completed / aborted cases.
# ---------------------------------------------------------------------------
!macro customInstall
  SetDetailsPrint both
  Call PixlGetWindowsVersion
  Pop $3
  DetailPrint "----------------------------------------------------------------"
  DetailPrint "PixlStash desktop ${VERSION}"
  DetailPrint "Install directory: $INSTDIR"
  DetailPrint "OS: $3"
  DetailPrint "Backend check: $PixlKillResult"
  DetailPrint "Bundled backend: $INSTDIR\resources\python\python.exe"
  DetailPrint "Application files extracted."
  DetailPrint "----------------------------------------------------------------"

  # Persist the log for completed installs (the abort path is covered by
  # .onInstFailed, defined in customHeader).
  Call PixlWriteLogs
!macroend

# ---------------------------------------------------------------------------
# customFinishPage: replace the default assisted finish page so we can add a
# "report an issue" link alongside the normal "run app" checkbox. The link opens
# the pre-filled GitHub new-issue URL. (Defining customFinishPage skips the
# template's default finish page, so we reproduce the run-after-finish option.)
# ---------------------------------------------------------------------------
!macro customFinishPage
  !ifndef HIDE_RUN_AFTER_FINISH
    Function StartApp
      ${if} ${isUpdated}
        StrCpy $1 "--updated"
      ${else}
        StrCpy $1 ""
      ${endif}
      ${StdUtils.ExecShellAsUser} $0 "$launchLink" "open" "$1"
    FunctionEnd

    !define MUI_FINISHPAGE_RUN
    !define MUI_FINISHPAGE_RUN_FUNCTION "StartApp"
  !endif

  # Clickable link on the finish page; opens the pre-filled GitHub issue.
  !define MUI_FINISHPAGE_LINK "If the installation didn't complete, report it here"
  !define MUI_FINISHPAGE_LINK_LOCATION "https://github.com/Pikselkroken/pixlstash/issues/new?labels=installer"
  !insertmacro MUI_PAGE_FINISH
!macroend
