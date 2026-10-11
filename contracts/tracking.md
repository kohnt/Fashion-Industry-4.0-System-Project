# Contract: the Track tab

Stage 4. The printer's job meets the record of each part it makes. The Track tab gives each part a record when Print is pressed, archives the print's data when the print ends, and adds what people at the inspection and assembly stations say happened to the part, until it is fitted into the assembly or rejected.

## What goes in and what comes out

**In.** The job file a person sends from the Print tab, such as `out/stage4_peg_pair__process_0.20mm_standard_a1m.gcode.3mf`, and its `.slice` record beside it (the small text file the slicer writes, holding the job's fingerprint: a code computed from the job file, the same for every part printed from it). The printer's reports while the print runs, through the Monitor tab's log `out/<job stem>_telemetry.csv`, where the job stem is the job file name without `.gcode.3mf`. If they exist for this print: the Automate log `out/<job stem>_automate_log.csv` and the webcam pictures in `out/<job stem>_frames/`. The STL the job was printed from, in `parts/`. Events typed by a person on the Track tab.

**Out.** One record per part: a JSON file (a plain text file of named values) in `out/records/`, named by its part number, such as `out/records/3bb338ef-01.json`. One archive folder per print in `prints/`, such as `prints/stage4_peg_pair_2026-10-08_1400/`. On the page: a table of every record, and a form to add an event.

The Track tab sends no command to the printer. It does not slice, and it does not press Print: the Print tab does. It never edits or deletes an event once written.

## Function and data types

```python
def create_records(job: Path, press_t: float) -> list[dict]
def add_event(part_number: str, station: str, what: str, **fields) -> dict
def fit(holder: str, parts: list[str], station: str) -> None
def archive_print(job: Path, press_t: float, final_state: str, telemetry_csv: Path | None) -> Path
```

`create_records` makes the records for one press of Print. `add_event` adds one event to one record. `fit` records one assembly step on every part in it. `archive_print` makes the archive folder for one print and adds its events.

| Type | Field | What it holds | Unit |
|---|---|---|---|
| Record | `fingerprint` | Copied from the job's `.slice` record | none |
| | `part_number` | The first 8 characters of the fingerprint, a dash, and a two-digit count, such as `3bb338ef-01` | none |
| | `job` | The job file's name | none |
| | `events` | A list of events, oldest first | none |
| | `part` | The STL the job was printed from, from the `.slice` record | none |
| | `print_folder` | The archive folder of the print, empty until it is made | none |
| | `identifier` | How a person holding the part tells it apart, such as `one dot`; empty until marked | none |
| | `fitted_in` | The part number of the part that holds it in the assembly; empty if none | none |
| | `fitted_with` | The part numbers this part holds in the assembly; empty if none | none |
| | `status` | `printing`, `printed`, `failed`, `fitted`, `accepted` or `rejected` | none |
| Event | `time` | When it happened, local time, such as `2026-10-08 14:00:05` | none |
| | `station` | Where it happened, such as `Team malateam inspection` | none |
| | `what` | What happened, in plain words | none |

| Function | From | What the tab uses it for |
|---|---|---|
| `read_job_record(job)` | `scaffold/slicer.py` | The job's `.slice` record, or nothing if the job file has changed since it was sliced |
| `TEAM` | `scaffold/config.py` | The team name in each station's name |

## The rules

1. **One record per part.** When Print is pressed on the Print tab and the printer accepts the job, `create_records` writes one record for each part the job prints: 2 for a job whose name starts `stage4_peg_pair`, 1 for every other job. Each starts with the event `Print pressed` at the station `Team <TEAM> Print tab`, and `status` `printing`. If the printer refuses the job, no record is written. So pressing Print on the peg pair job writes `out/records/3bb338ef-01.json` and `out/records/3bb338ef-02.json`.

2. **Part numbers differ.** The two-digit count is the next number not already used by a record with the same 8 characters. A part number is never reused. So printing the peg pair job a second time writes `3bb338ef-03` and `3bb338ef-04`, and `-01` and `-02` stay as they were.

3. **Events are only added.** `add_event` appends one event at the end of `events` and never changes or removes an earlier one. A mistake is put right by a new event that says so. The fields `identifier`, `fitted_in`, `fitted_with`, `status` and `print_folder` repeat what the events say, so the page can show them without reading every event; each changes only together with the event that says so.

4. **Archive when the print ends.** When the printer reports `FINISH` or `FAILED` for a print started on the Print tab (rule 4 of `contracts/telemetry.md` closes the log there), `archive_print` makes the folder `prints/<short job name>_<YYYY-MM-DD_HHMM>/`. The short job name is the job file name up to the first `__`, and the time is when Print was pressed. If that folder already exists, `_2`, `_3` and so on is added. The folder holds: the telemetry log, the job file, its `.slice` record and its STL. It also holds the Automate log and the webcam pictures, but only those written after Print was pressed. So a stale Automate log from a replay is not archived with a real print. So a peg pair print pressed at 14:00 on 8 October is archived in `prints/stage4_peg_pair_2026-10-08_1400/`.

5. **Events from the print.** After the copy, every record from that press gets these events, in this order:
   - `Print finished` or `Print failed` at the station `Team <TEAM> printer`.
   - One event for each pause in the Automate log, with its layer, label and confidence. If there is no Automate log, one event says `No Automate log for this print`.
   - `Print data archived in prints/...` at the station `Team <TEAM> Track tab`.

   `status` becomes `printed` or `failed`. A print started by the Automate replay on the simulator makes no record and no archive.

6. **A person's events.** The Track tab adds an event only for a record that exists and is not `rejected`. The station is one of `Team <TEAM> inspection` or `Team <TEAM> assembly`, and `what` is not empty. Marking an identifier sets `identifier`. So typing "Taken off the plate from the left; marked with one dot" with identifier `one dot` adds that event and sets `identifier` to `one dot`.

7. **Assembly.** `fit` takes the holder (the part the others are fitted into or under, such as the cap) and the parts it holds. The holder gets one event naming every part it holds and sets `fitted_with`. Each held part gets one event naming the holder and sets `fitted_in`. Every part in it gets `status` `fitted`. A part that is `rejected` or already fitted cannot be fitted. So fitting cap `9c1d…-01` over base `373bd278-01` and pegs `3bb338ef-01` and `3bb338ef-02` writes 4 events, one on each record.

8. **Rejection.** Rejecting a part needs a reason. It adds the event `Rejected: <reason>` and sets `status` to `rejected`. After it, rule 6 adds no more events to that record, so its last event says why it was rejected.

9. **Restart.** If the page is stopped during a print, the archive for that print is not made. Its records keep `status` `printing` and an empty `print_folder`. A person adds an event saying so.

## The decisions

**Rule 1** counts parts by job name because the `.slice` record does not say how many parts a job prints, and the given jobs are fixed: only the peg pair prints 2 parts.

**Rule 2** uses the fingerprint's first 8 characters plus a count. Chosen over a random code, because the first part ties the number to its job at a glance, and the count is what tells the two pegs apart. Their fingerprints are the same, since they come from one job file.

**Rule 3** keeps events append-only because a record is evidence. A record that can be edited cannot show what was known when.

**Rule 4** archives only files written after Print was pressed. The Automate log has one fixed name per job and is written again by every replay, so its date is the only way to tell whose it is.

**Rule 6.** The identifier is a felt-pen dot, chosen over an engraved code or a QR label, because the two pegs are identical and only come apart as they leave the plate. A dot is the quickest mark to make at that moment. The base already carries the engraved `S4-01`, and there is one cap.

## How to check it

On the simulator (`PRINTER_SIM=1`), before running, write down the part numbers you expect from each `.slice` record's fingerprint.

- **Print the peg pair.** `out/records/` gains `3bb338ef-01.json` and `3bb338ef-02.json`, each with `Print pressed`.
- **After `FINISH`.** `prints/stage4_peg_pair_<date>_<time>/` holds the telemetry log, the job file, its `.slice` record and `stage4_peg_pair.stl`. Both records now end with `Print finished`, the Automate line and `Print data archived in prints/...`.
- **Reprint the peg pair.** It gives `-03` and `-04`, and `-01` is unchanged.
- **Fit the cap over the base and the two pegs.** The cap's record names all three, and each peg's record names the cap.
- **Reject a part with a reason.** Its last event is the rejection, and the Track tab refuses a new event on it.

The viva. The TA asks: the two pegs share one fingerprint, so what tells their records apart, and at which moment could they be mixed up?
