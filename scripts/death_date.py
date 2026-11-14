#!/usr/bin/env python3
"""Model your death date from your own Apple Watch data.

  python3 scripts/death_date.py export.zip               # parse the export, fit the model
  python3 scripts/death_date.py watch.json                # reuse `watch_feed.py --json` output
  python3 scripts/death_date.py export.zip --big          # the year in giant digits
  python3 scripts/death_date.py export.zip --age 23 --sex male   # if the export has no birth date

How it works. It's a model, not medicine:
  1. baseline: US SSA period life table for your sex, from your exact age (data/ssa_period_life_table.csv)
  2. cardio fitness: your recent vo2max vs the FRIEND registry median for your age and sex.
     Each 1 MET (3.5 ml/kg/min) of difference = x0.87 yearly risk of dying (Kodama et al., JAMA 2009)
  3. resting heart rate vs the US mean for your age and sex (CDC NHANES 1999-2008).
     Each 10 bpm of difference = x1.16, already adjusted for fitness (Jensen et al., Heart 2013)
  4. proportional hazards: your yearly death probability = 1 - (1 - q)^HR, survival curve from there
  5. coin-flip year = the year your survival curve crosses 50%. Death window = 25% to 75% of outcomes
  6. the one habit: the same model with vo2max +10%
"""
import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import datetime
import random

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
KODAMA_PER_MET = 0.87
JENSEN_PER_10BPM = 1.16
MET = 3.5
AMBER, RED, GREEN, BOLD, DIM = "38;5;214", "1;38;5;203", "38;5;114", "1", "38;5;244"

FONT = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    " ": ["00000"] * 7,
}


