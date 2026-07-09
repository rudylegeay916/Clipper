param(
    [switch]$NoBrowser,
    [switch]$DryRun,
    [int]$StartupTimeoutSeconds = 60,
    [int]$PollMilliseconds = 500,
    [string]$HostScript,
    [string]$BrowserMarkerPath
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\check_system.ps1"

function Test-AppPortOpen {
    param([int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $task.Wait(500)) {
            return $false
        }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-AppServer {
    param(
        [int]$Port,
        [string]$PidPath,
        [datetime]$Deadline
    )
    do {
        if (Test-AppPortOpen -Port $Port) {
            return $true
        }
        Start-Sleep -Milliseconds $PollMilliseconds
    } while ((Get-Date) -lt $Deadline)
    return (Test-AppPortOpen -Port $Port)
}

function Get-AppLogTail {
    param(
        [string]$LogPath,
        [int]$LineCount = 20
    )
    if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        return ""
    }
    return (Get-Content -LiteralPath $LogPath -Encoding UTF8 -Tail $LineCount) -join [Environment]::NewLine
}

function Open-AppBrowser {
    param(
        [string]$Url,
        [string]$MarkerPath
    )
    try {
        if ($MarkerPath) {
            Set-Content -LiteralPath $MarkerPath -Encoding UTF8 -Value $Url
        } else {
            Start-Process $Url
        }
        return $true
    } catch {
        return $false
    }
}

function Write-AppReady {
    param(
        [string]$Url,
        [string]$LogPath = ""
    )
    Write-Host "Otherme Clipper est pret : $Url"
    if ($LogPath) {
        Write-Host "Logs : $LogPath"
    }
    if (-not $NoBrowser) {
        if (-not (Open-AppBrowser -Url $Url -MarkerPath $BrowserMarkerPath)) {
            Write-Host "Otherme Clipper fonctionne. Ouvrez $Url"
        }
    }
}

try {
    $root = Get-ProjectRoot
    $paths = Get-AppPaths -Root $root
    $port = Get-AppPort -Root $root
    $url = "http://localhost:$port"
    New-Item -ItemType Directory -Force -Path $paths.RuntimeDir, $paths.LogsDir | Out-Null
    Assert-SystemReady -Root $root | Out-Null

    $command = New-StreamlitCommand -Root $root -Port $port
    if ($DryRun) {
        [pscustomobject]@{
            root = $root
            port = $port
            command = $command
            pid_path = $paths.PidPath
            log_path = $paths.LogPath
        } | ConvertTo-Json -Depth 4
        exit 0
    }

    $existingPid = Read-AppPid -PidPath $paths.PidPath
    if (Test-AppPortOpen -Port $port) {
        Write-AppReady -Url $url
        exit 0
    }
    if ($existingPid -and (Get-ManagedProcess -ProcessId $existingPid -Root $root)) {
        $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
        if (Wait-AppServer -Port $port -PidPath $paths.PidPath -Deadline $deadline) {
            Write-AppReady -Url $url
            exit 0
        }
        Write-Host "Un processus Otherme Clipper existe (PID $existingPid), mais le serveur ne repond pas encore sur le port $port."
        exit 0
    }
    if ($existingPid) {
        Remove-AppPid -PidPath $paths.PidPath
    }

    Remove-AppPid -PidPath $paths.PidPath
    $hostScript = if ($HostScript) { $HostScript } else { Join-Path $PSScriptRoot "streamlit_host.ps1" }
    $hostProcess = Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $hostScript,
        "-ProjectRoot", $root,
        "-PythonPath", $paths.Python,
        "-Port", "$port",
        "-PidPath", $paths.PidPath,
        "-LogPath", $paths.LogPath
    ) -WindowStyle Hidden -PassThru

    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    if (Wait-AppServer -Port $port -PidPath $paths.PidPath -Deadline $deadline) {
        Write-AppReady -Url $url -LogPath $paths.LogPath
        exit 0
    }

    $pidValue = Read-AppPid -PidPath $paths.PidPath
    $managed = if ($pidValue) { Get-ManagedProcess -ProcessId $pidValue -Root $root } else { $null }
    if (Test-AppPortOpen -Port $port) {
        Write-AppReady -Url $url
        exit 0
    }
    if ($managed -or -not $hostProcess.HasExited) {
        Write-Host "Otherme Clipper est lance mais le port $port ne repond pas encore."
        Write-Host "Logs : $($paths.LogPath)"
        exit 0
    }

    $tail = Get-AppLogTail -LogPath $paths.LogPath
    if ($tail) {
        Write-Host "Dernieres lignes de logs :"
        Write-Host $tail
    }
    throw "Streamlit n'a pas demarre. Consultez $($paths.LogPath)"
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
