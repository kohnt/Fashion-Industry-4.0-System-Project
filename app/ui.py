"""Stage 1/2 UI page: Slice tab (pick a part and profile, step through layers)
and Print tab (send the sliced job to the printer, watch it run).

Written against contracts/ui.md and contracts/printer.md. Standard library
only (contract: "Standard library only"). Serves http://localhost:8303.
"""
from __future__ import annotations

import csv
import html
import json
import os
import threading
import time
import traceback
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app import track
from app.toolpath import parse_gcode
from scaffold.config import ROOT, STUDENT_NAME
from scaffold.gcode import render_layer
from scaffold.printer import RateLimited, get_printer
from scaffold.slicer import page_jobs, slice_stl
from scaffold.vision import VisionError, ask_vision

PARTS_DIR = ROOT / "parts"
PROFILES_DIR = ROOT / "profiles"
OUT_DIR = ROOT / "out"

MACHINE = PROFILES_DIR / "machine_a1mini_0.4.json"
FILAMENT = PROFILES_DIR / "filament_bambu_pla_basic_a1m.json"

PORT = 8303

# --- Print tab state (contract rule 2: opening the tab connects and reads;
# only pressing Print sends anything) -----------------------------------
_printer = None
_press_state = ""   # the state the printer reported right before Print was pressed (rule 4)
_press_t0 = 0.0      # time.time() when Print was pressed, for "seconds since Print"
_press_job = ""       # the job file name sent
_last_stop_was_us = False  # so a FAILED after our own Stop reads as "stopped", not a fault

# --- Monitor tab state (contracts/telemetry.md) -------------------------
_latest_report: dict = {}     # the newest report, shown even once the log window has closed (rule 4)
_log_path: Path | None = None # out/<job>_telemetry.csv for the job currently in the log window
_log_open = False              # True from the first logged row until FINISH/FAILED closes it (rule 4)
_log_press = 0.0                 # the _press_t0 the open/closed log belongs to, so a reprint of the same job starts fresh
_layer1_t: float | None = None  # seconds since Print that layer 1 was first seen in the window (rule 8)
_state_changes: list[tuple[float, str]] = []  # (seconds since Print, state) for the plot markers (rule 7)
_last_marked_state = ""          # avoids writing a marker twice for the same state
_log_points: list[dict] = []     # rows logged so far this job, for the two plots (rule 7)
_layer_zero_seen = False         # True once the layer has read 0 in this window (rule 4)

# --- Pause (printer.md rule 7) ------------------------------------------
_pause_from_page_t: float | None = None  # seconds since Print at which we pressed Pause, if pause() did not raise
_seen_pause = False                      # the printer has reported PAUSE since that press

# --- Track tab (contracts/tracking.md) -----------------------------------
_track_pending: tuple[Path, float] | None = None           # (job, press time) of a Print-tab press not yet accepted
_track_press: tuple[Path, float, list[str]] | None = None  # (job, press time, part numbers) of an accepted press


_printer_lock = threading.RLock()   # requests arrive together when the page opens: one printer only


def _watch(p) -> None:
    """telemetry.md rule 1: register the callback, taking reports only from the current printer,
    so a printer that has been replaced can never write into the log."""
    p.on_report(lambda report: _on_printer_report(report) if p is _printer else None)


def _get_printer():
    global _printer
    with _printer_lock:
        if _printer is None:
            p = get_printer()
            p.connect()
            _printer = p
            _watch(p)
        return _printer


def _connect_printer():
    """Connect button (rule 2): close() the old printer, read .env afresh, connect again."""
    global _printer
    with _printer_lock:
        if _printer is not None:
            _printer.close()
            _printer = None
        return _get_printer()


def _fmt1(v) -> str:
    """One decimal place, or '' for a missing value (telemetry.md rules 5 and 6)."""
    return "" if v is None else f"{float(v):.1f}"


def _round1(v):
    """One decimal place for the screen, or None for a missing value (telemetry.md rules 5 and 6)."""
    return None if v is None else round(float(v), 1)


def _on_printer_report(report: dict) -> None:
    """Registered with on_report() (telemetry.md rule 1). Stores the latest report
    and, while the log window for the current job is open, appends one CSV row
    (rule 2: no waiting, no command sent)."""
    global _latest_report, _log_path, _log_open, _log_press, _layer1_t, _last_marked_state, _log_points
    global _layer_zero_seen, _pause_from_page_t, _seen_pause
    _latest_report = report
    state = report.get("gcode_state")
    layer = report.get("layer_num")
    elapsed = round(time.time() - _press_t0, 1) if _press_t0 else 0.0

    # printer.md rule 7: the "paused from this page" line belongs to one pause. It ends
    # when the printer has reported PAUSE and then something else, or the job has ended.
    if _pause_from_page_t is not None:
        if state == "PAUSE":
            _seen_pause = True
        elif _seen_pause or state in ("IDLE", "FINISH", "FAILED"):
            _pause_from_page_t = None
            _seen_pause = False

    # rule 4: the window opens on the first report after the state has changed to
    # PREPARE or RUNNING since Print was pressed. Reports before that are the previous
    # job's. Keyed on _press_t0, not the job name, so printing the same job twice in a
    # row still starts fresh.
    job_started = _press_t0 and state in ("PREPARE", "RUNNING") and _press_state not in ("PREPARE", "RUNNING")
    if job_started and _log_press != _press_t0:
        _log_press = _press_t0
        job_stem = _press_job[:-len(".gcode.3mf")] if _press_job.endswith(".gcode.3mf") else Path(_press_job).stem
        _log_path = OUT_DIR / f"{job_stem}_telemetry.csv"
        with open(_log_path, "w", encoding="utf-8") as f:
            f.write("seconds since Print (s),state,nozzle temperature (°C),bed temperature (°C),layer,per cent (%)\n")
        _log_open = True
        _layer1_t = None
        _layer_zero_seen = False
        _state_changes.clear()
        _last_marked_state = ""
        _log_points = []
        _create_records()

    if not _log_open or _log_press != _press_t0:
        return

    # rule 4: the layer and per cent belong to this job only once the layer has read 0.
    # Until then they are the last print's leftovers: empty cells, off the layer plot.
    if layer == 0:
        _layer_zero_seen = True
    mine = _layer_zero_seen
    layer_cell = layer if (mine and layer is not None) else None
    percent_cell = report.get("mc_percent") if mine else None

    # rule 5: UNKNOWN or a missing state is an empty cell, never a made-up value.
    state_cell = "" if state in (None, "UNKNOWN") else state
    if state_cell and state_cell != _last_marked_state:
        _state_changes.append((elapsed, state_cell))
        _last_marked_state = state_cell

    if layer_cell == 1 and _layer1_t is None:
        _layer1_t = elapsed
    _live_maybe_start(layer_cell, state)   # automate.md 2026-10-12 rule 13

    row = [str(elapsed), state_cell, _fmt1(report.get("nozzle_temper")), _fmt1(report.get("bed_temper")),
           "" if layer_cell is None else str(layer_cell), "" if percent_cell is None else str(percent_cell)]
    with open(_log_path, "a", encoding="utf-8") as f:
        f.write(",".join(row) + "\n")
    _log_points.append({"t": elapsed, "nozzle_temper": report.get("nozzle_temper"),
                        "bed_temper": report.get("bed_temper"), "layer_num": layer_cell})

    if state in ("FINISH", "FAILED"):
        _log_open = False  # rule 4: ends here; no line comes after
        _start_archive(state)


def _create_records() -> None:
    """tracking.md rule 1: the records start once the printer has accepted the job, which is
    the state change after Print (printer.md rule 4), not start() returning."""
    global _track_pending, _track_press
    if _track_pending is None or _track_pending[1] != _press_t0:
        return  # the Automate replay's job: no record
    job, press_t = _track_pending
    _track_pending = None
    try:
        _track_press = (job, press_t, track.create_records(job, press_t))
        print(f"$ records {', '.join(_track_press[2])} for {job.name}")
    except (OSError, ValueError, KeyError) as exc:
        _track_press = None
        print(f"[track] no records for {job.name}: {exc}")


def _start_archive(final_state: str) -> None:
    """tracking.md rule 4: archive a Print-tab print once, off the report thread."""
    global _track_press
    if _track_press is None or _track_press[1] != _press_t0:
        return  # the Automate replay's job, or no records: nothing to archive
    job, press_t, numbers = _track_press
    _track_press = None
    log_path = _log_path
    live = _live["thread"] if _live["press"] == press_t else None

    def run():
        if live is not None:
            live.join(timeout=90)   # the last picture and log line are written before archiving
        try:
            folder = track.archive_print(job, press_t, final_state, log_path, numbers)
            print(f"$ archived {job.name} in {folder.relative_to(ROOT)}")
        except Exception as exc:
            print(f"[track] archive failed: {exc}")
    threading.Thread(target=run, daemon=True).start()


def _parts() -> list[str]:
    return sorted(p.name for p in PARTS_DIR.glob("*.stl"))


def _profiles() -> list[str]:
    # contract, "In": process profiles come from profiles/ or out/, named process_*.json
    found = list(PROFILES_DIR.glob("process_*.json")) + list(OUT_DIR.glob("process_*.json"))
    return sorted(f"profiles/{p.name}" if p.parent == PROFILES_DIR else f"out/{p.name}" for p in found)


def _profile_path(rel: str) -> Path:
    return ROOT / rel