def rows(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return list(csv.DictReader(line for line in f if not line.startswith("#")))


def life_table(sex):
    return {int(r["age"]): float(r["qx_" + sex]) for r in rows("ssa_period_life_table.csv")}


def friend(sex):
    return [(int(r["age_lo"]), int(r["age_hi"]), float(r["p25"]), float(r["p50"]), float(r["p75"]))
            for r in rows("friend_vo2max_2015.csv") if r["sex"] == sex]


def nhanes(sex, age):
    for r in rows("nhanes_resting_pulse.csv"):
        if r["sex"] == sex and int(r["age_lo"]) <= age <= int(r["age_hi"]):
            return r
    return [r for r in rows("nhanes_resting_pulse.csv") if r["sex"] == sex][0 if age < 20 else -1]


def vo2_median_at(age, table):
    mids = [((lo + hi + 1) / 2, p50) for lo, hi, _, p50, _ in table]
    if age <= mids[0][0]:
        return mids[0][1]
    if age >= mids[-1][0]:
        return mids[-1][1]
    for (a1, v1), (a2, v2) in zip(mids, mids[1:]):
        if a1 <= age <= a2:
            return v1 + (v2 - v1) * (age - a1) / (a2 - a1)


def vo2_percentile(vo2, age, table):
    group = next((g for g in table if g[0] <= age <= g[1]), table[0] if age < table[0][0] else table[-1])
    sd = (group[4] - group[2]) / 1.349
    return 50 * (1 + math.erf((vo2 - group[3]) / (sd * math.sqrt(2))))


def fitness_age(vo2, table):
    mids = [((lo + hi + 1) / 2, p50) for lo, hi, _, p50, _ in table]
    for (a1, v1), (a2, v2) in zip(mids, mids[1:]):
        if v2 <= vo2 <= v1:
            return a1 + (v1 - vo2) / (v1 - v2) * (a2 - a1)
    (a1, v1), (a2, v2) = (mids[0], mids[1]) if vo2 > mids[0][1] else (mids[-2], mids[-1])
    return max(18.0, min(95.0, a1 + (v1 - vo2) / (v1 - v2) * (a2 - a1)))


def rhr_percentile(rhr, ref):
    pts = [(float(k[1:]), float(v)) for k, v in ref.items() if k.startswith("p") and v]
    pts.sort()
    if rhr <= pts[0][1]:
        return pts[0][0]
    for (p1, v1), (p2, v2) in zip(pts, pts[1:]):
        if v1 <= rhr <= v2:
            return p1 + (p2 - p1) * ((rhr - v1) / (v2 - v1) if v2 > v1 else 1)
    return 99.5


def survival(q, age, hr):
    a0 = int(age)
    s = [1.0]
    for x in range(a0, 120):
        s.append(s[-1] * (1 - q.get(x, 1.0)) ** hr)
    s_age = s[0] + (s[1] - s[0]) * (age - a0)
    return a0, s, s_age


def age_at(p, a0, s, s_age):
    """Age at which survival, counted from today, falls to p."""
    target = p * s_age
    for i in range(1, len(s)):
        if s[i] <= target:
            hi, lo = s[i - 1], s[i]
            return a0 + i - 1 + ((hi - target) / (hi - lo) if hi > lo else 0)
    return float(a0 + len(s) - 1)


def coin_flip(q, age, hr):
    a0, s, s_age = survival(q, age, hr)
    return {p: age_at(p, a0, s, s_age) for p in (0.75, 0.5, 0.25)}


def fit(summary, age=None, sex=None, habit_gain=0.10):
    """summary: the dict from watch_feed.analyze() / the 'summary' block of its JSON."""
    vo2 = (summary.get("vo2max") or {}).get("new")
    rhr = (summary.get("resting_hr") or {}).get("new")
    if not vo2:
        raise ValueError("no vo2max in this export. The watch records it on outdoor walks, runs or hikes: "
                         "do a few, then export again")
    if not rhr:
        raise ValueError("no resting heart rate in this export, can't fit the model")
    sex = sex or summary.get("sex") or "male"
    now = datetime.fromisoformat(summary["export_date"]) if summary.get("export_date") else datetime.now()
    age_note = "from --age"
    if age is None:
        if not summary.get("dob"):
            raise ValueError("no birth date in this export: pass --age (and --sex)")
        dob = datetime.strptime(summary["dob"], "%Y-%m-%d")
        age = (now - dob).days / 365.2425
        age_note = "birth date in the export"
    q, table, ref = life_table(sex), friend(sex), nhanes(sex, age)
    ref_vo2, ref_rhr = vo2_median_at(age, table), float(ref["mean"])
    hr_fit = KODAMA_PER_MET ** ((vo2 - ref_vo2) / MET)
    hr_rhr = JENSEN_PER_10BPM ** ((rhr - ref_rhr) / 10)
    hr = hr_fit * hr_rhr
    now_dec = now.year + (now.timetuple().tm_yday - 1) / 365.25

    def year(a):
        return now_dec + (a - age)

    you, avg = coin_flip(q, age, hr), coin_flip(q, age, 1.0)
    hr_fit_better = KODAMA_PER_MET ** ((vo2 * (1 + habit_gain) - ref_vo2) / MET)
    better = coin_flip(q, age, hr_fit_better * hr_rhr)
    return dict(
        sex=sex, age=age, age_note=age_note, now=now.isoformat(),
        vo2=vo2, vo2_ref=ref_vo2, vo2_pct=vo2_percentile(vo2, age, table), fitness_age=fitness_age(vo2, table),
        rhr=rhr, rhr_ref=ref_rhr, rhr_pct=rhr_percentile(rhr, ref),
        hr=hr, hr_fit=hr_fit, hr_rhr=hr_rhr,
        coin_flip_age=you[0.5], coin_flip_year=year(you[0.5]),
        window=(year(you[0.75]), year(you[0.25])),
        avg_coin_flip_year=year(avg[0.5]),
        habit_gain=habit_gain, habit_year=year(better[0.5]), habit_years_back=better[0.5] - you[0.5],
        sources=["SSA period life table (ssa.gov/oact/STATS/table4c6.html)",
                 "Kodama et al., JAMA 2009;301(19):2024-2035",
                 "Jensen et al., Heart 2013;99(12):882-887",
                 "Kaminsky et al., Mayo Clin Proc 2015;90(11):1515-1523 (FRIEND)",
                 "Ostchega et al., NHSR No. 41, 2011 (CDC NHANES 1999-2008)"],
    )


def print_model(m, out, paint):
    man = {"male": "men", "female": "women"}[m["sex"]]
    one = {"male": "man", "female": "woman"}[m["sex"]]
    pct = m["vo2_pct"]
    rank = f"bottom {max(1, round(pct))}%" if pct < 50 else f"top {max(1, round(100 - pct))}%"
    fa = m["fitness_age"]
    fa_txt = "under 20" if fa < 20 else ("75+" if fa > 75 else f"{fa:.0f}")
    out(f"[model] you: {m['sex']}, {m['age']:.0f} ({m['age_note']})", 0.8)
    out(f"[model] vo2max {m['vo2']:.1f}: {rank} of {man} your age (FRIEND registry), fitness age {fa_txt}", 1.4)
    rp = m["rhr_pct"]
    rhr_txt = f"higher than {min(99, int(rp))}% of {man} your age" if rp >= 50 else f"lower than {int(100 - rp)}% of {man} your age"
    out(f"[model] resting HR {m['rhr']:.0f}: {rhr_txt} (CDC NHANES)", 1.4)
    out(f"[model] your yearly risk of dying: x{m['hr']:.2f} an average {one} your age", 1.0)
    out(f"          fitness x{m['hr_fit']:.2f} (Kodama 2009, 33 studies) * resting HR x{m['hr_rhr']:.2f} (Jensen 2013)", 1.2)
    out("[model] fitting it to the SSA life table, one year at a time, to age 119", 2.0)
    out(paint(f"[model] coin-flip year: {int(m['coin_flip_year'])}  (50% chance you're dead by then)", RED), 2.0)
    out(f"[model] death window: {int(m['window'][0])}-{int(m['window'][1])}  (the middle 50% of outcomes)", 1.2)
    out(f"[model] an average {one} your age: coin-flip year {int(m['avg_coin_flip_year'])}", 1.2)
    out(paint("[model] the one habit: zone 2, 3x40 min a week", GREEN), 0.8)
    out(paint(f"          vo2max +{m['habit_gain'] * 100:.0f}% -> coin-flip year {int(m['habit_year'])}, "
              f"{m['habit_years_back']:+.1f} years back", GREEN), 1.0)
    out(paint("[model] a model, not medicine: SSA life table + 4 studies, sources in the README", DIM), 0.3)


def big(text):
    lines = []
    for r in range(7):
        lines.append("  ".join("".join("██" if px == "1" else "  " for px in FONT[ch][r]) for ch in text))
    return lines


def print_big(year_text, caption, paint, roll=False):
    if roll:
        for _ in range(14):
            fake = str(random.randint(2030, 2099))
            for line in big(fake):
                print(paint(line, DIM))
            sys.stdout.write("\x1b[7A")
            sys.stdout.flush()
            time.sleep(0.09)
    print("\n".join(paint(line, RED) for line in big(year_text)))
    print(paint(caption, RED))


def main():
    ap = argparse.ArgumentParser(description="Model your death date from your Apple Watch data.")
    ap.add_argument("input", help="export.zip / apple_health_export folder / export.xml, or a watch.json")
    ap.add_argument("--age", type=float, help="your age, if the export has no birth date")
    ap.add_argument("--sex", choices=("male", "female"), help="if the export has no biological sex")
    ap.add_argument("--big", action="store_true", help="print the coin-flip year in giant digits")
    ap.add_argument("--demo", action="store_true", help="paced output for screen recording")
    ap.add_argument("--json", metavar="FILE", help="write summary + model to a JSON file")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    from watch_feed import analyze, enable_ansi, parse
    color = sys.stdout.isatty() and not args.no_color and enable_ansi()

    def paint(s, code):
        return f"\x1b[{code}m{s}\x1b[0m" if color else s

    def out(s="", pause=0.0):
        print(s, flush=True)
        if args.demo:
            time.sleep(pause)

    if args.input.lower().endswith(".json"):
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
        summary = data.get("summary", data)
    else:
        out(f"[model] reading {os.path.basename(os.path.normpath(args.input))} ...", 0.4)
        summary = analyze(parse(args.input))
    try:
        m = fit(summary, age=args.age, sex=args.sex)
    except ValueError as e:
        sys.exit(f"[model] {e}")
    print_model(m, out, paint)
    if args.big:
        print()
        print_big(str(int(m["coin_flip_year"])), f"  coin-flip year. {m['habit_years_back']:+.1f} years if you fix your cardio",
                  paint, roll=args.demo and color)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "model": m}, f, indent=1, default=str)


if __name__ == "__main__":
    main()
