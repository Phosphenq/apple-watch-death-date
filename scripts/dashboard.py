#!/usr/bin/env python3
"""Your death date as a full-screen terminal dashboard.

  python3 scripts/dashboard.py watch.json                # draw it in this terminal
  python3 scripts/dashboard.py export.zip                # parse the export first
  python3 scripts/dashboard.py watch.json --new-window   # open it in a new terminal window

Giant LED year, the four curves in braille, what the watch kept on you. Pure ANSI, no dependencies:
Windows Terminal, macOS Terminal / iTerm2, any Linux terminal. Redraws when you resize the window or
zoom the font (Ctrl + mouse wheel). Press q, Esc or Enter to close.
Set DEATH_DATE_WT_ARGS (e.g. "--pos 2560,0 -F") to place the Windows Terminal window.
"""
import argparse
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RED, AMBER, GREEN, CYAN, VIOLET = 203, 214, 114, 81, 141
TEXT, DIM, FAINT, BORDER = 252, 245, 236, 240
BRAILLE = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
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
}
MINI = {  # 3x5 pixel font, drawn 3 terminal rows tall with half blocks
    "0": ["###", "# #", "# #", "# #", "###"], "1": [" # ", "## ", " # ", " # ", "###"],
    "2": ["###", "  #", "###", "#  ", "###"], "3": ["###", "  #", "###", "  #", "###"],
    "4": ["# #", "# #", "###", "  #", "  #"], "5": ["###", "#  ", "###", "  #", "###"],
    "6": ["###", "#  ", "###", "# #", "###"], "7": ["###", "  #", " # ", " # ", " # "],
    "8": ["###", "# #", "###", "# #", "###"], "9": ["###", "# #", "###", "  #", "###"],
    "x": ["   ", "# #", " # ", "# #", "   "], "+": ["   ", " # ", "###", " # ", "   "],
    "-": ["   ", "   ", "###", "   ", "   "], ".": [" ", " ", " ", " ", "#"], ",": [" ", " ", " ", "#", "#"],
    "%": ["# #", "  #", " # ", "#  ", "# #"],
}


def fg(n, bold=False):
    return ("1;" if bold else "") + f"38;5;{n}"


class Screen:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.ch = [[" "] * w for _ in range(h)]
        self.st = [[""] * w for _ in range(h)]

    def put(self, x, y, s, style=""):
        if 0 <= y < self.h:
            for i, c in enumerate(s):
                if 0 <= x + i < self.w:
                    self.ch[y][x + i] = c
                    self.st[y][x + i] = style

    def rows(self, color=True):
        for y in range(self.h):
            if not color:
                yield "".join(self.ch[y]).rstrip()
                continue
            cur, row = None, []
            for x in range(self.w):
                s = self.st[y][x]
                if s != cur:
                    row.append("\x1b[0m" + (f"\x1b[{s}m" if s else ""))
                    cur = s
                row.append(self.ch[y][x])
            yield "".join(row) + "\x1b[0m"

    def ansi(self):
        return "".join(f"\x1b[{y + 1};1H{row}" for y, row in enumerate(self.rows()))


def box(scr, x, y, w, h, title="", color=BORDER):
    st = fg(color)
    scr.put(x, y, "╭" + "─" * (w - 2) + "╮", st)
    for r in range(1, h - 1):
        scr.put(x, y + r, "│", st)
        scr.put(x + w - 1, y + r, "│", st)
    scr.put(x, y + h - 1, "╰" + "─" * (w - 2) + "╯", st)
    if title:
        scr.put(x + 2, y, f" {title} "[:w - 4], fg(DIM))


def digits(scr, x, y, text, s, on, off):
    for i, ch in enumerate(text):
        glyph = FONT.get(ch, ["00000"] * 7)
        gx = x + i * 12 * s
        for r in range(7):
            for c in range(5):
                lit = glyph[r][c] == "1"
                for k in range(s):  # solid dark blocks for unlit pixels: a dotted shade pattern turns into moire on camera
                    scr.put(gx + c * 2 * s, y + r * s + k, "██" * s, on if lit else off)


