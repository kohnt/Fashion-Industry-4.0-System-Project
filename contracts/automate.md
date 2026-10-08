# Contract: the Automate tab

Stage 3. The detector's result meets the pause decision in the application. The Automate tab looks at each webcam picture of a print, asks the detector what it sees, and pauses the printer when the picture shows stringing (thin plastic hairs between the parts of a print).

## Part 1: How the printer should behave

Each row says what the printer does in one situation, and why. A label is one of `none`, `light` or `heavy`. A confidence is a number from 0 to 1 that says how sure the detector is. A checked picture is a picture the tab sends to the detector (see row 8).

| # | Situation | Action | Reason |
|---|---|---|---|
| 1 | No person has continued yet, and a checked picture is `light` or `heavy` at a confidence of 0.60 or above | The tab pauses the printer | Catch stringing without watching the print |
| 2 | The printer is paused, for any reason | The tab checks no pictures until the printer is `RUNNING` again | Saves API calls |
| 3 | The tab has paused the printer | It waits for a person at the printer to continue or stop. The tab never resumes the print and never stops it itself | The part is small, so someone is near. A person gives the most flexibility, and the decision is theirs |
| 4 | A person continued after a stringing pause | `light` is accepted from then on. Only `heavy` at 0.60 or above pauses again | Do not pause too much |
| 5 | A confidence is exactly 0.60 | It counts, in every rule | One threshold, easy to explain and test |
| 6 | The detector gives no answer on 3 checked pictures in a row | The tab pauses the printer | One failure is a blip. Three in a row, about 45 seconds, means the detector is down. At no more than 5% failures, a run of 3 is about 0.5% likely over the whole print |
| 7 | A person resumes after a pause caused by failed pictures | Nothing changes. `light` still causes the first pause later | `light` is accepted only after a person has looked at stringing |
| 8 | A picture is from before layer 1 | It is not checked, and it does not count toward the 3 failures | Nothing to string yet, fewer API calls, and fewer pauses from network blips |
| 9 | A live check runs | The printer gets no command | The live check tests only the connection to the detector |
| 10 | **Pass, replay.** The saved answers in `predictions_gpt-6-luna.csv` are replayed in order on the simulator in slow mode | The printer pauses 2 times, on p015 and on p033. It reports `PAUSE` at each pause and `RUNNING` after each resume | The result you worked out before running, checked against the log |
| 11 | **Pass, live check.** One picture is sent to the detector | The detector returns an answer (any label, with a confidence), shown beside the saved answer, and the printer gets no command | Tests the call to the detector, not the rules |

In the replay, nobody presses continue on a screen. The simulator resumes by itself 30 seconds after a pause, in place of the person.

## Part 2: How it is built

Each rule names the Part 1 row it carries out.

### What goes in and what comes out

**In.** For the replay: the pictures in the folder `30303 Lab 3 dataset/out/stage3_stringing_test_frames/` that `pictures.csv` marks `yes` in its `used` column, in the order of that file, and the detector's saved answer for each in `predictions_gpt-6-luna.csv`. For the live check: one picture you choose. For both: the printer's state, read through `scaffold/printer.py` (the simulator when `PRINTER_SIM=1`).

**Out.** On the page: one line for each picture the tab handled, and the printer's state. On disk: one log, `out/stage3_stringing_test_automate_log.csv`, with one line for each picture that arrived. The only command the tab ever sends to the printer is `pause()`.

The tab does not slice, does not print, does not resume and does not stop. It does not change `scaffold/vision.py`, `scaffold/printer.py` or the saved answers. In the replay it calls no API.

### Function and data types

| Function | From | What the tab uses it for |
|---|---|---|
| `ask_vision(picture)` | `scaffold/vision.py` | Sends one picture to the detector and returns its answer. Raises `VisionError` if the call fails. Used only in the live check |
| `get_printer()`, `connect()`, `close()` | `scaffold/printer.py` | The printer, as on the Print tab |
| `state()` | the same | The printer's state, one word |
| `on_report(callback)` | the same | Delivers each report, so the tab sees the state change from `PAUSE` to `RUNNING` |
| `pause()` | the same | Asks the printer to pause. The only command the tab sends |

