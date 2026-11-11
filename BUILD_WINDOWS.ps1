param([string]$SignThumbprint = '', [string]$TimestampUrl = 'http://timestamp.digicert.com', [string]$PythonPath = '', [string]$NativeBundle = $env:OWNPLAYER_BUILD_NATIVE, [string]$EmbedZip = $env:OWNPLAYER_BUILD_EMBED, [string]$EmbedSha256 = $env:OWNPLAYER_BUILD_EMBED_SHA256)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$logDirectory = Join-Path $PSScriptRoot 'build-logs'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$transcript = Join-Path $logDirectory ('build-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
Start-Transcript -Path $transcript | Out-Null
function Run([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
function Check-Packaged([string]$Mode) {
    $oldLocal = $env:LOCALAPPDATA
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ('Slynor-check-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temporary | Out-Null
    try {
        $env:LOCALAPPDATA = $temporary
        $process = Start-Process -FilePath (Join-Path $PSScriptRoot 'dist\Slynor\Slynor.exe') -ArgumentList $Mode -WorkingDirectory (Join-Path $PSScriptRoot 'dist\Slynor') -PassThru
        if (-not $process.WaitForExit(90000)) { $process.Kill(); throw "Packaged $Mode timed out after 90 seconds." }
        $process.Refresh()
        if ($process.ExitCode -ne 0) { throw "Packaged $Mode failed ($($process.ExitCode)). See build-logs." }
        $report = if ($Mode -eq '--smoke-test') { 'smoke-check.json' } else { 'runtime-check.json' }
        $reportPath = Join-Path $temporary ('OwnPlayerMusic\logs\' + $report)
        if (-not (Test-Path $reportPath)) { throw "Packaged $Mode produced no report." }
        $data = Get-Content -Raw -Encoding UTF8 $reportPath | ConvertFrom-Json
        if (-not $data.ok -or -not $data.frozen) { throw "Invalid or failed packaged $Mode report." }
        if ($Mode -eq '--smoke-test' -and -not $data.checks.'http:api-and-ui'.ok) { throw 'HTTP smoke test was not completed.' }
    } finally {
        $env:LOCALAPPDATA = $oldLocal
        $logs = Join-Path $temporary 'OwnPlayerMusic\logs'
        if (Test-Path $logs) { Copy-Item (Join-Path $logs '*') $logDirectory -Force }
        if (Test-Path $temporary) { Remove-Item $temporary -Recurse -Force }
    }
}
try {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) { throw 'Build requires Windows 10/11 x64.' }
    if (-not $NativeBundle -or -not $EmbedZip -or -not $EmbedSha256) { throw 'Self-contained build requires -NativeBundle, -EmbedZip and -EmbedSha256. See docs/BUILD_PORTABLE_STAGE23.md.' }
    $NativeBundle = (Resolve-Path -LiteralPath $NativeBundle).Path
    $EmbedZip = (Resolve-Path -LiteralPath $EmbedZip).Path
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User') + ';' + $env:Path
    $isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    $iscc = if ($isccCommand) { $isccCommand.Source } else { "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" }
    if (-not (Test-Path $iscc)) { throw 'Install Inno Setup 6 (https://jrsoftware.org/isinfo.php), then retry.' }
    $python = $PythonPath
    if (-not $python -and (Get-Command py -ErrorAction SilentlyContinue)) {
        $candidate = & py -3.13 -c 'import sys; print(sys.executable)'
        if ($LASTEXITCODE -eq 0) { $python = [string]($candidate | Select-Object -Last 1) }
    }
    if (-not $python) {
        foreach ($candidate in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe", "$env:ProgramFiles\Python313\python.exe")) {
            if (Test-Path $candidate) { $python = $candidate; break }
        }
    }
    if (-not $python -or -not (Test-Path $python)) { throw 'Python 3.13 x64 not found. Run INSTALL.ps1 first or pass -PythonPath.' }
    Run $python @('-c', "import struct,sys; assert struct.calcsize('P')==8 and sys.version_info[:2]==(3,13), 'Python 3.13 x64 required'")
    $buildLock = 'requirements/validated/build-win-py313.txt'
    if (-not (Test-Path -LiteralPath $buildLock)) { throw "Validated build set is missing: $buildLock. Restore it from Git before building." }
    $signtool = $null
    if ($SignThumbprint) {
        if ($SignThumbprint -notmatch '^[a-fA-F0-9]{40}$') { throw 'Certificate thumbprint must be 40 hex characters.' }
        $cert = Get-Item "Cert:\CurrentUser\My\$SignThumbprint"
        if (-not $cert.HasPrivateKey -or $cert.NotAfter -lt (Get-Date)) { throw 'Valid code-signing certificate with private key required.' }
        if (-not ($cert.EnhancedKeyUsageList | Where-Object { $_.ObjectId.Value -eq '1.3.6.1.5.5.7.3.3' })) { throw 'Certificate lacks code signing usage.' }
        $tool = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if ($tool) { $signtool = $tool.Source } else {
            $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
        }
        if (-not $signtool) { throw 'Install Windows SDK Signing Tools or add signtool.exe to PATH.' }
    }
    # Never mix msoc/spotDL dependencies into the frozen GUI build environment.
    Run $python @('-m','venv','--clear','.buildenv')
    $buildPython = Join-Path $PSScriptRoot '.buildenv\Scripts\python.exe'
    Run $buildPython @('-m','pip','install','--upgrade','pip')
    Run $buildPython @('-m','pip','install','--require-hashes','-r',$buildLock)
    Run $buildPython @('-m','pip','check')
    & $buildPython -m pip freeze | Set-Content -Encoding UTF8 (Join-Path $logDirectory 'build-environment.txt')
    Run $buildPython @('tools\build_app_icon.py')
    if (Test-Path 'dist\Slynor') { Remove-Item 'dist\Slynor' -Recurse -Force }
    if (Test-Path 'dist\installer') { Remove-Item 'dist\installer' -Recurse -Force }
    Run $buildPython @('-m','PyInstaller','--noconfirm','--clean','Slynor.spec')
    # Build-time staging only. The installed application never invokes pip/winget.
    Run $buildPython @('tools\prepare_portable.py','--output','dist\Slynor','--native',$NativeBundle,'--embed',$EmbedZip,'--embed-sha256',$EmbedSha256)
    Check-Packaged '--check-runtime'
    Check-Packaged '--smoke-test'
    function Sign([string]$Path) {
        Run $signtool @('sign','/sha1',$SignThumbprint,'/fd','SHA256','/tr',$TimestampUrl,'/td','SHA256',$Path)
        Run $signtool @('verify','/pa','/v',$Path)
        if ((Get-AuthenticodeSignature $Path).Status -ne 'Valid') { throw "Signature verification failed: $Path" }
    }
    if ($SignThumbprint) { Sign 'dist\Slynor\Slynor.exe' }
    Run $iscc @('packaging\Slynor.iss')
    if (-not (Test-Path 'dist\installer\Slynor-Setup.exe')) { throw 'Installer output missing.' }
    if ($SignThumbprint) { Sign 'dist\installer\Slynor-Setup.exe' } else { Write-Warning 'UNSIGNED build: SmartScreen warnings are possible.' }
    Compress-Archive -Path 'dist\Slynor' -DestinationPath 'dist\Slynor-Portable.zip' -Force
    Get-FileHash 'dist\Slynor\Slynor.exe','dist\installer\Slynor-Setup.exe','dist\Slynor-Portable.zip' -Algorithm SHA256 | Format-List | Out-File -Encoding UTF8 (Join-Path $logDirectory 'SHA256.txt')
    Write-Host 'Build and packaged API checks passed. Installer created.' -ForegroundColor Green
    Write-Host 'Visible GUI/audio and clean-PC installation still require Windows acceptance testing.'
} catch {
    Write-Host ('BUILD FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('See: ' + $transcript)
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
