param(
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\check_system.ps1"

try {
    $root = Get-ProjectRoot
    $paths = Get-AppPaths -Root $root
    $pidValue = Read-AppPid -PidPath $paths.PidPath
    if (-not $pidValue) {
        if (-not $Quiet) { Write-Host "Otherme Clipper n'est pas lance." }
        Remove-AppPid -PidPath $paths.PidPath
        exit 0
    }

    $process = Get-ManagedProcess -ProcessId $pidValue -Root $root
    if (-not $process) {
        if (-not $Quiet) { Write-Host "PID stale ou non gere par Otherme Clipper : $pidValue" }
        Remove-AppPid -PidPath $paths.PidPath
        exit 0
    }

    Stop-Process -Id $pidValue -Force
    Remove-AppPid -PidPath $paths.PidPath
    if (-not $Quiet) { Write-Host "Otherme Clipper arrete (PID $pidValue)." }
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