def _page_jobs() -> list[str]:
    """Job files this page sliced, newest first, as paths relative to ROOT (rule 3)."""
    return [str(j.relative_to(ROOT)) for j in page_jobs(OUT_DIR)]


def _print_section() -> str:
    job_options = "".join(
        f'<option value="{html.escape(j)}">{html.escape(j)}</option>' for j in _page_jobs()
    ) or '<option value="">(none sliced yet -- slice a part on the Slice tab first)</option>'
    return f"""
    <div class="card">
      <div class="muted" id="print-target" style="flex-basis:100%">Connecting...</div>
      <button id="connect-btn" onclick="doConnect()" style="background:#666">Connect</button>
    </div>
    <div class="card">
      <div class="field"><label>Job (sliced by this page)</label><select id="print-job">{job_options}</select></div>
      <button id="print-btn" onclick="doPrint()">Print</button>
      <button id="pause-btn" onclick="doPause()" style="background:#666" disabled>Pause</button>
      <button id="stop-btn" onclick="doStop()" style="background:#666" disabled>Stop</button>
    </div>
    <div id="print-error"></div>
    <div class="card" id="print-status">
      <div class="muted">Not connected yet.</div>
    </div>
    <script>
    function setButtons(state) {{
      const printable = (state === 'IDLE' || state === 'FINISH' || state === 'FAILED' || !state);
      document.getElementById('print-btn').disabled = !printable;
      document.getElementById('pause-btn').disabled = state !== 'RUNNING';
      document.getElementById('stop-btn').disabled = !(state === 'PREPARE' || state === 'RUNNING' || state === 'PAUSE');
    }}

    // printer.md rule 6: an error stays on the tab until the state next changes.
    let errState = null;
    function showErr(msg) {{
      errState = lastState;
      document.getElementById('print-error').innerHTML = '<div class="error"><b>Error</b><pre></pre></div>';
      document.querySelector('#print-error pre').textContent = msg;
    }}
    function clearErr() {{ errState = null; document.getElementById('print-error').innerHTML = ''; }}
    let lastState = null;

    async function doConnect() {{
      const res = await fetch('/print/connect');
      const data = await res.json();
      if (!res.ok) {{ showErr(data.error); return; }}
      clearErr();
      document.getElementById('print-target').textContent = data.target;
    }}
    doConnect();

    async function doPrint() {{
      const job = document.getElementById('print-job').value;
      if (!job) {{ showErr('No job sliced yet.'); return; }}
      const res = await fetch('/print/start?job=' + encodeURIComponent(job));
      const data = await res.json();
      if (!res.ok) {{ showErr(data.error); }}
    }}
    async function doStop() {{
      const res = await fetch('/print/stop');
      const data = await res.json();
      if (!res.ok) {{ showErr(data.error); }}
    }}
    async function doPause() {{
      const res = await fetch('/print/pause');
      const data = await res.json();
      if (!res.ok) {{ showErr(data.error); }}
    }}
    async function poll() {{
      try {{
        const res = await fetch('/print/status');
        const d = await res.json();
        const box = document.getElementById('print-status');
        if (d.target) document.getElementById('print-target').textContent = d.target;
        if (d.error) {{ box.innerHTML = '<div class="error"><b>Error</b><pre>' + d.error + '</pre></div>'; return; }}
        let note = '';
        if (d.gcode_state === 'FAILED' && d.we_stopped) note = ' (stopped, as requested)';
        if (errState !== null && d.gcode_state !== errState) clearErr();
        lastState = d.gcode_state;
        // rule 7: one line about where a pause came from, and not repeated
        let pauseLine = '';
        if (d.pause_from_page_t !== null && d.pause_from_page_t !== undefined) pauseLine = 'Paused from this page at ' + d.pause_from_page_t + ' s';
        else if (d.gcode_state === 'PAUSE') pauseLine = 'Paused by the printer';
        setButtons(d.gcode_state);
        box.innerHTML = `
          <div class="results">
            <div><b>${{d.gcode_state}}</b>${{note}}<div class="muted">since Print: ${{d.elapsed}} s</div>${{pauseLine ? '<div>' + pauseLine + '</div>' : ''}}</div>
            <div>${{d.nozzle_temper == null ? '-' : Number(d.nozzle_temper).toFixed(1)}} °C<div class="muted">nozzle</div></div>
            <div>${{d.bed_temper == null ? '-' : Number(d.bed_temper).toFixed(1)}} °C<div class="muted">bed</div></div>
            <div>${{d.layer_num ?? '-'}}<div class="muted">layer</div></div>
            <div>${{d.mc_percent ?? 0}}%<div class="muted">done</div></div>
          </div>`;
      }} catch (e) {{ /* keep last shown state on a transient fetch error */ }}
    }}
    poll();
    setInterval(poll, 2000);
    </script>
    """


def _monitor_section() -> str:
    """The Monitor tab: latest report, layer-1 time, and the two plots (telemetry.md)."""
    return """
    <div class="card" id="monitor-status">
      <div class="muted">Not connected yet.</div>
    </div>
    <div class="card">
      <canvas id="monitor-temp" height="160" style="width:100%"></canvas>
    </div>
    <div class="card">
      <canvas id="monitor-layer" height="160" style="width:100%"></canvas>
    </div>
    <script>
    function fmtVal(v, unit) {
      return (v === null || v === undefined || v === "not reported") ? "not reported" : (v + (unit || ""));
    }

    // series: [{key, color, name}]. Both plots share xMax so the time axes line up (rule 7).
    function drawPlot(canvas, points, markers, series, yLabel, xMax) {
      const ctx = canvas.getContext('2d');
      const w = canvas.width = canvas.clientWidth;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      const padL = 48, padR = 10, padT = 34, padB = 28;
      const plotW = w - padL - padR, plotH = h - padT - padB;
      const ys = [];
      series.forEach(sr => points.forEach(p => { if (p[sr.key] !== null && p[sr.key] !== undefined) ys.push(p[sr.key]); }));
      ctx.font = '11px sans-serif';
      if (points.length < 2 || ys.length < 1) {
        ctx.fillStyle = '#666'; ctx.fillText('(no data in the log window yet)', padL, padT + 14);
        return;
      }
      const yMin = Math.min(0, ...ys), yMax = Math.max(...ys, 1);
      const xOf = t => padL + (t / xMax) * plotW;
      const yOf = v => padT + plotH - ((v - yMin) / (yMax - yMin || 1)) * plotH;
      // axes, ticks and names (each axis names its quantity and unit)
      ctx.strokeStyle = '#ccc'; ctx.lineWidth = 1; ctx.beginPath();
      ctx.moveTo(padL, padT); ctx.lineTo(padL, padT + plotH); ctx.lineTo(padL + plotW, padT + plotH); ctx.stroke();
      ctx.fillStyle = '#666';
      ctx.textAlign = 'right';
      ctx.fillText(yMax.toFixed(0), padL - 4, yOf(yMax) + 4);
      ctx.fillText(yMin.toFixed(0), padL - 4, yOf(yMin) + 4);
      ctx.textAlign = 'left';
      ctx.fillText(yLabel, 2, 12);
      ctx.textAlign = 'right';
      ctx.fillText('seconds since Print (s): 0 to ' + xMax.toFixed(0), padL + plotW, h - 6);
      ctx.textAlign = 'left';
      // legend, on the first line next to the axis name
      let lx = padL + 150;
      series.forEach(sr => {
        ctx.fillStyle = sr.color; ctx.fillRect(lx, 5, 12, 3);
        ctx.fillStyle = '#444'; ctx.fillText(sr.name, lx + 16, 12);
        lx += 16 + ctx.measureText(sr.name).width + 14;
      });
      // state-change markers: a line at each change, labels on two rows, none written over another
      let rowEnd = [-1e9, -1e9];
      markers.forEach(([t, state]) => {
        const x = xOf(t);
        ctx.strokeStyle = '#bbb'; ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, padT + plotH); ctx.stroke();
        const wLabel = ctx.measureText(state).width;
        let row = rowEnd[0] + 3 <= x ? 0 : (rowEnd[1] + 3 <= x ? 1 : -1);
        if (row < 0) return; // no room: the line stays, the label is skipped rather than overwritten
        rowEnd[row] = x + 2 + wLabel;
        ctx.fillStyle = '#900'; ctx.fillText(state, x + 2, padT - 16 + row * 12);
      });
      series.forEach(sr => {
        ctx.strokeStyle = sr.color; ctx.lineWidth = 1.5; ctx.beginPath();
        let pen = false;
        points.forEach(p => {
          const v = p[sr.key];
          if (v === null || v === undefined) { pen = false; return; }
          const x = xOf(p.t), y = yOf(v);
          if (!pen) { ctx.moveTo(x, y); pen = true; } else ctx.lineTo(x, y);
        });
        ctx.stroke();
      });
    }

    async function pollMonitor() {
      try {
        const res = await fetch('/monitor/status');
        const d = await res.json();
        const box = document.getElementById('monitor-status');
        box.innerHTML = `
          <div class="results">
            <div><b>${fmtVal(d.gcode_state)}</b><div class="muted">state</div></div>
            <div>${fmtVal(d.nozzle_temper, ' °C')}<div class="muted">nozzle</div></div>
            <div>${fmtVal(d.bed_temper, ' °C')}<div class="muted">bed</div></div>
            <div>${fmtVal(d.layer_num)}<div class="muted">layer</div></div>
            <div>${fmtVal(d.mc_percent, '%')}<div class="muted">done</div></div>
            <div>${d.layer1_t === null ? 'not yet' : d.layer1_t + ' s'}<div class="muted">layer 1 at</div></div>
            <div class="muted">log: ${d.log_path || '(none yet)'}</div>
          </div>`;
        const xMax = Math.max(1, ...d.points.map(p => p.t));
        drawPlot(document.getElementById('monitor-temp'), d.points, d.state_changes,
                 [{key: 'nozzle_temper', color: '#990000', name: 'nozzle'}, {key: 'bed_temper', color: '#1f5fa8', name: 'bed'}],
                 'temperature (°C)', xMax);
        drawPlot(document.getElementById('monitor-layer'), d.points, d.state_changes,
                 [{key: 'layer_num', color: '#2a7a2a', name: 'layer'}], 'layer number', xMax);
      } catch (e) { /* keep last shown state on a transient fetch error */ }
    }
    pollMonitor();
    setInterval(pollMonitor, 2000);
    </script>
    """


