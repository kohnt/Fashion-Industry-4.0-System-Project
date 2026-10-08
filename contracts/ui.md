# Contract: the Stage 1 page

*Stage 1. This contract is for the page you build in `app/ui.py`: a web page that your own program serves on your laptop and that you open in your browser. On it you choose a part and a slicer profile, press Slice, and look at the toolpath one layer at a time. The page does no work of its own. It calls `slice_stl()` to slice, your `parse_gcode` to read the G-code, and `render_layer` to draw a layer, and it shows what they return. In Weeks 2 to 4 the same page gains a Print tab, a Data tab and an Automate tab, which is how a machine is run in Industry 4.0: from a screen on the network, not from the buttons on its front.*

*How to write it: see `TEMPLATE.md`.*

## What goes in and what comes out

**In.** Three choices made on the page: a part, which is an STL file from `parts/`; a process profile, which is a JSON file from `profiles/` or `out/` (`out/` so that your own copies of a profile appear too); and a layer number. The machine and filament profiles are fixed: `profiles/machine_a1mini_0.4.json` and `profiles/filament_bambu_pla_basic_a1m.json`.

**Out.** A G-code file in `out/`, named after the part and the profile, such as `out/week1_bracket__process_0.20mm_standard_a1m.gcode`, and on the page a picture of the chosen layer, saved next to the G-code as `out/<part>__<profile>_layer<N>.png`, N being the layer number, for example `out/week1_bracket__process_walls4_layer1.png`, with five numbers beside it: the number of layers, the z of the first and last layer, and the extrusion and travel moves in the layer shown. Nothing goes to the printer.

## Function and data types

`python3 -m app.ui` starts the page and opens `http://localhost:8303` in your browser. `localhost` means your own laptop; `8303` is the port, the number that tells the browser which program on the laptop to talk to. The program keeps running, answering the browser, until you stop it with Ctrl + C in the terminal.

| Function | From | What the page uses it for |
|---|---|---|
| `slice_stl(stl, machine, process, filament, out_name=...)` | `scaffold/slicer.py` | Turns the chosen STL into G-code with the chosen profiles, and returns the path of the G-code file |
| `parse_gcode(path)` | `app/toolpath.py` | Reads that file into a list of layers (your Stage 1 parser, `contracts/toolpath.md`) |
| `render_layer(layers, index, out_png, title)` | `scaffold/gcode.py` | Draws one layer from that list to a PNG file, which the page shows |

The page is served with Python's own `http.server`; no web framework is installed or used.

## The rules

1. **One parser.** The page reads G-code only through your `parse_gcode`. It has no G-code reading of its own. So what the page shows is what your parser found, and a fault in the parser shows on the page.
2. **One slicer call.** The page slices only through `slice_stl()`; it never runs OrcaSlicer itself. It offers the process profiles it finds in `profiles/` and `out/` and never edits one.
3. **Named output.** Each slice writes `out/<part>__<profile>.gcode`, using `out_name` of `slice_stl()`. So slicing `week1_bracket.stl` with `process_0.20mm_standard_a1m.json` and then with `out/process_walls4.json` gives two files, and neither overwrites the other. Slicing again with the same part and profile replaces that one file. The picture of layer N is saved as `out/<part>__<profile>_layer<N>.png`: two underscores between part and profile, one before `layer`, so `out/week1_bracket__process_walls4_layer1.png` and `out/week1_bracket__process_028_layer1.png`. The pictures of two slices can then be kept side by side and copied into the report under the names `report/stage1.md` gives.
4. **Signed.** The page title shows the name on the `STUDENT_NAME` line of `.env`, read through `scaffold/config.py`, and every picture it draws carries that name and the time, which `render_layer` adds on its own.
5. **Nothing runs unasked.** Opening the page slices nothing. Slicing starts when you press Slice, and the terminal then shows the command that ran (the line beginning `$` that `slice_stl()` prints).
6. **Errors shown.** If the slicer or the parser fails, the page shows the error text in place of the picture. It never shows the picture from an earlier slice as if it were the new one.

## The decisions

**Rule 1** makes the page a second check on your parser: the numbers on the page must match the numbers your parser printed in the terminal, for the same file. A page with its own G-code reading would hide a parser fault.

**Rule 3** exists for question 3 of `report/stage1.md`, which needs the bracket sliced with two profiles and both results kept.

**Rule 4** makes the name part of every output, so that a picture or a page in your report can be traced to you without a caption.

**Rule 5** is the same habit as reading a command before you approve it: the machine does nothing you did not ask for.

**Standard library only** because every laptop has it, and because the Week 2 Print tab, which sends commands to a printer, must not depend on a package the teaching team has not read.

## How to check it

**The bracket.** Choose `week1_bracket.stl` and `profiles/process_0.20mm_standard_a1m.json`, press Slice. The page shows 120 layers, first z 0.2, last z 24, and layer 1 looks the same as the picture your parser drew of the bracket's layer 1 in prompt 3. The terminal shows one line beginning `$`.

**Two profiles.** Choose `out/process_walls4.json` and slice again: `out/` now holds two bracket G-code files, and the walls on layer 1 are twice as wide. Choose `out/process_028.json`: 86 layers, last z still 24.

**A fault.** Rename `parts/week1_bracket.stl` for a moment and press Slice: the page shows an error and no picture. Rename it back.

**The viva.** The TA asks what happens between your pressing Slice and the picture appearing, and which function each step calls.
