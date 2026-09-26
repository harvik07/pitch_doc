# Export every slide of a .pptx to PNG with PowerPoint (visual check of the rendered deck).
# Usage: powershell -File scripts/export_slide_images.ps1 <path\to\pitch.pptx> <output folder>
param([Parameter(Mandatory = $true)][string]$Pptx, [Parameter(Mandatory = $true)][string]$OutDir)
$ErrorActionPreference = "Stop"
$Pptx = (Resolve-Path $Pptx).Path
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
$app = New-Object -ComObject PowerPoint.Application
try {
    # ReadOnly, Untitled, WithWindow = false
    $deck = $app.Presentations.Open($Pptx, -1, 0, 0)
    try {
        foreach ($slide in $deck.Slides) {
            $file = Join-Path $OutDir ("slide{0}.png" -f $slide.SlideIndex)
            $slide.Export($file, "PNG", 1600, 900)
            $file
        }
    } finally { $deck.Close() }
} finally { $app.Quit() }
