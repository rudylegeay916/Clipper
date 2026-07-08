import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launcher scripts")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
CHECK_SCRIPT = PROJECT_ROOT / "scripts" / "check_system.ps1"
START_SCRIPT = PROJECT_ROOT / "scripts" / "start_app.ps1"
STOP_SCRIPT = PROJECT_ROOT / "scripts" / "stop_app.ps1"


def run_ps(command: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"$ErrorActionPreference='Stop'; . '{CHECK_SCRIPT}'; {command}",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )


def run_script(script: Path, *args: str, timeout: int = 30,
               env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *args,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        env=merged_env,
    )


def fake_project(tmp_path: Path) -> Path:
    root = tmp_path / "Otherme project with spaces"
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    (root / ".streamlit").mkdir()
    (root / ".streamlit" / "config.toml").write_text("[server]\nport = 8765\n", encoding="utf-8")
    (root / "src" / "ui").mkdir(parents=True)
    (root / "src" / "ui" / "app.py").write_text("", encoding="utf-8")
    return root


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def start_fake_streamlit_process(port: int | None = None, delay: float = 0.0) -> subprocess.Popen:
    if port is None:
        code = "import time; time.sleep(60)"
    else:
        code = (
            "import socket, sys, time;"
            "port=int(sys.argv[1]); delay=float(sys.argv[2]);"
            "time.sleep(delay);"
            "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1);"
            "s.bind(('127.0.0.1', port)); s.listen(5);"
            "deadline=time.time()+60;"
            "\nwhile time.time() < deadline:\n"
            "    c, _ = s.accept()\n"
            "    c.sendall(b'HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\nOK')\n"
            "    c.close()\n"
        )
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            *( [str(port), str(delay)] if port is not None else [] ),
            "-m",
            "streamlit",
            "run",
            "src\\ui\\app.py",
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def write_fake_host_script(path: Path, delay: float = 0.0, fail: bool = False) -> Path:
    path.write_text(
        f"""
param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][string]$PidPath,
    [Parameter(Mandatory = $true)][string]$LogPath
)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $PidPath) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value "fake host"
if ({'$true' if fail else '$false'}) {{
    exit 0
}}
$serverPath = Join-Path $PSScriptRoot "fake_server.py"
@'
import socket
import sys
import time

port = int(sys.argv[1])
delay = float(sys.argv[2])
time.sleep(delay)
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", port))
sock.listen(5)
deadline = time.time() + 60
while time.time() < deadline:
    conn, _ = sock.accept()
    conn.sendall(b"HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\nOK")
    conn.close()
'@ | Set-Content -LiteralPath $serverPath -Encoding UTF8
$proc = Start-Process -FilePath $PythonPath -ArgumentList @($serverPath, "$Port", "{delay}", "-m", "streamlit", "run", "src\\ui\\app.py") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
Set-Content -LiteralPath $PidPath -Encoding UTF8 -Value "$($proc.Id)"
""",
        encoding="utf-8",
    )
    return path


def launcher_env(port: int) -> dict[str, str]:
    return {"OTHERME_PORT": str(port)}


def cleanup_pid_process(pid_path: Path):
    if not pid_path.exists():
        return
    raw = pid_path.read_text(encoding="utf-8").strip()
    if raw.isdigit():
        subprocess.run(
            ["taskkill", "/PID", raw, "/F"],
            capture_output=True,
            text=True,
        )
    pid_path.unlink(missing_ok=True)


def test_project_root_resolution():
    result = run_ps("(Get-ProjectRoot) | ConvertTo-Json")

    assert result.returncode == 0, result.stderr
    assert Path(json.loads(result.stdout)).resolve() == PROJECT_ROOT


def test_venv_detection_and_paths_with_spaces(tmp_path):
    root = fake_project(tmp_path)

    result = run_ps(f"Test-VenvPython -Root '{root}' | ConvertTo-Json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is True


def test_streamlit_command_uses_project_venv_and_configured_port(tmp_path):
    root = fake_project(tmp_path)

    result = run_ps(
        f"$port = Get-AppPort -Root '{root}'; "
        f"New-StreamlitCommand -Root '{root}' -Port $port | ConvertTo-Json"
    )

    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout)
    assert command[0] == str(root / ".venv" / "Scripts" / "python.exe")
    assert command[1:5] == ["-m", "streamlit", "run", "src\\ui\\app.py"]
    assert command[command.index("--server.port") + 1] == "8765"
    assert " " in command[0]


