# Contract: toolpath

Stage 1. This contract is for `parse_gcode`, the parser you write in `app/toolpath.py`. A parser is a translator program: it reads a file written for one program and rewrites the same information in a form another program can use. A spreadsheet program does this when it reads a CSV file (text, one row per line) and turns it into cells it can add up. `parse_gcode` reads a G-code file, the list of instructions OrcaSlicer writes for the printer, and turns it into a list of layers. A layer is one height of the part. It holds, in order, every straight-line movement the nozzle makes at that height, each marked as extrusion (filament comes out as the nozzle moves) or travel (the nozzle moves with no filament coming out). The G-code file comes from `slice_stl()` in `scaffold/slicer.py`, or from `parts/` in the case of the coupon. The list of layers goes to `render_layer` and `show_viewer` in `scaffold/gcode.py`, which draw the toolpath from that list, one layer at a time.

## What goes in and what comes out

**In.** The path of a G-code file, that is, where the file sits on your laptop, such as `parts/demo_coupon.gcode` or `out/week1_bracket.gcode`.

**Out.** A list of layers, that is, the print toolpath taken one height at a time, bottom layer first. Each layer holds its height (z) and its moves from one (x, y) point to the next, in the order the file gives them, each marked as extrusion or travel (rule 3). Nothing else comes out: no speeds, no temperatures, no comments.

## Function and data types

Your parser is one function, `parse_gcode`. It takes the path of a G-code file and returns a list of `Layer` values. `Layer` and `Move` are defined in `scaffold/gcode.py`; import them from there and do not write your own.

```python
from scaffold.gcode import Layer, Move

def parse_gcode(path: str | Path) -> list[Layer]   # path in, list of layers out

@dataclass
class Layer:
    z: float               # height of this layer, mm
    moves: list[Move]      # its moves, in the order the file gives them

@dataclass
class Move:
    x0: float; y0: float   # where the nozzle started, mm
    x1: float; y1: float   # where it ended, mm
    extrude: bool          # True for an extrusion, False for a travel (rule 3)
```

`layers[0]` is the bottom layer. All numbers are in mm, and X and Y are positions on the bed, not distances from the last point. That is what G-code means unless a line says otherwise (`G20` for inches, `G91` for distances), and neither of our files does outside the start and end routines (rule 8).

## The rules

In these rules, a *line* is one line of the G-code file. A *move* is one entry in a layer's list: the nozzle's path across the bed from one point to the next, marked as extrusion or travel. One line produces at most one move.

1. **Syntax.** Only `G0` and `G1` lines can move the nozzle. Everything after a `;` is a comment and is ignored, and so are blank lines and every other command, with three exceptions that change how E is read: `M82`, `M83` and `G92` (rule 3). One comment is also read, `; CHANGE_LAYER`, in rule 8. OrcaSlicer writes numbers without a leading zero (`Z.2`, `E.03`); they mean 0.2 and 0.03.
2. **Layer definition.** A layer is a height at which the printer lays down filament. The nozzle moving to a new height is not yet a layer; the first line with extrusion at that height (rule 3) starts a new layer, and the layer's z is that height. So a lift that comes back down before extruding (a Z-hop) is not a layer.
3. **Extrusion/Travel definition.** A move extrudes when it pushes filament forward, which the file shows as an increase in E (ΔE > 0). Under `M82` (absolute E, the default) the amount pushed is this line's E minus the last E seen; under `M83` (relative E) it is this line's E itself; `G92 E...` resets the last E seen. A line with no E, or where E does not increase (a retraction pulls filament back), is a travel.
4. **Null move.** A line that leaves X and Y unchanged adds nothing to the layer's list of moves. A pure Z move, a retraction, or a prime (pushing filament forward again after a retraction) all count as null moves.
5. **Unwritten axis assumption.** A line names only what changes. If a line has no X, the X position stays where the last line left it; the same for Y, Z and E. So `G1 Z0.60` moves the nozzle up without changing X or Y, and `G1 X130 E0.04` moves it along X at the Z it already had. Before the first line, the nozzle is at (0, 0, 0).
6. **First layer definition.** A layer begins with an extrusion, never with a travel. Any travel that comes before the first layer has started is dropped, because there is no layer to put it in. This includes the travel from where the start routine (rule 8) leaves the nozzle to the start of the part.
7. **Layer boundary.** The travel that carries the nozzle from the end of one layer to the start of the next belongs to the layer it left, not the layer it enters.
8. **Start and end routines.** A routine is the set of instructions the printer runs before the print begins and after it ends. The OrcaSlicer file contains `; CHANGE_LAYER` comment lines (the coupon does not). Everything before the first of them is the start routine and produces no moves. On the A1 mini the start routine levels the bed and draws a purge line: a short line of filament pushed out along the front edge of the bed to get the nozzle flowing. The parser still keeps track of the position and of `M82`, `M83` and `G92` through it, because the start routine sets them. The end routine (parking the nozzle and wiping it after the last extrusion) is kept, as travels in the last layer, because the file has no marker for where it begins; it changes no extrusion count.