Functions you write:

```python
def on_picture(picture: Picture, printer_state: str) -> Decision
def decide(answer: Answer | None, memory: Memory) -> Decision
```

`on_picture` handles one arriving picture and calls `decide` only if the picture is checked (rules 2 and 5). `decide` holds the pause rules (rules 3, 4 and 6) and sends nothing.

| Type | Field | What it holds | Unit |
|---|---|---|---|
| `Picture` | `file` | The picture's file name, such as `p013_t0181.jpg` | none |
| | `seconds` | Seconds since Print when it was taken | s |
| | `layer` | The layer from `pictures.csv`; empty before layer 1 | none |
| `Answer` | `label` | `none`, `light` or `heavy` | none |
| | `confidence` | How sure the detector is, from 0 to 1 | none |
| `Memory` | `light_accepted` | `True` once a person has continued after a stringing pause | none |
| | `failures` | Checked pictures in a row with no answer | count |
| | `pause_reason` | `stringing`, `failures` or empty: why the tab last paused | none |
| `Decision` | `action` | `pause` or `none` | none |
| | `row` | The Part 1 row that decided it | none |

`Answer` is `None` for a picture the detector gave no answer for: `ask_vision` raised `VisionError`, or its `label` came back empty.

### The rules

1. **Source of answers.** (Rows 10 and 11.) The replay reads each picture's answer from `predictions_gpt-6-luna.csv` by the picture's file name and calls no API. The live check calls `ask_vision` once, on the one picture. So a replay costs nothing and gives the same log every time.

2. **Which pictures.** (Row 8.) The replay hands the tab only the pictures `pictures.csv` marks `yes`. A picture with an empty layer or layer 0 is never checked and never counts toward `failures`. So `p001_t0001.jpg` has no layer in `pictures.csv` and is never checked.

3. **Pause rule.** (Rows 1, 4 and 5.) Compare `confidence` as a number with 0.60, and a confidence of 0.60 or more counts. While `light_accepted` is `False`, `decide` returns `pause` for the label `light` or `heavy`. Once it is `True`, `decide` returns `pause` for `heavy` only. For every other answer it returns `none`. So `light` at 0.58 is below 0.60 and does not pause, and `heavy` at exactly 0.60 pauses.

4. **Person continued.** (Rows 3, 4 and 7.) When the tab presses Pause for a stringing result, it sets `pause_reason` to `stringing`. When the printer's state goes from `PAUSE` to `RUNNING` and `pause_reason` is `stringing`, the tab sets `light_accepted` to `True` and clears `pause_reason`. When `pause_reason` is `failures`, the tab clears it and leaves `light_accepted` unchanged. A pause the tab did not press changes nothing. A new run (the state changes to `PREPARE`) sets `light_accepted` to `False`, `failures` to 0 and `pause_reason` to empty. So after a failure pause and a resume, a `light` result still pauses.

5. **Only while running.** (Row 2.) `on_picture` checks a picture only when `printer_state` is `RUNNING`. In any other state it drops the picture: no answer is read, no call is made, and nothing is kept for later. The line for it in the log says `dropped`. So a picture that arrives while the printer is `PAUSE`, `PREPARE`, `FINISH` or `FAILED` is never checked.

6. **Failed pictures.** (Rows 6 and 8.) A checked picture with no answer adds 1 to `failures`. A checked picture with an answer sets `failures` to 0. When `failures` reaches 3, `decide` returns `pause`, the tab sets `pause_reason` to `failures` and `failures` to 0. A dropped picture neither adds nor resets. So three checked pictures in a row with no answer pause the printer, and two of three do not.

7. **One command.** (Row 3.) The tab calls `pause()` and no other command: it never calls `resume()`, `stop()` or `start()`. It does not call `pause()` again until the printer has reported `PAUSE`. If `pause()` raises, including `RateLimited`, the tab shows the text, and the log line says the pause failed. So the tab never undoes a person's decision, and it presses Pause once for each pause.

