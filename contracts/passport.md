# Contract: the Passport tab

Stage 4. A part's record meets the person who decides whether to use the part. The Passport tab shows one part's record to a brand's quality control (QC) manager in the fashion sector. The QC manager decides whether a printed accessory part (the clasp assembly made of the base, the two pegs and the cap) goes into the production run.

## What goes in and what comes out

**In.** A part number, picked from a list or typed by the QC manager, such as `3bb338ef-01`. The QC manager reads it from the part's identifier (a felt-pen dot, or the engraved `S4-01` on the base) on the Track tab's table. Then the record `out/records/<part number>.json` and the files in its archive folder `prints/<...>/` (see `contracts/tracking.md`).

**Out.** On the page: one passport card for the part, with links to its archived print data. When the QC manager presses a decision button: one event added to the record.

The Passport tab sends nothing to the printer. It changes a record only by adding the decision event (rule 4 of `contracts/tracking.md`'s rule 3). It shows nothing from `.env`.

## Function and data types

```python
def passport(part_number: str) -> dict
```

`passport` reads one record and returns only the lines the QC manager needs (rule 2).

| Field | What it holds | Unit |
|---|---|---|
| `part_number`, `identifier` | Which part this is, and how to tell it apart in hand | none |
| `print` | One line: finished or failed, how many times Automate paused it, and whether a person resumed it | none |
| `job` | The part's STL, the process profile and the date it was sliced, from the `.slice` record | none |
| `inspection` | The newest event from the inspection station, or `Not inspected` | none |
| `assembly` | The part it is fitted in, or the parts it holds, as part numbers | none |
| `status` | The record's `status`; for `rejected`, the reason | none |
| `print_data` | Links to the files in the archive folder | none |

| Function | From | What the tab uses it for |
|---|---|---|
| `load_record(part_number)` | `app/track.py` | The part's record |
| `add_event(...)` | `app/track.py` | The decision event |

## The rules

1. **One part.** The tab shows the passport of exactly one part number. If no record has that number, it says `No record for <number>` and shows nothing else.

2. **Only what the decision needs.** The card shows the fields in the table above, in that order, and nothing else. It does not show temperatures, every event, or the fingerprint. Those stay in the record and the archive, reached by the links. So the passport of `3bb338ef-01` fits on one screen.

3. **Print line.** The print line comes from the record's events. It names `finished` or `failed`. It gives the number of Automate pause events, written `no pause` for 0. It says `resumed by the operator` if a pause was followed by `Print finished`. So a print with one Automate pause that ended in `FINISH` reads `Finished; paused once by Automate; resumed by the operator`.

4. **Decision.** Two buttons: **Accept for production run** and **Reject**. Reject needs a reason. Each adds one event at the station `Brand QC (passport)` and sets `status` to `accepted` or `rejected` (rules 3 and 8 of `contracts/tracking.md`). The buttons are disabled for a part whose `status` is `rejected` or `accepted`, or `printing`.

5. **Links follow the assembly.** Each part number in the assembly line opens that part's passport. So from the cap you can open each peg, and from a peg the cap.

6. **Print data links.** Each archived file is one link, served from `prints/` only. If `print_folder` is empty, the line says `Not archived` and there are no links.

**2026-10-11:** 7. **Full record on request.** The card has a **Full record** button, and the Print, Inspection, Assembly and Status lines each have a **details** link. Each opens a side panel over the page with every event of the record, oldest first, with its time, station and what, and a link to the record's JSON file. A details link marks the events behind that line (Print: the Print tab, printer, Automate and Track tab stations; Inspection and Assembly: that station; Status: the events that set `status`) and fades the rest. The panel only reads: it adds nothing to the record. It closes with its close button, Esc or a click beside it, and opening another part closes it. So the card stays one screen (rule 2), and the QC manager can still check any line they doubt.

## The decisions

**The stakeholder** is a brand's QC manager, chosen over the boutique buyer and the end customer. The QC manager is the person who decides whether a part enters the production run, and that decision needs the print and inspection evidence.

**Rule 2** hides the raw data because the QC manager decides from a summary: did the print run cleanly, was the part measured, what is it fitted to. The record holds every event for anyone who needs to check the summary.

**2026-10-11:** rule 7 keeps rule 2's summary and puts the evidence one click away, in a panel the QC manager opens only for a line they doubt. Chosen over a list of events under the card, which would push the decision buttons off the screen, and over sending them to the Track tab, which is the shop floor's page, not theirs.

**Rule 4** adds the decision as an event, chosen over a separate decisions file, so the record of each part holds its whole history from Print to the QC decision.

## How to check it

After the Track tab checks in `contracts/tracking.md`, before running, write down what you expect each line of `3bb338ef-01`'s passport to say.

- **Open `3bb338ef-01`.** The lines match what you wrote, the assembly line names the cap, and the print data links open the archived telemetry log and job file.
- **Open a part number that does not exist.** It shows `No record for ...`.
- **Press Accept.** The record's last event is the acceptance at `Brand QC (passport)`, and both buttons are now disabled.
- **Reject a part without a reason.** Nothing is added.

The viva. The TA asks: your record holds every event, so why does your passport leave most of them out, and how would the QC manager check a line they doubt?

**2026-10-11:** Open `3bb338ef-01` and press its Inspection line's **details**. The side panel lists every event of `out/records/3bb338ef-01.json`, the inspection events are marked, and the count beside **All** equals the number of events in the file. Press Accept with the panel open: the panel then ends with the acceptance at `Brand QC (passport)`. Press Esc: the panel closes and the card is unchanged.
