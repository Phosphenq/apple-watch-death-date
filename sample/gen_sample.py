#!/usr/bin/env python3
"""Generate a sample Apple Health export to try the repo without your own data.

Writes sample/watch-export.zip laid out like the zip the Health app gives you:
apple_health_export/export.xml and apple_health_export/workout-routes/*.gpx

Three years of a 23-year-old's watch data with the trends from the README baked in:
vo2max sliding from ~44 to ~39.6, resting HR creeping from 92 to 101, heart rate
every 5 minutes day and night, beat-by-beat HRV sessions that dip after Sunday
nights, sleep stages, and evening runs with GPS routes that start at the same door.
The person, the device names and the coordinates are made up.

Usage: python3 sample/gen_sample.py
"""
import io
import math
import os
import random
import zipfile
from datetime import datetime, timedelta

random.seed(42)
HERE = os.path.dirname(os.path.abspath(__file__))
ZIP_PATH = os.path.join(HERE, "watch-export.zip")
TZ = " -0500"
START = datetime(2023, 11, 14)
DAYS = 1096
EXPORT_DATE = START + timedelta(days=DAYS, hours=18, minutes=30)
DOB = "2003-05-21"
HOME = (40.7794, -73.9632)
WATCH = "Alex’s Apple Watch"
PHONE = "Alex’s iPhone"
DEV_WATCH = ("&lt;&lt;HKDevice: 0x6000021b4f00&gt;, name:Apple Watch, manufacturer:Apple Inc., "
             "model:Watch, hardware:Watch7,3, software:11.1&gt;")
DEV_PHONE = ("&lt;&lt;HKDevice: 0x6000021b5e40&gt;, name:iPhone, manufacturer:Apple Inc., "
             "model:iPhone, hardware:iPhone15,2, software:26.1&gt;")

HK = "HKQuantityTypeIdentifier"
DOCTYPE = """<!DOCTYPE HealthData [
<!-- HealthKit Export Version: 13 -->
<!ELEMENT HealthData (ExportDate,Me,(Record|Correlation|Workout|ActivitySummary)*)>
<!ATTLIST HealthData locale CDATA #REQUIRED>
<!ELEMENT ExportDate EMPTY>
<!ATTLIST ExportDate value CDATA #REQUIRED>
<!ELEMENT Me EMPTY>
<!ELEMENT Record ((MetadataEntry|HeartRateVariabilityMetadataList)*)>
<!ATTLIST Record type CDATA #REQUIRED unit CDATA #IMPLIED value CDATA #IMPLIED
  sourceName CDATA #REQUIRED sourceVersion CDATA #IMPLIED device CDATA #IMPLIED
  creationDate CDATA #IMPLIED startDate CDATA #REQUIRED endDate CDATA #REQUIRED>
<!ELEMENT HeartRateVariabilityMetadataList (InstantaneousBeatsPerMinute*)>
<!ELEMENT InstantaneousBeatsPerMinute EMPTY>
<!ATTLIST InstantaneousBeatsPerMinute bpm CDATA #REQUIRED time CDATA #REQUIRED>
]>"""


def stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") + TZ


def rec(rtype, unit, dt, value, end=None, watch=True, children=None):
    src, dev = (WATCH, DEV_WATCH) if watch else (PHONE, DEV_PHONE)
    unit_attr = f' unit="{unit}"' if unit else ""
    head = (f'<Record type="{rtype}" sourceName="{src}" sourceVersion="11.1" device="{dev}"{unit_attr} '
            f'creationDate="{stamp(end or dt)}" startDate="{stamp(dt)}" endDate="{stamp(end or dt)}" value="{value}"')
    if not children:
        return head + "/>\n"
    return head + ">\n" + children + "</Record>\n"


