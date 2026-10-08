"""Stage 1 scaffold: wrap the OrcaSlicer / BambuStudio command line.

Mirrors design_pipeline.slice_with_orca() and slice.py in the Talk2Print repo,
stripped to what a student needs:

    from scaffold.slicer import slice_stl
    gcode = slice_stl("parts/week1_bracket.stl", machine, process, filament)

The command it runs (OrcaSlicer 2.x):
    <exe> --arrange 1 --load-settings "machine.json;process.json" \
          --load-filaments filament.json --slice 0 \
          --export-3mf output.gcode.3mf --outputdir <dir> model.stl

This writes <dir>/plate_1.gcode and a sliced 3MF beside the renamed G-code.
OrcaSlicer on macOS requires --export-3mf to receive a bare file name while
--outputdir supplies its directory; passing an absolute export path fails.
extract_gcode() hands a .gcode file back unchanged and can also extract the
embedded G-code from a sliced 3MF.
Pin the slicer version in the setup guide; flags have changed between releases.

Every job file slice_stl() writes gets a slice record beside it, for example
out/coupon.gcode.3mf.slice: which part and profiles made it, when, and a
fingerprint of the file. page_jobs() lists the job files that have one, and
scaffold/printer.py refuses to send any other file (contracts/printer.md,
rule 3). The record is a file on disk, so it survives a restart of the page.
The name ends in .slice, not .json, so it never appears in a profile list.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import time
import shutil
import subprocess
import zipfile
from pathlib import Path

from .config import ORCA_EXE, OUT

CANDIDATES = {
    "Darwin": [
        "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
        "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
    ],
    "Windows": [
        r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
        r"C:\Program Files\Bambu Studio\bambu-studio.exe",
    ],
    "Linux": ["orca-slicer", "OrcaSlicer", "bambu-studio"],
}


def find_slicer() -> str:
    """Return the slicer executable path, or '' if none is installed."""
    if ORCA_EXE and Path(ORCA_EXE).exists():
        return ORCA_EXE
    for cand in CANDIDATES.get(platform.system(), []):
        if Path(cand).exists() or shutil.which(cand):
            return cand
    return ""


def slice_stl(stl: str | os.PathLike, machine: str, process: str, filament: str,
              out_dir: str | os.PathLike = OUT, plate: int = 0, out_name: str = "") -> Path:
    """Slice one STL to G-code and a printable 3MF; return the G-code path.

    The slicer moves the part onto the plate (--arrange 1) and writes
    plate_1.gcode into out_dir, which is renamed after the part, for example
    out/week1_bracket.gcode. It also saves the printable job beside it as
    out/week1_bracket.gcode.3mf. Give out_name to choose the shared base name,
    for example out_name="week1_bracket__process_walls4" when the same part
    is sliced with two profiles and both results must be kept.
    """
    exe = find_slicer()
    if not exe:
        raise RuntimeError("No OrcaSlicer/BambuStudio found. Set ORCA_EXE in .env.")
    stl = Path(stl).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plate_gcode = out_dir / f"plate_{plate if plate > 0 else 1}.gcode"
    base_name = out_name or stl.stem
    out_3mf = out_dir / f"{base_name}.gcode.3mf"
    started = time.time()
    cmd = [exe,
           "--arrange", "1",
           "--load-settings", f"{Path(machine).resolve()};{Path(process).resolve()}",
           "--load-filaments", str(Path(filament).resolve()),
           "--slice", str(plate),
           "--export-3mf", out_3mf.name,
           "--outputdir", str(out_dir),
           str(stl)]
    print("  $", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    fresh_gcode = plate_gcode.exists() and plate_gcode.stat().st_mtime >= started - 1
    fresh_3mf = out_3mf.exists() and out_3mf.stat().st_mtime >= started - 1
    if res.returncode != 0 or not fresh_gcode or not fresh_3mf:
        code = res.returncode - 256 if res.returncode > 127 else res.returncode
        raise RuntimeError(f"Slicer failed (exit {res.returncode}, slicer error code {code}):\n"
                           f"{res.stdout[-800:]}\n{res.stderr[-800:]}")
    out_gcode = out_dir / f"{base_name}.gcode"
    plate_gcode.replace(out_gcode)
    _write_job_record(out_3mf, out_gcode, stl, machine, process, filament, exe)
    return out_gcode


# ------------------------------------------------------------- slice records
RECORD_SUFFIX = ".slice"


def job_record_path(job: str | os.PathLike) -> Path:
    """out/coupon.gcode.3mf -> out/coupon.gcode.3mf.slice"""
    job = Path(job)
    return job.with_name(job.name + RECORD_SUFFIX)


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_job_record(job: Path, gcode: Path, stl: Path, machine: str, process: str,
                      filament: str, exe: str) -> Path:
    record = {
        "job": job.name,
        "gcode": gcode.name,
        "part": stl.name,
        "machine": Path(machine).name,
        "process": Path(process).name,
        "filament": Path(filament).name,
        "sliced_at": dt.datetime.now().isoformat(timespec="seconds"),
        "slicer": Path(exe).name,
        "fingerprint": _fingerprint(job),
    }
    path = job_record_path(job)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def read_job_record(job: str | os.PathLike) -> dict | None:
    """The slice record of a job file, or None if it has none or the file has changed since."""
    job = Path(job)
    rec_path = job_record_path(job)
    if not (job.is_file() and rec_path.is_file()):
        return None
    try:
        record = json.loads(rec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if record.get("job") != job.name or record.get("fingerprint") != _fingerprint(job):
        return None
    return record


def page_jobs(out_dir: str | os.PathLike = OUT) -> list[Path]:
    """The job files in out/ that slice_stl() made, newest first."""
    jobs = [j for j in Path(out_dir).glob("*.gcode.3mf") if read_job_record(j)]
    return sorted(jobs, key=lambda j: j.stat().st_mtime, reverse=True)


def extract_gcode(threemf: str | os.PathLike, out_path: str | os.PathLike | None = None) -> Path:
    """Pull Metadata/plate_1.gcode out of a sliced 3MF and save it as a .gcode file."""
    threemf = Path(threemf)
    if threemf.suffix == ".gcode":
        return threemf  # slice_stl() already returns G-code; nothing to extract
    out_path = Path(out_path) if out_path else threemf.with_suffix(".gcode")
    with zipfile.ZipFile(threemf) as z:
        names = [n for n in z.namelist() if n.startswith("Metadata/plate_") and n.endswith(".gcode")]
        if not names:
            raise RuntimeError(f"No G-code inside {threemf.name}; was it sliced?")
        out_path.write_bytes(z.read(sorted(names)[0]))
    return out_path