8. **Live check.** (Rows 9 and 11.) The live check calls `ask_vision` once and shows the answer beside the saved answer for the same picture. It calls no printer function that sends a command. If `ask_vision` raises, the tab shows the error text and nothing else happens.

9. **Replay pacing.** (Rows 2, 8 and 10.) The replay waits until the simulator's own pause for the filament check is over and the printer is `RUNNING`, and then hands the tab the first picture. It hands over each next picture 15 seconds later, the spacing of the course team's pictures. It makes the simulator's job long enough to last past the last picture. A pause pressed by the tab lasts 30 seconds on the simulator, so two picture slots fall inside it, and rule 5 drops the pictures in them. So a replay takes about twelve minutes.

10. **Log.** (Every row.) For each picture that arrives, the tab writes one line: the picture's file name, its layer, the label, the confidence, the printer's state, the action (`checked`, `dropped` or `pause`), and the Part 1 row number that decided it. A line with `pause` is written only once the printer has reported `PAUSE`. So for every line you can name the rule.

### The decisions

**Rule 3** uses one comparison for both stages of Part 1 because row 5 says one threshold. The `light` pictures before the first pause and after it differ only in whether `light_accepted` is `True`.

**Rule 4** sets `light_accepted` from a state change, not from a button, because the tab has no Resume button. A person resumes on the printer's own screen, and the printer reports it as `PAUSE` going to `RUNNING` (rule 7 of `contracts/printer.md`).

**Rule 5.** Chosen over keeping the newest picture that arrived during the pause and checking it on the resume. A picture taken during a pause shows a part that is not changing, and a result on it may only repeat hairs a person has already seen. The simulator also reports `RUNNING` while the nozzle reheats after a pause, so the first picture after the resume is checked as soon as it arrives.

**Rule 7** exists because the printer reports `PAUSE` about 3 to 5 seconds after the command, and a new picture can arrive in that gap. The real printer refuses commands that come less than 5 seconds apart. Without the wait, a second result would send a second command to a printer that is already stopping.

**Rule 9** chose 15 seconds because that is the spacing of the pictures in `pictures.csv`. The simulator's own pause, about 18 seconds into a job and 15 seconds long, would otherwise swallow a picture that the real print would have checked, so the replay starts after it. On the real printer, a pause of this kind comes in the first minutes of a print, before layer 1, when no picture is checked (row 8).

**The live check** uses one picture because one call is enough to show that the connection works, and each call costs money and waits at least 2 seconds after the last one.

### How to check it

**Before you run it.** Work out from `predictions_gpt-6-luna.csv` and the rules which pictures the replay checks, which it drops, and which it pauses on. Write that list down. The replay passes as row 10 says, and the live check passes as row 11 says.

**The pause rule.** Call `decide` by hand on a few answers: `light` 0.58 with `light_accepted` `False` gives `none`; `light` 0.60 gives `pause`; `heavy` 0.60 with `light_accepted` `True` gives `pause`; `light` 0.90 with `light_accepted` `True` gives `none`.

**The failure rule.** Copy `predictions_gpt-6-luna.csv` into `out/`, empty the answer of three checked pictures in a row, and replay on that copy: the printer pauses after the third. Empty two in a row, then one with an answer, then one without: it does not pause.

**The replay.** Set `PRINTER_SIM=1` and `PRINTER_SIM_FAST=0` in `.env`, start the page and run the replay. The log has one line for every picture that arrived. The pauses, the state at each pause and after each resume match row 10.

**The live check.** Choose the picture you predicted and run it. The detector's answer appears beside the saved one, and the printer's state does not change.

**No other command.** Search `app/ui.py` for the Automate tab's code: it contains `pause()` and none of `resume()`, `stop()` or `start()`.

**The viva.** The TA asks: when the printer is paused and a picture arrives, why is it dropped and not checked later, and which row says so?
