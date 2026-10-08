"""Stage 1 scaffold: the data types for a parsed toolpath, and two ways to draw it.

You write the parser, `parse_gcode`, in `app/toolpath.py`, against
`contracts/toolpath.md`. Import `Layer` and `Move` from here; do not redefine them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import STUDENT_NAME


@dataclass
class Move:
    x0: float; y0: float; x1: float; y1: float; extrude: bool


@dataclass
class Layer:
    z: float
    moves: list[Move] = field(default_factory=list)


def stamp(fig) -> None:
    """Write the student's name (STUDENT_NAME in .env) and the time in the corner of a figure."""
    import datetime
    who = STUDENT_NAME or "STUDENT_NAME not set in .env"
    fig.text(0.99, 0.01, f"{who}  ·  {datetime.datetime.now():%Y-%m-%d %H:%M}",
             ha="right", va="bottom", fontsize=7, color="#595959")


def render_layer(layers: list[Layer], index: int, out_png: str | Path, title: str = "") -> Path:
    """Draw one layer (extrusion in colour, travel dotted), stamp it with your name, and save a PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    layer = layers[index]
    fig, ax = plt.subplots(figsize=(6, 6))
    for m in layer.moves:
        if m.extrude:
            ax.plot([m.x0, m.x1], [m.y0, m.y1], color="#990000", lw=1.2)
        else:
            ax.plot([m.x0, m.x1], [m.y0, m.y1], color="#999999", lw=0.6, ls=":")
    ax.set_aspect("equal"); ax.set_xlabel("X (mm)"); ax.set_ylabel("Y (mm)")
    n_ext = sum(1 for m in layer.moves if m.extrude)
    ax.set_title(title or f"Layer {index + 1}/{len(layers)}  z = {layer.z:.2f} mm  ({n_ext} extrusion moves)")
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(); stamp(fig); fig.savefig(out_png, dpi=150); plt.close(fig)
    return Path(out_png)


def show_viewer(layers: list[Layer]) -> None:
    """Interactive layer scrubber (needs a display)."""
    import matplotlib.pyplot as plt
    from matplotlib.widgets import Slider

    fig, ax = plt.subplots(figsize=(7, 7)); plt.subplots_adjust(bottom=0.15)
    sl = Slider(plt.axes([0.15, 0.05, 0.7, 0.03]), "Layer", 1, len(layers), valinit=1, valstep=1)

    def draw(_=None):
        ax.clear(); L = layers[int(sl.val) - 1]
        for m in L.moves:
            ax.plot([m.x0, m.x1], [m.y0, m.y1], color="#990000" if m.extrude else "#999999",
                    lw=1.2 if m.extrude else 0.6, ls="-" if m.extrude else ":")
        ax.set_aspect("equal"); ax.set_title(f"Layer {int(sl.val)}/{len(layers)}  z = {L.z:.2f} mm"); fig.canvas.draw_idle()

    sl.on_changed(draw); draw(); stamp(fig); plt.show()