# ======================================================================
# Automate tab (contracts/automate.md, Part 2). The only command this
# code sends to the printer is pause() (rule 7): it never resumes, stops
# or starts anything itself, except that the replay starts its own job on
# the simulator (rule 9), which is not a decision about the print.
# ======================================================================
DATASET_FRAMES = Path(os.environ.get("AUTOMATE_FRAMES", "")) if os.environ.get("AUTOMATE_FRAMES") \
    else ROOT / "data" / "stage3_stringing_test_frames"
DEFAULT_ANSWERS = DATASET_FRAMES / "predictions_gpt-6-luna.csv"
AUTOMATE_LOG = OUT_DIR / "stage3_stringing_test_automate_log.csv"
AUTOMATE_COLUMNS = ["picture file", "layer", "label", "confidence", "printer state", "action", "Part 1 row"]
LEVELS = ("none", "light", "heavy")
THRESHOLD = 0.60            # row 5: a confidence of 0.60 or more counts
FAILURES_TO_PAUSE = 3       # row 6
PICTURE_EVERY = 15.0        # rule 9: seconds between pictures, as in pictures.csv
REPLAY_JOB_SECONDS = 900.0  # rule 9: the simulator's job lasts past the last picture
SLOW_RESUME_AFTER = 30.0    # the simulator resumes by itself this long after a pause, in slow mode
# Live watch (automate.md, 2026-10-12 rules 13 to 17): the webcam watches a real Print-tab print.
LIVE_WATCH = os.environ.get("AUTOMATE_LIVE", "1").strip() != "0"
WEBCAM_INDEX = int(os.environ.get("WEBCAM_INDEX", "0").strip() or 0)
WEBCAM_FLIP = os.environ.get("WEBCAM_FLIP", "0").strip() == "1"
LIVE_ENDS = ("FINISH", "FAILED", "IDLE")   # rule 16: the watch ends with the job


@dataclass
class Picture:
    file: str
    seconds: float
    layer: int | None


@dataclass
class Answer:
    label: str
    confidence: float


@dataclass
class Memory:
    light_accepted: bool = False   # row 4: a person has continued after a stringing pause
    failures: int = 0              # row 6: checked pictures in a row with no answer
    pause_reason: str = ""         # "stringing", "failures" or "": why the tab last paused


@dataclass
class Decision:
    action: str   # "pause" or "none"
    row: int      # the Part 1 row that decided it


_auto = {"running": False, "stop": False, "lines": [], "memory": Memory(), "answers": {},
         "message": "", "error": "", "position": 0, "total": 0,
         "ask": lambda pic: None,   # Picture -> Answer | None: saved answers (replay) or the detector (live)
         "log": AUTOMATE_LOG}
_auto_lock = threading.Lock()
# The live watch of one Print-tab print: the press it belongs to, its thread, and a stop flag.
_live = {"press": 0.0, "thread": None, "stop": False, "active": False, "job": ""}


def decide(answer: Answer | None, memory: Memory) -> Decision:
    """The pause rules (Part 2 rules 3, 4 and 6). Sends nothing."""
    if answer is None or answer.label not in LEVELS:
        memory.failures += 1                      # row 6
        if memory.failures >= FAILURES_TO_PAUSE:
            memory.failures = 0
            return Decision("pause", 6)
        return Decision("none", 6)
    memory.failures = 0
    stringing = answer.label in ("light", "heavy")
    if stringing and answer.confidence < THRESHOLD:
        return Decision("none", 5)                # below the threshold
    if memory.light_accepted:
        return Decision("pause" if answer.label == "heavy" else "none", 4)
    return Decision("pause" if stringing else "none", 1)


def _read_used(folder: Path) -> list[Picture]:
    """The pictures pictures.csv marks 'yes', in the order of that file (rule 2)."""
    pics = []
    with (folder / "pictures.csv").open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r["used"].strip().lower().startswith("yes"):
                continue
            layer = r["layer"].strip()
            pics.append(Picture(r["picture file"], float(r["seconds since Print (s)"]), int(layer) if layer else None))
    return pics


def _read_answers(path: Path) -> dict:
    """Saved detector answers by picture file name; an empty prediction is no answer (rule 1)."""
    answers = {}
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            label = (r.get("Detector's prediction") or "").strip().lower()
            try:
                conf = float(r.get("Detector's confidence") or 0)
            except ValueError:
                conf = 0.0
            answers[r["Picture file"]] = Answer(label, conf) if label in LEVELS else None
    return answers


