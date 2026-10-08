"""Stage 3 scaffold: grab a frame from the station camera.

Three sources, chosen automatically:
  1. A folder of images (FRAMES_DIR) - for replaying a recorded print, and for
     running the detector without hardware.
  2. A USB webcam (WEBCAM_INDEX), as in Talk2Print's defect.py / find_camera.py.
  3. The printer's own camera via bambulabs_api (camera_start / get_camera_image),
     as in control.py take_photo().
"""
from __future__ import annotations

import os
import time
from pathlib import Path

FRAMES_DIR = os.environ.get("FRAMES_DIR", "")
WEBCAM_INDEX = int(os.environ.get("WEBCAM_INDEX", "0"))


class FrameSource:
    def __init__(self, printer=None):
        self._printer = printer
        self._files: list[Path] = []
        self._i = 0
        if FRAMES_DIR and Path(FRAMES_DIR).is_dir():
            self._files = sorted(p for p in Path(FRAMES_DIR).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        self._cap = None

    def grab_frame(self, out_path: str | Path) -> Path | None:
        """Save one frame to out_path and return it, or None if no source works."""
        out_path = Path(out_path)
        if self._files:
            src = self._files[self._i % len(self._files)]; self._i += 1
            out_path.write_bytes(src.read_bytes()); return out_path
        try:
            import cv2
            if self._cap is None:
                self._cap = cv2.VideoCapture(WEBCAM_INDEX)
            ok, frame = self._cap.read()
            if ok:
                cv2.imwrite(str(out_path), frame); return out_path
        except Exception:
            pass
        if self._printer is not None and hasattr(self._printer, "_p"):
            try:
                self._printer._p.camera_start(); time.sleep(2.0)
                self._printer._p.get_camera_image().save(str(out_path)); return out_path
            except Exception:
                pass
            finally:
                try:
                    self._printer._p.camera_stop()
                except Exception:
                    pass
        return None