def test_pid_read_write_and_remove(tmp_path):
    pid_path = tmp_path / "runtime" / "streamlit.pid"

    result = run_ps(
        f"Write-AppPid -PidPath '{pid_path}' -ProcessId 12345; "
        f"$pidValue = Read-AppPid -PidPath '{pid_path}'; "
        f"Remove-AppPid -PidPath '{pid_path}'; "
        "[pscustomobject]@{ pid = $pidValue; exists = (Test-Path -LiteralPath "
        f"'{pid_path}') }} | ConvertTo-Json"
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {"pid": 12345, "exists": False}


def test_missing_streamlit_and_ffmpeg_are_reported():
    result = run_ps(
        "[pscustomobject]@{ "
        "streamlit = (Test-StreamlitInstalled -PythonPath 'C:\\missing\\python.exe'); "
        "ffmpeg = (Test-FFmpegAvailable -CommandName 'definitely_missing_ffmpeg') "
        "} | ConvertTo-Json"
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"streamlit": False, "ffmpeg": False}


def test_stop_script_only_stops_recorded_otherme_process():
    proc = start_fake_streamlit_process()
    pid_path = PROJECT_ROOT / "runtime" / "streamlit.pid"
    pid_path.parent.mkdir(exist_ok=True)
    pid_path.write_text(str(proc.pid), encoding="utf-8")

    try:
        time.sleep(0.5)
        result = run_script(STOP_SCRIPT, "-Quiet", timeout=30)

        assert result.returncode == 0, result.stderr
        proc.wait(timeout=10)
        assert not pid_path.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        pid_path.unlink(missing_ok=True)


def test_start_script_refuses_second_instance_without_opening_browser():
    port = free_port()
    proc = start_fake_streamlit_process(port=port)
    pid_path = PROJECT_ROOT / "runtime" / "streamlit.pid"
    pid_path.parent.mkdir(exist_ok=True)
    pid_path.write_text(str(proc.pid), encoding="utf-8")

    try:
        time.sleep(0.5)
        result = run_script(
            START_SCRIPT,
            "-NoBrowser",
            "-StartupTimeoutSeconds", "3",
            "-PollMilliseconds", "100",
            timeout=30,
            env=launcher_env(port),
        )

        assert result.returncode == 0, result.stderr
        assert "Otherme Clipper est pret" in result.stdout
        assert proc.poll() is None
        assert pid_path.read_text(encoding="utf-8").strip() == str(proc.pid)
    finally:
        if proc.poll() is None:
            proc.kill()
        pid_path.unlink(missing_ok=True)


def test_start_waits_until_server_is_available_after_delay(tmp_path):
    port = free_port()
    pid_path = PROJECT_ROOT / "runtime" / "streamlit.pid"
    host_script = write_fake_host_script(tmp_path / "fake_host.ps1", delay=1.0)

    try:
        result = run_script(
            START_SCRIPT,
            "-NoBrowser",
            "-HostScript", str(host_script),
            "-StartupTimeoutSeconds", "5",
            "-PollMilliseconds", "100",
            timeout=30,
            env=launcher_env(port),
        )

        assert result.returncode == 0, result.stderr
        assert "Otherme Clipper est pret" in result.stdout
        assert f"http://localhost:{port}" in result.stdout
        assert pid_path.exists()
    finally:
        cleanup_pid_process(pid_path)


def test_start_handles_slow_startup_with_longer_timeout(tmp_path):
    port = free_port()
    pid_path = PROJECT_ROOT / "runtime" / "streamlit.pid"
    host_script = write_fake_host_script(tmp_path / "fake_host.ps1", delay=2.0)

    try:
        result = run_script(
            START_SCRIPT,
            "-NoBrowser",
            "-HostScript", str(host_script),
            "-StartupTimeoutSeconds", "6",
            "-PollMilliseconds", "100",
            timeout=30,
            env=launcher_env(port),
        )

        assert result.returncode == 0, result.stderr
        assert "Otherme Clipper est pret" in result.stdout
    finally:
        cleanup_pid_process(pid_path)


def test_start_opens_browser_only_after_port_is_ready(tmp_path):
    port = free_port()
    pid_path = PROJECT_ROOT / "runtime" / "streamlit.pid"
    marker = tmp_path / "browser.txt"
    host_script = write_fake_host_script(tmp_path / "fake_host.ps1", delay=0.5)

    try:
        result = run_script(
            START_SCRIPT,
            "-HostScript", str(host_script),
            "-BrowserMarkerPath", str(marker),
            "-StartupTimeoutSeconds", "5",
            "-PollMilliseconds", "100",
            timeout=30,
            env=launcher_env(port),
        )

        assert result.returncode == 0, result.stderr
        assert marker.read_text(encoding="utf-8").lstrip("\ufeff").strip() == f"http://localhost:{port}"
    finally:
        cleanup_pid_process(pid_path)


def test_browser_open_failure_does_not_fail_start(tmp_path):
    port = free_port()
    pid_path = PROJECT_ROOT / "runtime" / "streamlit.pid"
    invalid_marker = tmp_path / "missing" / "browser.txt"
    host_script = write_fake_host_script(tmp_path / "fake_host.ps1", delay=0.0)

    try:
        result = run_script(
            START_SCRIPT,
            "-HostScript", str(host_script),
            "-BrowserMarkerPath", str(invalid_marker),
            "-StartupTimeoutSeconds", "5",
            "-PollMilliseconds", "100",
            timeout=30,
            env=launcher_env(port),
        )

        assert result.returncode == 0, result.stderr
        assert "Otherme Clipper fonctionne. Ouvrez" in result.stdout
    finally:
        cleanup_pid_process(pid_path)


def test_stale_pid_is_removed_and_new_server_can_start(tmp_path):
    port = free_port()
    pid_path = PROJECT_ROOT / "runtime" / "streamlit.pid"
    pid_path.parent.mkdir(exist_ok=True)
    pid_path.write_text("999999", encoding="utf-8")
    host_script = write_fake_host_script(tmp_path / "fake_host.ps1", delay=0.0)

    try:
        result = run_script(
            START_SCRIPT,
            "-NoBrowser",
            "-HostScript", str(host_script),
            "-StartupTimeoutSeconds", "5",
            "-PollMilliseconds", "100",
            timeout=30,
            env=launcher_env(port),
        )

        assert result.returncode == 0, result.stderr
        assert "Otherme Clipper est pret" in result.stdout
        assert pid_path.read_text(encoding="utf-8").strip() != "999999"
    finally:
        cleanup_pid_process(pid_path)


def test_host_process_ended_but_port_open_is_success():
    port = free_port()
    proc = start_fake_streamlit_process(port=port)
    pid_path = PROJECT_ROOT / "runtime" / "streamlit.pid"
    pid_path.parent.mkdir(exist_ok=True)
    pid_path.write_text("999999", encoding="utf-8")

    try:
        time.sleep(0.5)
        result = run_script(
            START_SCRIPT,
            "-NoBrowser",
            "-StartupTimeoutSeconds", "3",
            "-PollMilliseconds", "100",
            timeout=30,
            env=launcher_env(port),
        )

        assert result.returncode == 0, result.stderr
        assert "Otherme Clipper est pret" in result.stdout
        assert proc.poll() is None
    finally:
        if proc.poll() is None:
            proc.kill()
        pid_path.unlink(missing_ok=True)


def test_true_failure_requires_stopped_process_and_closed_port(tmp_path):
    port = free_port()
    pid_path = PROJECT_ROOT / "runtime" / "streamlit.pid"
    host_script = write_fake_host_script(tmp_path / "fake_host.ps1", fail=True)

    try:
        result = run_script(
            START_SCRIPT,
            "-NoBrowser",
            "-HostScript", str(host_script),
            "-StartupTimeoutSeconds", "1",
            "-PollMilliseconds", "100",
            timeout=30,
            env=launcher_env(port),
        )

        assert result.returncode == 1
        assert "Streamlit n'a pas demarre" in result.stderr
    finally:
        cleanup_pid_process(pid_path)
