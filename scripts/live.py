#!/usr/bin/env python3
"""Live views of your own export, streamed in a terminal. Every line is a real record from the file.

  python3 scripts/live.py beats export.zip      # every single heartbeat your watch kept, timestamped
  python3 scripts/live.py hr export.zip         # heart rate readings, every 5 minutes for years
  python3 scripts/live.py sleep export.zip      # every night as a hypnogram strip
  python3 scripts/live.py gps export.zip        # all your workout routes drawn on top of each other
  python3 scripts/live.py survival export.zip   # the death date model, one year of life at a time

Add --once to stop after one pass (default: loop), --speed 2 to go faster.
--sync FILE waits until FILE is touched and restarts from the first record every time it is touched again
(watch_feed.py touches it when DEATH_DATE_SYNC points to it), so a wall of live views starts with the run.
"""
import argparse
import math
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from watch_feed import HR, HRV, SLEEP, LAT, LON, TRKPT, analyze, enable_ansi, open_export, parse, ts  # noqa: E402

RED, AMBER, GREEN, CYAN, VIOLET, BLUE, TEXT, DIM, FAINT = 203, 214, 114, 81, 141, 69, 252, 245, 238
BRAILLE = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
COLOR = True


def c(s, n, bold=False):
    return f"\x1b[{'1;' if bold else ''}38;5;{n}m{s}\x1b[0m" if COLOR else s


class Sync:
    """Start (and restart from the top) when a trigger file is touched."""

    def __init__(self, path):
        self.path = path
        self.stamp = self._mtime()

    def _mtime(self):
        try:
            return os.path.getmtime(self.path) if self.path else None
        except OSError:
            return None

    def wait(self, label):
        if not self.path:
            return
        print(c(f"[{label}] ready, waiting for the run ...", DIM), flush=True)
        while not self.fired():
            time.sleep(0.1)
        self.consume()

    def fired(self):
        m = self._mtime()
        return self.path is not None and m is not None and m != self.stamp

    def consume(self):
        self.stamp = self._mtime()
        if COLOR:
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.flush()


def records(path, wanted):
    stream, _, gpx, _ = open_export(path)
    ctx = ET.iterparse(stream, events=("start", "end"))
    _, root = next(ctx)
    for event, el in ctx:
        if event == "end" and el.tag == "Record":
            if el.get("type") in wanted:
                yield el
            root.clear()


def clock_to_dt(day, text):
    # "2:14:03.27 AM" on the record's day
    try:
        t, ampm = text.split()
        h, mnt, sec = t.split(":")
        h = int(h) % 12 + (12 if ampm.upper() == "PM" else 0)
        return day.replace(hour=h, minute=int(mnt), second=0, microsecond=0) + timedelta(seconds=float(sec))
    except ValueError:
        return None


