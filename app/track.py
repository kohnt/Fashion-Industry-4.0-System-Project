"""Stage 4: part records, the print archive and the passport (contracts/tracking.md,
contracts/passport.md). No HTML here and no printer command: app/ui.py calls these.
Standard library only.
"""
from __future__ import annotations

import csv
import json
import shutil
import threading
import time
from pathlib import Path

from scaffold.config import OUT, ROOT, TEAM
from scaffold.slicer import job_record_path

RECORDS_DIR = OUT / "records"
PRINTS_DIR = ROOT / "prints"
JOB_PARTS = {"stage4_peg_pair": 2}   # tracking.md rule 1: every other job prints 1 part
STATIONS = {
    "print": f"Team {TEAM} Print tab",
    "printer": f"Team {TEAM} printer",
    "automate": f"Team {TEAM} Automate tab",
    "track": f"Team {TEAM} Track tab",
    "inspection": f"Team {TEAM} inspection",
    "assembly": f"Team {TEAM} assembly",
    "qc": "Brand QC (passport)",
}
PERSON_STATIONS = ("inspection", "assembly")   # rule 6
_lock = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def short_name(job_name: str) -> str:
    """stage4_peg_pair__process_0.20mm_standard_a1m.gcode.3mf -> stage4_peg_pair (rule 4)."""
    return job_name.split("__", 1)[0].removesuffix(".gcode.3mf")


def job_stem(job_name: str) -> str:
    return job_name.removesuffix(".gcode.3mf")


def _path(part_number: str) -> Path:
    return RECORDS_DIR / f"{Path(part_number).name}.json"


def load_record(part_number: str) -> dict | None:
    p = _path(part_number)
    if not part_number or not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _save(rec: dict) -> None:
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    _path(rec["part_number"]).write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")


def list_records() -> list[dict]:
    if not RECORDS_DIR.is_dir():
        return []
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in RECORDS_DIR.glob("*.json")]
    return sorted(recs, key=lambda r: (r["events"][0]["time"] if r["events"] else "", r["part_number"]))


def _slice_record(job: Path) -> dict:
    return json.loads(job_record_path(job).read_text(encoding="utf-8"))


def create_records(job: Path, press_t: float) -> list[str]:
    """Rule 1 and 2: one record per part, numbered by the fingerprint's first 8 characters
    and the next count not yet used. Returns the new part numbers."""
    rec = _slice_record(job)
    fp = rec["fingerprint"]
    with _lock:
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)
        used = [int(p.stem.rsplit("-", 1)[1]) for p in RECORDS_DIR.glob(f"{fp[:8]}-*.json")
                if p.stem.rsplit("-", 1)[1].isdigit()]
        start = max(used, default=0) + 1
        numbers = []
        for i in range(JOB_PARTS.get(short_name(job.name), 1)):
            pn = f"{fp[:8]}-{start + i:02d}"
            _save({
                "fingerprint": fp, "part_number": pn, "job": job.name,
                "events": [{"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(press_t)),
                            "station": STATIONS["print"], "what": "Print pressed"}],
                "part": rec.get("part", ""), "print_folder": "", "identifier": "",
                "fitted_in": "", "fitted_with": [], "status": "printing",
            })
            numbers.append(pn)
    return numbers


def add_event(part_number: str, station: str, what: str, _refuse: tuple[str, ...] = (), **fields) -> dict:
    """Rule 3: append one event; the summary fields change only with it. A record whose
    status is in _refuse takes no event (checked under the lock, so two presses cannot both pass)."""
    with _lock:
        rec = load_record(part_number)
        if rec is None:
            raise ValueError(f"No record for {part_number}.")
        if rec["status"] == "rejected":
            raise ValueError(f"{part_number} is rejected; its record takes no more events (rule 8).")
        if rec["status"] in _refuse:
            raise ValueError(f"{part_number} is {rec['status']}; no decision can be added.")
        if not what.strip():
            raise ValueError("Say what happened.")
        rec["events"].append({"time": _now(), "station": station, "what": what.strip()})
        rec.update(fields)
        _save(rec)
        return rec


def person_event(part_number: str, station_key: str, what: str, identifier: str = "") -> dict:
    """Rule 6: an event typed at the inspection or assembly station."""
    if station_key not in PERSON_STATIONS:
        raise ValueError("Station must be inspection or assembly.")
    fields = {"identifier": identifier.strip()} if identifier.strip() else {}
    return add_event(part_number, STATIONS[station_key], what, **fields)


def fit(holder: str, parts: list[str], station_key: str = "assembly") -> None:
    """Rule 7: one event on the holder naming every part it holds, one on each held part."""
    parts = list(dict.fromkeys(p for p in parts if p and p != holder))   # each part once
    if not parts:
        raise ValueError("Pick at least one part to fit.")
    for pn in [holder] + parts:
        r = load_record(pn)
        if r is None:
            raise ValueError(f"No record for {pn}.")
        if r["status"] == "rejected":
            raise ValueError(f"{pn} is rejected and cannot be fitted.")
        if r["fitted_in"] or r["fitted_with"]:
            raise ValueError(f"{pn} is already fitted.")
    station = STATIONS[station_key]
    add_event(holder, station, "Fitted over " + ", ".join(parts), fitted_with=parts, status="fitted")
    for pn in parts:
        add_event(pn, station, f"Fitted under {holder}", fitted_in=holder, status="fitted")


def reject(part_number: str, reason: str, station: str) -> dict:
    """Rule 8: the rejection is the last event, and says why."""
    if not reason.strip():
        raise ValueError("A rejection needs a reason.")
    return add_event(part_number, station, f"Rejected: {reason.strip()}", status="rejected")


def _newer(path: Path, press_t: float) -> bool:
    return path.is_file() and path.stat().st_mtime >= press_t


