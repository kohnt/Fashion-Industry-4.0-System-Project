"""Stage 3: classify every picture of your print with the detector, then line up the predictions for the evaluation spreadsheet.

Run from the top of your project folder, with the folder's .venv:

    python -m scaffold.classify_all             every picture from layer 1 on, not yet predicted
    python -m scaffold.classify_all --limit 5   at most 5 new pictures (a cheap first try)

It sends each picture used for evaluation (layer 1 until the print ended, see scaffold/capture.py)
to scaffold/vision.py and writes the predictions to out/<job>_frames/predictions_<model>.csv. Pictures
the same model has already predicted are skipped, so running it again costs nothing for them.
Each call costs money from your team's API key, and the key is never printed.

If the team's agreed labels exist (marks_team.csv, from python -m scaffold.mark --who team), it
also writes out/<job>_frames/scoring_<model>.csv with the same columns, in the same order, as the
Pictures sheet of the Week 3B evaluation spreadsheet, so you can paste it into a copy of that sheet
(update 03 README, step 8).
Use --marks with a name to line up someone else's labels instead.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from .capture import JOB, frames_dir, used_pictures
from .mark import marks_file, read_marks
from .vision import DEFAULT_MODEL, VisionError, ask_vision

PREDICTION_COLUMNS = ["Picture file", "Layer", "Detector's prediction", "Detector's confidence", "Detector's note", "Detector"]
# Columns A to H of the Pictures sheet of "30.303 Week 3B - evaluation spreadsheet.xlsx".
SCORING_COLUMNS = ["No.", "Picture file", "Print", "Layer", "Detector's prediction", "Detector's confidence",
                   "Team's label", "Team's note"]


def run(job: str, limit: int | None, marks_by: str) -> int:
    folder = frames_dir(job)
    model = os.environ.get("OPENAI_VISION_MODEL", DEFAULT_MODEL)
    pics = used_pictures(job)
    out = folder / f"predictions_{model}.csv"
    done = {}
    if out.is_file():
        with out.open(newline="", encoding="utf-8") as f:
            done = {r["Picture file"]: r for r in csv.DictReader(f) if r["Detector's prediction"]}
    todo = [p for p in pics if p["picture file"] not in done]
    if limit is not None:
        todo = todo[:limit]
    print(f"Detector: {model}. {len(pics)} pictures from layer 1 on; {len(pics) - len(done)} not yet predicted; "
          f"asking about {len(todo)} now.")
    stopped = 0
    for p in todo:
        try:
            a = ask_vision(folder / p["picture file"])
        except VisionError as exc:
            print(f"Stopped at {p['picture file']}: {exc}")
            stopped = 1
            break
        done[p["picture file"]] = {"Picture file": p["picture file"], "Layer": p["layer"],
                                   "Detector's prediction": a["stringing"] or "", "Detector's confidence": a["confidence"],
                                   "Detector's note": a["note"], "Detector": model}
        layer = f"layer {p['layer']}" if p["layer"] != "" else f"{p['seconds since Print (s)']} s"
        print(f"  {p['picture file']}  {layer:>9}  {a['stringing'] or 'unreadable':>10}  {a['confidence']:.2f}  {a['note']}")
        _write(out, pics, done, PREDICTION_COLUMNS)  # after every prediction, so an interrupted run loses nothing
    _write(out, pics, done, PREDICTION_COLUMNS)
    predicted = sum(1 for p in pics if p["picture file"] in done)
    print(f"Predictions: {predicted} of {len(pics)} pictures, in out/{folder.name}/{out.name}")

    mfile = marks_file(job, marks_by)
    marks = read_marks(mfile)
    if not marks:
        print(f"No labels in out/{folder.name}/{mfile.name} yet, so no file for the evaluation spreadsheet. Label with: .venv/bin/python "
              f"-m scaffold.mark --who {marks_by}")
        return stopped
    score = folder / f"scoring_{model}.csv"
    with score.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(SCORING_COLUMNS)
        for n, p in enumerate(pics, 1):
            a, m = done.get(p["picture file"], {}), marks.get(p["picture file"], {})
            conf = a.get("Detector's confidence", "")
            w.writerow([n, p["picture file"], "our print", p["layer"], a.get("Detector's prediction", ""),
                        float(conf) if conf != "" else "", m.get("mark", ""), m.get("note", "")])
    print(f"File for the evaluation spreadsheet: out/{folder.name}/{score.name} (labels from {mfile.name}). Paste its rows, without the heading"
          " line, into a copy of the Pictures sheet (update 03 README, step 8).")
    return stopped


def _write(path: Path, pics: list[dict], done: dict, columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(done[p["picture file"]] for p in pics if p["picture file"] in done)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.classify_all", description="Classify every picture of your print with the detector.")
    ap.add_argument("--job", default=JOB)
    ap.add_argument("--limit", type=int, default=None, help="ask about at most this many new pictures")
    ap.add_argument("--marks", default="team", help="whose labels to line up: team (default) or a name")
    a = ap.parse_args(argv)
    return run(a.job, a.limit, a.marks)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
