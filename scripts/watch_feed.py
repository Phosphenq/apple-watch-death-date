#!/usr/bin/env python3
"""Map everything your Apple Watch has logged about you, straight from the Health export.

  python3 scripts/watch_feed.py export.zip                  # privacy map + 3-year trends
  python3 scripts/watch_feed.py export.zip --death          # + the death date model
  python3 scripts/watch_feed.py export.zip --json watch.json
  python3 scripts/watch_feed.py export.zip --mask           # hide name, birth date, GPS before screenshots
  python3 scripts/watch_feed.py export.zip --demo           # same output, paced for screen recording

Input: the export.zip from the Health app (Health > profile picture > Export All Health Data),
the unzipped apple_health_export folder, or export.xml itself. The XML is streamed, so
multi-GB exports work. Read-only: nothing is changed, nothing leaves your machine.
"""
import argparse
import glob
import json
import math
import os
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from array import array
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mask import mask_line  # noqa: E402

HKQ = "HKQuantityTypeIdentifier"
HR, RHR, VO2, HRV = HKQ + "HeartRate", HKQ + "RestingHeartRate", HKQ + "VO2Max", HKQ + "HeartRateVariabilitySDNN"
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
EPOCH = datetime(2000, 1, 1)
HW = re.compile(r"name:([^,>]+),.*?hardware:(.*?),\s*software:")
TRKPT = re.compile(rb"<trkpt\b([^>]*)>")
LAT = re.compile(rb'lat="(-?[\d.]+)"')
LON = re.compile(rb'lon="(-?[\d.]+)"')
SPARK = "▁▂▃▄▅▆▇█"
AMBER, RED, GREEN, BOLD, DIM = "38;5;214", "1;38;5;203", "38;5;114", "1", "38;5;244"


def ts(s):
    """'2023-11-14 08:00:00 -0500' (real exports) or ISO 'T' form -> naive local datetime."""
    try:
        return datetime(int(s[0:4]), int(s[5:7]), int(s[8:10]), int(s[11:13]), int(s[14:16]), int(s[17:19]))
    except (TypeError, ValueError, IndexError):
        return None


class Progress:
    """File wrapper that reports how much of the XML has been read."""

    def __init__(self, f, total, callback):
        self.f, self.total, self.callback, self.done = f, max(total, 1), callback, 0

    def read(self, n=-1):
        chunk = self.f.read(n)
        self.done += len(chunk)
        self.callback(self.done / self.total)
        return chunk


def open_export(path):
    """Return (binary xml stream, xml size, list of gpx loaders, ecg count)."""
    if os.path.isdir(path):
        for cand in (os.path.join(path, "export.xml"), os.path.join(path, "apple_health_export", "export.xml")):
            if os.path.exists(cand):
                path = cand
                break
        else:
            sys.exit(f"no export.xml in {path}")
    if zipfile.is_zipfile(path):
        z = zipfile.ZipFile(path)
        names = z.namelist()
        xml = next((n for n in names if n == "export.xml" or n.endswith("/export.xml")), None)
        if not xml:
            sys.exit(f"{path} has no export.xml inside: is it the zip from Health > Export All Health Data?")
        gpx = [(lambda n=n: z.read(n)) for n in names if n.lower().endswith(".gpx")]
        ecg = sum(1 for n in names if "electrocardiograms/" in n and n.endswith(".csv"))
        return z.open(xml), z.getinfo(xml).file_size, gpx, ecg
    if not os.path.exists(path):
        sys.exit(f"not found: {path}")
    base = os.path.dirname(os.path.abspath(path))
    gpx = [(lambda p=p: open(p, "rb").read()) for p in sorted(glob.glob(os.path.join(base, "workout-routes", "*.gpx")))]
    ecg = len(glob.glob(os.path.join(base, "electrocardiograms", "*.csv")))
    return open(path, "rb"), os.path.getsize(path), gpx, ecg