def _auto_log(pic: Picture, label: str, conf: str, state: str, action: str, row: int) -> None:
    """One line for each picture that arrives (rule 10)."""
    line = [pic.file, "" if pic.layer is None else pic.layer, label, conf, state, action, row]
    with _auto_lock:
        _auto["lines"].append(line)
        with open(_auto["log"], "a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(line)


def _auto_pause(row: int) -> tuple[str, str]:
    """Press Pause once and wait until the printer reports PAUSE (rule 7). Returns (action, state)."""
    global _pause_from_page_t, _seen_pause
    p = _printer
    mem = _auto["memory"]
    t = round(time.time() - _press_t0, 1) if _press_t0 else 0.0
    try:
        p.pause()
    except (RateLimited, RuntimeError) as exc:
        _auto["message"] = f"Pause failed: {exc}"
        return "pause failed", p.state()
    _pause_from_page_t = t
    _seen_pause = False
    mem.pause_reason = "failures" if row == 6 else "stringing"
    deadline = time.time() + 10
    while time.time() < deadline:
        if p.state() == "PAUSE":
            return "pause", "PAUSE"
        time.sleep(0.2)
    mem.pause_reason = ""
    _auto["message"] = "Pause pressed, but the printer did not report PAUSE within 10 s."
    return "pause not confirmed", p.state()


def on_picture(picture: Picture, printer_state: str) -> Decision:
    """One arriving picture (rules 2, 5, 6, 7): check it only while the printer is RUNNING."""
    if picture.layer is None or picture.layer < 1:
        _auto_log(picture, "", "", printer_state, "dropped", 8)           # row 8
        return Decision("none", 8)
    if printer_state != "RUNNING":
        _auto_log(picture, "", "", printer_state, "dropped", 2)           # row 2
        return Decision("none", 2)
    answer = _auto["ask"](picture)
    decision = decide(answer, _auto["memory"])
    label = answer.label if answer else ""
    conf = f"{answer.confidence:.2f}" if answer else ""
    if decision.action == "pause":
        action, state_now = _auto_pause(decision.row)
        _auto_log(picture, label, conf, state_now, action, decision.row if action == "pause" else 3)
    else:
        _auto_log(picture, label, conf, printer_state, "checked", decision.row)
    return decision


def _auto_track(prev: str, now: str) -> str:
    """Rule 4: a person continued when the state goes from PAUSE to RUNNING after a stringing pause."""
    if prev == "PAUSE" and now == "RUNNING":
        mem = _auto["memory"]
        if mem.pause_reason == "stringing":
            mem.light_accepted = True
        mem.pause_reason = ""
    return now


def _replay(job_rel: str, answers_path: Path) -> None:
    """Replay the saved answers on the simulator in slow mode (rules 1, 2, 5 and 9)."""
    global _printer, _press_state, _press_t0, _press_job, _last_stop_was_us, _pause_from_page_t, _seen_pause
    try:
        pics = _read_used(DATASET_FRAMES)
        answers = _read_answers(answers_path)
        _auto.update(answers=answers, ask=lambda pic: answers.get(pic.file), log=AUTOMATE_LOG,
                     memory=Memory(), lines=[], message="", error="", position=0, total=len(pics))
        with open(AUTOMATE_LOG, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(AUTOMATE_COLUMNS)

        p = get_printer(duration=REPLAY_JOB_SECONDS, layers=75)
        if getattr(p, "resume_after", None) != SLOW_RESUME_AFTER:
            try:
                p.close()
            except Exception:
                pass
            raise RuntimeError("The replay runs only on the simulator in slow mode. "
                               "Set PRINTER_SIM=1 and PRINTER_SIM_FAST=0 in .env, then run it again.")
        p.connect()
        with _printer_lock:
            old, _printer = _printer, p  # swap in one step: the page never sees "no printer"
            _watch(p)
            if old is not None:
                old.close()

        job = ROOT / job_rel
        _press_state, _press_t0, _press_job = p.state(), time.time(), job.name
        p.start(job)
        _last_stop_was_us, _pause_from_page_t, _seen_pause = False, None, False

        # Rule 9: wait until the simulator's own pause is over, then hand over the first picture.
        _auto["message"] = "Waiting for the simulator's own pause to end..."
        seen_pause, deadline = False, time.time() + 150
        while time.time() < deadline and not _auto["stop"]:
            s = p.state()
            seen_pause = seen_pause or s == "PAUSE"
            if s == "RUNNING" and seen_pause:
                break
            time.sleep(0.25)
        if not seen_pause:
            _auto["message"] = "The simulator's own pause was not seen; the replay started anyway."
        else:
            _auto["message"] = ""

        prev = p.state()
        t0 = time.time()
        for i, pic in enumerate(pics):
            due = t0 + i * PICTURE_EVERY
            while not _auto["stop"]:
                remaining = due - time.time()
                if remaining <= 0:
                    break
                prev = _auto_track(prev, p.state())
                time.sleep(min(0.2, remaining))
            if _auto["stop"]:
                break
            now = p.state()
            prev = _auto_track(prev, now)
            on_picture(pic, now)
            _auto["position"] = i + 1
        _auto["message"] = "Replay stopped." if _auto["stop"] else "Replay finished."
    except Exception as exc:
        _auto["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _auto["running"] = False


# ----------------------------------------------------------------------
# Live watch (automate.md, 2026-10-12 rules 13 to 17). From layer 1 of a
# Print-tab print, the webcam takes a picture every 15 s into
# out/<job stem>_frames/, the detector is asked about it, and on_picture
# applies the same rules as the replay. The only printer command is pause().
# ----------------------------------------------------------------------
def _job_stem(job_name: str) -> str:
    return job_name[:-len(".gcode.3mf")] if job_name.endswith(".gcode.3mf") else Path(job_name).stem


def _live_maybe_start(layer: int | None, state: str | None) -> None:
    """Rule 13: start the watch once, on the first report of layer 1 or more while RUNNING."""
    if not LIVE_WATCH or _auto["running"] or _live["active"] or _live["press"] == _press_t0:
        return
    if state != "RUNNING" or layer is None or layer < 1:
        return
    _live.update(press=_press_t0, stop=False, active=True, job=_press_job)
    _live["thread"] = threading.Thread(target=_live_watch, args=(_press_t0, _press_job), daemon=True)
    _live["thread"].start()


def _live_answer(folder: Path):
    """Rule 14: ask the detector about one picture; any failure is no answer (row 6)."""
    def ask(pic: Picture) -> Answer | None:
        try:
            a = ask_vision(folder / pic.file)
        except Exception as exc:   # VisionError, or a reply in an unexpected shape
            _auto["message"] = f"No answer for {pic.file}: {exc}"
            return None
        if a.get("stringing") not in LEVELS:
            return None
        return Answer(a["stringing"], float(a.get("confidence") or 0))
    return ask


def _live_state() -> str:
    p = _printer
    try:
        return p.state() if p is not None else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _live_layer() -> int | None:
    """The layer of the newest report, once it belongs to this job (telemetry.md rule 4)."""
    layer = _latest_report.get("layer_num")
    return layer if (_layer_zero_seen and isinstance(layer, int)) else None


def _live_watch(press_t: float, job_name: str) -> None:
    stem = _job_stem(job_name)
    folder = OUT_DIR / f"{stem}_frames"
    folder.mkdir(exist_ok=True)
    log = OUT_DIR / f"{stem}_automate_log.csv"
    with open(log, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(AUTOMATE_COLUMNS)
    # Rule 15: number after any pictures already in the folder, so none is overwritten.
    taken = [int(p.stem[1:4]) for p in folder.glob("p[0-9][0-9][0-9]_t*.jpg")]
    n = max(taken, default=0)
    with _auto_lock:
        _auto.update(ask=_live_answer(folder), log=log, memory=Memory(), lines=[], error="",
                     message=f"Live watch of {job_name}: webcam {WEBCAM_INDEX}, one picture every {PICTURE_EVERY:.0f} s.",
                     position=0, total=0)
    cap = None
    try:
        try:
            from scaffold.capture import _grab, _open_camera, _settle
            cv2, cap = _open_camera(WEBCAM_INDEX)
        except SystemExit as exc:   # capture.py reports a missing OpenCV or camera this way
            raise RuntimeError(str(exc)) from None
        _settle(cap, 3.0)
        prev, due, failed = _live_state(), time.time(), 0
        while True:
            # Wait for the next slot, following the state for rule 4 (a person continued).
            while not _live["stop"] and _press_t0 == press_t and time.time() < due:
                prev = _auto_track(prev, _live_state())
                time.sleep(0.2)
            now = _live_state()
            prev = _auto_track(prev, now)
            if _live["stop"] or _press_t0 != press_t or now in LIVE_ENDS:   # rule 16
                break
            due = max(due + PICTURE_EVERY, time.time())
            seconds = time.time() - press_t
            name = f"p{n + 1:03d}_t{seconds:04.0f}.jpg"
            try:
                _settle(cap, 0.5)
                cv2.imwrite(str(folder / name), _grab(cv2, cap, WEBCAM_FLIP), [cv2.IMWRITE_JPEG_QUALITY, 95])
                failed = 0
            except Exception as exc:   # no picture arrived, so nothing for on_picture (rule 17)
                failed += 1
                _auto["message"] = f"The webcam gave no picture ({exc})." + \
                    (" Three in a row: check the cable and WEBCAM_INDEX in .env." if failed >= 3 else "")
                continue
            n += 1
            on_picture(Picture(name, round(seconds, 1), _live_layer()), now)
            _auto["position"] += 1
        _auto["message"] = "Live watch stopped." if _live["stop"] else f"Live watch ended ({_live_state()})."
    except Exception as exc:
        _auto["error"] = f"Live watch: {type(exc).__name__}: {exc}"
    finally:
        if cap is not None:
            cap.release()
        _live["active"] = False


def _within_root(rel: str) -> Path | None:
    path = (ROOT / rel).resolve()
    return path if path.is_file() and path.is_relative_to(ROOT.resolve()) else None


def _automate_section() -> str:
    jobs = _page_jobs()
    default_job = next((j for j in jobs if not Path(j).name.startswith("stage3_")), jobs[0] if jobs else "")
    job_options = "".join(
        f'<option value="{html.escape(j)}"{" selected" if j == default_job else ""}>{html.escape(j)}</option>' for j in jobs
    ) or '<option value="">(none sliced yet)</option>'
    answer_files = []
    if DEFAULT_ANSWERS.is_file():
        answer_files.append(DEFAULT_ANSWERS)
    answer_files += sorted(OUT_DIR.glob("*predictions*.csv"))
    answer_options = "".join(
        f'<option value="{html.escape(str(a.relative_to(ROOT)))}">{html.escape(str(a.relative_to(ROOT)))}</option>'
        for a in answer_files
    ) or '<option value="">(no saved answers found)</option>'
    try:
        pictures = [p.file for p in _read_used(DATASET_FRAMES)]
    except Exception:
        pictures = []
    picture_options = "".join(f'<option value="{html.escape(f)}">{html.escape(f)}</option>' for f in pictures) \
        or '<option value="">(dataset folder not found)</option>'
    page = """
    <div class="card">
      <b>Live watch</b>
      <div class="muted" style="flex-basis:100%">For a print sent from the Print tab: from layer 1 the webcam takes a picture every 15 s
        into out/&lt;job&gt;_frames/ and asks the detector about it (one API call each), and the tab pauses on stringing by the same rules.
        It ends with the print. Set in .env: WEBCAM_INDEX, WEBCAM_FLIP=1 for an upside-down webcam, AUTOMATE_LIVE=0 to switch it off.</div>
      <div id="live-state" style="flex-basis:100%" class="muted"></div>
      <button id="live-stop" onclick="liveStop()" style="background:#666" disabled>Stop live watch</button>
    </div>
    <div class="card">
      <b>Replay</b>
      <div class="muted" style="flex-basis:100%">Plays the saved answers back in order, 15 s apart, on the simulator in slow mode.
        It takes about 12 minutes. No API call. The Monitor tab writes its own log for the job you choose, replacing an older one for that job.</div>
      <div class="field"><label>Job for the simulator</label><select id="auto-job">__JOBS__</select></div>
      <div class="field"><label>Saved answers</label><select id="auto-answers">__ANSWERS__</select></div>
      <button id="auto-start" onclick="autoStart()">Replay</button>
      <button id="auto-stop" onclick="autoStop()" style="background:#666" disabled>Stop replay</button>
    </div>
    <div class="card">
      <b>Live check</b>
      <div class="muted" style="flex-basis:100%">Sends one picture to the real detector (one API call, costs a little). The printer gets no command.</div>
      <div class="field"><label>Picture</label><select id="auto-picture">__PICTURES__</select></div>
      <button id="auto-live-btn" onclick="autoLive()">Live check</button>
      <div id="auto-live" style="flex-basis:100%"></div>
    </div>
    <div id="auto-error"></div>
    <div class="card" id="auto-wait" style="display:none;border-color:#c98a00;background:#fff8e6"></div>
    <div class="card" id="auto-status"><div class="muted">No replay yet.</div></div>
    <div class="card">
      <table class="nums" id="auto-table">
        <thead><tr><td>picture</td><td>layer</td><td>label</td><td>confidence</td><td>printer state</td><td>action</td><td>Part 1 row</td></tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <script>
    async function autoStart() {
      const job = document.getElementById('auto-job').value;
      const answers = document.getElementById('auto-answers').value;
      const res = await fetch('/automate/replay/start?job=' + encodeURIComponent(job) + '&answers=' + encodeURIComponent(answers));
      const d = await res.json();
      if (!res.ok) { document.getElementById('auto-error').innerHTML = '<div class="error"><b>Error</b><pre></pre></div>';
                     document.querySelector('#auto-error pre').textContent = d.error; }
    }
    async function autoStop() { await fetch('/automate/replay/stop'); }
    async function liveStop() { await fetch('/automate/watch/stop'); }
    async function autoLive() {
      const box = document.getElementById('auto-live');
      box.textContent = 'Asking the detector...';
      const res = await fetch('/automate/live?picture=' + encodeURIComponent(document.getElementById('auto-picture').value));
      const d = await res.json();
      if (!res.ok) { box.innerHTML = '<div class="error"><pre></pre></div>'; box.querySelector('pre').textContent = d.error; return; }
      const fmt = a => a ? (a.label + ' at ' + Number(a.confidence).toFixed(2)) : 'no saved answer';
      box.innerHTML = '<div class="results"><div><b></b><div class="muted">live answer</div></div><div><b></b><div class="muted">saved answer</div></div><div class="muted"></div></div>';
      const cells = box.querySelectorAll('b');
      cells[0].textContent = fmt(d.live); cells[1].textContent = fmt(d.saved);
      box.querySelector('.results > .muted').textContent = d.live.note || '';
    }
    async function pollAutomate() {
      try {
        const d = await (await fetch('/automate/status')).json();
        const live = d.live;
        document.getElementById('auto-start').disabled = d.running || live.active;
        document.getElementById('auto-stop').disabled = !d.running;
        document.getElementById('live-stop').disabled = !live.active;
        document.getElementById('live-state').textContent = !live.on ? 'Off (AUTOMATE_LIVE=0 in .env).'
          : live.active ? 'Watching ' + live.job + ' with webcam ' + live.webcam + (live.flip ? ' (flipped)' : '') + '.'
          : 'Waiting for layer 1 of the next Print-tab print (webcam ' + live.webcam + (live.flip ? ', flipped' : '') + ').';
        const mem = d.memory;
        const wait = document.getElementById('auto-wait');
        wait.style.display = d.waiting ? 'block' : 'none';
        wait.textContent = d.waiting || '';
        document.getElementById('auto-status').innerHTML = '<div class="results">'
          + '<div><b>' + (d.running ? 'replay running' : live.active ? 'live watch' : 'idle') + '</b><div class="muted">Automate</div></div>'
          + '<div>' + d.position + (d.total ? ' of ' + d.total : '') + '<div class="muted">pictures handled</div></div>'
          + '<div>' + (mem.light_accepted ? 'accepted' : 'not accepted') + '<div class="muted">light stringing</div></div>'
          + '<div>' + mem.failures + '<div class="muted">failures in a row</div></div>'
          + '<div class="muted"></div></div>';
        document.querySelector('#auto-status .results > .muted').textContent = d.message || '';
        if (d.error) { document.getElementById('auto-error').innerHTML = '<div class="error"><b>Error</b><pre></pre></div>';
                       document.querySelector('#auto-error pre').textContent = d.error; }
        const body = document.querySelector('#auto-table tbody');
        body.innerHTML = '';
        d.lines.forEach(l => { const tr = document.createElement('tr');
          l.forEach(v => { const td = document.createElement('td'); td.textContent = v; tr.appendChild(td); });
          body.appendChild(tr); });
      } catch (e) { /* keep the last view on a transient error */ }
    }
    pollAutomate();
    setInterval(pollAutomate, 2000);
    </script>
    """
    return page.replace("__JOBS__", job_options).replace("__ANSWERS__", answer_options).replace("__PICTURES__", picture_options)


# ======================================================================
# Track and Passport tabs (contracts/tracking.md, contracts/passport.md).
# The record logic is in app/track.py; these send no printer command.
# ======================================================================
def _track_section() -> str:
    return """
    <div class="card">
      <b>Records</b> <span class="muted">one per part, in out/records/; click a row to see its events</span>
      <div id="track-msg" class="muted"></div>
      <table class="nums" id="track-table"><thead><tr>
        <td>part number</td><td>job</td><td>identifier</td><td>status</td><td>last event</td></tr></thead>
        <tbody></tbody></table>
    </div>
    <div class="card" id="track-events" style="display:none"></div>
    <div class="card">
      <b>Add an event</b> <span class="muted">(rule 6)</span>
      <div class="results" style="margin-top:0.6em">
        <div class="field"><label>Part</label><select id="ev-part" class="track-parts"></select></div>
        <div class="field"><label>Station</label><select id="ev-station">
          <option value="inspection">inspection</option><option value="assembly">assembly</option></select></div>
        <div class="field" style="flex:1"><label>What happened</label>
          <input id="ev-what" type="text" style="padding:0.4em" placeholder="Taken off the plate from the left; marked with one dot"></div>
        <div class="field"><label>Identifier (optional)</label>
          <select id="ev-ident"><option value=""></option><option>one dot</option><option>two dots</option>
          <option>engraved S4-01</option><option>the only cap</option></select></div>
        <button onclick="trackEvent()">Add event</button>
      </div>
    </div>
    <div class="card">
      <b>Fit an assembly</b> <span class="muted">(rule 7: the holder, such as the cap, and the parts it holds)</span>
      <div class="results" style="margin-top:0.6em">
        <div class="field"><label>Holder</label><select id="fit-holder" class="track-parts"></select></div>
        <div class="field"><label>Parts it holds</label><div id="fit-parts"></div></div>
        <button onclick="trackFit()">Fit</button>
      </div>
    </div>
    <div class="card">
      <b>Reject a part</b> <span class="muted">(rule 8: the reason is its last event)</span>
      <div class="results" style="margin-top:0.6em">
        <div class="field"><label>Part</label><select id="rej-part" class="track-parts"></select></div>
        <div class="field"><label>Station</label><select id="rej-station">
          <option value="inspection">inspection</option><option value="assembly">assembly</option></select></div>
        <div class="field" style="flex:1"><label>Reason</label>
          <input id="rej-reason" type="text" style="padding:0.4em" placeholder="stringing between the pillars"></div>
        <button onclick="trackReject()">Reject</button>
      </div>
    </div>
    <script>
    function esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
    let trackRecords = [], trackShown = '';
    function shortJob(j) { return j.split('__')[0].replace('.gcode.3mf', ''); }
    function trackLabel(r) { return r.part_number + (r.identifier ? ' (' + r.identifier + ')' : '') + ' ' + shortJob(r.job); }
    async function trackCall(url, msgId) {
      const res = await fetch(url); const d = await res.json();
      document.getElementById(msgId || 'track-msg').textContent = d.error ? 'Error: ' + d.error : 'Saved.';
      await pollTrack(); return !d.error;
    }
    function q(o) { return Object.entries(o).map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&'); }
    async function trackEvent() {
      if (await trackCall('/track/event?' + q({part: ev_val('ev-part'), station: ev_val('ev-station'),
            what: ev_val('ev-what'), identifier: ev_val('ev-ident')}))) document.getElementById('ev-what').value = '';
    }
    async function trackFit() {
      const parts = [...document.querySelectorAll('#fit-parts input:checked')].map(c => c.value);
      await trackCall('/track/fit?' + q({holder: ev_val('fit-holder'), parts: parts.join(',')}));
    }
    async function trackReject() {
      if (await trackCall('/track/reject?' + q({part: ev_val('rej-part'), station: ev_val('rej-station'),
            reason: ev_val('rej-reason')}))) document.getElementById('rej-reason').value = '';
    }
    function ev_val(id) { return document.getElementById(id).value; }
    function showEvents(pn) {
      trackShown = pn; const r = trackRecords.find(x => x.part_number === pn); const box = document.getElementById('track-events');
      if (!r) { box.style.display = 'none'; return; }
      box.style.display = 'block';
      box.innerHTML = '<b>' + esc(r.part_number) + '</b> <span class="muted">fingerprint ' + esc(r.fingerprint.slice(0, 16)) +
        '&hellip;, job ' + esc(r.job) + (r.print_folder ? ', archive ' + esc(r.print_folder) : '') +
        '</span><table class="nums">' + r.events.map(e => '<tr><td>' + esc(e.time) + '</td><td>' + esc(e.station) +
        '</td><td>' + esc(e.what) + '</td></tr>').join('') + '</table>';
    }
    async function pollTrack() {
      try {
        const d = await (await fetch('/track/records')).json();
        trackRecords = d.records;
        document.querySelector('#track-table tbody').innerHTML = trackRecords.length ? trackRecords.map(r =>
          '<tr style="cursor:pointer" onclick="showEvents(\\'' + esc(r.part_number) + '\\')"><td><b>' + esc(r.part_number) +
          '</b></td><td>' + esc(shortJob(r.job)) + '</td><td>' + esc(r.identifier) + '</td><td>' + esc(r.status) +
          '</td><td>' + esc(r.events[r.events.length - 1].what) + '</td></tr>').join('')
          : '<tr><td class="muted">No records yet. Press Print on the Print tab.</td></tr>';
        const open = trackRecords.filter(r => r.status !== 'rejected');
        document.querySelectorAll('.track-parts').forEach(sel => {
          const keep = sel.value;
          sel.innerHTML = open.map(r => '<option value="' + esc(r.part_number) + '">' + esc(trackLabel(r)) + '</option>').join('');
          if (open.some(r => r.part_number === keep)) sel.value = keep;
        });
        const box = document.getElementById('fit-parts'), ticked = new Set([...box.querySelectorAll('input:checked')].map(c => c.value));
        box.innerHTML = open.filter(r => !r.fitted_in && !r.fitted_with.length).map(r => '<label style="display:block"><input type="checkbox" value="' +
          esc(r.part_number) + '"' + (ticked.has(r.part_number) ? ' checked' : '') + '> ' + esc(trackLabel(r)) + '</label>').join('') || '<span class="muted">none free</span>';
        if (trackShown) showEvents(trackShown);
        const dl = document.getElementById('passport-list');
        if (dl) dl.innerHTML = trackRecords.map(r => '<option value="' + esc(r.part_number) + '">' + esc(trackLabel(r)) + '</option>').join('');
      } catch (e) { /* keep the last view on a transient error */ }
    }
    pollTrack();
    setInterval(pollTrack, 4000);
    </script>
    """


def _passport_section() -> str:
    return """
    <div class="card">
      <div class="results">
        <div class="field" style="flex:1"><label>Part number (from the part's identifier)</label>
          <input id="pp-part" list="passport-list" type="text" style="padding:0.4em" placeholder="3bb338ef-01"></div>
        <datalist id="passport-list"></datalist>
        <button onclick="openPassport(document.getElementById('pp-part').value)">Open passport</button>
      </div>
    </div>
    <div id="pp-card"></div>
    <div id="pp-shade" onclick="ppCloseRecord()"></div>
    <aside id="pp-drawer" aria-hidden="true" aria-label="Full record">
      <div class="pp-drawer-head">
        <b id="pp-drawer-title">Full record</b>
        <button class="pp-x" onclick="ppCloseRecord()" title="Close (Esc)">&times;</button>
      </div>
      <div id="pp-drawer-meta" class="muted"></div>
      <div id="pp-drawer-filter" class="pp-chips"></div>
      <div id="pp-drawer-body"></div>
    </aside>
    <style>
      .pp { border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: var(--card); margin-bottom: 1.2em; }
      .pp h2 { background: var(--red); color: white; margin: 0; padding: 0.7em 1em; font-size: 1.15em; }
      .pp table { width: 100%; border-collapse: collapse; }
      .pp td { padding: 0.7em 1em; border-bottom: 1px solid var(--border); vertical-align: top; }
      .pp td:first-child { font-weight: 600; width: 9em; color: var(--muted); }
      .pp .actions { padding: 1em; display: flex; gap: 0.8em; flex-wrap: wrap; align-items: center; }
      .pp button.ok { background: #2a7a2a; }
      .pp button:disabled { background: #aaa; cursor: default; }
      .pp h2 { display: flex; justify-content: space-between; align-items: center; gap: 1em; }
      .pp h2 button { background: rgba(255,255,255,0.18); border: 1px solid rgba(255,255,255,0.5); padding: 0.3em 0.9em; font-size: 0.8em; }
      .pp a.more { float: right; font-size: 0.82em; color: var(--muted); text-decoration: none; margin-left: 1em; }
      .pp a.more:hover { color: var(--red); text-decoration: underline; }
      #pp-shade { position: fixed; inset: 0; background: rgba(0,0,0,0.25); opacity: 0; pointer-events: none; transition: opacity 0.2s; z-index: 20; }
      #pp-shade.open { opacity: 1; pointer-events: auto; }
      #pp-drawer { position: fixed; top: 0; right: 0; height: 100vh; width: min(480px, 100vw); background: var(--card);
                   border-left: 1px solid var(--border); box-shadow: -4px 0 16px rgba(0,0,0,0.12); z-index: 21;
                   transform: translateX(100%); transition: transform 0.22s ease-out; display: flex; flex-direction: column; }
      #pp-drawer.open { transform: translateX(0); }
      .pp-drawer-head { display: flex; justify-content: space-between; align-items: center; padding: 1em 1.2em 0.4em; }
      .pp-drawer-head b { color: var(--red); font-size: 1.1em; }
      .pp-x { background: none; color: var(--muted); font-size: 1.6em; padding: 0 0.3em; line-height: 1; }
      #pp-drawer-meta { padding: 0 1.2em 0.6em; line-height: 1.6; word-break: break-all; }
      .pp-chips { display: flex; gap: 0.4em; flex-wrap: wrap; padding: 0 1.2em 0.8em; border-bottom: 1px solid var(--border); }
      .pp-chips button { background: var(--card); color: var(--text); border: 1px solid var(--border); padding: 0.25em 0.8em; font-size: 0.82em; border-radius: 999px; }
      .pp-chips button.on { background: var(--red); color: white; border-color: var(--red); }
      #pp-drawer-body { overflow-y: auto; padding: 0.4em 0 2em; flex: 1; }
      .pp-ev { padding: 0.6em 1.2em; border-left: 3px solid transparent; }
      .pp-ev + .pp-ev { border-top: 1px solid var(--border); }
      .pp-ev .when { font-size: 0.8em; color: var(--muted); }
      .pp-ev .where { font-size: 0.8em; font-weight: 600; color: var(--muted); }
      .pp-ev.hit { background: #fff8e6; border-left-color: #c98a00; }
      .pp-ev.dim { opacity: 0.45; }
    </style>
    <script>
    let ppShown = '';
    async function openPassport(pn) {
      pn = (pn || '').trim(); if (pn !== ppShown) ppCloseRecord();
      ppShown = pn; document.getElementById('pp-part').value = pn;
      const box = document.getElementById('pp-card');
      const d = await (await fetch('/passport/data?part=' + encodeURIComponent(pn))).json();
      if (d.error) { box.innerHTML = '<div class="card">' + esc(d.error) + '</div>'; return; }
      const asm = d.assembly.parts.length ? d.assembly.text + ' ' + d.assembly.parts.map(p =>
          '<a href="#" onclick="openPassport(\\'' + esc(p) + '\\'); return false">' + esc(p) + '</a>').join(', ') : esc(d.assembly.text);
      const data = d.print_folder ? (d.print_data.map(f => '<a href="/' + esc(encodeURI(f)) + '" target="_blank">' +
          esc(f.split('/').slice(2).join('/') || f) + '</a>').join('<br>') || 'Folder is empty') : 'Not archived';
      const dis = d.can_decide ? '' : ' disabled';
      // Each summary line can open the full record with the events behind it marked.
      const more = line => '<a href="#" class="more" onclick="ppOpenRecord(\\'' + line + '\\'); return false">details &rsaquo;</a>';
      box.innerHTML = '<div class="pp"><h2><span>Passport: ' + esc(d.part_number) + ' (' + esc(d.identifier) + ')</span>' +
        '<button onclick="ppOpenRecord(\\'all\\')">Full record &rsaquo;</button></h2><table>' +
        '<tr><td>Print</td><td>' + more('print') + esc(d.print) + '</td></tr>' +
        '<tr><td>Job</td><td>' + esc(d.job) + '</td></tr>' +
        '<tr><td>Inspection</td><td>' + more('inspection') + esc(d.inspection) + '</td></tr>' +
        '<tr><td>Assembly</td><td>' + more('assembly') + asm + '</td></tr>' +
        '<tr><td>Status</td><td>' + more('status') + '<b>' + esc(d.status) + '</b></td></tr>' +
        '<tr><td>Print data</td><td>' + data + '</td></tr></table>' +
        '<div class="actions"><button class="ok" onclick="ppDecide(\\'accept\\')"' + dis + '>Accept for production run</button>' +
        '<input id="pp-reason" type="text" style="padding:0.4em; flex:1" placeholder="reason for rejecting"' + dis + '>' +
        '<button onclick="ppDecide(\\'reject\\')"' + dis + '>Reject</button>' +
        '<span id="pp-msg" class="muted"></span></div></div>';
    }
    async function ppDecide(decision) {
      const reason = (document.getElementById('pp-reason') || {}).value || '';
      const d = await (await fetch('/passport/decide?part=' + encodeURIComponent(ppShown) + '&decision=' + decision +
                                   '&reason=' + encodeURIComponent(reason))).json();
      if (d.error) { document.getElementById('pp-msg').textContent = 'Error: ' + d.error; return; }
      await openPassport(ppShown); pollTrack();
      if (document.getElementById('pp-drawer').classList.contains('open')) ppOpenRecord(ppFocus);
    }

    // The full record, in a side drawer: every event, oldest first, read from the record
    // itself (/passport/record), so the QC manager can check any line of the summary.
    const PP_LINES = {
      all: {name: 'All', test: e => true},
      print: {name: 'Print', test: e => ['print', 'printer', 'automate', 'track'].includes(e.key)},
      inspection: {name: 'Inspection', test: e => e.key === 'inspection'},
      assembly: {name: 'Assembly', test: e => e.key === 'assembly'},
      status: {name: 'Status', test: e => e.sets_status},
    };
    let ppFocus = 'all';
    async function ppOpenRecord(line) {
      ppFocus = line || 'all';
      const r = await (await fetch('/passport/record?part=' + encodeURIComponent(ppShown))).json();
      const drawer = document.getElementById('pp-drawer');
      if (r.error) { document.getElementById('pp-drawer-body').innerHTML = '<div class="pp-ev">' + esc(r.error) + '</div>'; }
      else {
        document.getElementById('pp-drawer-title').textContent = 'Full record: ' + r.part_number;
        document.getElementById('pp-drawer-meta').innerHTML =
          'Fingerprint ' + esc(r.fingerprint) + '<br>Job ' + esc(r.job) + '<br>Part ' + esc(r.part) +
          '<br>Archive ' + (r.print_folder ? esc(r.print_folder) : 'not archived') +
          '<br>' + r.events.length + ' events &middot; <a href="/out/records/' + encodeURIComponent(r.part_number) +
          '.json" target="_blank">raw record (JSON)</a>';
        document.getElementById('pp-drawer-filter').innerHTML = Object.entries(PP_LINES).map(([k, v]) =>
          '<button class="' + (k === ppFocus ? 'on' : '') + '" onclick="ppOpenRecord(\\'' + k + '\\')">' + v.name +
          ' (' + r.events.filter(v.test).length + ')</button>').join('');
        const test = PP_LINES[ppFocus].test, focused = ppFocus !== 'all';
        document.getElementById('pp-drawer-body').innerHTML = r.events.map(e =>
          '<div class="pp-ev' + (focused ? (test(e) ? ' hit' : ' dim') : '') + '"><div class="when">' + esc(e.time) +
          '</div><div class="where">' + esc(e.station) + '</div><div>' + esc(e.what) + '</div></div>').join('')
          || '<div class="pp-ev muted">No events.</div>';
        const first = document.querySelector('#pp-drawer-body .hit');
        if (first) first.scrollIntoView({block: 'center'});
      }
      drawer.classList.add('open'); drawer.setAttribute('aria-hidden', 'false');
      document.getElementById('pp-shade').classList.add('open');
    }
    function ppCloseRecord() {
      document.getElementById('pp-drawer').classList.remove('open');
      document.getElementById('pp-drawer').setAttribute('aria-hidden', 'true');
      document.getElementById('pp-shade').classList.remove('open');
    }
    document.addEventListener('keydown', e => { if (e.key === 'Escape') ppCloseRecord(); });
    </script>
    """


def _page(parts: list[str], profiles: list[str], selected_part: str = "", selected_profile: str = "",
          layer: int = 1, num_layers: int = 0, error: str = "", picture: str = "",
          nums: dict | None = None) -> str:
    part_options = "".join(
        f'<option value="{html.escape(p)}"{" selected" if p == selected_part else ""}>{html.escape(p)}</option>'
        for p in parts
    )
    profile_options = "".join(
        f'<option value="{html.escape(p)}"{" selected" if p == selected_profile else ""}>{html.escape(p)}</option>'
        for p in profiles
    )
    nums = nums or {}
    rows = "".join(
        f"<tr><td>{html.escape(k)}</td><td><b>{html.escape(str(v))}</b></td></tr>" for k, v in nums.items()
    )
    table_html = f'<table class="nums">{rows}</table>' if rows else ""
    error_html = f'<div class="error"><b>Error</b><pre>{html.escape(error)}</pre></div>' if error else ""
    picture_html = (f'<div class="picture"><img src="/{html.escape(picture)}?t={hash(picture)}" alt="layer picture"></div>'
                     if picture and not error else "")
    slider_html = ""
    if num_layers:
        slider_html = f"""
        <div class="layer-row">
          <label for="layer-range">Layer</label>
          <input id="layer-range" type="range" name="layer" min="1" max="{num_layers}" value="{layer}"
                 oninput="document.getElementById('layer-num').value=this.value">
          <input id="layer-num" type="number" name="layer" min="1" max="{num_layers}" value="{layer}"
                 oninput="document.getElementById('layer-range').value=this.value">
          <span class="muted">of {num_layers}</span>
        </div>
        """
    who = STUDENT_NAME or "STUDENT_NAME not set in .env"
    return f"""<!doctype html>
<html>
<head>
<title>30.303 Stage 1: toolpath &middot; {html.escape(who)}</title>
<style>
  :root {{ --red: #990000; --bg: #f7f6f4; --card: #ffffff; --border: #ddd; --text: #222; --muted: #666; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; background: var(--bg);
          color: var(--text); max-width: 960px; margin: 0 auto; padding: 2em 1.5em 4em; }}
  h1 {{ color: var(--red); font-size: 1.4em; border-bottom: 2px solid var(--red); padding-bottom: 0.4em; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
           padding: 1.2em 1.4em; margin-bottom: 1.2em; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
  form.card {{ display: flex; flex-wrap: wrap; gap: 1em; align-items: flex-end; }}
  .field {{ display: flex; flex-direction: column; gap: 0.3em; }}
  .field label {{ font-size: 0.85em; color: var(--muted); font-weight: 600; }}
  select, input[type=number] {{ padding: 0.4em 0.5em; border: 1px solid var(--border); border-radius: 6px; font-size: 0.95em; }}
  button {{ background: var(--red); color: white; border: none; border-radius: 6px;
            padding: 0.55em 1.4em; font-size: 0.95em; cursor: pointer; }}
  button:hover {{ opacity: 0.9; }}
  .layer-row {{ display: flex; align-items: center; gap: 0.6em; flex-basis: 100%; }}
  .layer-row input[type=range] {{ flex: 1; }}
  .layer-row input[type=number] {{ width: 5em; }}
  .muted {{ color: var(--muted); font-size: 0.85em; }}
  .results {{ display: flex; gap: 1.4em; align-items: flex-start; flex-wrap: wrap; }}
  .picture {{ text-align: center; flex: 1 1 420px; min-width: 0; }}
  .picture img {{ max-width: 100%; border: 1px solid var(--border); border-radius: 8px; }}
  .nums-wrap {{ flex: 1 1 280px; min-width: 240px; }}
  table.nums {{ width: 100%; border-collapse: collapse; }}
  table.nums td {{ padding: 0.5em 0.7em; border-bottom: 1px solid var(--border); }}
  table.nums tr:last-child td {{ border-bottom: none; }}
  table.nums td:first-child {{ color: var(--muted); }}
  .error {{ background: #fdecea; border: 1px solid #f5c2c0; border-radius: 8px; padding: 1em; color: #900; }}
  .error pre {{ white-space: pre-wrap; font-size: 0.85em; }}
  .tabs {{ display: flex; gap: 0.5em; margin-bottom: 1em; }}
  .tabs button {{ background: var(--card); color: var(--text); border: 1px solid var(--border); }}
  .tabs button.active {{ background: var(--red); color: white; border-color: var(--red); }}
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}
</style>
</head>
<body>
<h1>30.303 Stage 1: toolpath &middot; {html.escape(who)}</h1>
<div class="tabs">
  <button id="tab-btn-slice" class="active" onclick="showTab('slice')">Slice</button>
  <button id="tab-btn-print" onclick="showTab('print')">Print</button>
  <button id="tab-btn-monitor" onclick="showTab('monitor')">Monitor</button>
  <button id="tab-btn-automate" onclick="showTab('automate')">Automate</button>
  <button id="tab-btn-track" onclick="showTab('track')">Track</button>
  <button id="tab-btn-passport" onclick="showTab('passport')">Passport</button>
</div>
<div id="tab-slice" class="tab-panel active">
<form method="get" action="/slice" class="card">
  <div class="field"><label>Part</label><select name="part">{part_options}</select></div>
  <div class="field"><label>Process profile</label><select name="profile">{profile_options}</select></div>
  <button type="submit">Slice</button>
  {slider_html}
</form>
{error_html}
{f'<div class="card results">{picture_html}<div class="nums-wrap">{table_html}</div></div>' if (picture_html or table_html) and not error else ""}
</div>
<div id="tab-print" class="tab-panel">
{_print_section()}
</div>
<div id="tab-monitor" class="tab-panel">
{_monitor_section()}
</div>
<div id="tab-automate" class="tab-panel">
{_automate_section()}
</div>
<div id="tab-track" class="tab-panel">
{_track_section()}
</div>
<div id="tab-passport" class="tab-panel">
{_passport_section()}
</div>
<script>
function showTab(name) {{
  for (const n of ['slice', 'print', 'monitor', 'automate', 'track', 'passport']) {{
    document.getElementById('tab-' + n).classList.toggle('active', name === n);
    document.getElementById('tab-btn-' + n).classList.toggle('active', name === n);
  }}
}}
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send_html(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj: dict, status: int = 200) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        data = path.read_bytes()
        self.send_response(200)
        ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".csv": "text/csv; charset=utf-8",
                 ".json": "application/json; charset=utf-8", ".slice": "application/json; charset=utf-8",
                 ".txt": "text/plain; charset=utf-8"}.get(path.suffix.lower(), "application/octet-stream")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _track_route(self, path: str, q: dict) -> None:
        """Track and Passport tabs (contracts/tracking.md, contracts/passport.md). No printer command."""
        try:
            if path == "/track/records":
                self._send_json({"records": track.list_records(), "stations": list(track.PERSON_STATIONS)})
            elif path == "/track/event":
                track.person_event(q.get("part", ""), q.get("station", ""), q.get("what", ""), q.get("identifier", ""))
                self._send_json({"ok": True})
            elif path == "/track/fit":
                track.fit(q.get("holder", ""), [p for p in q.get("parts", "").split(",") if p])
                self._send_json({"ok": True})
            elif path == "/track/reject":
                station = q.get("station", "")
                if station not in track.PERSON_STATIONS:
                    raise ValueError("Station must be inspection or assembly.")
                track.reject(q.get("part", ""), q.get("reason", ""), track.STATIONS[station])
                self._send_json({"ok": True})
            elif path == "/passport/data":
                card = track.passport(q.get("part", ""))
                if card is None:
                    raise ValueError(f"No record for {q.get('part', '').strip()}")
                self._send_json(card)
            elif path == "/passport/record":
                rec = track.full_record(q.get("part", ""))
                if rec is None:
                    raise ValueError(f"No record for {q.get('part', '').strip()}")
                self._send_json(rec)
            elif path == "/passport/decide":
                track.decide(q.get("part", ""), q.get("decision", ""), q.get("reason", ""))
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "not found"}, status=404)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)

    def do_GET(self) -> None:
        global _press_state, _press_t0, _press_job, _last_stop_was_us, _pause_from_page_t, _seen_pause, _track_pending
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/":
            self._send_html(_page(_parts(), _profiles()))
            return

        if parsed.path.startswith("/out/"):
            self._send_file(ROOT / parsed.path.lstrip("/"))
            return

        if parsed.path.startswith("/prints/"):
            # passport.md rule 6: archived files are served from prints/ only.
            path = (ROOT / unquote(parsed.path.lstrip("/"))).resolve()
            if path.is_relative_to(track.PRINTS_DIR.resolve()):
                self._send_file(path)
            else:
                self.send_response(404)
                self.end_headers()
            return

        if parsed.path.startswith("/track/") or parsed.path.startswith("/passport/"):
            self._track_route(parsed.path, {k: v[0] for k, v in qs.items()})
            return

        if parsed.path == "/slice":
            part = qs.get("part", [""])[0]
            profile = qs.get("profile", [""])[0]
            layer = int(qs.get("layer", ["1"])[0] or 1)
            try:
                stl = PARTS_DIR / part
                process = _profile_path(profile)
                out_name = f"{stl.stem}__{process.stem}"
                print(f"$ slicing {part} with {profile}")
                gcode = slice_stl(stl, MACHINE, process, FILAMENT, out_name=out_name)
                layers = parse_gcode(gcode)
                layer = max(1, min(layer, len(layers)))
                png_name = f"out/{out_name}_layer{layer}.png"
                render_layer(layers, layer - 1, ROOT / png_name,
                             title=f"{stl.stem} __ {process.stem} layer {layer}/{len(layers)} z = {layers[layer-1].z:.2f}")
                L = layers[layer - 1]
                n_ext = sum(1 for m in L.moves if m.extrude)
                n_trv = sum(1 for m in L.moves if not m.extrude)
                nums = {
                    "Number of layers": len(layers),
                    "z of the first and last layer (mm)": f"{layers[0].z:g} and {layers[-1].z:g}",
                    "Layer shown": f"{layer}, z = {L.z:g} mm",
                    "Extrusion moves in this layer": n_ext,
                    "Travel moves in this layer": n_trv,
                }
                self._send_html(_page(_parts(), _profiles(), part, profile, layer, len(layers),
                                       picture=png_name, nums=nums))
            except Exception:
                err = traceback.format_exc(limit=3)
                self._send_html(_page(_parts(), _profiles(), part, profile, error=err))
            return

        if parsed.path == "/print/connect":
            # Automate rule 11 (Part 1 row 12): while a replay runs, Connect changes nothing.
            if _auto["running"] and _printer is not None:
                self._send_json({"ok": True, "target": f"{_printer.target} (a replay is running: Connect changed nothing)"})
                return
            try:
                p = _connect_printer()
                self._send_json({"ok": True, "target": p.target})
            except (RateLimited, RuntimeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/print/start":
            job_rel = qs.get("job", [""])[0]
            try:
                if not job_rel:
                    raise RuntimeError("No job selected. Slice a part on the Slice tab first.")
                job = ROOT / job_rel
                p = _get_printer()
                prev = (_press_state, _press_t0, _press_job)
                # rule 4: the state right before Print, so a stale FINISH is not mistaken for this job.
                # The press time is set before start(), so reports that arrive while the file is
                # being sent are counted from Print too (telemetry.md rule 3).
                _press_state = p.state()
                _press_t0 = time.time()
                _press_job = job.name
                # tracking.md rule 1: the records are written once the printer reports the new
                # job (_create_records, from the report callback), not when start() returns. Set
                # before start(), since that report can arrive before start() returns.
                _track_pending = (job, _press_t0)
                try:
                    p.start(job)
                except Exception:
                    _press_state, _press_t0, _press_job = prev
                    _track_pending = None   # refused: no record
                    raise
                _last_stop_was_us = False
                _pause_from_page_t = None
                _seen_pause = False
                print(f"$ sent {job_rel} to the printer")
                self._send_json({"ok": True, "job": job.name})
            except (RateLimited, RuntimeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/print/stop":
            try:
                p = _get_printer()
                p.stop()
                _last_stop_was_us = True
                self._send_json({"ok": True})
            except (RateLimited, RuntimeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/print/pause":
            try:
                p = _get_printer()
                if p.state() != "RUNNING":
                    raise RuntimeError("Pause works only while the printer is RUNNING.")
                t = round(time.time() - _press_t0, 1) if _press_t0 else 0.0
                p.pause()  # raises RateLimited if sent too soon; then nothing is recorded below
                _pause_from_page_t = t
                _seen_pause = False
                self._send_json({"ok": True, "pressed_at": t})
            except (RateLimited, RuntimeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/print/status":
            try:
                p = _get_printer()
                t = p.telemetry()
                elapsed = round(time.time() - _press_t0, 1) if _press_t0 else 0
                t["elapsed"] = elapsed
                t["we_stopped"] = _last_stop_was_us
                t["pause_from_page_t"] = _pause_from_page_t
                t["target"] = p.target
                self._send_json(t)
            except (RateLimited, RuntimeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/monitor/status":
            # Reads module state only; sends nothing to the printer (contracts/telemetry.md).
            r = _latest_report
            self._send_json({
                "gcode_state": r.get("gcode_state") if r.get("gcode_state") != "UNKNOWN" else "not reported",
                "nozzle_temper": _round1(r.get("nozzle_temper")),
                "bed_temper": _round1(r.get("bed_temper")),
                "layer_num": r.get("layer_num"),
                "mc_percent": r.get("mc_percent"),
                "layer1_t": _layer1_t,
                "log_path": str(_log_path.relative_to(ROOT)) if _log_path else "",
                "points": _log_points,
                "state_changes": _state_changes,
            })
            return

        if parsed.path == "/automate/replay/start":
            try:
                if _auto["running"]:
                    raise RuntimeError("A replay is already running.")
                if _live["active"]:
                    raise RuntimeError("A live watch of a real print is running. Stop it first.")
                job_rel = qs.get("job", [""])[0]
                if not job_rel:
                    raise RuntimeError("No job for the simulator. Slice a part on the Slice tab first.")
                answers = _within_root(qs.get("answers", [""])[0])
                if answers is None:
                    raise RuntimeError("The saved answers file was not found inside the project.")
                if not (DATASET_FRAMES / "pictures.csv").is_file():
                    raise RuntimeError("The pictures folder data/stage3_stringing_test_frames (or AUTOMATE_FRAMES) was not found.")
                _auto["running"], _auto["stop"] = True, False
                threading.Thread(target=_replay, args=(job_rel, answers), daemon=True).start()
                self._send_json({"ok": True})
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/automate/replay/stop":
            _auto["stop"] = True   # stops the replay's loop only; it sends nothing to the printer
            self._send_json({"ok": True})
            return

        if parsed.path == "/automate/watch/stop":
            _live["stop"] = True   # rule 16: stops taking pictures only; it sends nothing to the printer
            self._send_json({"ok": True})
            return

        if parsed.path == "/automate/status":
            with _auto_lock:
                lines = list(_auto["lines"])
            m = _auto["memory"]
            # Rule 12: the waiting banner. Text only; no button, and nothing is sent to the printer.
            waiting = ""
            if m.pause_reason and _latest_report.get("gcode_state") == "PAUSE":
                why = "stringing seen" if m.pause_reason == "stringing" else "no answer for 3 pictures"
                waiting = f"Paused by Automate ({why}). Waiting for a person to continue or stop."
                if _auto["running"]:
                    waiting += " On the simulator it resumes by itself after 30 s, in place of the person."
            self._send_json({"waiting": waiting, "running": _auto["running"], "message": _auto["message"], "error": _auto["error"],
                             "live": {"on": LIVE_WATCH, "active": _live["active"], "job": _live["job"],
                                      "webcam": WEBCAM_INDEX, "flip": WEBCAM_FLIP},
                             "position": _auto["position"], "total": _auto["total"], "lines": lines,
                             "memory": {"light_accepted": m.light_accepted, "failures": m.failures}})
            return

        if parsed.path == "/automate/live":
            # Rule 8: one call to the detector. No printer function is called here.
            name = Path(qs.get("picture", [""])[0]).name
            picture = DATASET_FRAMES / name
            try:
                if not picture.is_file():
                    raise RuntimeError(f"No picture {name} in the dataset folder.")
                saved = _read_answers(DEFAULT_ANSWERS).get(name)
                live = ask_vision(picture)
                self._send_json({"picture": name, "live": {"label": live["stringing"] or "unreadable",
                                                           "confidence": live["confidence"], "note": live["note"]},
                                 "saved": {"label": saved.label, "confidence": saved.confidence} if saved else None})
            except (VisionError, RuntimeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def main() -> None:
    server = ThreadingHTTPServer(("localhost", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Serving Stage 1 UI page at {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
