# Render the Endbot icon sources to the PNGs the repository and the pack use.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/render_icons.ps1
#
# The SVGs in assets/icon are the source of truth; the PNGs next to them and ui-pack/pack_icon.png are exports.
# Rendering needs Chrome (headless): it opens a tiny page that draws the SVG at the target size and screenshots it,
# so the vector is rasterised at each size instead of a large PNG being downscaled by hand.
param(
    [string]$Chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe",
    [int[]]$RepoSizes = @(512, 128, 64, 48, 32, 16),
    [int]$PackSize = 128
)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$icons = Join-Path $root "assets\icon"

function Render([string]$svg, [string]$outDir, [string]$baseName, [int[]]$sizes) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $svgUri = ([System.Uri](Resolve-Path $svg).Path).AbsoluteUri
    $work = Join-Path ([System.IO.Path]::GetTempPath()) ("endbot-icon-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    foreach ($size in $sizes) {
        $html = Join-Path $work "page-$size.html"
        @"
<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:transparent}img{display:block;width:${size}px;height:${size}px}
</style></head><body><img src="$svgUri" width="$size" height="$size"></body></html>
"@ | Set-Content -Path $html -Encoding UTF8
        $png = Join-Path $outDir "$baseName-$size.png"
        if (Test-Path $png) { Remove-Item $png -Force }
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Chrome
        $psi.Arguments = (@(
            "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--hide-scrollbars", "--force-device-scale-factor=1", "--window-size=$size,$size",
            "--virtual-time-budget=4000", "--user-data-dir=$(Join-Path $work "profile-$size")",
            "--screenshot=$png", ([System.Uri]$html).AbsoluteUri
        ) | ForEach-Object { '"' + $_ + '"' }) -join ' '
        $psi.UseShellExecute = $false
        $psi.RedirectStandardError = $true
        $psi.RedirectStandardOutput = $true
        $process = [System.Diagnostics.Process]::Start($psi)
        $process.StandardError.ReadToEnd() | Out-Null
        $process.StandardOutput.ReadToEnd() | Out-Null
        $process.WaitForExit(30000) | Out-Null
        if (Test-Path $png) { Write-Output ("{0} ({1} bytes)" -f (Split-Path $png -Leaf), (Get-Item $png).Length) }
        else { Write-Output "MISSING $png" }
    }
    Remove-Item $work -Recurse -Force
}

Render (Join-Path $icons "endbot-icon.svg") $icons "endbot-icon" $RepoSizes
Render (Join-Path $icons "endbot-icon-light.svg") $icons "endbot-icon-light" $RepoSizes
Copy-Item (Join-Path $icons "endbot-icon-$PackSize.png") (Join-Path $root "ui-pack\pack_icon.png") -Force
Write-Output "ui-pack/pack_icon.png updated"
