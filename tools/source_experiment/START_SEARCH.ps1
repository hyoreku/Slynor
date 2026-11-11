param(
    [ValidateSet('home_owner','vps_ru','sandbox')][string]$Network = 'home_owner',
    [ValidateSet('smoke','full')][string]$Profile = 'smoke',
    [switch]$Prepare,
    [switch]$PrepareMsoc,
    [switch]$PrepareCatalogue,
    [ValidateSet('yt-dlp.youtube','msoc.hitmo','msoc.muzbomb','archive.http','ytmusicapi.catalogue')]
    [string[]]$Adapters = @('yt-dlp.youtube','msoc.hitmo','msoc.muzbomb','archive.http'),
    [switch]$CheckOnly,
    [string]$ProxyUrl = ''
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $project
function Run([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Command failed: ' + (Split-Path -Leaf $Program)) }
}
$basePython = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $candidate = & py -3.13 -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -eq 0) { $basePython = [string]($candidate | Select-Object -Last 1) }
}
if (-not $basePython) {
    $candidate = Join-Path $project '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $basePython = $candidate }
}
if (-not $basePython) { throw 'Python 3.13 x64 required. No system installations are performed by this script.' }
Run $basePython @('-I','-c',"import struct,sys; assert struct.calcsize('P')==8 and sys.version_info[:2]==(3,13), 'Need Python 3.13 x64'")
$envRoot = Join-Path $project '.local-artifacts\source-experiment\envs'
$current = $project
foreach ($name in @('.local-artifacts','source-experiment','envs')) {
    $current = Join-Path $current $name
    if (Test-Path -LiteralPath $current) {
        if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Research directory is a reparse point; stop without changing it.' }
    }
}
function Prepare-Env([string]$Name, [string]$Requirements, [string]$CheckCode) {
    $folder = Join-Path $envRoot $Name
    $python = Join-Path $folder 'Scripts\python.exe'
    if (Test-Path -LiteralPath $folder) {
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw ('Incomplete research environment: ' + $Name + '. It is preserved; no automatic clearing.') }
        Write-Host ('Using existing research environment: ' + $Name)
    } else {
        New-Item -ItemType Directory -Path $envRoot -Force | Out-Null
        Run $basePython @('-I','-m','venv',$folder)
        Run $python @('-I','-m','pip','--isolated','install','--index-url','https://pypi.org/simple','--only-binary=:all:','-r',(Join-Path $PSScriptRoot $Requirements))
    }
    Run $python @('-I','-m','pip','check')
    Run $python @('-I','-c',$CheckCode)
}
if ($Prepare) {
    Prepare-Env 'controller' 'requirements-controller.txt' "from importlib.metadata import version; assert version('psutil')=='7.2.2'"
    Prepare-Env 'yt' 'requirements-yt.txt' "from importlib.metadata import version; assert version('yt-dlp')=='2026.8.19'; assert version('yt-dlp-ejs')=='0.8.0'"
}
if ($PrepareMsoc) {
    Prepare-Env 'msoc' 'requirements-msoc.txt' "from importlib.metadata import version; assert version('msoc')=='0.3.1'; assert version('httpx')=='0.28.1'"
}
if ($PrepareCatalogue) {
    Prepare-Env 'catalogue' 'requirements-catalogue.txt' "from importlib.metadata import version; expected={'ytmusicapi':'1.12.3','requests':'2.34.2','urllib3':'2.8.0','certifi':'2026.7.22','charset-normalizer':'3.5.2','idna':'3.20','psutil':'7.2.2'}; assert all(version(k)==v for k,v in expected.items()), 'Research lock mismatch; preserve environment for review'"
    if ($Adapters -notcontains 'ytmusicapi.catalogue') { $Adapters += 'ytmusicapi.catalogue' }
}
$controller = Join-Path $envRoot 'controller\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $controller -PathType Leaf)) {
    $catalogueController = Join-Path $envRoot 'catalogue\Scripts\python.exe'
    if (Test-Path -LiteralPath $catalogueController -PathType Leaf) { $controller = $catalogueController }
    else { $controller = $basePython }
}
$arguments = @('-I',(Join-Path $PSScriptRoot 'run.py'),'--network',$Network,'--profile',$Profile)
if ($ProxyUrl) { $arguments += @('--proxy', $ProxyUrl) }
$arguments += '--adapters'
$arguments += $Adapters
if ($CheckOnly) { $arguments += '--preflight' }
Run $controller $arguments
if (-not $CheckOnly) {
    $reportArguments = @('-I',(Join-Path $PSScriptRoot 'report.py'),'--network',$Network)
    if ($ProxyUrl) { $reportArguments += @('--route','proxy') }
    Run $controller $reportArguments
}
