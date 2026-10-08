# Contract: <name>

_Replace each line in italics with your own text, then delete the italic line. `toolpath.md` is a finished example._

**How to write it.** Write for someone meeting each idea for the first time. Explain every technical word in plain words in the sentence where it first appears, then use one word per thing throughout: never a synonym. Keep sentences short, one idea each, and say plainly what happens and what does not. A contract states what is true and what the code must do, not how you found it out: no dates, and no stories about tests. Keep it to what goes in and what comes out; where the data goes afterwards belongs in the lab brief or the slides. `toolpath.md` follows all of this.

_The stage, then one sentence: whose code meets whose. For example: "Stage 3. The detector's result meets the pause decision in the application."_

## What goes in and what comes out

_In plain words: what does this part of the program receive, and what does it give back? Use real file names. Also say what does not go in or come out._

## Function and data types

_For a function you write: its name, what it takes and what it returns, written as a Python definition, then the fields of each data type it uses, each with its unit. For functions you only call: a table with the columns Function, From, and What it is used for. Each cell says only what its column asks; rules and timings go in the sections below._

```python
def name(argument: type) -> type
```

## The rules

_Numbered rules the code must follow. Start each with a bold label saying what it is about. Then the rule, written so that it can be tested. Then "So" and what it means in a concrete case in your own files: a file name, a line, a value. A rule says what the code must do, not why; the reason goes in the decisions. Point to other rules by number instead of repeating them._

1. 

## The decisions

_Why each rule exists. Where there was a real alternative, write "Chosen over" and name it, then give the reason as a plain fact about the machine or the file, for example "the A1 mini reports `PAUSE` about 3 seconds after the command". Measured values belong here. A rule with only one sensible way to write it needs no entry._

## How to check it

_The test that shows the code obeys the contract: what you will work out before running the code, and the result that counts as a pass, as plain numbers or states. From Stage 3 this is behaviour you can observe, first on the simulator and then on the printer, for example "after a stringing result, the printer's state reads `PAUSE`". End with one question a reviewer could ask to test that you understand the rules._