def clock(t):
    # Apple writes beat times like "2:14:03.27 AM"
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d}:{t.second:02d}.{t.microsecond // 10000:02d} {'AM' if t.hour < 12 else 'PM'}"


def main():
    zf = zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED)
    raw = zf.open("apple_health_export/export.xml", "w", force_zip64=True)
    out = io.TextIOWrapper(raw, encoding="utf-8", newline="\n")
    w = out.write
    counts = {"records": 0, "beats": 0, "routes": 0, "points": 0}

    def put(line, n=1):
        counts["records"] += n
        w(line)

    w('<?xml version="1.0" encoding="UTF-8"?>\n' + DOCTYPE + '\n<HealthData locale="en_US">\n')
    w(f' <ExportDate value="{stamp(EXPORT_DATE)}"/>\n')
    w(f' <Me HKCharacteristicTypeIdentifierDateOfBirth="{DOB}" HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexMale" '
      'HKCharacteristicTypeIdentifierBloodType="HKBloodTypeNotSet" HKCharacteristicTypeIdentifierFitzpatrickSkinType="HKFitzpatrickSkinTypeNotSet" '
      'HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse="None"/>\n')

    # vo2max and resting HR first, same random draws as earlier samples, so the headline numbers stay put
    for d in range(DAYS):
        dt = START + timedelta(days=d)
        if d % 7 == 3:
            v = 44.0 - 4.4 * (d / DAYS) + random.uniform(-0.7, 0.7)
            put(rec(HK + "VO2Max", "mL/min·kg", dt.replace(hour=8), f"{v:.2f}"))
    rhr_by_day = []
    for d in range(DAYS):
        dt = START + timedelta(days=d)
        v = 92 + 9 * (d / DAYS) + random.uniform(-3.0, 3.0)
        rhr_by_day.append(v)
        put(rec(HK + "RestingHeartRate", "count/min", dt.replace(hour=8), f"{v:.0f}"))

    # which evenings have a run: Tue/Thu/Sat, less often as fitness slides
    runs = [d for d in range(DAYS)
            if (START + timedelta(days=d)).weekday() in (1, 3, 5) and random.random() < 0.75 - 0.4 * d / DAYS]
    run_days = set(runs)

    # heart rate every 5 minutes, off-wrist 07:00-07:55 while charging
    for d in range(DAYS):
        dt = START + timedelta(days=d)
        base = 92 + 9 * (d / DAYS)
        for slot in range(288):
            t = dt + timedelta(minutes=5 * slot)
            if t.hour == 7:
                continue
            if t.hour < 7:
                v = base - 6 + random.uniform(-4, 5)
            elif d in run_days and t.hour == 19 and t.minute < 40:
                v = 158 + random.uniform(-8, 14)
            elif t.hour >= 21:
                v = base + random.uniform(-3, 8)
            else:
                v = base + 10 + random.uniform(-6, 14)
            put(rec(HK + "HeartRate", "count/min", t, f"{v:.0f}"))

    # HRV: two beat-by-beat sessions a night, lower after Sunday night
    for d in range(DAYS):
        day = START + timedelta(days=d)
        base = 48 - 19 * (d / DAYS) - (11 if day.weekday() == 0 else 0)
        for _ in range(2):
            t0 = day + timedelta(hours=1, minutes=random.randint(0, 239), seconds=random.randint(0, 59))
            sdnn = max(14.0, base + random.uniform(-5, 5))
            bpm_avg = 86 + 11 * (d / DAYS) + random.uniform(-4, 4)
            beats, t = [], t0
            for _ in range(int(bpm_avg)):
                bpm = bpm_avg + random.uniform(-6, 6)
                beats.append(f'  <InstantaneousBeatsPerMinute bpm="{bpm:.0f}" time="{clock(t)}"/>\n')
                t += timedelta(seconds=60.0 / bpm)
            counts["beats"] += len(beats)
            kids = " <HeartRateVariabilityMetadataList>\n" + "".join(beats) + " </HeartRateVariabilityMetadataList>\n"
            put(rec(HK + "HeartRateVariabilitySDNN", "ms", t0, f"{sdnn:.1f}", end=t, children=kids))

    # sleep stages: ~6.2 h asleep on work nights, ~7.5 h on Friday and Saturday nights
    for d in range(DAYS):
        day = START + timedelta(days=d)
        if day.weekday() not in (4, 5):
            t = day.replace(hour=23, minute=20) + timedelta(minutes=random.randint(0, 40))
            target = 60 * (6.2 + random.uniform(-0.8, 0.9))
        else:
            t = day + timedelta(days=1, hours=0, minutes=random.randint(30, 90))
            target = 60 * (7.5 + random.uniform(-0.7, 0.8))
        bed = t
        asleep = 0.0
        cycle = 0
        while asleep < target:
            plan = [("AsleepCore", random.uniform(35, 55)),
                    ("AsleepDeep", max(5.0, random.uniform(14, 28) - 4 * cycle)),
                    ("AsleepCore", random.uniform(10, 20)),
                    ("AsleepREM", random.uniform(12, 22) + 4 * cycle)]
            for stage, mins in plan:
                mins = min(mins, target - asleep)
                if mins <= 0:
                    break
                end = t + timedelta(minutes=mins)
                put(rec("HKCategoryTypeIdentifierSleepAnalysis", "", t, "HKCategoryValueSleepAnalysis" + stage, end=end))
                asleep += mins
                t = end
            if asleep < target and random.random() < 0.6:
                end = t + timedelta(minutes=random.uniform(2, 9))
                put(rec("HKCategoryTypeIdentifierSleepAnalysis", "", t, "HKCategoryValueSleepAnalysisAwake", end=end))
                t = end
            cycle += 1
        put(rec("HKCategoryTypeIdentifierSleepAnalysis", "", bed, "HKCategoryValueSleepAnalysisInBed", end=t, watch=False))

    # steps every hour while awake, and the daily stuff nobody looks at
    for d in range(DAYS):
        day = START + timedelta(days=d)
        t = d / DAYS
        total = 0
        for h in range(8, 22):
            n = int(random.uniform(150, 900)) + (3800 if (d in run_days and h == 19) else 0)
            total += n
            put(rec(HK + "StepCount", "count", day.replace(hour=h), n, end=day.replace(hour=h, minute=59)))
        noon, night = day.replace(hour=12), day.replace(hour=4)
        put(rec(HK + "ActiveEnergyBurned", "Cal", noon, f"{total * 0.045:.1f}"))
        put(rec(HK + "BasalEnergyBurned", "Cal", noon, f"{1790 + 30 * t + random.uniform(-25, 25):.1f}"))
        put(rec(HK + "AppleExerciseTime", "min", noon, f"{(35 if d in run_days else 0) + random.uniform(4, 22):.0f}"))
        put(rec(HK + "AppleStandTime", "min", noon, f"{random.uniform(40, 140):.0f}"))
        put(rec(HK + "DistanceWalkingRunning", "km", noon, f"{total * 0.00074:.2f}"))
        put(rec(HK + "FlightsClimbed", "count", noon, int(random.uniform(2, 14)), watch=False))
        put(rec(HK + "RespiratoryRate", "count/min", night, f"{random.uniform(14, 17.5):.1f}"))
        put(rec(HK + "OxygenSaturation", "%", night, f"{random.uniform(0.94, 0.99):.2f}"))
        put(rec(HK + "AppleSleepingWristTemperature", "degC", night, f"{35.2 + random.uniform(-0.4, 0.4):.2f}"))
        put(rec(HK + "WalkingHeartRateAverage", "count/min", noon, f"{106 + 10 * t + random.uniform(-4, 4):.0f}"))
        put(rec(HK + "WalkingSpeed", "km/hr", noon, f"{4.9 - 0.3 * t + random.uniform(-0.3, 0.3):.2f}", watch=False))
        put(rec(HK + "HeadphoneAudioExposure", "dBASPL", day.replace(hour=17), f"{random.uniform(62, 84):.1f}", watch=False))
        put(rec(HK + "EnvironmentalAudioExposure", "dBASPL", day.replace(hour=15), f"{random.uniform(55, 78):.1f}"))
        put(rec(HK + "TimeInDaylight", "min", noon, f"{random.uniform(5, 70) * (1.5 if day.month in (5, 6, 7, 8) else 1):.0f}"))
        if d % 14 == 0:
            put(rec(HK + "BodyMass", "kg", day.replace(hour=9), f"{72 + 8 * t + random.uniform(-0.6, 0.6):.1f}", watch=False))

    # runs with GPS routes: 9 in 10 start within a few meters of the same door
    gpx_files = []
    lat_m = 111320.0
    lon_m = 111320.0 * math.cos(math.radians(HOME[0]))
    for d in runs:
        day = START + timedelta(days=d)
        t0 = day.replace(hour=19, minute=random.randint(0, 9))
        mins = random.uniform(24, 40)
        if random.random() < 0.9:
            lat, lon = HOME[0] + random.uniform(-15, 15) / lat_m, HOME[1] + random.uniform(-15, 15) / lon_m
        else:
            lat, lon = HOME[0] + random.uniform(1500, 2500) / lat_m, HOME[1] + random.uniform(-2500, 2500) / lon_m
        heading = random.uniform(0, 360)
        pts, t, dist = [], t0, 0.0
        steps = int(mins * 6)
        for i in range(steps):
            speed = random.uniform(2.5, 3.2)
            heading += random.uniform(-25, 25) + (180.0 / steps if i > steps // 2 else 0)
            lat += speed * 10 * math.cos(math.radians(heading)) / lat_m
            lon += speed * 10 * math.sin(math.radians(heading)) / lon_m
            dist += speed * 10
            utc = t + timedelta(hours=5)
            pts.append(f'   <trkpt lon="{lon:.6f}" lat="{lat:.6f}"><ele>{38 + random.uniform(-3, 6):.1f}</ele>'
                       f'<time>{utc:%Y-%m-%dT%H:%M:%SZ}</time><extensions><speed>{speed:.2f}</speed>'
                       f'<course>{heading % 360:.1f}</course><hAcc>{random.uniform(2, 6):.1f}</hAcc>'
                       f'<vAcc>{random.uniform(1, 4):.1f}</vAcc></extensions></trkpt>\n')
            t += timedelta(seconds=10)
        hh = t0.hour % 12 or 12
        name = f"route_{t0:%Y-%m-%d}_{hh}.{t0.minute:02d}{'am' if t0.hour < 12 else 'pm'}.gpx"
        gpx = ('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Apple Health Export" '
               'xmlns="http://www.topografix.com/GPX/1/1">\n <metadata><time>'
               f'{t0 + timedelta(hours=5):%Y-%m-%dT%H:%M:%SZ}</time></metadata>\n <trk><name>Route {t0:%Y-%m-%d} '
               f'{hh}:{t0.minute:02d}pm</name><trkseg>\n' + "".join(pts) + ' </trkseg></trk>\n</gpx>\n')
        gpx_files.append((name, gpx))
        counts["routes"] += 1
        counts["points"] += len(pts)
        w(f' <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="{mins:.2f}" durationUnit="min" '
          f'sourceName="{WATCH}" sourceVersion="11.1" device="{DEV_WATCH}" creationDate="{stamp(t)}" '
          f'startDate="{stamp(t0)}" endDate="{stamp(t)}">\n'
          '  <MetadataEntry key="HKIndoorWorkout" value="0"/>\n'
          f'  <WorkoutStatistics type="{HK}DistanceWalkingRunning" startDate="{stamp(t0)}" endDate="{stamp(t)}" '
          f'sum="{dist / 1000:.2f}" unit="km"/>\n'
          f'  <WorkoutRoute sourceName="{WATCH}" sourceVersion="11.1" creationDate="{stamp(t)}" '
          f'startDate="{stamp(t0)}" endDate="{stamp(t)}">\n'
          f'   <FileReference path="/workout-routes/{name}"/>\n  </WorkoutRoute>\n </Workout>\n')

    # the rings
    for d in range(DAYS):
        day = START + timedelta(days=d)
        run = d in run_days
        move = random.uniform(280, 520) + (330 if run else 0)
        ex = (35 if run else 0) + random.uniform(4, 22)
        stand = random.randint(7, 13)
        w(f' <ActivitySummary dateComponents="{day:%Y-%m-%d}" activeEnergyBurned="{move:.1f}" activeEnergyBurnedGoal="600" '
          f'activeEnergyBurnedUnit="Cal" appleMoveTime="0" appleMoveTimeGoal="0" appleExerciseTime="{ex:.0f}" '
          f'appleExerciseTimeGoal="30" appleStandHours="{stand}" appleStandHoursGoal="12"/>\n')

    w("</HealthData>\n")
    out.close()
    for name, gpx in gpx_files:
        zf.writestr("apple_health_export/workout-routes/" + name, gpx)
    zf.close()

    print(f"records: {counts['records']:,}  heartbeats: {counts['beats']:,}  "
          f"routes: {counts['routes']}  gps points: {counts['points']:,}")
    print(f"zip: {os.path.getsize(ZIP_PATH) / 1e6:.1f} MB  {ZIP_PATH}")


if __name__ == "__main__":
    main()