def parse(path, on_progress=None):
    stream, size, gpx_loaders, ecg = open_export(path)
    src = Progress(stream, size, on_progress) if on_progress else stream
    counts, first, last = Counter(), {}, {}
    sources, hardware, dev_cache = Counter(), Counter(), {}
    hr_t, hr_v = array("q"), array("f")
    series = defaultdict(list)
    sleep_min, inbed_nights = defaultdict(float), set()
    workouts, workout_types, route_refs = 0, Counter(), 0
    rings_days, rings_closed = 0, 0
    beats, me, export_date = 0, {}, None

    ctx = ET.iterparse(src, events=("start", "end"))
    _, root = next(ctx)
    for event, el in ctx:
        if event == "start":
            continue
        tag = el.tag
        if tag == "InstantaneousBeatsPerMinute":
            beats += 1
            continue
        if tag == "Record":
            t = el.get("type") or "?"
            counts[t] += 1
            s = el.get("sourceName")
            if s:
                sources[s] += 1
            dev = el.get("device")
            if dev:
                hw = dev_cache.get(dev)
                if hw is None:
                    m = HW.search(dev)
                    hw = dev_cache[dev] = f"{m.group(1).strip()} ({m.group(2).strip()})" if m else ""
                if hw:
                    hardware[hw] += 1
            dt = ts(el.get("startDate"))
            if dt:
                if t not in first or dt < first[t]:
                    first[t] = dt
                if t not in last or dt > last[t]:
                    last[t] = dt
                try:
                    if t == HR:
                        hr_t.append(int((dt - EPOCH).total_seconds() // 60))
                        hr_v.append(float(el.get("value")))
                    elif t in (VO2, RHR, HRV):
                        series[t].append((dt, float(el.get("value"))))
                    elif t == SLEEP:
                        end = ts(el.get("endDate")) or dt
                        night = (end - timedelta(hours=12)).date()
                        v = el.get("value") or ""
                        if "Asleep" in v:
                            sleep_min[night] += (end - dt).total_seconds() / 60
                        elif v.endswith("InBed"):
                            inbed_nights.add(night)
                except (TypeError, ValueError):
                    pass
            root.clear()
        elif tag == "Workout":
            workouts += 1
            workout_types[(el.get("workoutActivityType") or "").replace("HKWorkoutActivityType", "")] += 1
            route_refs += sum(1 for _ in el.iter("FileReference"))
            root.clear()
        elif tag == "ActivitySummary":
            rings_days += 1
            try:
                if (float(el.get("activeEnergyBurned") or 0) >= float(el.get("activeEnergyBurnedGoal") or 1e9)
                        and float(el.get("appleExerciseTime") or 0) >= float(el.get("appleExerciseTimeGoal") or 1e9)
                        and float(el.get("appleStandHours") or 0) >= float(el.get("appleStandHoursGoal") or 1e9)):
                    rings_closed += 1
            except ValueError:
                pass
            root.clear()
        elif tag == "Me":
            me = dict(el.attrib)
        elif tag == "ExportDate":
            export_date = ts(el.get("value"))
    if on_progress:
        on_progress(1.0)

    return dict(counts=counts, first=first, last=last, sources=sources, hardware=hardware,
                hr_t=hr_t, hr_v=hr_v, series=series, sleep_min=sleep_min, inbed_nights=inbed_nights,
                workouts=workouts, workout_types=workout_types, route_refs=route_refs,
                rings_days=rings_days, rings_closed=rings_closed, beats=beats, me=me,
                export_date=export_date, gpx=gpx_loaders, ecg=ecg, xml_size=size)


def haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def gps_summary(loaders):
    starts, points = [], 0
    for load in loaders:
        data = load()
        first = None
        for m in TRKPT.finditer(data):
            points += 1
            if first is None:
                a, b = LAT.search(m.group(1)), LON.search(m.group(1))
                if a and b:
                    first = (float(a.group(1)), float(b.group(1)))
        if first:
            starts.append(first)
    if not starts:
        return None
    cells = Counter((round(la, 3), round(lo, 3)) for la, lo in starts)
    cell = cells.most_common(1)[0][0]
    near = [p for p in starts if haversine(p, cell) < 400]
    center = (mean(p[0] for p in near), mean(p[1] for p in near))
    home = sum(1 for p in starts if haversine(p, center) <= 150)
    return dict(routes=len(loaders), with_points=len(starts), points=points,
                home_share=home / len(starts), home=center)


def monthly(pairs):
    acc = defaultdict(list)
    for dt, v in pairs:
        acc[(dt.year, dt.month)].append(v)
    return [(f"{y}-{m:02d}", mean(vs)) for (y, m), vs in sorted(acc.items())]


def trend(pairs):
    pts = sorted(p for p in pairs if p[0] and p[1])
    if len(pts) < 10:
        return None
    k = max(2, len(pts) // 10)
    return mean(v for _, v in pts[:k]), mean(v for _, v in pts[-k:])


def spark(values, width=24):
    if not values:
        return ""
    if len(values) > width:
        step = len(values) / width
        values = [mean(values[int(i * step):max(int((i + 1) * step), int(i * step) + 1)]) for i in range(width)]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    return "".join(SPARK[min(7, int((v - lo) / span * 8))] for v in values)


def analyze(p):
    out = {}
    counts = p["counts"]
    total = sum(counts.values())
    starts = [d for d in p["first"].values()]
    ends = [d for d in p["last"].values()]
    out["records"], out["types"] = total, len(counts)
    out["span"] = [min(starts).isoformat() if starts else None, max(ends).isoformat() if ends else None]
    years = (max(ends) - min(starts)).days / 365.25 if starts else 0
    out["years"] = round(years, 1)
    out["export_date"] = p["export_date"].isoformat() if p["export_date"] else None
    out["xml_mb"] = round(p["xml_size"] / 1e6, 1)
    out["top_types"] = [(t.replace(HKQ, "").replace("HKCategoryTypeIdentifier", ""), c,
                         p["first"][t].strftime("%b %Y") if t in p["first"] else "",
                         p["last"][t].strftime("%b %Y") if t in p["last"] else "")
                        for t, c in counts.most_common(15)]
    hw_total = sum(p["hardware"].values()) or 1
    out["devices"] = [(name, c / hw_total) for name, c in p["hardware"].most_common(4)]
    out["sources"] = [s for s, _ in p["sources"].most_common(3)]

    me = p["me"]
    dob = me.get("HKCharacteristicTypeIdentifierDateOfBirth") or None
    sex = (me.get("HKCharacteristicTypeIdentifierBiologicalSex") or "").replace("HKBiologicalSex", "").lower() or None
    out["dob"], out["sex"] = dob, (sex if sex in ("male", "female") else None)

    # heart rate density
    hr_t = sorted(p["hr_t"])
    if hr_t:
        gaps = [b - a for a, b in zip(hr_t, hr_t[1:]) if 0 < b - a <= 60]
        days = len({m // 1440 for m in hr_t})
        night = sum(1 for m in hr_t if (m % 1440) < 7 * 60)
        out["heart_rate"] = dict(readings=len(hr_t), every_min=median(gaps) if gaps else None,
                                 days=days, night_share=night / len(hr_t))
    out["heartbeats"] = p["beats"]

    # sleep
    nights = p["sleep_min"]
    if nights:
        hours = {n: m / 60 for n, m in nights.items() if m > 60}
        last30 = sorted(hours)[-30:]
        work = [h for n, h in hours.items() if n.weekday() not in (4, 5)]
        out["sleep"] = dict(nights=len(hours), avg_h=mean(hours.values()),
                            work_nights_h=mean(work) if work else None,
                            debt_30_h=sum(max(0.0, 8 - hours[n]) for n in last30),
                            monthly=monthly([(datetime(n.year, n.month, n.day), h) for n, h in hours.items()]))

    # HRV after which night is lowest
    by_night = defaultdict(list)
    for dt, v in p["series"].get(HRV, []):
        if dt and dt.hour < 12:
            by_night[(dt.weekday() - 1) % 7].append(v)
    if len(by_night) == 7 and min(len(v) for v in by_night.values()) >= 5:
        avg = {d: mean(v) for d, v in by_night.items()}
        worst = min(avg, key=avg.get)
        rest = mean(x for d, vals in by_night.items() if d != worst for x in vals)
        out["hrv_worst_night"] = dict(night=DAYS[worst], ms=avg[worst], rest_ms=rest)

    for key, rtype in (("vo2max", VO2), ("resting_hr", RHR), ("hrv", HRV)):
        tr = trend(p["series"].get(rtype, []))
        if tr:
            out[key] = dict(old=tr[0], new=tr[1], readings=len(p["series"][rtype]),
                            monthly=monthly(p["series"][rtype]))
    if "sleep" in out and len(out["sleep"]["monthly"]) >= 4:
        m = [v for _, v in out["sleep"]["monthly"]]
        k = max(1, len(m) // 10)
        out["sleep"]["old"], out["sleep"]["new"] = mean(m[:k]), mean(m[-k:])

    out["gps"] = gps_summary(p["gpx"]) if p["gpx"] else None
    out["workouts"] = dict(count=p["workouts"], types=p["workout_types"].most_common(3), routes=p["route_refs"])
    out["rings"] = dict(days=p["rings_days"], all_closed=p["rings_closed"]) if p["rings_days"] else None
    out["ecg"] = p["ecg"]
    return out


def main():
    ap = argparse.ArgumentParser(description="Map what your Apple Watch logged about you.")
    ap.add_argument("export", help="export.zip, the apple_health_export folder, or export.xml")
    ap.add_argument("--death", action="store_true", help="add the death date model (scripts/death_date.py)")
    ap.add_argument("--big", action="store_true", help="with --death: the coin-flip year in giant digits")
    ap.add_argument("--dashboard", action="store_true", help="with --death: open the full-screen dashboard in a new terminal")
    ap.add_argument("--age", type=float, help="your age, if the export has no birth date")
    ap.add_argument("--sex", choices=("male", "female"), help="if the export has no biological sex")
    ap.add_argument("--json", metavar="FILE", help="write everything to a JSON file for death_date.py / dashboard.py")
    ap.add_argument("--mask", action="store_true", help="mask name, birth date, GPS in the output")
    ap.add_argument("--show-home", action="store_true", help="print the exact spot your runs start from")
    ap.add_argument("--demo", action="store_true", help="paced output for screen recording")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()
    args.demo = args.demo or os.environ.get("DEATH_DATE_PACE") == "1"

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    tty = sys.stdout.isatty()
    color = tty and not args.no_color and enable_ansi()

    def paint(s, code):
        return f"\x1b[{code}m{s}\x1b[0m" if color else s

    def out(s="", pause=0.0):
        print(mask_line(s) if args.mask else s, flush=True)
        if args.demo:
            time.sleep(pause)

    state = {"shown": -1.0, "t": 0.0}

    def progress(frac):
        now = time.time()
        if frac < 1.0 and now - state["t"] < 0.15:
            return
        state["t"] = now
        n = int(frac * 24)
        line = f"[watch] streaming export.xml [{'#' * n}{'.' * (24 - n)}] {frac * 100:5.1f}%"
        sys.stdout.write("\r" + line + ("\n" if frac >= 1.0 else ""))
        sys.stdout.flush()

    sync = os.environ.get("DEATH_DATE_SYNC")
    if sync:  # tells `live.py --sync` panes that a run started: they restart from the first record
        with open(sync, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    zipped = os.path.getsize(args.export) / 1e6 if os.path.isfile(args.export) else None
    out(f"[watch] export: {os.path.basename(os.path.normpath(args.export))}"
        + (f" ({zipped:.1f} MB)" if zipped else ""), 0.6)
    t0 = time.time()
    raw = parse(args.export, progress if tty else None)
    a = analyze(raw)
    secs = time.time() - t0

    out(f"[watch] {a['records']:,} records, {a['types']} record types, {a['xml_mb']:,.1f} MB of XML in {secs:.0f} s", 0.9)
    if a["span"][0]:
        out(f"[watch] history: {a['span'][0][:7]} to {a['span'][1][:7]} ({a['years']} years)", 0.6)
    if a["devices"]:
        out("[watch] devices: " + ", ".join(f"{n} {s * 100:.0f}%" for n, s in a["devices"]), 0.6)
    out("", 0.2)
    out(paint("[watch] top record types:", BOLD), 0.4)
    for name, c, f, l in a["top_types"]:
        out(f"  {name:.<36} {c:>9,}  {f} - {l}", 0.12)
    out("", 0.4)

    out(paint("[privacy] what it kept on you:", BOLD), 0.5)
    hr = a.get("heart_rate")
    if hr:
        every = f", one every {hr['every_min']:.0f} min" if hr["every_min"] else ""
        out(f"  heart rate     {hr['readings']:,} readings{every}, day and night, {hr['days']:,} days", 1.2)
    if a["heartbeats"]:
        out(f"  heartbeats     {a['heartbeats']:,} single beats, each timestamped to 1/100 s", 1.2)
    sl = a.get("sleep")
    if sl:
        out(f"  sleep          {sl['nights']:,} nights, every stage, {sl['avg_h']:.1f} h a night on average", 1.0)
    g = a.get("gps")
    if g:
        if args.show_home:
            spot = f"{g['home'][0]:.5f}, {g['home'][1]:.5f}"
        else:
            lat, lon = (str(int(v * 10) / 10) for v in g["home"])
            spot = f"{lat}****, {lon}****"
        out(f"  gps            {g['routes']:,} workout routes, {g['points']:,} points", 0.8)
        out(f"                 {g['home_share'] * 100:.0f}% start within 150 m of one spot: {spot}", 1.4)
    if a["dob"]:
        out(f"  birth date     {a['dob'][:4]}-**-** ({a['sex'] or 'sex not set'})", 0.8)
    if a.get("rings"):
        r = a["rings"]
        out(f"  rings          all three closed on {r['all_closed']:,} of {r['days']:,} days", 0.8)
    if a["ecg"]:
        out(f"  ECG            {a['ecg']} recordings", 0.6)
    w = a.get("hrv_worst_night")
    if w:
        out(f"  HRV            lowest after {w['night']} nights: {w['ms']:.0f} ms vs {w['rest_ms']:.0f} ms the rest of the week", 1.4)
    out("", 0.4)

    out(paint(f"[trend] the full {a['years']}-year history, month by month:", BOLD), 0.5)
    rows = [("vo2max", "vo2max", "ml/kg/min", 1), ("resting_hr", "resting HR", "bpm", 0), ("hrv", "HRV", "ms", 0)]
    for key, label, unit, dec in rows:
        tr = a.get(key)
        if not tr:
            continue
        pct = (tr["new"] - tr["old"]) / tr["old"] * 100
        line = (f"  {label:<11}{tr['old']:>6.{dec}f} -> {tr['new']:<6.{dec}f}{unit:<10}{'(%+.1f%%)' % pct:<10} "
                f"{spark([v for _, v in tr['monthly']])}")
        out(paint(line, AMBER) if key in ("vo2max", "resting_hr") else line, 1.6 if key != "hrv" else 1.0)
    if sl and "old" in sl:
        out(f"  {'sleep':<11}{sl['old']:>6.1f} -> {sl['new']:<6.1f}{'h/night':<10}{'':<10} "
            f"{spark([v for _, v in sl['monthly']])}", 1.0)
        out(f"  {'':<11}sleep debt: {sl['debt_30_h']:.0f} h in the last 30 nights", 0.8)

    result = {"summary": a}
    if args.death:
        from death_date import fit, print_big, print_model
        out("", 0.5)
        try:
            model = fit(a, age=args.age, sex=args.sex)
        except ValueError as e:
            out(f"[model] {e}")
            model = None
        if model:
            print_model(model, out, paint)
            result["model"] = model
            if args.big:
                out("", 0.3)
                print_big(str(int(model["coin_flip_year"])),
                          f"  coin-flip year. {model['habit_years_back']:+.1f} years if you fix your cardio",
                          paint, roll=args.demo and color)
    json_path = args.json or (os.path.join(os.getcwd(), "watch.json") if args.dashboard and "model" in result else None)
    if json_path:
        os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1, default=str)
        out(paint(f"[watch] saved {json_path}", DIM))
    if args.dashboard and "model" in result:
        from dashboard import open_new_window
        open_new_window(argparse.Namespace(input=json_path, age=None, sex=None))


def enable_ansi():
    if os.name != "nt":
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(k32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


if __name__ == "__main__":
    main()
