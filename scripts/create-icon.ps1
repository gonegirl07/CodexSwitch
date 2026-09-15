#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$assetPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'assets'
$bitmap = [System.Drawing.Bitmap]::new(256, 256)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
function RoundRect($x, $y, $w, $h, $r, $color) {
    $shape = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $shape.AddArc($x,$y,$r*2,$r*2,180,90)
    $shape.AddArc($x+$w-$r*2,$y,$r*2,$r*2,270,90)
    $shape.AddArc($x+$w-$r*2,$y+$h-$r*2,$r*2,$r*2,0,90)
    $shape.AddArc($x,$y+$h-$r*2,$r*2,$r*2,90,90)
    $shape.CloseFigure()
    $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($color))
    $graphics.FillPath($brush,$shape)
    $brush.Dispose(); $shape.Dispose()
}
try {
    RoundRect 0 0 256 256 56 '#142c3e'
    RoundRect 73 51 133 136 22 '#306071'
    RoundRect 45 77 140 131 22 '#51dec0'
    $pen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#142c3e'),11)
    $pen.StartCap = $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $graphics.DrawLines($pen,[System.Drawing.Point[]]@([System.Drawing.Point]::new(94,117),[System.Drawing.Point]::new(70,142),[System.Drawing.Point]::new(94,167)))
    $graphics.DrawLines($pen,[System.Drawing.Point[]]@([System.Drawing.Point]::new(136,117),[System.Drawing.Point]::new(160,142),[System.Drawing.Point]::new(136,167)))
    $pen.Dispose()
    $bitmap.Save((Join-Path $assetPath 'logo.png'),[System.Drawing.Imaging.ImageFormat]::Png)
    $stream = [System.IO.MemoryStream]::new()
    $writer = [System.IO.BinaryWriter]::new($stream)
    $sizes = @(16,32,48,256)
    $images = @()
    foreach ($size in $sizes) {
        $scaled = [System.Drawing.Bitmap]::new($bitmap,[System.Drawing.Size]::new($size,$size))
        $png = [System.IO.MemoryStream]::new()
        $scaled.Save($png,[System.Drawing.Imaging.ImageFormat]::Png)
        $images += ,$png.ToArray()
        $scaled.Dispose(); $png.Dispose()
    }
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$sizes.Count)
    $offset = 6 + 16*$sizes.Count
    for ($i=0; $i -lt $sizes.Count; $i++) {
        $size = $sizes[$i] % 256
        $writer.Write([byte]$size); $writer.Write([byte]$size); $writer.Write([uint16]0)
        $writer.Write([uint16]1); $writer.Write([uint16]32)
        $writer.Write([uint32]$images[$i].Length); $writer.Write([uint32]$offset)
        $offset += $images[$i].Length
    }
    foreach ($png in $images) { $writer.Write([byte[]]$png) }
    [System.IO.File]::WriteAllBytes((Join-Path $assetPath 'app.ico'),$stream.ToArray())
    $writer.Dispose(); $stream.Dispose()
}
finally { $graphics.Dispose(); $bitmap.Dispose() }
