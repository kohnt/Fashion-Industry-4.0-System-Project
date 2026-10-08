"""Stage 1 parser: read a G-code file into a list of layers.

Written against contracts/toolpath.md. See that file for the numbered rules
referenced in comments below.
"""
from __future__ import annotations

import re
from pathlib import Path

from scaffold.gcode import Layer, Move

_MOVE_RE = re.compile(r"^(G0|G1)\b")
_WORD_RE = re.compile(r"([A-Za-z])(-?\d*\.?\d+)")


def _strip_comment(line: str) -> str:
    return line.split(";", 1)[0].strip()


def parse_gcode(path: str | Path) -> list[Layer]:
    x = y = z = 0.0          # rule 5: nozzle starts at (0, 0, 0)
    last_e = 0.0              # last E seen, for M82 (absolute E)
    relative_e = False        # M83 makes E relative; M82 (default) makes it absolute
    started = False           # rule 6: true once the first extrusion of the file has happened
    in_start_routine = False  # rule 8: true from the first '; CHANGE_LAYER' comment onward is False again;
                               # this flag tracks whether we are still BEFORE that first marker
    seen_change_layer = False
    layers: list[Layer] = []
    current: Layer | None = None

    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    has_change_layer_marker = any("; CHANGE_LAYER" in ln for ln in lines)
    in_start_routine = has_change_layer_marker  # rule 8: only OrcaSlicer files carry the marker

    for raw_line in lines:
        if in_start_routine and "; CHANGE_LAYER" in raw_line:
            in_start_routine = False  # rule 8: the marker ends the start routine

        code = _strip_comment(raw_line)
        if not code:
            continue

        if code.startswith("M82"):
            relative_e = False
            continue
        if code.startswith("M83"):
            relative_e = True
            continue
        if code.startswith("G92"):
            for letter, value in _WORD_RE.findall(code):
                if letter.upper() == "E":
                    last_e = float(value)
            continue

        if not _MOVE_RE.match(code):     # rule 1: only G0/G1 move the nozzle
            continue

        x0, y0 = x, y
        e_here = None
        for letter, value in _WORD_RE.findall(code):
            v = float(value)
            letter = letter.upper()
            if letter == "X":
                x = v
            elif letter == "Y":
                y = v
            elif letter == "Z":
                z = v
            elif letter == "E":
                e_here = v

        if e_here is None:
            d_e = 0.0
        elif relative_e:
            d_e = e_here          # rule 3: under M83, the line's E is the amount pushed
        else:
            d_e = e_here - last_e  # rule 3: under M82, this line's E minus the last E seen
            last_e = e_here
        if relative_e and e_here is not None:
            last_e += e_here

        if x == x0 and y == y0:   # rule 4: no X/Y change -> null move, nothing recorded
            continue

        if in_start_routine:       # rule 8: start routine tracks position/E but adds no moves
            continue

        extrude = d_e > 0          # rule 3: extrusion iff E increased

        if not started:
            if not extrude:
                continue           # rule 6: travel before the first layer is dropped
            started = True

        if current is None or (extrude and z != current.z):
            # rule 2: a new layer starts at the first extrusion at a new z
            current = Layer(z=z, moves=[])
            layers.append(current)

        current.moves.append(Move(x0=x0, y0=y0, x1=x, y1=y, extrude=extrude))

    return layers


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "parts/demo_coupon.gcode"
    result = parse_gcode(target)
    print(f"Number of layers: {len(result)}")
    if result:
        print(f"z of first and last layer: {result[0].z:g} and {result[-1].z:g}")
    for i, layer in enumerate(result, start=1):
        n_ext = sum(1 for m in layer.moves if m.extrude)
        n_trv = sum(1 for m in layer.moves if not m.extrude)
        print(f"  layer {i}: z={layer.z:g}  extrusions={n_ext}  travels={n_trv}")
    first = next((m for L in result for m in L.moves if m.extrude), None)
    if first:
        print(f"First extrusion: ({first.x0:g}, {first.y0:g}) -> ({first.x1:g}, {first.y1:g})")
