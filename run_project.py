from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
BACKEND_VENV_DIR = BACKEND_DIR / ".venv"
BACKEND_REQUIREMENTS = BACKEND_DIR / "requirements.txt"
FRONTEND_ENV = FRONTEND_DIR / ".env"
FRONTEND_ENV_EXAMPLE = FRONTEND_DIR / ".env.example"

SITE_URL = "http://localhost:5173/html/index.html"
API_BASE_URL = "http://127.0.0.1:8000"


def backend_python_path() -> Path:
    if sys.platform.startswith("win"):
        return BACKEND_VENV_DIR / "Scripts" / "python.exe"
    return BACKEND_VENV_DIR / "bin" / "python"


def run_checked(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def ensure_backend_venv() -> None:
    if not BACKEND_VENV_DIR.exists():
        print("[setup] Creating backend virtual environment...")
        run_checked([sys.executable, "-m", "venv", str(BACKEND_VENV_DIR)], cwd=ROOT_DIR)


def ensure_backend_dependencies() -> None:
    py = backend_python_path()
    if not py.exists():
        raise FileNotFoundError(f"Python executable not found in venv: {py}")
    print("[setup] Installing backend dependencies...")
    run_checked([str(py), "-m", "pip", "install", "-r", str(BACKEND_REQUIREMENTS)], cwd=ROOT_DIR)


def ensure_frontend_env_file() -> None:
    if FRONTEND_ENV.exists():
        return
    if FRONTEND_ENV_EXAMPLE.exists():
        FRONTEND_ENV.write_text(FRONTEND_ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        FRONTEND_ENV.write_text(f"VITE_API_BASE_URL={API_BASE_URL}\n", encoding="utf-8")
    print("[setup] Created frontend/.env file.")


def ensure_frontend_dependencies() -> None:
    if shutil.which("npm") is None:
        raise EnvironmentError("npm is not installed or is not in PATH.")

    node_modules = FRONTEND_DIR / "node_modules"
    if node_modules.exists():
        print("[setup] frontend/node_modules already exists, skipping npm install.")
        return

    print("[setup] Installing frontend dependencies (npm install)...")
    run_checked(["npm", "install"], cwd=FRONTEND_DIR)


def to_ps_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def open_new_terminal(title: str, cwd: Path, command: str) -> None:
    if not sys.platform.startswith("win"):
        raise OSError("This launcher currently supports Windows terminal startup only.")

    ps_script = (
        f"$Host.UI.RawUI.WindowTitle = {to_ps_single_quoted(title)}; "
        f"Set-Location -LiteralPath {to_ps_single_quoted(str(cwd))}; "
        f"Write-Host '[launcher] Working directory:' (Get-Location).Path -ForegroundColor Cyan; "
        f"Write-Host '[launcher] Command:' {to_ps_single_quoted(command)} -ForegroundColor DarkCyan; "
        f"{command}"
    )

    subprocess.Popen(
        [
            "powershell",
            "-NoLogo",
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_script,
        ],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def wait_for_port(host: str, port: int, timeout_sec: int) -> bool:
    started = time.time()
    while time.time() - started < timeout_sec:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.6)
    return False


def start_services() -> None:
    py = backend_python_path()
    backend_cmd = f'& {to_ps_single_quoted(str(py))} -m uvicorn backend.main:app --host 127.0.0.1 --port 8000'
    frontend_cmd = "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort"

    open_new_terminal("Backend API", ROOT_DIR, backend_cmd)
    open_new_terminal("Frontend Vite", FRONTEND_DIR, frontend_cmd)


def main() -> None:
    print("[info] Project root:", ROOT_DIR)
    ensure_backend_venv()
    ensure_backend_dependencies()
    ensure_frontend_env_file()
    ensure_frontend_dependencies()
    start_services()

    print("[info] Waiting for backend (127.0.0.1:8000)...")
    backend_ready = wait_for_port("127.0.0.1", 8000, timeout_sec=90)
    print("[info] Waiting for frontend (127.0.0.1:5173)...")
    frontend_ready = wait_for_port("127.0.0.1", 5173, timeout_sec=120)

    if backend_ready and frontend_ready:
        print(f"[info] Opening {SITE_URL}")
        webbrowser.open(SITE_URL)
        print("[done] Backend and frontend are running.")
    else:
        print("[warn] Services did not become ready in time.")
        print("[warn] Please check both opened terminals for error messages.")
        print(f"[warn] Expected backend: {API_BASE_URL}")
        print(f"[warn] Expected frontend: {SITE_URL}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[error] {exc}")
        sys.exit(1)
