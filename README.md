# Printer Automate App

A small web page that runs on your own laptop. It slices a part, sends it to a
Bambu Lab A1 mini (or to a built-in simulator), records what the printer reports,
and checks pictures of the print for **stringing** (thin plastic hairs between
the parts of a print). When the detector sees stringing, the Automate tab pauses
the printer. A person then decides to continue or stop.

It has four tabs:

| Tab | What it does |
|---|---|
| **Slice** | Turns an STL part into G-code with a slicer profile and draws any layer |
| **Print** | Sends a sliced job to the printer or simulator, shows its state, Pause and Stop buttons |
| **Monitor** | Records every printer report to a CSV log and plots temperature and layer against time |
| **Automate** | Replays saved detector answers on the simulator, or asks the real detector about one picture |

## What the Automate tab does today

The Automate tab has two tests:

- **Replay.** It plays back the course team's recorded print: 43 pictures, 15 seconds
  apart, each with a saved detector answer. It runs on the simulator, makes no API
  call, and takes about 12 minutes. It shows when the tab would pause and writes a log.
- **Live check.** It sends **one** picture to the real detector and shows the answer
  beside the saved one. The printer gets no command.

It does **not** yet watch a real print through a webcam. The pause rules are in place
and tested on the replay, but nothing feeds live webcam pictures into them.

## The pause rules

The rules are written in `contracts/automate.md` (Part 1 is the table, Part 2 is
how it is built). In short:

1. The first time a picture is `light` or `heavy` at a confidence of 0.60 or more, the tab pauses the printer.
2. After a pause, the tab waits for a person at the printer to continue or stop. The tab never resumes or stops a print itself.
3. Once a person has continued after a stringing pause, `light` is accepted. From then on only `heavy` at 0.60 or more pauses again.
4. While the printer is paused, no picture is checked. A picture that arrives then is dropped.
5. Pictures from before layer 1 are not checked.
6. If the detector gives no answer for 3 checked pictures in a row, the tab pauses the printer.
7. A person who resumes after such a failure pause does not change rule 3: `light` still causes the first pause later.

Every line of the Automate log names the rule (the "Part 1 row") that decided it.

## Install

You need Python 3.10 or newer.

```
git clone <your repository address>
cd printer-automate-app
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Then open `.env` and fill in what you need:

| Line | When you need it |
|---|---|
| `PRINTER_SIM=1` | Always for the replay. `1` = simulator, `0` = real printer |
| `PRINTER_SIM_FAST=0` | `0` = slow mode (the replay requires it). `1` = a short job, for testing the page |
| `OPENAI_API_KEY` | For the live check |
| `PRINTER_IP`, `PRINTER_SERIAL`, `PRINTER_ACCESS_CODE` | Only for the real printer (shown on its touchscreen) |
| `ORCA_EXE` | Only for the Slice tab, if OrcaSlicer is not found by itself |
| `STUDENT_NAME` | Optional: shown in the page title and on pictures |

Never commit `.env`. It holds your key and the printer's access code, and `.gitignore` already excludes it.

**Optional installs**

- Real printer: `pip install bambulabs_api`
- Webcam capture: `pip install opencv-python`
- Slicing: install [OrcaSlicer](https://github.com/SoftFever/OrcaSlicer). The replay and the live check do not need it.

## Run

```
python -m app.ui
```

The page opens at `http://localhost:8303`. Press Ctrl + C in the terminal to stop it.
After you change code or `.env` lines the page reads at startup, stop it and start it again.

## Use it

### Replay (no API call, about 12 minutes)

1. In `.env`, set `PRINTER_SIM=1` and `PRINTER_SIM_FAST=0`. Start the page.
2. Open the **Automate** tab. Leave the job and the saved answers at their defaults.
3. Press **Replay**. The simulator first goes through its own short pause, then the tab hands over one picture every 15 seconds.
4. Watch the table fill. Each line has the picture, its layer, the label and confidence, the printer's state, the action (`checked`, `dropped` or `pause`) and the Part 1 row.
5. When it finishes, the log is in `out/stage3_stringing_test_automate_log.csv`.

While a replay runs, **do not reload the page, open it in a second tab or press Connect**. Each of these replaces the simulator and breaks the run.

### Live check (one API call)

1. Put the pictures in `data/stage3_stringing_test_frames/` (see "Data" below). Set `OPENAI_API_KEY` in `.env`.
2. On the Automate tab choose a picture and press **Live check**.
3. The live answer (label and confidence) appears next to the saved answer.

### Print and Monitor (simulator or printer)

1. **Slice** tab: choose a part and a profile, press Slice. The G-code and a job file `out/<part>__<profile>.gcode.3mf` are written.
2. **Print** tab: choose the job and press **Print**. The state goes `PREPARE`, `RUNNING`, `FINISH`. **Pause** works only while `RUNNING`. There is no Resume button: on a real printer you resume on its touchscreen. The simulator resumes by itself after 30 seconds.
3. **Monitor** tab: the latest report and two plots (temperature, layer). The log is `out/<job name>_telemetry.csv`. Printing the same job again replaces its log.

### Using a real printer

Set `PRINTER_SIM=0` and the three printer lines in `.env`, install `bambulabs_api`, start the page and press Connect on the Print tab. A person presses Print. Only a person resumes a paused print.

## Data

- `data/stage3_stringing_test_frames/pictures.csv` lists the pictures and says which ones count (layer 1 until the last layer starts).
- `predictions_gpt-6-luna.csv` in the same folder holds the saved detector answer for each one.
- The picture files (`p013_t0181.jpg` and so on) are **not** in this repository. The replay does not need them. The live check does: copy them into the same folder. To keep them somewhere else, set `AUTOMATE_FRAMES` to that folder in `.env`.
- The sample job `out/stage3_stringing_test.gcode.3mf` and its `.slice` record let the Print tab work straight after installing.

## Folder layout

```
app/        the page (ui.py) and the G-code reader (toolpath.py)
scaffold/   printer wrapper with simulator, slicer call, detector call, G-code drawing, webcam capture
contracts/  the written specifications the code follows; start with automate.md
parts/      stringing_test.stl
profiles/   machine, filament and process profiles for the A1 mini
data/       saved detector answers for the replay
out/        everything the page writes: G-code, job files, logs
```

## Known limitations

- The Automate tab does not yet read live webcam pictures during a real print.
- The replay starts its own job on the simulator. `contracts/automate.md` says the tab never starts a print; the replay is the one exception, and the contract has not yet been updated to say so.
- Reloading the page or pressing Connect during a replay replaces the simulator and invalidates that run.
- The detector's answers vary from run to run; the saved answers are one recorded run.

## Origin

`scaffold/`, `profiles/` and `contracts/` come from the 30.303 Industry 4.0 & 3D Printing course (SUTD). The recorded pictures and answers come from the course team's stringing-test print. Check that you may publish them before making this repository public.
