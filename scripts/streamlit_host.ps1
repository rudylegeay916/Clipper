param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][string]$PidPath,
    [Parameter(Mandatory = $true)][string]$LogPath
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $PidPath) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null

$stdoutPath = $LogPath
$stderrPath = "$LogPath.err"
Set-Content -LiteralPath $stdoutPath -Encoding UTF8 -Value "[$(Get-Date -Format o)] Demarrage Otherme Clipper sur le port $Port"
Set-Content -LiteralPath $stderrPath -Encoding UTF8 -Value ""

$process = Start-Process -FilePath $PythonPath -ArgumentList @(
    "-m",
    "streamlit",
    "run",
    "src\ui\app.py",
    "--server.port",
    "$Port",
    "--server.headless",
    "true"
) -WorkingDirectory $ProjectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdoutPath `
  -RedirectStandardError $stderrPath `
  -PassThru

Set-Content -LiteralPath $PidPath -Encoding UTF8 -Value "$($process.Id)"
