"""Stage 3: save a webcam picture every 15 seconds while a print runs.

Run from the top of your project folder, with the folder's .venv:

    python -m scaffold.capture --aim --webcam 0        live view to aim and focus; s saves, q quits
    python -m scaffold.capture --webcam 0              capture during a print
    python -m scaffold.capture --layers                add the layer to each picture afterwards

Capture opens no printer connection, so it works whether or not your Monitor tab works yet.
It opens the webcam, then waits: press Enter at the moment you press Print. From then on it
saves one picture every 15 seconds into out/<job>_frames/, named by the seconds since Print
(p007_t0090.jpg is picture 7, taken 90 s after Print). Press Ctrl-C when the print has finished.

The layer is added afterwards (--layers, and the labelling page and classify_all do it for you): each
picture is matched against your Monitor tab's log, out/<job>_telemetry.csv, if one exists for
this print. The result is out/<job>_frames/pictures.csv. Pictures from before layer 1, and from
the last layer on (the last layer takes a few seconds, then the end routine moves the bed away), are
set aside as not used. With no log, every picture is used and the layer is blank.

Options:
    --webcam N     the camera number: 0 is usually the laptop's own camera, 1 an added webcam
    --flip         turn the pictures the right way up if the webcam is mounted upside down
    --job NAME     the job's name (default stage3_stringing_test, the Week 3 job)
    --every S      seconds between pictures (default 15)
    --minutes M    stop by itself after M minutes (default 40)

Needs OpenCV (opencv-python) in the project's .venv; update 03's README gives the install step.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from .config import OUT

JOB = "stage3_stringing_test"
PICTURES_CSV = "pictures.csv"
COLUMNS = ["picture file", "seconds since Print (s)", "layer", "used"]


def frames_dir(job: str) -> Path:
    return OUT / f"{job}_frames"


def picture_seconds(path: Path) -> int | None:
    """p007_t0090.jpg -> 90. None for a file not named by this script."""
    part = path.stem.rsplit("_t", 1)
    return int(part[1]) if len(part) == 2 and part[1].isdigit() else None


def _open_camera(index: int):
    try:
        import cv2
    except ImportError:
        raise SystemExit("OpenCV is missing from this folder's .venv. Update 03's README, Lab 3 section, "
                         "gives the install command.") from None
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise SystemExit(f"No camera at number {index}. Try another number, for example --webcam 1. On a Mac, "
                         "also check System Settings, Privacy & Security, Camera: the app you run this from "
                         "(Terminal or Codex) must be switched on.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    return cv2, cap


def _settle(cap, seconds: float) -> None:
    """Read and drop frames so that focus and exposure catch up and no old frame is saved."""
    end = time.time() + seconds
    while time.time() < end:
        cap.grab()


def _grab(cv2, cap, flip: bool):
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError("the webcam returned no picture")
    return cv2.rotate(frame, cv2.ROTATE_180) if flip else frame


def aim(index: int, flip: bool) -> None:
    """Live window with a box for the pillars and a sharpness number (higher is sharper)."""
    cv2, cap = _open_camera(index)
    box, best = 0.30, 0.0
    snap = OUT / f"webcam{index}_snap.jpg"
    title = f"Webcam {index}: s save, f flip, b/n box size, q quit"
    print("A window shows the webcam. Put the gap between the two pillars inside the box and adjust\n"
          "until the sharpness number stops rising. Keys, with the window selected: s save a picture,\n"
          "f flip, b bigger box, n smaller box, q quit.")
    while True:
        frame = _grab(cv2, cap, flip)
        h, w = frame.shape[:2]
        bw, bh = int(w * box), int(h * box)
        x0, y0 = (w - bw) // 2, (h - bh) // 2
        gray = cv2.cvtColor(frame[y0:y0 + bh, x0:x0 + bw], cv2.COLOR_BGR2GRAY)
        sharp = cv2.Laplacian(gray, cv2.CV_64F).var()
        best = max(best, sharp)
        view = frame.copy()
        cv2.rectangle(view, (x0, y0), (x0 + bw, y0 + bh), (0, 0, 255), 3)
        cv2.putText(view, f"sharpness {sharp:5.0f}   best so far {best:5.0f}", (x0, max(40, y0 - 15)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 3)
        cv2.imshow(title, cv2.resize(view, (w // 2, h // 2)))
        k = cv2.waitKey(30) & 0xFF
        if k == ord("q"):
            break
        if k == ord("f"):
            flip = not flip
        if k == ord("b"):
            box = min(0.9, box + 0.05)
        if k == ord("n"):
            box = max(0.1, box - 0.05)
        if k == ord("s"):
            cv2.imwrite(str(snap), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            print(f"Saved out/{snap.name} (sharpness {sharp:.0f}{', flipped: add --flip when you capture' if flip else ''})")
    cap.release()
    cv2.destroyAllWindows()


def _live_layer(log: Path, since: float) -> str:
    """The layer in the last line of the Monitor tab's log, if it was written during this capture."""
    try:
        if not log.is_file() or log.stat().st_mtime < since:
            return ""
        rows = read_log(log)
        return f"  layer {rows[-1]['layer']}" if rows and rows[-1]["layer"] != "" else ""
    except Exception:
        return ""


