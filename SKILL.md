---
name: death-date
description: Reverse-engineer your own Apple Health export. Maps everything your Apple Watch logged about you (heart rate density, single heartbeats, sleep, GPS routes, birth date) and models your death date from vo2max and resting heart rate against the US SSA life table, with the one habit that moves it. Use when someone shares an Apple Health export (export.zip, apple_health_export folder, export.xml) or asks what their Apple Watch knows about them, their fitness age, when they will die, or their death date.
---

# death-date

Everything runs locally with the Python 3 standard library. The scripts sit next to this file, in `scripts/` of this skill's base directory. Use `python3` on macOS and Linux, `python` on Windows.

## Rules

- The export is medical data. Read-only: never upload it, never paste raw records into the chat, never print the user's exact GPS spot or full birth date.
- Report only what the scripts measured. The death date is a model built on the SSA life table and four studies, say so once in plain words, then give the numbers straight. No motivational fluff, no sugarcoating.
- If the user wants a screenshot, rerun with `--mask`.

## 1. Find the export

Use the file the user named. Otherwise look for `export.zip`, `apple_health_export/` or `export.xml` in the current folder, then in `~/Downloads`. The zip can be passed as is, no need to unzip.

No export yet: tell them to open the Health app on the iPhone, tap the profile picture in the top right, tap Export All Health Data, and get the zip onto this computer (AirDrop, Files, a cable). Wait until it's there.

## 2. Map it

```
python3 <skill dir>/scripts/watch_feed.py <export> --json watch.json
```

It streams export.xml (fine at several GB) and prints the record map, the `[privacy]` block and 3-year trends. Tell the user, in your words: how many records over how many years, how often the watch sampled their heart, how many single heartbeats it kept, nights of sleep, how many workout routes and what share start from one spot, that the birth date is stored, and which night their HRV is lowest after.

## 3. The death date

```
python3 <skill dir>/scripts/death_date.py watch.json --big
```

- "no birth date": ask age and sex, rerun with `--age N --sex male|female`.
- "no vo2max": stop here and say why. The watch records vo2max on outdoor walks, runs and hikes. No guessing from steps.

## 4. Dashboard

```
python3 <skill dir>/scripts/dashboard.py watch.json --new-window
```

Opens a full-screen terminal dashboard in a new terminal window: the coin-flip year in giant LED digits, the four curves, what the watch kept. Tell the user to look at the new window, it closes on q.

If they want to watch their own data stream: `python3 <skill dir>/scripts/live.py beats|hr|sleep|gps|survival <export>`.

## How to answer

End with three headings:

**Your numbers**: vo2max with its percentile and fitness age, resting heart rate with its percentile, the yearly risk multiplier and its two parts.

**Your window**: coin-flip year (50% chance they're dead by then), the death window (middle 50% of outcomes), an average person their age for comparison.

**The one habit**: zone 2, 3x40 min a week, and what vo2max +10% does to the coin-flip year.

## How the model works

Yearly death probability from `data/ssa_period_life_table.csv` for their sex, from their exact age. Multiplied by 0.87 for every MET (3.5 ml/kg/min) their recent vo2max sits above the FRIEND registry median for their age and sex (Kodama et al., JAMA 2009, 33 studies), and by 1.16 for every 10 bpm their resting heart rate sits above the CDC NHANES mean (Jensen et al., Heart 2013, adjusted for fitness). Proportional hazards, survival curve year by year to 119, coin-flip year where it crosses 50%.
