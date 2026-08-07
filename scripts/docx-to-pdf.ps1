# Render a DOCX/DOC to PDF via Word COM so the conversion reviewer can read the
# original as pages. The vault QA gate (bin/review-conversion.sh) reads the
# original to check fidelity; a .docx cannot be read visually, so without this
# the review silently degrades to md-only mode and cannot catch dropped content
# or fabricated clause numbering.
#
# Word is opened invisible and ReadOnly, so the source is never modified.
#
# Usage: powershell -NoProfile -File scripts/docx-to-pdf.ps1 <source.docx> <out.pdf>

param(
  [Parameter(Mandatory = $true)][string]$Source,
  [Parameter(Mandatory = $true)][string]$Out
)

$ErrorActionPreference = 'Stop'
$wdExportFormatPDF = 17

$Source = (Resolve-Path -LiteralPath $Source).Path
$outDir = Split-Path -Parent $Out
if ($outDir -and -not (Test-Path $outDir)) {
  New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}
if (-not [System.IO.Path]::IsPathRooted($Out)) {
  $Out = Join-Path (Get-Location).Path $Out
}

$word = $null
$doc = $null
try {
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  # ReadOnly + AddToRecentFiles:$false — never modify the source, and don't
  # pollute the user's MRU list during batch runs.
  $doc = $word.Documents.Open($Source, $false, $true, $false)
  $doc.ExportAsFixedFormat($Out, $wdExportFormatPDF)
  Write-Output "OK $Out"
}
catch {
  Write-Error "docx-to-pdf failed: $($_.Exception.Message)"
  exit 1
}
finally {
  if ($doc) { try { $doc.Close($false) } catch {} }
  if ($word) { try { $word.Quit() } catch {} }
}