## The decisions

**Rule 2, layers.** Chosen over "every change in Z is a new layer". OrcaSlicer lifts the nozzle on many travels (a Z-hop), so a parser that counts changes in Z reports about twice as many layers as the file has.

**Rule 3, extrusion.** Chosen over "a line with an E value is an extrusion". A retraction also has an E value, but a smaller one than before; only an increase means filament came out.

**Rule 6, first layer.** Chosen over keeping the travel from where the start routine leaves the nozzle as part of layer 1. It is not part of the toolpath, and there is no layer to put it in.

**Rule 7, layer boundary.** A convenience, not a fact about the printer: by the time the parser sees the first extrusion of the next layer, the travel that led to it is already in the list. One consequence to expect in the coupon: its last layer has one travel fewer than the others. In the OrcaSlicer file the end routine adds travels to the last layer instead (rule 8).

**Rule 8, start and end routines.** Chosen over "drop moves that lie outside the bed". OrcaSlicer writes the `; CHANGE_LAYER` marker in every file, while the purge line sits in a different place on each printer. Without rule 8 the purge line becomes a layer at z 0.3, and the OrcaSlicer file has one layer more than its header says.

**Not handled.** Arcs (`G2`, `G3`) and relative moves (`G91`, where each line gives a distance from the last point instead of a position) are not parsed. OrcaSlicer writes both only inside the start and end routines: arcs while wiping the nozzle, and `G91` for a few lines of each routine. A parser that misreads them still gets the right answer, because rule 8 drops the start routine and rule 6 drops the travel that follows it. If arcs ever appear in the layers of the part, arc fitting has been switched on in the process profile; switch it off there rather than extend the parser.

## How to check it

**The coupon, before any code.** From the rules and `parts/demo_coupon.gcode`, work out on paper: how many layers; the z of the first and last; how many extrusions in a layer; where the first extrusion starts and ends; how many travels in a layer. Write them in the Predicted column of `report/stage1.md` and commit. Then run your parser on the coupon, write what it reports in the Measured column, and explain every difference: either your reading of a rule or the code is wrong, and you must say which.

**The OrcaSlicer file.** Slice `parts/week1_bracket.stl` at 0.2 mm and run your parser on the result. Its layer count must equal the number after `; total layer number:` near the top of the file, and its last z must equal the number after `; max_z_height:`. If your parser reports one layer more, with a first layer at z 0.3 instead of 0.2, it has counted the start routine (rule 8). Slice again at a different layer height: the layer count changes, and the last z stays at the part's height, 24. Then run your parser on `parts/practice.gcode`: line 14 changes X while E goes down, and your parser must mark it travel.

**The viva.** The TA points at one `G1` line in the file. You say, from rules 2 and 3, whether it starts a layer and whether it extrudes, and why.
