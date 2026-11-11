# One entry point, Windows 10/11 x64. No elevation, registry policy changes or user-data deletion.
param([switch]$SkipSystem, [switch]$RuntimeOnly, [string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
# Keep Cyrillic winget output readable in Windows PowerShell 5.1.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
Set-Location $PSScriptRoot
function Run-Native([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed, exit code $LASTEXITCODE" }
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
}
try {
    if ($RuntimeOnly -and -not (Test-Path -LiteralPath '.\Slynor.exe')) { throw 'RuntimeOnly requires Slynor.exe beside this script.' }
    if (-not $SkipSystem) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw 'Install App Installer (winget) from Microsoft Store, or install prerequisites manually and use -SkipSystem.' }
        foreach ($id in @('Python.Python.3.13','VideoLAN.VLC','Microsoft.EdgeWebView2Runtime','Gyan.FFmpeg')) {
            Write-Host "Checking/installing $id"
            & winget list --id $id --exact --source winget --accept-source-agreements --disable-interactivity | Out-Host
            if ($LASTEXITCODE -ne 0) {
                Run-Native 'winget' @('install','--source','winget','--id',$id,'--exact','--accept-source-agreements','--accept-package-agreements','--disable-interactivity')
            }
        }
        Refresh-Path
    }
    # Do not trust the Microsoft Store python alias or silently use a different architecture.
    $python = $null
    if ($PythonPath) {
        if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Explicit PythonPath was not found.' }
        $python = (Resolve-Path -LiteralPath $PythonPath).Path
    }
    if (-not $python -and (Get-Command py -ErrorAction SilentlyContinue)) {
        $candidate = & py -3.13 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0) { $python = [string]($candidate | Select-Object -Last 1) }
    }
    if (-not $python) {
        foreach ($candidate in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe", "$env:ProgramFiles\Python313\python.exe")) {
            if (Test-Path -LiteralPath $candidate) { $python = $candidate; break }
        }
    }
    if (-not $python) { throw 'Python 3.13 not found. Restart PowerShell after installation, then run INSTALL.ps1 again.' }
    Run-Native $python @('-c', "import struct,sys; assert struct.calcsize('P')==8 and sys.version_info[:2]==(3,13), 'Need Python 3.13 x64'")
    $ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
    if (-not $ffmpeg -or -not $ffprobe) { throw 'ffmpeg.exe and ffprobe.exe must both be in PATH. Restart PowerShell or see docs/STAGE15.md.' }
    Run-Native $ffmpeg.Source @('-version')
    Run-Native $ffprobe.Source @('-version')
    $vlc = @("$env:ProgramFiles\VideoLAN\VLC\libvlc.dll", "${env:ProgramFiles(x86)}\VideoLAN\VLC\libvlc.dll") | Where-Object { Test-Path -LiteralPath $_ }
    if (-not $vlc) { throw 'VLC not found in its standard installation folder. Install VLC x64.' }
    # Dependencies are installed only from the validated hash-locked sets.
    $validated = 'requirements/validated'
    foreach ($group in @('gui','provider','search')) {
        $lock = Join-Path $validated ($group + '-win-py313.txt')
        if (-not (Test-Path -LiteralPath $lock)) { throw "Validated dependency set is missing: $lock. Restore it from Git before installing." }
    }
    if (-not $RuntimeOnly) {
    Run-Native $python @('-m','venv','.venv')
    Run-Native '.\.venv\Scripts\python.exe' @('-m','pip','install','--upgrade','pip')
    Run-Native '.\.venv\Scripts\python.exe' @('-m','pip','install','--require-hashes','-r',(Join-Path $validated 'gui-win-py313.txt'))
    Run-Native '.\.venv\Scripts\python.exe' @('-m','pip','check')
    }
    # Provider packages have their own dependency versions: keep them out of the GUI environment.
    Run-Native $python @('-m','venv','.providers')
    Run-Native '.\.providers\Scripts\python.exe' @('-m','pip','install','--upgrade','pip')
    Run-Native '.\.providers\Scripts\python.exe' @('-m','pip','install','--require-hashes','-r',(Join-Path $validated 'provider-win-py313.txt'))
    Run-Native '.\.providers\Scripts\python.exe' @('-m','pip','check')
    # msoc/textual needs rich>=14, while spotDL needs rich<14: do not merge.
    Run-Native $python @('-m','venv','.search')
    Run-Native '.\.search\Scripts\python.exe' @('-m','pip','install','--upgrade','pip')
    Run-Native '.\.search\Scripts\python.exe' @('-m','pip','install','--require-hashes','-r',(Join-Path $validated 'search-win-py313.txt'))
    Run-Native '.\.search\Scripts\python.exe' @('-m','pip','check')
    Run-Native '.\.search\Scripts\python.exe' @('-c', "import msoc,httpx; print('Search imports OK')")
    @{ffmpeg=$ffmpeg.Source} | ConvertTo-Json | Set-Content -Encoding UTF8 runtime_paths.json
    if ($RuntimeOnly) {
        $check = Start-Process -FilePath (Join-Path $PSScriptRoot 'Slynor.exe') -ArgumentList '--check-runtime' -PassThru
        if (-not $check.WaitForExit(60000)) { $check.Kill(); throw 'Packaged runtime check timed out after 60 seconds.' }
        $check.Refresh()
        if ($check.ExitCode -ne 0) { throw 'Packaged runtime check failed. See %LOCALAPPDATA%\OwnPlayerMusic\logs\runtime-check.json.' }
    } else {
        Run-Native '.\.venv\Scripts\python.exe' @('-c', "import webview,fastapi,httpx,mutagen,av,numpy,PIL,pystray,vlc; i=vlc.Instance('--no-video'); assert i is not None; i.release(); print('GUI dependencies and LibVLC OK')")
    }
    Run-Native '.\.providers\Scripts\python.exe' @('-c', "import spotdl,scdl,yt_dlp; print('Provider imports OK')")
    Write-Host 'Dependencies checked. Start the app with START.cmd. Online availability still depends on providers/network.' -ForegroundColor Green
} catch {
    Write-Host "Installation stopped: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Fix the error above and run INSTALL.ps1 again. Your library/settings were not removed.'
    exit 1
}
