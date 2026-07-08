param(
    [string]$ProjectRoot,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

function Get-ProjectRoot {
    if ($ProjectRoot) {
        return (Resolve-Path -LiteralPath $ProjectRoot).Path
    }
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

function Get-AppPort {
    param([string]$Root = (Get-ProjectRoot))
    if ($env:OTHERME_PORT) {
        return [int]$env:OTHERME_PORT
    }
    $config = Join-Path $Root ".streamlit\config.toml"
    if (Test-Path -LiteralPath $config) {
        $inServer = $false
        foreach ($line in Get-Content -LiteralPath $config -Encoding UTF8) {
            $trimmed = $line.Trim()
            if ($trimmed -match '^\[(.+)\]$') {
                $inServer = ($Matches[1] -eq "server")
                continue
            }
            if ($inServer -and $trimmed -match '^port\s*=\s*(\d+)') {
                return [int]$Matches[1]
            }
        }
    }
    return 8501
}

function Get-AppPaths {
    param([string]$Root = (Get-ProjectRoot))
    [pscustomobject]@{
        Root       = $Root
        Python     = Join-Path $Root ".venv\Scripts\python.exe"
        App        = Join-Path $Root "src\ui\app.py"
        RuntimeDir = Join-Path $Root "runtime"
        LogsDir    = Join-Path $Root "logs"
        PidPath    = Join-Path $Root "runtime\streamlit.pid"
        LogPath    = Join-Path $Root "logs\app.log"
    }
}

function Test-VenvPython {
    param([string]$Root = (Get-ProjectRoot))
    $paths = Get-AppPaths -Root $Root
    return (Test-Path -LiteralPath $paths.Python -PathType Leaf)
}

function Test-StreamlitInstalled {
    param([string]$PythonPath)
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }
    & $PythonPath -c "import streamlit" *> $null
    return ($LASTEXITCODE -eq 0)
}

function Test-FFmpegAvailable {
    param([string]$CommandName = "ffmpeg")
    return ($null -ne (Get-Command $CommandName -ErrorAction SilentlyContinue))
}

function New-StreamlitCommand {
    param(
        [string]$Root = (Get-ProjectRoot),
        [int]$Port = (Get-AppPort -Root $Root)
    )
    $paths = Get-AppPaths -Root $Root
    return @(
        $paths.Python,
        "-m",
        "streamlit",
        "run",
        "src\ui\app.py",
        "--server.port",
        "$Port",
        "--server.headless",
        "true"
    )
}

function Read-AppPid {
    param([string]$PidPath)
    if (-not (Test-Path -LiteralPath $PidPath -PathType Leaf)) {
        return $null
    }
    $raw = (Get-Content -LiteralPath $PidPath -Encoding UTF8 -Raw).Trim()
    if ($raw -match '^\d+$') {
        return [int]$raw
    }
    return $null
}

function Write-AppPid {
    param(
        [string]$PidPath,
        [int]$ProcessId
    )
    $dir = Split-Path -Parent $PidPath
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Set-Content -LiteralPath $PidPath -Value "$ProcessId" -Encoding UTF8
}

function Remove-AppPid {
    param([string]$PidPath)
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}

function Get-ManagedProcess {
    param(
        [int]$ProcessId,
        [string]$Root = (Get-ProjectRoot)
    )
    if (-not $ProcessId) {
        return $null
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) {
        return $null
    }
    $command = [string]$process.CommandLine
    $app = Join-Path $Root "src\ui\app.py"
    $appAlt = $app.Replace("\", "\\")
    if ($command -match "streamlit" -and (
            $command.Contains("src\ui\app.py") -or
            $command.Contains("src/ui/app.py") -or
            $command.Contains($app) -or
            $command.Contains($appAlt))) {
        return $process
    }
    return $null
}

function Test-AppAlreadyRunning {
    param(
        [string]$Root = (Get-ProjectRoot),
        [string]$PidPath = (Get-AppPaths -Root $Root).PidPath
    )
    $pidValue = Read-AppPid -PidPath $PidPath
    return ($null -ne (Get-ManagedProcess -ProcessId $pidValue -Root $Root))
}

function Assert-SystemReady {
    param([string]$Root = (Get-ProjectRoot))
    $paths = Get-AppPaths -Root $Root
    if (-not (Test-Path -LiteralPath $paths.App -PathType Leaf)) {
        throw "Application Streamlit introuvable : $($paths.App)"
    }
    if (-not (Test-VenvPython -Root $Root)) {
        throw "Python virtuel introuvable : $($paths.Python). Lancez 'Installer les dependances.bat'."
    }
    if (-not (Test-StreamlitInstalled -PythonPath $paths.Python)) {
        throw "Streamlit n'est pas installe dans .venv. Lancez 'Installer les dependances.bat'."
    }
    if (-not (Test-FFmpegAvailable)) {
        throw "FFmpeg est introuvable dans le PATH. Installez-le puis relancez Otherme Clipper."
    }
    return $true
}

if ($MyInvocation.InvocationName -ne ".") {
    try {
        $root = Get-ProjectRoot
        Assert-SystemReady -Root $root | Out-Null
        if (-not $Quiet) {
            $paths = Get-AppPaths -Root $root
            Write-Host "OK - Otherme Clipper est pret."
            Write-Host "Projet   : $root"
            Write-Host "Python   : $($paths.Python)"
            Write-Host "Port     : $(Get-AppPort -Root $root)"
            Write-Host "FFmpeg   : $((Get-Command ffmpeg).Source)"
        }
        exit 0
    } catch {
        Write-Error $_.Exception.Message
        exit 1
    }
}
