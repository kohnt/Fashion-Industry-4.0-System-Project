# Contracts

A contract is a short page, written before any code exists, that says what one part of your program takes in, what it gives back, and the rules it must follow. You check your agent's code against the contract, and at each lab checkpoint the TA does the same.

The teaching team supplies `toolpath.md`, `ui.md`, `printer.md`, `telemetry.md` and `events.md`, each added to the template before the stage that needs it. You write `verdict.md` in Stage 3, starting from `TEMPLATE.md`.

## What a contract contains

Every contract has the same five headings.

| Heading | What it says |
|---|---|
| What goes in and what comes out | In plain words, what this part of the program takes in and gives back |
| Function and data types | The function's name, what it takes and returns, and the fields of each data type, with units |
| The rules | What the code must do, numbered so you can refer to them |
| The decisions | Why each rule is what it is, and what was considered instead |
| How to check it | The numbers or behaviour that show the code obeys the rules |

## When there is a contract

Only where two people's code meets, where your code meets the printer, or where you meet your program (the page). In Week 1 you work alone, but the toolpath contract still applies: in Week 2 your team keeps one member's `parse_gcode` (the code that reads the G-code file into layers), and everyone's code must then work with it.

## Changing a contract

Never rewrite a contract. To change it, add a line at the end of each section that changed, starting with the date in bold, and leave the old lines in place, so the page keeps its history. Under The rules, write the rule in full after the date, with a new number to add a rule or an existing number to replace one. A new or changed rule usually also needs a dated line under The decisions (why) and How to check it (how to test it).

For example, suppose a teammate's test file is written in inches (`G20`). Rule 1 of `contracts/toolpath.md` ignores every command except three, so the parser skips the `G20` line and reads every size as millimetres, 25.4 times too small. Your team adds a rule, and the amended parts of the contract then read:

> **The rules**
>
> 1. **Syntax.** Only `G0` and `G1` lines can move the nozzle. Everything after a `;` is a comment and is ignored, and so are blank lines and every other command, with three exceptions that change how E is read: `M82`, `M83` and `G92` (rule 3). ...
>
> Rules 2 to 8, unchanged.
>
> **2026-09-24:** 9. **Units.** A file containing `G20` (inches) is refused with an error.
>
> **The decisions**
>
> **2026-09-24:** rule 9 refuses inch files rather than converting them, so a wrong unit is never drawn silently.
>
> **How to check it**
>
> **2026-09-24:** a copy of the coupon with `G20` added as its first line must stop with an error.

## Contracts this term

| Stage | Contract | Where code meets |
|---|---|---|
| 1. Toolpath | `toolpath.md` | The G-code file meets your `parse_gcode`; the list of layers it returns meets `render_layer` |
| 1. Toolpath | `ui.md` | Your page meets `slice_stl`, your `parse_gcode` and `render_layer`; you meet your program |
| 2. Print | `printer.md` | Your application meets the printer wrapper (`start`, `pause`, `resume`, `stop`, `state`) |
| 3. Data | `telemetry.md` | The printer's status reports meet your `on_report` function |
| 3. Data | `verdict.md` | The stringing detector's result meets the pause decision in your application |
| 4. Automate | `events.md` | Your application's events meet the program that acts on a detected stringing defect |

In Stages 1 and 2 you check the code against numbers you work out by hand before running anything. From Stage 3 you check behaviour, because the output is a live print.
