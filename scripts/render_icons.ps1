# Render the Endbot icon source to the PNGs the repository and the pack use.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/render_icons.ps1 [-Chrome <path to chrome.exe>]
#
# The SVGs in assets/icon are the source of truth; the PNGs next to them and ui-pack/pack_icon.png are exports.
# Rendering needs Chrome (headless): it opens a tiny page that draws the SVG at the target size and screenshots it,
# so the vector is rasterised at each size instead of a large PNG being downscaled by hand. Each PNG is written to a
# temporary directory first and replaces the tracked file only after every size rendered, so a failure leaves the
# tracked files untouched and exits non-zero.
param(
    [string]$Chrome = "",
    [int[]]$RepoSizes = @(512, 128),
    [int]$PackSize = 128
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$icons = Join-Path $root "assets\icon"

function Find-Chrome {
    if ($Chrome) {
        if (Test-Path $Chrome -PathType Leaf) { return (Resolve-Path $Chrome).Path }
        throw "Chrome not found at $Chrome"
    }
    foreach ($name in @("chrome", "chrome.exe")) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { return $command.Source }
    }
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if (-not $base) { continue }
        $candidate = Join-Path $base "Google\Chrome\Application\chrome.exe"
        if (Test-Path $candidate -PathType Leaf) { return $candidate }
    }
    throw "Chrome not found on PATH or in the usual install locations; pass -Chrome <path to chrome.exe>"
}

function Render([string]$chromeExe, [string]$svg, [string]$work, [string]$baseName, [int[]]$sizes) {
    $svgUri = ([System.Uri](Resolve-Path $svg).Path).AbsoluteUri
    foreach ($size in $sizes) {
        $html = Join-Path $work "page-$size.html"
        @"
<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:transparent}img{display:block;width:${size}px;height:${size}px}
</style></head><body><img src="$svgUri" width="$size" height="$size"></body></html>
"@ | Set-Content -Path $html -Encoding UTF8
        $png = Join-Path $work "$baseName-$size.png"
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $chromeExe
        $psi.Arguments = (@(
            "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--hide-scrollbars", "--force-device-scale-factor=1", "--window-size=$size,$size",
            "--virtual-time-budget=4000", "--user-data-dir=$(Join-Path $work "profile-$size")",
            "--screenshot=$png", ([System.Uri]$html).AbsoluteUri
        ) | ForEach-Object { '"' + $_ + '"' }) -join ' '
        # Chrome's own output is not redirected, so a full pipe cannot block it and the timeout below holds.
        $psi.UseShellExecute = $false
        $process = [System.Diagnostics.Process]::Start($psi)
        if (-not $process.WaitForExit(30000)) {
            $process.Kill()
            throw "Chrome did not finish $baseName-$size.png within 30 s"
        }
        if (-not (Test-Path $png)) { throw "Chrome did not write $baseName-$size.png (exit $($process.ExitCode))" }
    }
}

$chromeExe = Find-Chrome
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("endbot-icon-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {
    Render $chromeExe (Join-Path $icons "endbot-icon.svg") $work "endbot-icon" (@($RepoSizes) + $PackSize | Sort-Object -Unique)
    foreach ($size in $RepoSizes) {
        Copy-Item (Join-Path $work "endbot-icon-$size.png") (Join-Path $icons "endbot-icon-$size.png") -Force
        $png = Join-Path $icons "endbot-icon-$size.png"
        Write-Output ("{0} ({1} bytes)" -f (Split-Path $png -Leaf), (Get-Item $png).Length)
    }
    Copy-Item (Join-Path $work "endbot-icon-$PackSize.png") (Join-Path $root "ui-pack\pack_icon.png") -Force
    Write-Output "ui-pack/pack_icon.png updated"
}
finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