def archive_print(job: Path, press_t: float, final_state: str, telemetry_csv: Path | None,
                  part_numbers: list[str]) -> Path:
    """Rules 4 and 5: one folder per print, then the print's events on every record."""
    base = PRINTS_DIR / f"{short_name(job.name)}_{time.strftime('%Y-%m-%d_%H%M', time.localtime(press_t))}"
    folder, n = base, 2
    while folder.exists():
        folder, n = Path(f"{base}_{n}"), n + 1
    folder.mkdir(parents=True)

    if telemetry_csv is not None and telemetry_csv.is_file():
        shutil.copy2(telemetry_csv, folder)
    for f in (job, job_record_path(job)):
        if f.is_file():
            shutil.copy2(f, folder)
    stl = ROOT / "parts" / _slice_record(job).get("part", "")
    if stl.is_file():
        shutil.copy2(stl, folder)

    # Only what was written after Print was pressed (rule 4).
    automate_log = None
    for name in {job_stem(job.name), short_name(job.name)}:
        log = OUT / f"{name}_automate_log.csv"
        if automate_log is None and _newer(log, press_t):
            automate_log = Path(shutil.copy2(log, folder))
        frames = OUT / f"{name}_frames"
        if frames.is_dir():
            new = [p for p in frames.iterdir() if _newer(p, press_t)]
            if new:
                (folder / frames.name).mkdir(exist_ok=True)
                for p in new:
                    shutil.copy2(p, folder / frames.name)

    events = [(STATIONS["printer"], "Print finished" if final_state == "FINISH" else "Print failed")]
    if automate_log is None:
        events.append((STATIONS["automate"], "No Automate log for this print"))
    else:
        with automate_log.open(newline="", encoding="utf-8") as f:
            pauses = [r for r in csv.DictReader(f) if r.get("action") == "pause"]
        for r in pauses:
            events.append((STATIONS["automate"], f"Paused by Automate at layer {r['layer']} "
                                                 f"({r['label']}, confidence {r['confidence']})"))
        if not pauses:
            events.append((STATIONS["automate"], "Watched the whole print; no pause"))
    rel = folder.relative_to(ROOT).as_posix() + "/"
    events.append((STATIONS["track"], f"Print data archived in {rel}"))

    status = "printed" if final_state == "FINISH" else "failed"
    for pn in part_numbers:
        for i, (station, what) in enumerate(events):
            last = i == len(events) - 1
            add_event(pn, station, what, **({"status": status, "print_folder": rel} if last else {}))
    return folder


# ---------------------------------------------------------------- passport

def passport(part_number: str) -> dict | None:
    """contracts/passport.md rules 1-3 and 6: only what the QC manager's decision needs."""
    rec = load_record(part_number.strip())
    if rec is None:
        return None
    ev = rec["events"]
    whats = [e["what"] for e in ev]
    pauses = sum(1 for w in whats if w.startswith("Paused by Automate"))
    if "Print finished" in whats:
        line = "Finished"
    elif "Print failed" in whats:
        line = "Failed"
    else:
        line = "Still printing, or not archived"
    if rec["print_folder"]:   # the Automate lines are added with the archive (tracking.md rule 5)
        line += "; no pause" if pauses == 0 else f"; paused {'once' if pauses == 1 else f'{pauses} times'} by Automate"
        if pauses and "Print finished" in whats:
            line += "; resumed by the operator"
        if any(w.startswith("No Automate log") for w in whats):
            line += " (no Automate log for this print)"

    # The archived .slice record is the one this part was printed from; the one in out/
    # is replaced if the job is sliced again.
    archived = ROOT / rec["print_folder"] / job_record_path(Path(rec["job"])).name if rec["print_folder"] else None
    try:
        s = _slice_record(OUT / rec["job"]) if archived is None or not archived.is_file() \
            else json.loads(archived.read_text(encoding="utf-8"))
        job_line = f"{s.get('part', '')}, {s.get('process', '').removesuffix('.json')}, sliced {s.get('sliced_at', '')}"
    except (OSError, ValueError):
        job_line = rec["job"]

    inspections = [e for e in ev if e["station"] == STATIONS["inspection"]]
    if rec["fitted_in"]:
        assembly = {"text": "Fitted under", "parts": [rec["fitted_in"]]}
    elif rec["fitted_with"]:
        assembly = {"text": "Fitted over", "parts": rec["fitted_with"]}
    else:
        assembly = {"text": "Not fitted", "parts": []}

    status = rec["status"]
    if status == "rejected":
        status = ev[-1]["what"]

    files = []
    if rec["print_folder"]:
        folder = ROOT / rec["print_folder"]
        if folder.is_dir():
            files = sorted(p.relative_to(ROOT).as_posix() for p in folder.rglob("*") if p.is_file())

    return {
        "part_number": rec["part_number"], "identifier": rec["identifier"] or "not marked",
        "print": line, "job": job_line,
        "inspection": inspections[-1]["what"] if inspections else "Not inspected",
        "assembly": assembly, "status": status,
        "can_decide": rec["status"] not in ("rejected", "accepted", "printing"),
        "print_folder": rec["print_folder"], "print_data": files,
    }


def decide(part_number: str, decision: str, reason: str = "") -> dict:
    """passport.md rule 4: the QC decision is one more event on the record."""
    if decision == "accept":
        what, fields = "Accepted for production run", {"status": "accepted"}
    elif decision == "reject":
        if not reason.strip():
            raise ValueError("A rejection needs a reason.")
        what, fields = f"Rejected: {reason.strip()}", {"status": "rejected"}
    else:
        raise ValueError("Decision must be accept or reject.")
    return add_event(part_number, STATIONS["qc"], what, _refuse=("accepted", "printing"), **fields)
