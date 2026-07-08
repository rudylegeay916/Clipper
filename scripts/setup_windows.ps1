param()

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\check_system.ps1"

function Get-Python311Command {
    $candidates = @(
        @("py", "-3.11"),
        @("python", "")
    )
    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $arg = $candidate[1]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
            continue
        }
        $args = @()
        if ($arg) { $args += $arg }
        $args += @("-c", "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)")
        $process = Start-Process -FilePath $exe -ArgumentList $args -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput ([System.IO.Path]::GetTempFileName()) `
            -RedirectStandardError ([System.IO.Path]::GetTempFileName())
        if ($process.ExitCode -eq 0) {
            return @($exe) + $(if ($arg) { @($arg) } else { @() })
        }
    }
    return $null
}

try {
    $root = Get-ProjectRoot
    $paths = Get-AppPaths -Root $root
    $requirements = Join-Path $root "requirements.txt"
    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        throw "requirements.txt introuvable : $requirements"
    }

    if (-not (Test-VenvPython -Root $root)) {
        $python = Get-Python311Command
        if (-not $python) {
            throw "Python 3.11 est introuvable. Installez-le par exemple avec : winget install Python.Python.3.11"
        }
        Write-Host "Creation de .venv avec Python 3.11..."
        $pythonArgs = @()
        if ($python.Count -gt 1) {
            $pythonArgs = $python[1..($python.Count - 1)]
        }
        $pythonExe = $python[0]
        & $pythonExe @pythonArgs -m venv (Join-Path $root ".venv")
    }

    if (Test-StreamlitInstalled -PythonPath $paths.Python) {
        Write-Host ".venv est deja pret, installation pip ignoree."
    } else {
        Write-Host "Installation des dependances Python..."
        & $paths.Python -m pip install -r $requirements
    }

    if (-not (Test-FFmpegAvailable)) {
        throw "FFmpeg est introuvable. Installez-le puis relancez ce script, par exemple : winget install Gyan.FFmpeg"
    }

    Write-Host "Installation prete. Double-cliquez sur 'Lancer Otherme Clipper.bat'."
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
