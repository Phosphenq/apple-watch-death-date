#!/usr/bin/env python3
"""Mask identifying details in Apple Health output before it goes on screen.

Usage:
  some-command | python3 scripts/mask.py
  python3 scripts/watch_feed.py export.zip --mask     (same masking, built in; use this on Windows PowerShell)

Masks: birth dates (keeps the year), the owner's name in device names ("Alex's Apple Watch"),
device serials and memory addresses, emails, MAC addresses, UUIDs, and GPS coordinates
(keeps ~10 km of precision). Keeps record types, counts, units, dates of records and values.
"""
import re
import sys

PATTERNS = [
    # HKCharacteristicTypeIdentifierDateOfBirth="2003-05-21", date_of_birth=..., "born 2003-05-21"
    (re.compile(r'((?:DateOfBirth|date_of_birth|birth)[^=":\n]{0,20}[=:]\s*"?)(\d{4})-(\d\d)-(\d\d)', re.I),
     lambda m: m.group(1) + m.group(2) + "-**-**"),
    # "Alex's Apple Watch", "Anna’s iPhone"
    (re.compile(r"\b[\w.\-]{1,40}(['’]s\s+(?:Apple Watch|iPhone|iPad|Mac(?:Book)?))"),
     lambda m: "****" + m.group(1)),
    # lat="40.779400" lon="-73.963200" and plain "40.7794, -73.9632"
    (re.compile(r'((?:lat|lon|latitude|longitude)="?\s*)(-?\d{1,3})\.(\d)\d*', re.I),
     lambda m: m.group(1) + m.group(2) + "." + m.group(3) + "****"),
    (re.compile(r"(?<![\w.])(-?\d{1,2})\.(\d)\d{3,},\s*(-?\d{1,3})\.(\d)\d{3,}"),
     lambda m: f"{m.group(1)}.{m.group(2)}****, {m.group(3)}.{m.group(4)}****"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), lambda m: "****@****"),
    (re.compile(r"\b([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), lambda m: m.group(0)[:8] + ":**:**:**"),
    (re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b"),
     lambda m: m.group(0)[:8] + "-****-****-****-************"),
    (re.compile(r"\b0x[0-9A-Fa-f]{6,}\b"), lambda m: "0x****"),
    (re.compile(r"\b([A-Z0-9]{4})[A-Z0-9]{8,}\b"), lambda m: m.group(1) + "********"),
]


def mask_line(line):
    for pattern, repl in PATTERNS:
        line = pattern.sub(repl, line)
    return line


def main():
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    for raw in sys.stdin:
        sys.stdout.write(mask_line(raw))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
