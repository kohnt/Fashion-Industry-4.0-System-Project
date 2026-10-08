"""Stage 2/3 scaffold: the bounded printer wrapper.

Students only ever see this interface:

    p = get_printer()          # real Bambu over LAN, or the simulator (PRINTER_SIM=1)
    p.connect()                # may be called again; see below
    p.target                   # "Simulator" or "Printer at 192.168.x.x": show it on the tab
    page_jobs()                # the job files your page sliced, newest first
    p.start(path_to_3mf)       # upload + start; only a job from page_jobs()
    p.pause(); p.resume(); p.stop()
    p.state()                  # "IDLE" | "PREPARE" | "RUNNING" | "PAUSE" | "FINISH" | "FAILED"
    p.telemetry()              # dict with nozzle_temper, bed_temper, layer_num, mc_percent, ...
    p.on_report(callback)      # callback(dict) on every telemetry update

No raw MQTT publish, no G-code passthrough, no temperature set-points.
Commands are rate-limited.  This is the "bounded control wrapper" the audit
found missing from Generation A of Talk2Print (control.py sends raw G-code).

The real backend uses bambulabs_api exactly as the repo's send.py/control.py do.

Course update 03 made two changes, both so that a page restart costs nothing:
  - get_printer() reads the printer lines of .env each time it is called. After
    fixing a line in .env, call p.close(), then get_printer() and connect() again,
    for example from a Connect button. No restart of the page is needed.
  - start() accepts only a .gcode.3mf that slice_stl() made, recognised by the
    slice record written beside it. page_jobs() lists them, so the tab can find
    its jobs in out/ after a restart instead of remembering them.

It also says which printer you are talking to, in p.target, and the simulator
warns there when bambulabs_api is missing, because the real printer needs that
package and the simulator does not: a missing package would otherwise show up
only at the printer.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from .config import MIN_SECONDS_BETWEEN_COMMANDS, printer_settings
from .slicer import page_jobs, read_job_record  # noqa: F401  (page_jobs is for the tab)

NO_PACKAGE = ("bambulabs_api is not installed in this folder's .venv, so the real printer "
              "cannot connect. Ask your agent to install it, then press Connect.")


def bambulabs_installed() -> bool:
    """True when the package the real printer needs can be imported."""
    import importlib.util
    return importlib.util.find_spec("bambulabs_api") is not None


ALLOWED_STATES = ("IDLE", "PREPARE", "RUNNING", "PAUSE", "FINISH", "FAILED", "UNKNOWN")


class RateLimited(RuntimeError):
    pass


def check_job(job: str | Path) -> Path:
    """Raise unless job is a .gcode.3mf that this page sliced (contracts/printer.md, rule 3)."""
    job = Path(job)
    if job.name.endswith(".gcode") or not job.name.endswith(".gcode.3mf"):
        raise ValueError(f"{job.name} is not a job file. Send the .gcode.3mf your Slice tab made; "
                         "the printer ignores a .gcode without saying so.")
    if not job.is_file():
        raise ValueError(f"{job.name} is not in out/. Slice the part on the Slice tab first.")
    if read_job_record(job) is None:
        raise ValueError(f"{job.name} was not sliced by your page, or has changed since. "
                         "Slice it again on the Slice tab, then send it.")
    return job


class _Bounded:
    """Common rate limiting and callback plumbing for both backends."""

    def __init__(self) -> None:
        self._last_cmd = 0.0
        self._callbacks: list[Callable[[dict], None]] = []
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self.target = ""  # which printer this is, in plain words, for the tab to show

    def close(self) -> None:
        """Stop reading this printer. Call it before get_printer() gives you a new one."""
        self._closed.set()

    def _gate(self, name: str) -> None:
        now = time.time()
        with self._lock:
            if now - self._last_cmd < MIN_SECONDS_BETWEEN_COMMANDS:
                raise RateLimited(f"{name}: wait {MIN_SECONDS_BETWEEN_COMMANDS - (now - self._last_cmd):.1f}s")
            self._last_cmd = now

    def on_report(self, cb: Callable[[dict], None]) -> None:
        self._callbacks.append(cb)

    def _emit(self, report: dict) -> None:
        for cb in list(self._callbacks):
            try:
                cb(report)
            except Exception as exc:  # a bad student callback must not kill telemetry
                print(f"[printer] callback error: {exc}")


# ---------------------------------------------------------------- real backend
class BambuPrinter(_Bounded):
    def __init__(self, settings: dict | None = None) -> None:
        super().__init__()
        try:
            import bambulabs_api as bl  # pip install bambulabs_api
        except ImportError:
            raise RuntimeError(NO_PACKAGE) from None
        s = settings or printer_settings()
        if not (s["ip"] and s["serial"] and s["access_code"]):
            raise RuntimeError("PRINTER_IP / PRINTER_SERIAL / PRINTER_ACCESS_CODE missing in .env")
        self._p = bl.Printer(s["ip"], s["access_code"], s["serial"])
        self._poll = None
        self.target = f"Printer at {s['ip']}"

    def connect(self, timeout: float = 10.0) -> None:
        self._p.connect()
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self._p.mqtt_client_connected():
                    break
            except Exception:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Printer not reachable: check IP, Wi-Fi, Developer Mode")
        if self._poll is None:
            self._poll = threading.Thread(target=self._poll_loop, daemon=True); self._poll.start()

    def close(self) -> None:
        super().close()
        try:
            disconnect = getattr(self._p, "disconnect", None)
            if disconnect:
                disconnect()
        except Exception:
            pass

    def _poll_loop(self) -> None:
        while not self._closed.is_set():
            try:
                self._emit(self.telemetry())
            except Exception:
                pass
            time.sleep(2.0)

    def state(self) -> str:
        s = str(self._p.get_state() or "UNKNOWN").upper()
        for k in ALLOWED_STATES:
            if k in s:
                return k
        return "UNKNOWN"

    def telemetry(self) -> dict:
        def safe(fn, default=None):
            try:
                return fn()
            except Exception:
                return default
        return {
            "t": time.time(),
            "gcode_state": self.state(),
            "nozzle_temper": safe(self._p.get_nozzle_temperature),
            "bed_temper": safe(self._p.get_bed_temperature),
            "layer_num": safe(self._p.current_layer_num),
            "mc_percent": safe(self._p.get_percentage),
            "mc_remaining_time": safe(self._p.get_time),
            "cooling_fan_speed": safe(self._p.get_fan_speed) if hasattr(self._p, "get_fan_speed") else None,
        }

    def start(self, threemf: str | Path, plate: int = 1) -> bool:
        threemf = check_job(threemf)
        self._gate("start")
        if self.state() in ("RUNNING", "PAUSE", "PREPARE"):
            raise RuntimeError("Printer busy; refusing to start another job")
        with open(threemf, "rb") as f:
            self._p.upload_file(f, filename=threemf.name)
        return bool(self._p.start_print(threemf.name, plate, use_ams=False, flow_calibration=False))

    def pause(self) -> None:
        self._gate("pause"); self._p.pause_print()

    def resume(self) -> None:
        self._gate("resume"); self._p.resume_print()

    def stop(self) -> None:
        self._gate("stop"); self._p.stop_print()


# ------------------------------------------------------------ simulated backend
class FakePrinter(_Bounded):
    """A printer that behaves like a Bambu report stream, in fast-forward.

    A 'print' takes `duration` seconds: heat-up ramp, then layers, then FINISH.
    Pause freezes progress. Use it to run every stage without hardware.

    Once in each print it pauses itself, as the real machine does when it asks
    on its own screen for the filament purge to be confirmed. It goes back to
    RUNNING on its own after `check_for` seconds; the real machine waits until
    someone presses OK on its screen. A tab that stops watching on PAUSE, or
    shows it as a fault, fails here before it fails on the printer.

    A pause you ask for with pause() behaves as the machine did on 25 September
    (course update 03): the nozzle cools while paused, and after the resume the
    state reads RUNNING while the nozzle heats up again, with the layer number
    unchanged until printing starts again. The simulator has no screen to press
    resume on, so it resumes by itself `resume_after` seconds after your pause,
    as if someone at the printer had pressed it.

    After stop() the reading is held where the print stopped, as the machine
    holds its last report; only the state changes, to FAILED.

    For the first two seconds after start() the layer number and per cent are
    still the previous job's, as on the machine: its first PREPARE report on
    25 September carried layer 75 and 100 per cent from the print before.

    PRINTER_SIM_FAST=1 in .env runs a short job, about 25 seconds from Print to
    FINISH instead of about 75, with the same states in the same order. Use it
    while you are testing a page; leave it at 0 otherwise.
    """

    FAST = dict(duration=20.0, layers=20, check_at=10.0, check_for=4.0, resume_after=8.0)
    PRINT_TEMP = 220.0      # nozzle while printing, °C
    COOL_TAU = 40.0         # s; fast-forward: the machine took about 130 s to fall from 240 to 124 °C
    REHEAT_RATE = 3.2       # °C per second, as measured on the machine

    def __init__(self, duration: float | None = None, layers: int | None = None,
                 check_at: float | None = None, check_for: float | None = None,
                 resume_after: float | None = None) -> None:
        super().__init__()
        slow = dict(duration=60.0, layers=60, check_at=18.0, check_for=15.0, resume_after=30.0)
        d = dict(self.FAST if printer_settings()["sim_fast"] else slow)
        for k, v in (("duration", duration), ("layers", layers), ("check_at", check_at),
                     ("check_for", check_for), ("resume_after", resume_after)):
            if v is not None:
                d[k] = v
        self.duration, self.layers = d["duration"], d["layers"]
        # The check comes after the heat-up, so RUNNING is seen before PAUSE.
        self.check_at, self.check_for = max(d["check_at"], 10.0), d["check_for"]
        self.resume_after = d["resume_after"]
        self._state = "IDLE"; self._t0 = 0.0; self._paused_at = 0.0; self._paused_total = 0.0
        self._checked = False; self._in_check = False
        self._user_pause = False; self._pause_temp = self.PRINT_TEMP; self._cool_at = 0.0
        self._reheating = False; self._reheat_at = 0.0; self._reheat_from = self.PRINT_TEMP
        self._held: dict | None = None
        self._prev = (0, 0); self._stale_until = 0.0
        self._job = ""
        self.target = "Simulator" if bambulabs_installed() else \
            "Simulator (bambulabs_api not installed: the real printer will not connect)"
        threading.Thread(target=self._loop, daemon=True).start()

    def connect(self, timeout: float = 0) -> None:
        print("[sim] connected to fake printer")

    def _frozen(self) -> bool:
        return self._state == "PAUSE" or self._reheating

    def _elapsed(self) -> float:
        if self._frozen():
            return self._paused_at - self._t0 - self._paused_total
        return time.time() - self._t0 - self._paused_total

    def state(self) -> str:
        return self._state

    def _cooled(self) -> float:
        import math
        return 25 + (self._pause_temp - 25) * math.exp(-(time.time() - self._cool_at) / self.COOL_TAU)

    def telemetry(self) -> dict:
        import math, random
        if self._state == "FAILED" and self._held is not None:
            return {**self._held, "t": time.time(), "gcode_state": "FAILED"}
        # At FINISH the reading is held at the end of the job, as the machine holds
        # its last report, so a finished print does not read as one that never ran.
        e = 0.0 if self._state == "IDLE" else min(self._elapsed(), self.duration)
        heat = min(1.0, e / 8.0)
        frac = 0.0 if e < 8 else min(1.0, (e - 8) / max(1.0, self.duration - 8))
        layer = self.layers if self._state == "FINISH" else int(frac * self.layers)
        nozzle = 25 + heat * (self.PRINT_TEMP - 25) + (random.uniform(-0.6, 0.6) if e > 8 else 0)
        if e > 8 and abs((e - 8) % (max(1.0, (self.duration - 8) / self.layers)) ) < 0.4:
            nozzle -= 3.0  # dip at layer change
        if self._state == "PAUSE" and self._user_pause:
            nozzle = self._cooled()
        elif self._reheating:
            nozzle = min(self.PRINT_TEMP, self._reheat_from + self.REHEAT_RATE * (time.time() - self._reheat_at))
        if self._state == "IDLE":
            nozzle, bed = 25.0 + 0.3 * math.sin(time.time()), 25.0
        else:
            bed = 25 + heat * 35
        percent = int(frac * 100)
        if self._state == "PREPARE" and time.time() < self._stale_until:
            layer, percent = self._prev   # the machine reports the old job's layer at first
        return {"t": time.time(), "gcode_state": self._state, "nozzle_temper": round(nozzle, 1),
                "bed_temper": round(bed, 1), "layer_num": layer, "mc_percent": percent,
                "mc_remaining_time": int((1 - frac) * self.duration / 60), "cooling_fan_speed": 255 if layer > 2 else 0,
                "subtask_name": self._job}

    def _begin_reheat(self) -> None:
        """Back to RUNNING after a pause you asked for: heat up first, then print."""
        self._reheat_from = self._cooled(); self._reheat_at = time.time()
        self._user_pause = False; self._reheating = True; self._state = "RUNNING"

    def _loop(self) -> None:
        while not self._closed.is_set():
            if self._state == "PREPARE" and self._elapsed() > 8:
                self._state = "RUNNING"
            if (self._state == "RUNNING" and not self._reheating and not self._checked
                    and self._elapsed() >= self.check_at):
                self._checked = True; self._in_check = True
                self._paused_at = time.time(); self._state = "PAUSE"
                print("[sim] paused: the screen is asking for the filament purge to be confirmed")
            if (self._state == "PAUSE" and self._in_check
                    and time.time() - self._paused_at >= self.check_for):
                self._paused_total += time.time() - self._paused_at
                self._in_check = False
                self._state = "RUNNING"; print("[sim] the check was confirmed; printing again")
            if (self._state == "PAUSE" and self._user_pause
                    and time.time() - self._cool_at >= self.resume_after):
                self._begin_reheat()
                print("[sim] resumed, as if someone pressed resume on the printer's screen; heating up again")
            if self._reheating and self._reheat_from + self.REHEAT_RATE * (time.time() - self._reheat_at) >= self.PRINT_TEMP:
                self._paused_total += time.time() - self._paused_at
                self._reheating = False; print("[sim] back at printing temperature; printing again")
            if self._state == "RUNNING" and not self._reheating and self._elapsed() >= self.duration:
                self._state = "FINISH"
            self._emit(self.telemetry()); time.sleep(1.0)

    def start(self, threemf: str | Path, plate: int = 1) -> bool:
        threemf = check_job(threemf)
        self._gate("start")
        if self._state in ("RUNNING", "PAUSE", "PREPARE"):
            raise RuntimeError("Printer busy; refusing to start another job")
        last = self.telemetry()
        self._prev = (last["layer_num"], last["mc_percent"]); self._stale_until = time.time() + 2.0
        self._job = threemf.name; self._t0 = time.time(); self._paused_total = 0.0
        self._checked = False; self._in_check = False
        self._user_pause = False; self._reheating = False; self._held = None
        self._state = "PREPARE"; print(f"[sim] job {self._job} started"); return True

    def pause(self) -> None:
        self._gate("pause")
        if self._state == "RUNNING":
            self._pause_temp = self.telemetry()["nozzle_temper"]
            if not self._reheating:          # a pause during the reheat keeps progress frozen
                self._paused_at = time.time()
            self._cool_at = time.time(); self._reheating = False
            self._in_check = False; self._user_pause = True
            self._state = "PAUSE"; print("[sim] paused")

    def resume(self) -> None:
        self._gate("resume")
        if self._state == "PAUSE" and self._user_pause:
            self._begin_reheat(); print("[sim] resumed; heating up again")
        elif self._state == "PAUSE":
            self._paused_total += time.time() - self._paused_at; self._in_check = False
            self._state = "RUNNING"; print("[sim] resumed")

    def stop(self) -> None:
        self._gate("stop")
        if self._state in ("PREPARE", "RUNNING", "PAUSE"):
            self._held = self.telemetry()
        self._user_pause = False; self._reheating = False
        self._state = "FAILED"; print("[sim] stopped")


def get_printer(**sim_kwargs):
    """A printer from the .env lines as they are now: the simulator if PRINTER_SIM=1."""
    s = printer_settings()
    return FakePrinter(**sim_kwargs) if s["sim"] else BambuPrinter(s)