def capture(index: int, flip: bool, job: str, every: float, minutes: float) -> None:
    folder = frames_dir(job)
    old = sorted(folder.glob("*.jpg")) if folder.is_dir() else []
    if old:
        raise SystemExit(f"out/{folder.name} already has {len(old)} pictures from an earlier capture. Rename that "
                         f"folder (for example to {folder.name}_old), or use --job with another name, then try again.")
    folder.mkdir(parents=True, exist_ok=True)
    cv2, cap = _open_camera(index)
    _settle(cap, 3.0)
    _grab(cv2, cap, flip)  # proves the camera gives pictures before the print starts
    log = OUT / f"{job}_telemetry.csv"
    input(f"Camera {index} is ready. Press Enter at the moment you press Print. ")
    t0 = time.time()
    print(f"Saving one picture every {every:.0f} s into out/{folder.name}/. Press Ctrl-C when the print has finished.")
    n, failures = 0, 0
    try:
        while time.time() - t0 < minutes * 60:
            n += 1
            due = t0 + (n - 1) * every
            if due > time.time():
                time.sleep(due - time.time())
            _settle(cap, 0.5)
            seconds = time.time() - t0
            name = f"p{n:03d}_t{seconds:04.0f}.jpg"
            try:
                cv2.imwrite(str(folder / name), _grab(cv2, cap, flip), [cv2.IMWRITE_JPEG_QUALITY, 95])
                print(f"{seconds:6.0f} s  {name}{_live_layer(log, t0)}")
                failures = 0
            except Exception as exc:
                failures += 1
                print(f"{seconds:6.0f} s  no picture: {exc}")
                if failures == 3:
                    print("Three pictures in a row failed. Check the webcam cable and the camera number.")
        print(f"Stopped by itself after {minutes:.0f} minutes.")
    except KeyboardInterrupt:
        print("\nStopped. The print was not touched.")
    cap.release()
    saved = len(list(folder.glob("*.jpg")))
    print(f"Saved {saved} pictures in out/{folder.name}/.")
    if saved:
        match_layers(job)


