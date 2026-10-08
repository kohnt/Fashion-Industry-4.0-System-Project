# Contract: the Monitor tab

*Stage 3. This contract is for the Monitor tab you add to `app/ui.py`. The tab keeps a record of what the printer reports during a print: it shows each report as it arrives, writes it to a log file, and draws it against time. It sends nothing to the printer. The Pause button belongs to the Print tab and is rule 7 of `contracts/printer.md`.*

## What goes in and what comes out

**In.** The printer's reports. A report is one reading of the printer, sent by the printer about every two seconds (every second on the simulator). Your page receives them through `scaffold/printer.py`, the same file the Print tab uses.

**Out.** On the page: the latest report, and two plots against time. On disk: one log file for each print, `out/<job name>_telemetry.csv`. For the job `out/stage3_stringing_test.gcode.3mf` the log is `out/stage3_stringing_test_telemetry.csv`.

The tab does not change the printer's state, and it does not judge whether the print is good. Judging is the detector's job, and the detector is evaluated on recorded pictures, not on this tab.

## Function and data types

| Function | From | What the tab uses it for |
|---|---|---|
| `on_report(callback)` | `scaffold/printer.py` | Registers your function, called the callback, which the printer wrapper then calls once for every report, with the report as its one argument |

A report is a Python dictionary. The tab uses five of its fields:

| Field | What it holds | Unit |
|---|---|---|
| `gcode_state` | The printer's state, one word, as in rule 4 of `contracts/printer.md` | none |
| `nozzle_temper` | Nozzle temperature | °C |
| `bed_temper` | Bed temperature | °C |
| `layer_num` | The layer being printed; 0 until the first layer starts | none |
| `mc_percent` | The printer's own estimate of how much of the job is done | % |

The callback is a function you write. You choose its name; for example:

```python
def on_printer_report(report: dict) -> None:
    """Called by the printer wrapper once for every report, with the report as
    its one argument. Stores the report and writes one line to the log
    (rules 2 and 3). Returns nothing."""
```

## The rules

1. **Source.** The tab gets its reports only from the callback it registers with `on_report()`. It registers it once, after `connect()`, and again after Connect gives it a new printer (rule 2 of `contracts/printer.md`). It never opens a connection of its own. So the Print tab and the Monitor tab read the same reports.

2. **Callback.** The callback stores the report and writes one line to the log. It sends no command and does not wait for anything. It runs once for every report, so a callback that waits would delay the next report, and the tab would fall behind the printer.

3. **Log file.** The log is a CSV file, a plain table with one line per report and commas between the values. Its first line names the columns with their units: `seconds since Print (s), state, nozzle temperature (°C), bed temperature (°C), layer, per cent (%)`. Seconds are counted from the moment you pressed Print. Pressing Print again for the same job starts the file afresh, so copy a log you want to keep before you print that job again.

4. **Log window.** The log starts with the first report after the new job is accepted: the state has changed to `PREPARE` or `RUNNING` since you pressed Print (rule 4 of `contracts/printer.md`). It ends with the first `FINISH` or `FAILED` after that. Reports from before are the previous job's. The tab shows them, but they go in neither the log nor the plots. The state changes before the layer number does, so the layer number and per cent belong to the new job only once the layer has read 0 after Print. So if the first `PREPARE` report still carries layer 75 and 100 per cent from the last print, its layer and per cent cells are left empty in the log and the report is left off the layer plot. The tab keeps showing the latest report after the log has ended.

5. **Missing value.** If a report lacks a field, or the state is `UNKNOWN`, the tab shows "not reported" and the log has an empty cell. It never shows or writes 0 in its place, because 0 is a real layer number: the layer reads 0 all through the heat-up.

6. **Numbers.** Temperatures are shown and logged to one decimal place. The printer sends values such as `24.03125`, and the last digits mean nothing at the accuracy of its sensor.

7. **Plots.** The tab draws two plots with the same time axis, in seconds since Print: nozzle and bed temperature, and the layer number. Each axis names its quantity and unit. Each change of state is marked on the time axis, and every label can be read: none is written over another.

8. **First layer.** The tab shows the seconds since Print at which layer 1 was first reported, counting only reports inside the log window (rule 4). So for the stringing test part (`stringing_test.stl`, sliced with retraction off so that it strings on purpose), layer 1 starts several minutes after Print, at the end of the heat-up, not at the first `RUNNING`, and that is the time the tab shows. Each new print starts with no layer 1 time shown.

## The decisions

**Rule 2** keeps the tab from falling behind. Reports arrive whether or not the tab is ready, so the work done for each one has to be small.

**Rule 4** exists because a leftover report looks like a real one. A log that started at Print opened with layer 75 at 100 per cent, and the layer plot began at the top of the part before dropping to 0. Starting at the state change removed most of it, but the first `PREPARE` report still carried layer 75, and the layer plot began with a spike. Waiting for the layer to read 0 keeps every value in the log about this job. Measured on the A1 mini: after Print, the printer went on reporting `FINISH` at layer 75 for about four seconds.

**Rule 5** exists because a missing value written as 0 cannot be told from a real 0 afterwards, when you read the log.

**Rule 8** is the first-layer lesson from Week 2 again: the state reads `RUNNING` long before plastic goes down, and the layer number is what tells you it has. Measured on the A1 mini: the stringing test part reaches layer 1 about 315 to 330 seconds after Print. The heat-up is not always the same: on a first print after standing idle, the nozzle first rises to about 250 °C to purge filament.

## How to check it

**Before you run it.** Sketch the two plots for one simulator print: where the nozzle line rises, where the layer steps begin, and where the simulator's own pause (rule 5 of `contracts/printer.md`) will show.

**On the simulator.** Set `PRINTER_SIM=1`, print a job, and open the log after `FINISH`. The first line is the column names with units. The first report is `PREPARE` or `RUNNING`, and the last is `FINISH`. No line comes after it.

**Stale state.** Print a second job straight after the first. The new log does not start with the old job's `FINISH`, its first lines have an empty layer and per cent until the layer reads 0, the layer plot has no spike at the start, and the layer 1 time stays empty until the new job reaches layer 1.

**A pause.** Press Pause on the Print tab while the simulator prints. The log shows `PAUSE`, then `RUNNING` again with the layer number unchanged while the nozzle heats back up, then the layer rising again.

**On the machine.** Print the stringing test part. Layer 1 appears a few minutes after Print. Before it, the nozzle line does not simply rise: it goes up and down at least once before it reaches 240 °C for the first layer. Your plot shows this, and the layer 1 time comes after the last rise.

**The viva.** The TA points at a line of your log and asks what the printer was doing at that moment, and how you know.