def beats(path, speed, once, sync):
    print(c("[beats] streaming every single heartbeat stored in HRV sessions ...", DIM))
    allb = []
    for el in records(path, {HRV}):
        day = ts(el.get("startDate"))
        for b in el.iter("InstantaneousBeatsPerMinute"):
            t = clock_to_dt(day, b.get("time") or "")
            if t:
                allb.append((t, int(float(b.get("bpm") or 0))))
    sync.wait("beats")
    while True:
        print(c(f"[beats] {len(allb):,} single heartbeats, each timestamped to 1/100 s", TEXT, True))
        for i, (t, bpm) in enumerate(allb, 1):
            if sync.fired():
                sync.consume()
                break
            room = max(4, shutil.get_terminal_size((100, 30)).columns - 46)
            bar = "█" * max(1, min(room, (bpm - 40) // 4))
            col = RED if bpm >= 100 else AMBER if bpm >= 85 else GREEN
            print(f"{c(t.strftime('%Y-%m-%d %H:%M:%S.') + f'{t.microsecond // 10000:02d}', DIM)}  "
                  f"{c(f'#{i:>7,}', FAINT + 6)}  {c(f'{bpm:>3} bpm', col, True)}  {c(bar, col)}", flush=True)
            time.sleep(0.016 / speed)
        if once:
            return


def heart_rate(path, speed, once, sync):
    print(c("[hr] streaming heart rate readings ...", DIM))
    rows = [(ts(el.get("startDate")), float(el.get("value"))) for el in records(path, {HR})]
    rows.sort()
    sync.wait("hr")
    while True:
        print(c(f"[hr] {len(rows):,} readings, day and night", TEXT, True))
        for t, v in rows:
            if sync.fired():
                sync.consume()
                break
            room = max(4, shutil.get_terminal_size((100, 30)).columns - 42)
            night = t.hour < 7
            col = RED if v >= 110 else AMBER if v >= 95 else CYAN
            print(f"{c(t.strftime('%a %Y-%m-%d %H:%M'), DIM)}  {c(f'{v:>5.0f}', col, True)} bpm  "
                  f"{c(chr(9608) * min(room, int((v - 40) / 5)), col)}{c('  asleep' if night else '', VIOLET)}", flush=True)
            time.sleep(0.012 / speed)
        if once:
            return


STAGE = {"AsleepDeep": ("█", BLUE), "AsleepCore": ("▆", CYAN), "AsleepREM": ("▃", VIOLET),
         "Awake": ("▁", RED)}


def sleep_strips(path, speed, once, sync):
    print(c("[sleep] rebuilding every night from sleep stages ...", DIM))
    nights = {}
    for el in records(path, {SLEEP}):
        stage = (el.get("value") or "").replace("HKCategoryValueSleepAnalysis", "")
        if stage not in STAGE:
            continue
        a, b = ts(el.get("startDate")), ts(el.get("endDate"))
        nights.setdefault((b - timedelta(hours=12)).date(), []).append((a, b, stage))
    keys = sorted(nights)
    sync.wait("sleep")
    while True:
        print(c(f"[sleep] {len(keys):,} nights, every stage    {c(chr(9608), BLUE)} deep  {c(chr(9606), CYAN)} core  "
                f"{c(chr(9603), VIOLET)} REM  {c(chr(9601), RED)} awake", TEXT, True))
        for k in keys:
            if sync.fired():
                sync.consume()
                break
            width = max(30, shutil.get_terminal_size((100, 30)).columns - 30)
            segs = sorted(nights[k])
            start, end = segs[0][0], segs[-1][1]
            span = (end - start).total_seconds() or 1
            strip = []
            for i in range(width):
                t = start + timedelta(seconds=span * (i + 0.5) / width)
                st = next((s for a, b, s in segs if a <= t < b), "Awake")
                ch, col = STAGE[st]
                strip.append(c(ch, col))
            asleep = sum((b - a).total_seconds() for a, b, s in segs if s != "Awake") / 3600
            col = RED if asleep < 6 else AMBER if asleep < 7 else GREEN
            print(f"{c(k.strftime('%a %Y-%m-%d'), DIM)} {c(f'{asleep:4.1f} h', col, True)} {''.join(strip)}", flush=True)
            time.sleep(0.07 / speed)
        if once:
            return


def gps(path, speed, once, sync):
    _, _, loaders, _ = open_export(path)
    routes = []
    for load in loaders:
        pts = []
        for m in TRKPT.finditer(load()):
            a, b = LAT.search(m.group(1)), LON.search(m.group(1))
            if a and b:
                pts.append((float(a.group(1)), float(b.group(1))))
        if pts:
            routes.append(pts)
    if not routes:
        sys.exit("[gps] no workout routes in this export")
    starts = Counter((round(r[0][0], 3), round(r[0][1], 3)) for r in routes)
    home = starts.most_common(1)[0][0]
    near = [r for r in routes if abs(r[0][0] - home[0]) < 0.0015 and abs(r[0][1] - home[1]) < 0.0015]
    share = len(near) / len(routes)
    k = math.cos(math.radians(home[0]))
    frame_pts = [p for r in near for p in r]  # frame the map on the runs from the door, the rest run off the edge
    xs, ys = [p[1] * k for p in frame_pts], [p[0] for p in frame_pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sync.wait("gps")
    while True:
        cols, lines = shutil.get_terminal_size((100, 30))
        w, h = cols - 1, lines - 2
        W, H = w * 2, h * 4
        s = min((W - 1) / ((x1 - x0) or 1e-9), (H - 1) / ((y1 - y0) or 1e-9))
        ox = (W - (x1 - x0) * s) / 2
        oy = (H - (y1 - y0) * s) / 2
        grid = [[0] * w for _ in range(h)]
        age = [[-999] * w for _ in range(h)]
        hot = [[False] * w for _ in range(h)]
        sys.stdout.write("\x1b[2J")
        restarted = False
        for n, r in enumerate(routes, 1):
            if sync.fired():
                sync.consume()
                restarted = True
                break
            for lat, lon in r:
                px, py = int((lon * k - x0) * s + ox), int((y1 - lat) * s + oy)
                if 0 <= px < W and 0 <= py < H:
                    grid[py // 4][px // 2] |= BRAILLE[py % 4][px % 2]
                    age[py // 4][px // 2] = n
            if r in near:
                px, py = int((r[0][1] * k - x0) * s + ox), int((y1 - r[0][0]) * s + oy)
                if 0 <= px < W and 0 <= py < H:
                    hot[py // 4][px // 2] = True
            if n % 2 == 0 or n == len(routes):
                done = n == len(routes)
                head = (c(f"[gps] run {n}/{len(routes)}  ", TEXT, True)
                        + c(f"{share * 100:.0f}% start at the same door" if done else "drawing every route you ran", RED if done else DIM)
                        + c("  (coords hidden)", DIM))
                out = ["\x1b[H\x1b[2K" + head]
                for y in range(h):
                    row = []
                    for x in range(w):
                        if hot[y][x]:
                            row.append(c("█", RED, True))
                        elif grid[y][x]:
                            row.append(c(chr(0x2800 + grid[y][x]), GREEN if n - age[y][x] < 20 else 29))
                        else:
                            row.append(" ")
                    out.append("\x1b[" + str(y + 2) + ";1H" + "".join(row))
                sys.stdout.write("".join(out))
                sys.stdout.flush()
                time.sleep(0.12 / speed)
        if restarted:
            continue
        if once:
            return
        for _ in range(int(40 / speed)):  # hold the finished map ~4 s, unless a new run starts
            if sync.fired():
                break
            time.sleep(0.1)


def survival(path, speed, once, sync):
    from death_date import fit, life_table, survival as curve
    print(c("[model] reading the export ...", DIM))
    m = fit(analyze(parse(path)))
    q = life_table(m["sex"])
    age, hr = m["age"], m["hr"]
    now = datetime.fromisoformat(m["now"])
    now_dec = now.year + (now.timetuple().tm_yday - 1) / 365.25
    a0, s, s_age = curve(q, age, hr)  # same curve as death_date.py, so the years match
    sync.wait("model")
    while True:
        print(c(f"[model] SSA life table, {m['sex']}, from age {age:.1f}, yearly risk x{hr:.2f}", TEXT, True), flush=True)
        marks = set()
        restarted = False
        for i in range(1, len(s)):
            if sync.fired():
                sync.consume()
                restarted = True
                break
            width = shutil.get_terminal_size((100, 30)).columns - 1
            wide = width >= 96
            x = a0 + i
            if x <= age:
                continue
            before, alive = min(1.0, s[i - 1] / s_age), s[i] / s_age
            qx = q.get(x - 1, 1.0)
            qu = 1 - (1 - qx) ** hr
            tag = ""
            for p, label in ((0.75, "1 in 4 gone"), (0.5, "COIN FLIP"), (0.25, "3 in 4 gone")):
                if before > p >= alive and p not in marks:
                    marks.add(p)
                    cross = (x - 1) + (before - p) / (before - alive)
                    tag = f" <- {label}: {int(now_dec + cross - age)}"
            year = int(now_dec + (x - age))
            head = f"{year}  age {x:>3}  " + (f"q {qx:.5f} x{hr:.2f} = {qu:.5f}  " if wide else "")
            pct = f"alive {alive * 100:6.2f}% "
            bar = "█" * max(0, int(alive * (width - len(head) - len(pct) - len(tag) - 1)))
            col = RED if "COIN" in tag else (AMBER if tag else (GREEN if alive > 0.75 else TEXT))
            print(c(head, DIM) + c(pct, col, True) + c(bar, col) + c(tag, col, True), flush=True)
            time.sleep((0.6 if tag else 0.11) / speed)
            if alive < 0.002:
                break
        if restarted:
            continue
        if once:
            return
        for _ in range(int(30 / speed)):
            if sync.fired():
                break
            time.sleep(0.1)


def main():
    global COLOR
    ap = argparse.ArgumentParser(description="Stream your export live in the terminal.")
    ap.add_argument("view", choices=("beats", "hr", "sleep", "gps", "survival"))
    ap.add_argument("export", help="export.zip, apple_health_export folder or export.xml")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--sync", metavar="FILE", help="start, and restart from the top, whenever FILE is touched")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    COLOR = sys.stdout.isatty() and enable_ansi()
    views = {"beats": beats, "hr": heart_rate, "sleep": sleep_strips, "gps": gps, "survival": survival}
    code = 0
    try:
        views[args.view](args.export, args.speed, args.once, Sync(args.sync))
    except KeyboardInterrupt:
        code = 130  # non-zero, so Windows Terminal offers "press Enter to restart"
    except BrokenPipeError:
        pass
    finally:
        if COLOR:
            sys.stdout.write("\x1b[0m")
    sys.exit(code)


if __name__ == "__main__":
    main()