def mini_width(text):
    return sum(len(MINI[c][0]) + 1 for c in text if c in MINI) - 1


def mini(scr, x, y, text, style):
    """Draw text in the 3x5 font, 3 rows tall. Returns the width used."""
    cx = x
    for ch in text:
        g = MINI.get(ch)
        if g is None:
            continue
        w = len(g[0])
        for r in range(3):
            top, bot = g[2 * r], (g[2 * r + 1] if 2 * r + 1 < 5 else " " * w)
            cells = "".join("█" if a == "#" and b == "#" else "▀" if a == "#" else "▄" if b == "#" else " "
                            for a, b in zip(top, bot))
            scr.put(cx, y + r, cells, style)
        cx += w + 1
    return cx - x


def chart(scr, x, y, w, h, values, color, progress=1.0):
    if len(values) < 2 or w < 2 or h < 1:
        return
    big_w, big_h = w * 2, h * 4
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    pts = [(round(i * (big_w - 1) / (len(values) - 1)), round((big_h - 2) - (v - lo) / span * (big_h - 2)))
           for i, v in enumerate(values)]
    limit = progress * (big_w - 1)
    grid = [[0] * w for _ in range(h)]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for k in range(steps + 1):
            px, py = round(x0 + (x1 - x0) * k / steps), round(y0 + (y1 - y0) * k / steps)
            for qy in (py, py + 1):  # 2 dots thick, reads better on camera
                if px <= limit and 0 <= px < big_w and 0 <= qy < big_h:
                    grid[qy // 4][px // 2] |= BRAILLE[qy % 4][px % 2]
    for r in range(h):
        for c in range(w):
            if grid[r][c]:
                scr.put(x + c, y + r, chr(0x2800 + grid[r][c]), fg(color))


def load(args):
    from death_date import fit
    from watch_feed import analyze, parse
    if args.input.lower().endswith(".json"):
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
        summary, model = data.get("summary", data), data.get("model")
    else:
        summary, model = analyze(parse(args.input)), None
    if model is None or args.age or args.sex:
        model = fit(summary, age=args.age, sex=args.sex)
    return summary, model


def texts(s, m):
    man = "man" if m["sex"] == "male" else "woman"
    men = "men" if m["sex"] == "male" else "women"
    fa = m["fitness_age"]
    fa_txt = "under 20" if fa < 20 else ("75+" if fa > 75 else f"{fa:.0f}")
    pct = m["vo2_pct"]
    rank = f"bottom {max(1, round(pct))}%" if pct < 50 else f"top {max(1, round(100 - pct))}%"
    rp = m["rhr_pct"]
    rhr_rank = f"higher than {min(99, int(rp))}%" if rp >= 50 else f"lower than {int(100 - rp)}%"
    big_fa = all(c in MINI for c in fa_txt)
    tiles = [(f"x{m['hr']:.2f}", "", RED, "yearly risk of dying", f"vs an average {man} your age"),
             (fa_txt if big_fa else "", "" if big_fa else fa_txt, AMBER, f"fitness age, you're {m['age']:.0f}",
              f"vo2max {m['vo2']:.1f}, {rank} of {men} your age"),
             (f"{m['rhr']:.0f}", " bpm", RED, "resting heart rate", f"{rhr_rank} of {men} your age"),
             (f"{m['habit_years_back']:+.1f}", " years", GREEN, "the one habit", "zone 2, 3x40 min a week")]
    hr = s.get("heart_rate") or {}
    priv = [(s["records"], f"records in {s['years']} years"),
            (hr.get("readings", 0), f"heart rate, every {hr.get('every_min') or 0:.0f} min"),
            (s.get("heartbeats", 0), "single heartbeats"),
            ((s.get("sleep") or {}).get("nights", 0), "nights of sleep")]
    if s.get("gps"):
        priv.append((s["gps"]["home_share"] * 100, "% of runs from one spot"))
    if s.get("rings"):
        priv.append((s["rings"]["all_closed"], f"days of {s['rings']['days']:,} rings closed"))
    sub = [[("death window ", TEXT), (f"{int(m['window'][0])}-{int(m['window'][1])}", RED)],
           [(f"average {man} your age ", TEXT), (str(int(m["avg_coin_flip_year"])), TEXT)],
           [("fix your cardio ", TEXT), (str(int(m["habit_year"])), GREEN)]]
    return man, tiles, priv, sub


def frame(W, H, s, m, year_text, year_on, chart_p, count_p):
    scr = Screen(W, H)
    man, tiles, priv, sub = texts(s, m)
    left = 2
    head = (f" apple-watch-death-date │ {m['sex']}, {m['age']:.0f} │ export {str(s.get('export_date'))[:10]} "
            f"│ {int(s['records'] * count_p):,} records over {s['years']} years ")
    scr.put(left, 0, head, fg(DIM))
    if W - len(head) > 30:
        scr.put(W - 21, 0, "model, not medicine", fg(FAINT + 4))

    # pick the biggest layout that fits
    scale = 2 if (W >= 152 and H >= 46) else 1
    dig_w, dig_h = 46 * scale, 7 * scale
    tile_h = 7 if scale == 2 or H >= 40 else 6
    tiles_right = W - (left + dig_w + 6) >= 2 * 24 + 2
    one_line_sub = sum(len(t) for line in sub for t, _ in line) + 10 <= dig_w + 4
    sub_lines = 1 if one_line_sub else 3
    hero_h = max(1 + dig_h + 1 + sub_lines, (1 + 2 * tile_h + 1) if tiles_right else 0)
    chart_min, priv_h = 6, 7
    rest = H - 2 - hero_h - 2
    show_priv = rest >= chart_min + priv_h + 3
    show_charts = rest - (priv_h + 3 if show_priv else 0) >= chart_min

    scr.put(left, 2, "COIN-FLIP YEAR · 50% chance you're dead by then", fg(DIM, True))
    digits(scr, left, 3, year_text, scale, fg(year_on), fg(234))
    sub_y = 3 + dig_h + 1
    lines = [sub[0] + [("  ·  ", DIM)] + sub[1] + [("  ·  ", DIM)] + sub[2]] if one_line_sub else sub
    for n, line in enumerate(lines):
        x = left
        for text, color in line:
            scr.put(x, sub_y + n, text, fg(color, color in (RED, GREEN)))
            x += len(text)

    if tiles_right:
        tx = left + dig_w + 6
        tw = min(46, (W - tx - 2 - 2) // 2)
        for i, (big_val, small, color, k1, k2) in enumerate(tiles):
            bx, by = tx + (i % 2) * (tw + 2), 2 + (i // 2) * (tile_h + 1)
            box(scr, bx, by, tw, tile_h)
            used = mini(scr, bx + 2, by + 1, big_val, fg(color, True)) if big_val else 0
            if small:
                scr.put(bx + 2 + used, by + 3, small, fg(color, True))
            scr.put(bx + 2, by + 4, k1[:tw - 4], fg(TEXT))
            if tile_h >= 7:
                scr.put(bx + 2, by + 5, k2[:tw - 4], fg(DIM))

    y = 2 + hero_h + 1
    if show_charts:
        ch_h = H - y - 2 - (priv_h + 2 if show_priv else 0)
        blocks = [("vo2max", "ml/kg/min", s.get("vo2max"), AMBER, 1), ("resting heart rate", "bpm", s.get("resting_hr"), RED, 0),
                  ("heart rate variability", "ms", s.get("hrv"), CYAN, 0), ("sleep per night", "h", s.get("sleep"), VIOLET, 1)]
        blocks = [b for b in blocks if b[2] and "monthly" in b[2] and "old" in b[2]]
        if blocks:
            cw = (W - 2 * left - 2 * (len(blocks) - 1)) // len(blocks)
            for i, (title, unit, b, color, dec) in enumerate(blocks):
                bx = left + i * (cw + 2)
                box(scr, bx, y, cw, ch_h, title)
                change = (b["new"] - b["old"]) / b["old"] * 100
                for val in (f"{b['old']:.{dec}f} -> {b['new']:.{dec}f} {unit} ({change:+.1f}%)",
                            f"{b['new']:.{dec}f} {unit} ({change:+.1f}%)", f"{change:+.1f}%"):
                    if len(val) <= cw - 4:
                        scr.put(bx + cw - len(val) - 2, y + 1, val, fg(color, True))
                        break
                vals = [v for _, v in b["monthly"]]
                if title.startswith("sleep") and len(vals) > 4:  # nightly noise: 3-month rolling mean
                    vals = [sum(vals[max(0, i - 1):i + 2]) / len(vals[max(0, i - 1):i + 2]) for i in range(len(vals))]
                chart(scr, bx + 2, y + 2, cw - 4, ch_h - 3, vals, color, chart_p)
        y += ch_h + 1

    if show_priv:
        scr.put(left, y, "WHAT YOUR WATCH KEPT ON YOU", fg(DIM, True))
        cells = []
        for n, label in priv:
            if label.startswith("%"):
                cells.append((f"{n * count_p:.0f}%", label[2:]))
            else:
                cells.append((f"{int(n * count_p):,}", label))
        pw = (W - 2 * left - 2 * (len(cells) - 1)) // len(cells)
        big_ok = all(mini_width(v) <= pw - 4 for v, _ in cells)
        for i, (value, label) in enumerate(cells):
            bx = left + i * (pw + 2)
            box(scr, bx, y + 1, pw, priv_h - 1)
            if big_ok:
                mini(scr, bx + 2, y + 2, value, fg(TEXT, True))
                scr.put(bx + 2, y + 5, label[:pw - 4], fg(DIM))
            else:  # narrow: value on one line, label wrapped on two
                scr.put(bx + 2, y + 2, value, fg(TEXT, True))
                words, rows_, cur = label.split(), [], ""
                for word in words:
                    if len(cur) + len(word) + (1 if cur else 0) > pw - 4:
                        rows_.append(cur)
                        cur = word
                    else:
                        cur = (cur + " " + word).strip()
                rows_.append(cur)
                for n, text in enumerate(rows_[:2]):
                    scr.put(bx + 2, y + 3 + n, text[:pw - 4], fg(DIM))
    foot = "SSA life table · Kodama 2009 · Jensen 2013 · FRIEND 2015 · CDC NHANES 1999-2008   q / Esc to close"
    scr.put(left, H - 1, foot[:W - left - 1], fg(FAINT + 6))
    return scr


def raise_own_window(title="death-date", block=False):
    """Windows: a terminal opened from a background process can start minimized.
    Watch the window with this title for a few seconds and bring it back up if it is."""
    if os.name != "nt":
        return

    def watch():
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            raised = False
            for _ in range(25):
                found = []

                def check(hwnd, _):
                    buf = ctypes.create_unicode_buffer(256)
                    user32.GetWindowTextW(hwnd, buf, 256)
                    if buf.value == title:
                        found.append(hwnd)
                    return True

                user32.EnumWindows(proc(check), 0)
                for hwnd in found:
                    if user32.IsIconic(hwnd):
                        user32.ShowWindow(hwnd, 9)
                        raised = False
                    if not raised:
                        user32.BringWindowToTop(hwnd)
                        user32.SetForegroundWindow(hwnd)
                        raised = True
                time.sleep(0.2)
        except Exception:
            pass

    if block:
        watch()
    else:
        import threading
        threading.Thread(target=watch, daemon=True).start()


def term_size():
    size = shutil.get_terminal_size((160, 46))
    return max(60, size.columns - 1), max(20, size.lines - 1)


def key_pressed():
    """Non-blocking: True if q / Esc / Enter was pressed."""
    try:
        import msvcrt
        while msvcrt.kbhit():
            if msvcrt.getwch() in ("q", "Q", "\x1b", "\r"):
                return True
        return False
    except ImportError:
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1) in ("q", "Q", "\x1b", "\r", "\n")
        return False


def open_new_window(args):
    me = os.path.abspath(__file__)
    title = f"death-date {random.randint(0x1000, 0xffff):x}"
    child = [sys.executable, me, os.path.abspath(args.input), "--hold", "--title", title]
    if args.age:
        child += ["--age", str(args.age)]
    if args.sex:
        child += ["--sex", args.sex]
    cwd = os.getcwd()
    if os.name == "nt":
        wt = shutil.which("wt") or shutil.which("wt.exe")
        if wt:
            extra = shlex.split(os.environ.get("DEATH_DATE_WT_ARGS", ""))
            subprocess.Popen([wt, "-w", "new"] + extra + ["-d", cwd, "--title", title, "--suppressApplicationTitle"] + child)
            print("[dashboard] opening in a new terminal window ...", flush=True)
            raise_own_window(title, block=True)  # the launcher may raise it even when the new window may not
        else:
            subprocess.Popen(child, cwd=cwd, creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif sys.platform == "darwin":
        cmd = "cd " + shlex.quote(cwd) + " && " + " ".join(shlex.quote(c) for c in child)
        script = 'tell application "Terminal" to do script "' + cmd.replace("\\", "\\\\").replace('"', '\\"') + '"'
        subprocess.Popen(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'])
    else:
        for term, flag in (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"), ("konsole", "-e"),
                           ("xfce4-terminal", "-x"), ("xterm", "-e")):
            if shutil.which(term):
                subprocess.Popen([term, flag] + child, cwd=cwd)
                break
        else:
            sys.exit("[dashboard] no terminal emulator found, run it in your terminal instead")
    print("[dashboard] opened in a new terminal window")


def main():
    ap = argparse.ArgumentParser(description="Your death date as a terminal dashboard.")
    ap.add_argument("input", help="watch.json from watch_feed.py --json, or the export itself")
    ap.add_argument("--age", type=float)
    ap.add_argument("--sex", choices=("male", "female"))
    ap.add_argument("--new-window", action="store_true", help="open the dashboard in a new terminal window")
    ap.add_argument("--hold", action="store_true", help="keep it on screen until q / Esc / Enter")
    ap.add_argument("--title", default="death-date", help=argparse.SUPPRESS)
    ap.add_argument("--no-anim", action="store_true")
    ap.add_argument("--size", metavar="WxH", help="render at a fixed size (for previews)")
    ap.add_argument("--frame", metavar="FILE", help="write the final frame with colors to a file and exit")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    if args.new_window:
        open_new_window(args)
        return

    try:
        s, m = load(args)
    except ValueError as e:
        sys.exit(f"[dashboard] {e}")
    year = str(int(m["coin_flip_year"]))
    fixed = tuple(int(v) for v in args.size.lower().split("x")) if args.size else None
    if args.frame or not sys.stdout.isatty():
        W, H = fixed or (160, 46)
        rows = list(frame(W, H, s, m, year, RED, 1.0, 1.0).rows(color=bool(args.frame)))
        if args.frame:
            with open(args.frame, "w", encoding="utf-8") as f:
                f.write("\n".join(rows) + "\n")
            print(f"[dashboard] frame saved to {args.frame}")
        else:
            print("\n".join(rows))
        return

    from watch_feed import enable_ansi
    enable_ansi()
    out = sys.stdout
    if args.hold:
        raise_own_window(args.title)
    time.sleep(0.4)  # a new window may still be maximizing
    W, H = fixed or term_size()
    out.write("\x1b[?25l\x1b[2J")

    def draw(*a):
        nonlocal W, H
        nw, nh = fixed or term_size()
        if (nw, nh) != (W, H):
            W, H = nw, nh
            out.write("\x1b[2J")
        out.write(frame(W, H, s, m, *a).ansi())
        out.flush()

    try:
        if not args.no_anim:
            for i in range(18):
                p = (i + 1) / 18
                draw(str(random.randint(2030, 2099)), FAINT + 8, p * 0.6, 1 - (1 - p) ** 3)
                time.sleep(0.08)
            for i in range(10):
                draw(year, RED, 0.6 + 0.4 * (i + 1) / 10, 1.0)
                time.sleep(0.06)
        draw(year, RED, 1.0, 1.0)
        if args.hold:
            while not key_pressed():
                time.sleep(0.15)
                if (fixed or term_size()) != (W, H):
                    draw(year, RED, 1.0, 1.0)
    finally:
        out.write(f"\x1b[0m\x1b[{H + 1};1H\x1b[?25h\n")
        out.flush()


if __name__ == "__main__":
    main()
