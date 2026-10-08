# Contract: the Print tab

*Stage 2. This contract is for the Print tab you add to `app/ui.py`, the page you built in Week 1. On it you choose a part, send it to a real printer on the network, and watch what the printer reports while it prints. The tab does no printing of its own. It calls `scaffold/printer.py`, which is the only thing in this course that talks to the machine. In Weeks 3 and 4 the same page gains a Monitor tab and an Automate tab. Rule 7, the Pause button, was added in Week 3.*

*How to write it: see `TEMPLATE.md`.*

## What goes in and what comes out

**In.** A job file your page made on the Slice tab: a `.gcode.3mf` in `out/`, such as `out/coupon__process_0.20mm_standard_a1m.gcode.3mf`. The Print tab does not slice. The printer's address, serial number and access code are read from the `.env` file; you never type them on the page.

**Out.** On the page, the printer's state and the latest reading while the print runs.

## Function and data types

| Function | From | What the tab uses it for |
|---|---|---|
| `get_printer()` | `scaffold/printer.py` | Gives you the printer. With `PRINTER_SIM=1` in `.env` it gives you the simulator instead, which behaves the same way and heats nothing |
| `connect()` | the same | Opens the connection to the printer. If it cannot reach the printer, it gives up after about ten seconds and raises an error (Python's way of saying the call failed) |
| `target` | the same | Which printer you have, in plain words: `Simulator`, or `Printer at` and its address. On the simulator it adds a warning when `bambulabs_api`, the package the real printer needs, is not installed |
| `close()` | the same | Closes the connection that `connect()` opened. The tab receives no more reports until it connects again |
| `page_jobs()` | the same | The job files your page sliced, newest first, found in `out/` by the slice record `slice_stl()` writes beside each one |
| `start(job, plate=1)` | the same | Sends the job file to the printer and asks it to print. It refuses any file that is not in `page_jobs()` |
| `state()` | the same | One word: `IDLE`, `PREPARE`, `RUNNING`, `PAUSE`, `FINISH`, `FAILED` or `UNKNOWN` |
| `telemetry()` | the same | One reading: the state, the nozzle and bed temperatures, the layer number, the percentage done |
| `pause()` | the same | Asks the printer to pause |
| `stop()` | the same | Stops the print |

## The rules

1. **Interface.** The tab talks to the machine only through `scaffold/printer.py`. It opens no network connection of its own and it sends no G-code. So everything the machine is asked to do passes through one file that the teaching team has read.

2. **Start command.** Opening the tab connects and reads. It sends nothing. A print starts when you press Print, and the terminal then shows the name of the file that was sent. A Connect button connects again at any time: it calls `close()`, then `get_printer()` and `connect()`, and `get_printer()` reads `.env` afresh, so after you correct a printer line in `.env` you press Connect, not restart the page. Beside the state the tab shows `target`, so you can always see whether you are on the simulator or the printer, and after Connect it shows the new one.

3. **Job file.** You send a job file your own page made: slice on the Slice tab, then send. The tab lists the jobs from `page_jobs()`, reading them from `out/` each time, so a job sliced before a restart of the page can still be printed. Before you press Print, the tab shows the name of the job file it will send. You do not send a file you exported by hand from OrcaSlicer, and you do not send a `.gcode` file: `start()` refuses both, and the tab shows its message. A `.gcode` file would otherwise upload without any complaint and be ignored by the printer, which looks exactly like a print that failed silently.

4. **State transition.** The printer remembers the last job. Before you press Print, write down the state it is reporting. Your job has started only when the state has changed to `PREPARE` or `RUNNING` after that moment. `start()` returning `True` means the message was sent, not that the printer took the job, so it is never your evidence.

5. **Telemetry.** While the print runs, the tab shows the latest reading, the layer number and the seconds since you pressed Print, refreshed every two seconds. Temperatures are shown to one decimal place: the printer sends values such as `232.90625`, and the last digits mean nothing at the accuracy of its sensor. The layer number is how you know printing has started: the state reads `RUNNING` through the whole heat-up, while the layer number stays at 0. So a print that shows `RUNNING` and layer 0 at 300 seconds has not put down any plastic yet. `UNKNOWN` means the printer has not reported yet, which is normal in the first seconds after connecting, and it is shown as that, not as an error. `PAUSE` is shown too: the machine pauses itself sometimes, and that is not a fault. On its first print after standing idle, the machine may stop by itself about eighty seconds in and wait for someone to press OK on its screen. That is not a fault either: stay at the printer until it goes back to `RUNNING`.

6. **Fault handling.** If the printer reports `FAILED`, or a call raises, including the call that opens the connection, the tab shows the text. The tab has a Stop button that calls `stop()`. The printer stops about four seconds later and then reports `FAILED`, and its own touchscreen says “Printing Canceled”: that is what a stop you asked for looks like, not a fault. The printer's own touchscreen stops a print too, and either way is proper. The nozzle stays hot afterwards, so nothing is safe to touch because you stopped it. Commands are limited to one every five seconds: a command sent sooner raises `RateLimited`, and the tab shows that rather than swallowing it. An error message stays on the tab until the state next changes. The buttons follow the state as it is reported, without reloading the page: Stop can be pressed while the state is `PREPARE`, `RUNNING` or `PAUSE`, and Print cannot.

7. **Pause.** The tab has a Pause button that calls `pause()`. It can be pressed only while the state is `RUNNING`, and nothing else in the page calls `pause()` in Week 3. Your evidence is the state, as in rule 4: the pause has happened when the printer reports `PAUSE`, not when you press the button. The tab shows the seconds since Print at which you pressed, only if `pause()` did not raise, as "Paused from this page at 404.2 s". During `PAUSE`, the tab shows one line saying where the pause came from: "Paused from this page at 404.2 s" or "Paused by the printer" (rule 5). It does not repeat this in a second message. There is no Resume button: you resume on the printer's own touchscreen, after looking at the part. While paused, the nozzle cools, and the longer the pause, the colder it gets. After you resume, the printer reports `RUNNING` while it heats up again, and the layer number stays the same until it prints. So after a pause, as at the start, the layer number tells you that printing has started again: a print paused at layer 49 shows `RUNNING` and layer 49 for about a minute after you resume, then layer 50.

## The decisions

**Rule 1** is the difference between a machine you can reason about and one you cannot. Everything the machine is asked to do is in one file, so when something unexpected happens there is one place to look.

**Rule 3** exists because this failure is silent. The upload succeeds, the printer says nothing, and the job never runs. The slice record is kept on disk rather than in the page's memory because a page restarts often, and a restart used to make a correctly sliced job unprintable.

**Rule 2's label** exists because the simulator and the printer look the same on the page. Without it, a job meant for the simulator can go to the real machine, and a missing package shows up only at the printer.

**Rule 2's Connect button** exists because the printer lines in `.env` are easy to mistype. When the page read them only at startup, a corrected line did nothing until a restart, and the restart then forgot the jobs: two lost steps for one typing error.

**Rule 4** exists because the printer goes on reporting the previous job's state for a few seconds after Print. A program that trusted the first report read the old `FINISH` and reported the new job complete 1.5 seconds after Print.

**Rule 6** exists because a stop looks like a failure. The word the printer sends is `FAILED`, whether you asked for the stop or the machine hit a problem, so the tab has to say which one happened.

**Rule 7** exists because the decision to go on belongs to someone at the machine. A pause means something looked wrong, so the page can pause the print but only a person standing at the printer, having looked at the part, starts it again. In Week 4 the Automate tab will press Pause for you; resuming stays with a person. Measured on the A1 mini: `PAUSE` is reported 3 to 5 seconds after the press. While paused, the nozzle cools from 240 °C to about 124 °C in two minutes and about 90 °C in four. After the resume, the layer number changes about a minute later (54 seconds after a two-minute pause, 62 after a four-minute one).

**The simulator** exists so that the tab can be built and tested before a printer is free, and so that a broken machine does not stop your work. A tab that only works against real hardware has not been tested.

## How to check it

**On the simulator.** Set `PRINTER_SIM=1` in `.env`. Press Print. The state goes `PREPARE`, then `RUNNING`, then `FINISH`, and the page keeps up. Press Print again at once: the tab says the printer is busy and refuses.

**Stale state.** With the simulator still reporting `FINISH` from that run, press Print again. The tab must not report the new job finished. If it does, rule 4 is not applied.

**On the machine.** Set `PRINTER_SIM=0`, fill in the three printer lines of `.env` from the printer's own touchscreen, and print your own coupon. The tab shows the state changing from `PREPARE` to `RUNNING`, and the reading keeps up until `FINISH`.

**A corrected address.** Set `PRINTER_SIM=0` and put a wrong `PRINTER_IP` in `.env`, then open the tab: it shows that the printer is not reachable. Correct the line and press Connect, without restarting the page. The tab connects.

**Simulator or printer.** Change `PRINTER_SIM` in `.env` and press Connect: the label beside the state changes between `Simulator` and `Printer at` your printer's address. If the simulator's label warns that `bambulabs_api` is not installed, install it before you go to the printer.

**A restart.** Slice your coupon, stop the page and start it again. The job is still listed, and Print sends it. Then copy that job file under a new name and try to send the copy: the tab shows that it was not sliced by your page.

**A stop.** With the simulator running a print, press Stop. The state goes to `FAILED` and the tab says the print was stopped, not that something went wrong.

**A pause.** On the simulator, press Pause while the state is `RUNNING`. The state goes to `PAUSE` and the tab shows the seconds at which you pressed. The simulator then resumes by itself after 30 seconds (8 with `PRINTER_SIM_FAST=1`), as if someone had pressed resume on its screen, and reports `RUNNING` while the layer number stays the same and the nozzle heats back up. During the simulator's own pause (rule 5), the tab shows that you did not press Pause, Stop can be pressed and Print cannot.

**A pause on the machine.** Print the stringing test part (`stringing_test.stl`, sliced with retraction off so that it strings on purpose) and press Pause when you see the first hairs between the pillars. Note the layer number and both times: your press, and the `PAUSE` report. Look at the part, then resume on the printer's screen.

**The viva.** The TA asks what happens between your pressing Print and the first plastic on the plate, and which of your readings tells you that it has started.
