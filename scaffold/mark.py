"""Stage 3: label your own pictures as none, light or heavy stringing.

Run from the top of your project folder, with the folder's .venv:

    python -m scaffold.mark                 label the pictures in out/stage3_stringing_test_frames/
    python -m scaffold.mark --who team      the team's agreed labels, in a file of their own

It opens a page in your browser showing one picture at a time. Your labels are saved after every
click into out/<job>_frames/marks_<your name>.csv (or marks_team.csv with --who team), so you can
close the page and carry on later. Press Ctrl-C in the terminal when you have finished.

Only the pictures used for evaluation are shown: from layer 1 until the print ended, as decided from
the Monitor tab's log (see scaffold/capture.py). The page runs on this computer only; nothing is sent.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .capture import JOB, frames_dir, used_pictures
from .config import STUDENT_NAME

PAGE = Path(__file__).with_name("mark.html")
MARKS = ("none", "light", "heavy", "can't tell")
COLUMNS = ["picture file", "seconds since Print (s)", "layer", "label", "note", "labelled by"]   # the file says label; the code keeps "mark" inside


def marks_file(job: str, who: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", who.lower()).strip("_") or "noname"
    return frames_dir(job) / f"marks_{slug}.csv"


def read_marks(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        rows = {}
        for r in csv.DictReader(f):
            r["mark"] = r.get("label", r.get("mark", ""))   # files written before 30 Sep 2026 said "mark"
            rows[r["picture file"]] = r
        return rows


def write_marks(path: Path, items: list[dict], marks: dict[str, dict], who: str) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for it in items:
            m = marks.get(it["picture file"], {})
            w.writerow({"picture file": it["picture file"], "seconds since Print (s)": it["seconds since Print (s)"],
                        "layer": it["layer"], "label": m.get("mark", ""), "note": m.get("note", ""), "labelled by": who})
    tmp.replace(path)


def serve(job: str, who: str, open_browser: bool = True, port: int = 0) -> ThreadingHTTPServer:
    folder = frames_dir(job)
    items = used_pictures(job)
    if not items:
        raise SystemExit(f"No pictures from layer 1 onwards in out/{folder.name}/ to label.")
    out = marks_file(job, who)
    marks = read_marks(out)
    lock = threading.Lock()
    names = {it["picture file"] for it in items}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/":
                data = {"job": job, "who": who, "file": f"out/{folder.name}/{out.name}", "items": items,
                        "marks": {k: {"mark": v.get("mark", ""), "note": v.get("note", "")} for k, v in marks.items()}}
                html = PAGE.read_text(encoding="utf-8").replace("/*DATA*/null", json.dumps(data))
                self._send(200, html.encode(), "text/html; charset=utf-8")
            elif self.path.startswith("/pic/") and self.path[5:] in names:
                self._send(200, (folder / self.path[5:]).read_bytes(), "image/jpeg")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if self.path != "/save":
                return self._send(404, b"not found", "text/plain")
            try:
                got = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
                with lock:
                    for name, m in got.items():
                        if name in names and m.get("mark", "") in MARKS + ("",):
                            marks[name] = {"mark": m.get("mark", ""), "note": str(m.get("note", ""))[:300]}
                    write_marks(out, items, marks, who)
                done = sum(1 for it in items if marks.get(it["picture file"], {}).get("mark"))
                self._send(200, json.dumps({"saved": done}).encode(), "application/json")
            except Exception as exc:
                self._send(500, str(exc).encode(), "text/plain")

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Labelling {len(items)} pictures as {who}. Labels are saved to out/{folder.name}/{out.name} after every click.")
    print(f"The page is at {url} . Press Ctrl-C here when you have finished.")
    if open_browser:
        webbrowser.open(url)
    return httpd


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.mark", description="Label your own pictures.")
    ap.add_argument("--job", default=JOB)
    ap.add_argument("--who", default=STUDENT_NAME or "", help="whose labels: your name (from .env) or team")
    a = ap.parse_args(argv)
    if not a.who:
        raise SystemExit("Your name is not in .env (STUDENT_NAME=). Put it there, or add --who with your name.")
    httpd = serve(a.job, a.who)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Your labels are saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