def read_log(log: Path) -> list[dict]:
    """The Monitor tab's log (telemetry.md rule 3) as rows of seconds, state and layer.

    Columns are found by their names (the one naming seconds, the state, the layer), so a log whose
    headings are worded a little differently still works."""
    with log.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return []
    head = [h.strip().lower() for h in rows[0]]
    def col(word: str) -> int | None:
        return next((i for i, h in enumerate(head) if word in h), None)
    i_s, i_state, i_layer = col("second"), col("state"), col("layer")
    if i_s is None or i_layer is None:
        raise ValueError("its first line does not name a seconds column and a layer column")
    out = []
    for r in rows[1:]:
        try:
            s = float(r[i_s])
        except (ValueError, IndexError):
            continue
        layer = r[i_layer].strip() if i_layer < len(r) else ""
        state = r[i_state].strip() if i_state is not None and i_state < len(r) else ""
        out.append({"seconds": s, "state": state, "layer": layer})
    return out


def match_layers(job: str = JOB, quiet: bool = False) -> list[dict]:
    """Give each picture the layer from the log and decide whether it is used. Writes pictures.csv."""
    folder = frames_dir(job)
    pics = sorted((p for p in folder.glob("*.jpg") if picture_seconds(p) is not None), key=picture_seconds)
    if not pics:
        raise SystemExit(f"No pictures in out/{folder.name}/. Capture first (update 03 README, step 2).")
    log = OUT / f"{job}_telemetry.csv"
    rows, why = [], ""
    if not log.is_file():
        why = f"no log out/{log.name}"
    elif log.stat().st_mtime < pics[0].stat().st_mtime - 600:  # 10 min slack: copying files can reset their times
        why = f"out/{log.name} was last written before the first picture, so it belongs to an earlier print"
    else:
        try:
            rows = read_log(log)
            if not rows:
                why = f"out/{log.name} has no lines"
        except ValueError as exc:
            why = f"out/{log.name} could not be read: {exc}"
    table = []
    last = rows[-1] if rows else {}
    finished = last.get("state") == "FINISH"
    top = max((int(r["layer"]) for r in rows if r["layer"].isdigit()), default=None)
    for p in pics:
        s = picture_seconds(p)
        if not rows:
            table.append({"picture file": p.name, "seconds since Print (s)": s, "layer": "", "used": "yes"})
            continue
        before = [r for r in rows if r["seconds"] <= s]
        if not before:
            layer, used = "", "no: before layer 1"
        elif s > last["seconds"] and last["state"] in ("FINISH", "FAILED"):
            layer, used = last["layer"], "no: after the print ended"
        else:
            layer = before[-1]["layer"]
            used = "yes" if layer.isdigit() and int(layer) >= 1 else "no: before layer 1"
            # A finished print's last layer takes a few seconds; after it the end routine moves the bed away.
            if used == "yes" and finished and int(layer) == top:
                used = "no: the print was ending"
        table.append({"picture file": p.name, "seconds since Print (s)": s, "layer": layer, "used": used})
    with (folder / PICTURES_CSV).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(table)
    used = sum(1 for t in table if t["used"] == "yes")
    if not quiet:
        if why:
            print(f"Layers left blank ({why}). All {len(table)} pictures are used.")
        else:
            print(f"Layers matched from out/{log.name}: {used} of {len(table)} pictures are from layer 1 "
                  "until the print ended and are used.")
        print(f"Written: out/{folder.name}/{PICTURES_CSV}")
    return table


def used_pictures(job: str = JOB) -> list[dict]:
    return [t for t in match_layers(job, quiet=True) if t["used"] == "yes"]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.capture", description="Save webcam pictures during a print.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--aim", action="store_true", help="live view for aiming and focusing")
    mode.add_argument("--layers", action="store_true", help="add the layer to each picture from the Monitor tab's log")
    ap.add_argument("--webcam", type=int, default=0)
    ap.add_argument("--flip", action="store_true")
    ap.add_argument("--job", default=JOB)
    ap.add_argument("--every", type=float, default=15.0)
    ap.add_argument("--minutes", type=float, default=40.0)
    a = ap.parse_args(argv)
    if a.aim:
        aim(a.webcam, a.flip)
    elif a.layers:
        match_layers(a.job)
    else:
        capture(a.webcam, a.flip, a.job, a.every, a.minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
