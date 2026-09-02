#!/usr/bin/env python3
"""ControlPlane.ai — one command to run everything.

    python run.py

Creates a private Python environment if there is not one, installs what it
needs, uses the pre-built dashboard (or rebuilds it if you have Node and it is
missing), picks a free port, starts the server and opens your browser.

Useful flags:
    --port 8123     use a specific port instead of picking one
    --no-browser    do not open a browser window
    --no-seed       start with an empty dashboard instead of backfilled traffic
    --rebuild-ui    force a frontend rebuild (needs Node.js)
    --dev           reload the server when backend files change
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"

IS_WINDOWS = os.name == "nt"
VENV_PY = VENV / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")

# Windows consoles still default to a legacy code page, which turns any box
# drawing character into a crash. Ask for UTF-8 and fall back quietly.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

# Colour only where it will render.
_COLOUR = sys.stdout.isatty() and (not IS_WINDOWS or os.environ.get("WT_SESSION")
                                   or os.environ.get("TERM") or os.environ.get("ANSICON"))


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def say(msg: str) -> None:
    print(f"  {msg}", flush=True)


def step(msg: str) -> None:
    print("\n" + _c("95", ">") + " " + _c("1", msg), flush=True)


def ensure_venv() -> Path:
    if VENV_PY.exists():
        return VENV_PY
    step("Creating a private Python environment (.venv)")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    say("done")
    return VENV_PY


def ensure_deps(python: Path) -> None:
    probe = subprocess.run(
        [str(python), "-c", "import fastapi, uvicorn, sqlalchemy, aiosqlite"],
        capture_output=True,
    )
    if probe.returncode == 0:
        return
    step("Installing dependencies (about a minute the first time)")
    subprocess.check_call([
        str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
        "-r", str(BACKEND / "requirements.txt"),
    ])
    say("done")


def ensure_frontend(force: bool = False) -> None:
    if DIST.exists() and (DIST / "index.html").exists() and not force:
        return
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        say(_c("93", "Node.js not found and no pre-built dashboard present."))
        say("The API will still run; install Node.js and re-run to get the UI.")
        return
    step("Building the dashboard")
    if not (FRONTEND / "node_modules").exists():
        subprocess.check_call([npm, "install", "--silent"], cwd=FRONTEND, shell=IS_WINDOWS)
    subprocess.check_call([npm, "run", "build"], cwd=FRONTEND, shell=IS_WINDOWS)
    say("done")


def free_port(preferred: int = 8000) -> int:
    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("could not find a free port between 8000 and 8040")


def open_browser_when_ready(url: str, health: str) -> None:
    import urllib.error
    import urllib.request

    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=2):
                webbrowser.open(url)
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run ControlPlane.ai")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-seed", action="store_true")
    ap.add_argument("--rebuild-ui", action="store_true")
    ap.add_argument("--dev", action="store_true")
    args = ap.parse_args()

    bar = "=" * 58
    print("\n" + _c("95", bar))
    print("  " + _c("1", "ControlPlane.ai") + " - risk-adaptive oversight for LLMs")
    print("  Team A308 | Accenture Innovation Challenge 2026")
    print(_c("95", bar))

    python = ensure_venv()
    ensure_deps(python)
    ensure_frontend(force=args.rebuild_ui)

    port = args.port or free_port()
    if args.port is None and port != 8000:
        say(_c("93", f"Port 8000 was busy, using {port} instead."))

    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    env["PORT"] = str(port)
    env["HOST"] = args.host
    if args.no_seed:
        env["SEED_DEMO_DATA"] = "false"

    url = f"http://{args.host}:{port}"
    step("Starting")
    say(f"dashboard   {url}")
    say(f"API docs    {url}/docs")
    say("press Ctrl+C to stop\n")

    if not args.no_browser:
        threading.Thread(
            target=open_browser_when_ready, args=(url, f"{url}/health"), daemon=True
        ).start()

    cmd = [str(python), "-m", "uvicorn", "app.main:app",
           "--host", args.host, "--port", str(port)]
    if args.dev:
        cmd += ["--reload", "--reload-dir", str(BACKEND / "app")]

    try:
        return subprocess.call(cmd, cwd=BACKEND, env=env)
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
