param([Parameter(Mandatory)][string]$Destination, [switch]$UseSystemWebView2)

$ErrorActionPreference = 'Stop'
$Destination = New-Item -ItemType Directory -Force -Path $Destination | Select-Object -ExpandProperty FullName
$native = Join-Path $Destination 'native'
$licenses = Join-Path $native 'licenses'
New-Item -ItemType Directory -Force -Path $licenses | Out-Null

$sources = [ordered]@{
    python = @{
        Name = 'python-3.13.15-embed-amd64.zip'
        Url = 'https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip'
        Sha256 = 'd1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf'
    }
    vlc = @{
        Name = 'vlc-3.0.24-win64.zip'
        Url = 'https://download.videolan.org/pub/videolan/vlc/3.0.24/win64/vlc-3.0.24-win64.zip'
        Sha256 = 'fcf30850371ad10c9373cc4f0f4501e7dee49e3e9ae9f20c72fb2661a1ca6323'
    }
    ffmpeg = @{
        Name = 'ffmpeg-8.0.1-essentials_build.zip'
        Url = 'https://github.com/GyanD/codexffmpeg/releases/download/8.0.1/ffmpeg-8.0.1-essentials_build.zip'
        Sha256 = 'e2aaeaa0fdbc397d4794828086424d4aaa2102cef1fb6874f6ffd29c0b88b673'
    }
    deno = @{
        Name = 'deno-x86_64-pc-windows-msvc.zip'
        Url = 'https://github.com/denoland/deno/releases/download/v2.9.7/deno-x86_64-pc-windows-msvc.zip'
        Sha256 = 'a0c3101b4158d1dfb7d6a78a7bf0f3de80c96bb423c152beec8beb22786f2238'
    }
    webview2 = @{
        Name = 'Microsoft.WebView2.FixedVersionRuntime.157.0.4341.60.x64.cab'
        Url = 'https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/9d6605ff-c172-41a1-8116-e1bb02b93a01/Microsoft.WebView2.FixedVersionRuntime.157.0.4341.60.x64.cab'
        Sha256 = '1bad3df802a4c21f6b271cf3cd640912056291e3915b86fa7577a18899f761b9'
    }
}
if ($UseSystemWebView2) { $sources.Remove('webview2') }

function Download([string]$Url, [string]$Path) {
    & curl.exe --fail --silent --show-error --location --retry 3 --output $Path $Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $Url" }
}

foreach ($component in $sources.Keys) {
    $source = $sources[$component]
    $file = Join-Path $Destination $source.Name
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        Write-Host "Downloading $($source.Name)"
        Download $source.Url "$file.partial"
        Move-Item -LiteralPath "$file.partial" -Destination $file
    }
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $source.Sha256) {
        throw "SHA-256 mismatch: $($source.Name)"
    }
}

Expand-Archive -LiteralPath (Join-Path $Destination $sources.vlc.Name) -DestinationPath $Destination -Force
Expand-Archive -LiteralPath (Join-Path $Destination $sources.ffmpeg.Name) -DestinationPath $Destination -Force
Expand-Archive -LiteralPath (Join-Path $Destination $sources.deno.Name) -DestinationPath (Join-Path $native 'deno') -Force

$vlcPath = Join-Path $native 'vlc'
$ffmpegPath = Join-Path $native 'ffmpeg'
New-Item -ItemType Directory -Force -Path $vlcPath, $ffmpegPath | Out-Null
Get-ChildItem -LiteralPath (Join-Path $Destination 'vlc-3.0.24') | Copy-Item -Destination $vlcPath -Recurse -Force
$ffmpeg = Join-Path $Destination 'ffmpeg-8.0.1-essentials_build'
Copy-Item -LiteralPath (Join-Path $ffmpeg 'bin\ffmpeg.exe'), (Join-Path $ffmpeg 'bin\ffprobe.exe') -Destination $ffmpegPath
Copy-Item -LiteralPath (Join-Path $ffmpeg 'LICENSE') -Destination (Join-Path $licenses 'FFmpeg.txt')
Download 'https://raw.githubusercontent.com/denoland/deno/v2.9.7/LICENSE.md' (Join-Path $licenses 'Deno.md')

if (-not $UseSystemWebView2) {
    $webviewPath = Join-Path $native 'webview2'
    $cab = Join-Path $Destination $sources.webview2.Name
    $cabPath = Join-Path $Destination 'webview2-cab'
    New-Item -ItemType Directory -Force -Path $cabPath, $webviewPath | Out-Null
    & expand.exe $cab '-F:*' $cabPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not extract WebView2' }
    $runtime = Get-ChildItem -LiteralPath $cabPath -Filter 'msedgewebview2.exe' -File -Recurse | Select-Object -First 1
    if (-not $runtime) { throw 'WebView2 runtime executable is missing' }
    $signature = Get-AuthenticodeSignature -LiteralPath $runtime.FullName
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
        throw 'WebView2 executable signature is invalid'
    }
    Get-ChildItem -LiteralPath $runtime.DirectoryName | Copy-Item -Destination $webviewPath -Recurse -Force
}

$sources | ConvertTo-Json -Depth 3 | Set-Content -Encoding utf8 (Join-Path $licenses 'WINDOWS-BUILD-INPUTS.json')
@'
CPython: https://www.python.org/downloads/release/python-31315/
VLC source: https://download.videolan.org/pub/videolan/vlc/3.0.24/
FFmpeg source: https://ffmpeg.org/releases/ffmpeg-8.0.1.tar.xz
FFmpeg build information: https://github.com/GyanD/codexffmpeg/releases/tag/8.0.1
Deno source: https://github.com/denoland/deno/tree/v2.9.7
Microsoft WebView2: https://developer.microsoft.com/microsoft-edge/webview2/
Microsoft WebView2 license: https://aka.ms/webview2/License
'@ | Set-Content -Encoding utf8 (Join-Path $licenses 'SOURCE-LINKS.txt')

@{ native = $native; embed = (Join-Path $Destination $sources.python.Name); embed_sha256 = $sources.python.Sha256 }
