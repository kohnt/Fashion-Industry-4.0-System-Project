"""Shared configuration for the 30.303 lab scaffold.

Everything secret or machine-specific comes from a .env file next to this
package (never committed).  Copy .env.example to .env and fill it in.

    STUDENT_NAME=Tan Ah Kow           # goes on every picture and file you hand in
    PRINTER_IP=192.168.1.50
    PRINTER_SERIAL=01P00A000000000
    PRINTER_ACCESS_CODE=12345678
    TEAM=team1
    VISION_PROVIDER=openai          # or anthropic
    OPENAI_API_KEY=...
    ANTHROPIC_API_KEY=...
    LAB_BROKER=192.168.1.10         # Mosquitto on the fab-lab PC
    ORCA_EXE=                       # leave blank to auto-detect
    FREECAD_CMD=                    # FreeCAD's command-line program; blank to auto-detect
    PRINTER_SIM=1                   # 1 = use the fake printer (no hardware)
    PRINTER_SIM_FAST=0              # 1 = the fake printer runs a short job, for testing
"""
from __future__ import annotations

import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "out"
SAMPLE = ROOT / "sample"
OUT.mkdir(exist_ok=True)


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()

PRINTER_IP = os.environ.get("PRINTER_IP", "")
PRINTER_SERIAL = os.environ.get("PRINTER_SERIAL", "")
PRINTER_ACCESS_CODE = os.environ.get("PRINTER_ACCESS_CODE", "")
TEAM = os.environ.get("TEAM", "team1")
STUDENT_NAME = os.environ.get("STUDENT_NAME", "").strip()


def student_slug() -> str:
    """Your name as a file name: 'Tan Ah Kow' -> 'tan_ah_kow'."""
    return re.sub(r"[^a-z0-9]+", "_", STUDENT_NAME.lower()).strip("_") or "noname"
VISION_PROVIDER = os.environ.get("VISION_PROVIDER", "openai")
LAB_BROKER = os.environ.get("LAB_BROKER", "localhost")
ORCA_EXE = os.environ.get("ORCA_EXE", "")
# FreeCAD's command-line program, used when your agent builds a part in FreeCAD.
# Leave FREECAD_CMD blank in .env and the usual places are tried:
#   macOS   /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd
#   Windows C:\\Program Files\\FreeCAD 1.1\\bin\\FreeCADCmd.exe
FREECAD_CMD = os.environ.get("FREECAD_CMD", "")
PRINTER_SIM = os.environ.get("PRINTER_SIM", "1") == "1"
# A short job on the fake printer, for testing a page quickly: about 25 seconds
# from Print to FINISH instead of about 75, with the same states in the same order.
PRINTER_SIM_FAST = os.environ.get("PRINTER_SIM_FAST", "0") == "1"

def read_env() -> dict[str, str]:
    """The .env file as it is now, read afresh on every call."""
    env = ROOT / ".env"
    found: dict[str, str] = {}
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            found[k.strip()] = v.strip()
    return found


def printer_settings() -> dict:
    """The printer lines of .env, read afresh each time get_printer() is called.

    So a corrected PRINTER_IP, PRINTER_SERIAL or PRINTER_ACCESS_CODE takes effect
    the next time the tab connects, without restarting the page. A line in .env
    wins over the same name set in the terminal.
    """
    env = read_env()

    def get(key: str, default: str = "") -> str:
        return env.get(key, os.environ.get(key, default)).strip()

    return {"ip": get("PRINTER_IP"), "serial": get("PRINTER_SERIAL"),
            "access_code": get("PRINTER_ACCESS_CODE"),
            "sim": get("PRINTER_SIM", "1") == "1", "sim_fast": get("PRINTER_SIM_FAST", "0") == "1"}


# Rate limit for printer commands (seconds between commands). The wrapper
# refuses anything faster.  Talk2Print's FabLab_Demo_Fast_T.json carries the
# same idea as llm_policy.min_seconds_between_actions; here it is enforced.
MIN_SECONDS_BETWEEN_COMMANDS = 5.0
