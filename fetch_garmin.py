#!/usr/bin/env python3
"""
Fetch Garmin Connect running data and output JSON for the marathon dashboard.
Usage:
  python3 fetch_garmin.py
  GARMIN_EMAIL=you@email.com GARMIN_PASSWORD=secret python3 fetch_garmin.py
"""

import os
import sys
import json
import getpass
import datetime
from collections import defaultdict

try:
    import garminconnect
    import garth
except ImportError:
    print("ERROR: garminconnect not installed. Run: pip3 install garminconnect")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
WEEKS_BACK = 20          # how many weeks of history to fetch
MAX_ACTIVITIES = 300     # cap on activities fetched from API
METERS_PER_MILE = 1609.344
TOKEN_STORE = os.path.expanduser("~/.garth_marathon")

# ── Auth ──────────────────────────────────────────────────────────────────────
def get_credentials():
    email = os.environ.get("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = os.environ.get("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")
    return email, password

def login():
    """
    Login strategy:
      1. Try loading saved tokens from TOKEN_STORE (no password needed on repeat runs).
      2. If missing/expired, do a fresh login — handles MFA interactively.
      3. Save tokens so next run is instant.
    """
    # ── Try saved session ──────────────────────────────────────────────────────
    if os.path.isdir(TOKEN_STORE):
        try:
            client = garminconnect.Garmin()
            client.login(tokenstore=TOKEN_STORE)
            print("Loaded saved session. (Delete ~/.garth_marathon to force re-login)", flush=True)
            return client
        except Exception:
            print("Saved session expired, re-authenticating...", flush=True)

    # ── Fresh login ────────────────────────────────────────────────────────────
    email, password = get_credentials()
    print("Logging in to Garmin Connect...", flush=True)
    print("  (If you have 2FA enabled, you'll be prompted for a code.)", flush=True)
    try:
        client = garminconnect.Garmin(email, password)
        client.login()
    except garminconnect.GarminConnectAuthenticationError as e:
        print(f"\nERROR: Authentication failed — {e}")
        print("  • Double-check your Garmin Connect email and password.")
        print("  • If you use 'Sign in with Apple/Google', you need your Garmin password.")
        print("  • Try logging in at connect.garmin.com to verify credentials.")
        sys.exit(1)

    # ── Save tokens ────────────────────────────────────────────────────────────
    os.makedirs(TOKEN_STORE, exist_ok=True)
    client.garth.dump(TOKEN_STORE)
    print(f"Session saved to {TOKEN_STORE}. Future runs won't need your password.", flush=True)
    print("Login successful.", flush=True)
    return client

# ── Data fetching ─────────────────────────────────────────────────────────────
def meters_to_miles(m):
    return round(m / METERS_PER_MILE, 2) if m else 0

def pace_from_speed(speed_ms):
    """Convert m/s to min/mile string like '9:30'"""
    if not speed_ms or speed_ms <= 0:
        return None
    secs_per_mile = METERS_PER_MILE / speed_ms
    mins = int(secs_per_mile // 60)
    secs = int(secs_per_mile % 60)
    return f"{mins}:{secs:02d}"

def pace_to_seconds(pace_str):
    """'9:30' -> 570"""
    if not pace_str:
        return None
    parts = pace_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])

def seconds_to_pace(secs):
    if not secs or secs <= 0:
        return None
    return f"{int(secs // 60)}:{int(secs % 60):02d}"

def categorize_run(distance_mi, avg_hr, duration_mins, date_str=None):
    """Heuristic run type classification."""
    # Fri/Sat/Sun (weekday >= 4) AND >= 5 miles → Long Run
    if date_str and distance_mi >= 5:
        d = datetime.date.fromisoformat(date_str[:10])
        if d.weekday() >= 4:  # 4=Fri, 5=Sat, 6=Sun
            return "Long Run"
    if distance_mi >= 12:
        return "Long Run"
    if duration_mins and duration_mins < 25 and distance_mi < 4:
        return "Recovery"
    if avg_hr and avg_hr >= 165:
        return "Speed/Interval"
    if avg_hr and avg_hr >= 155:
        return "Tempo"
    return "Easy"

def week_start(date_str):
    """Return the Monday of the week containing date_str."""
    d = datetime.date.fromisoformat(date_str[:10])
    return (d - datetime.timedelta(days=d.weekday())).isoformat()

STRENGTH_TYPE_KEYS = {
    "strength_training": "Strength",
    "fitness_equipment": "Gym",
    "indoor_cardio": "Cardio",
    "bouldering": "Climbing",
    "yoga": "Yoga",
    "pilates": "Pilates",
    "hiit": "HIIT",
    "cardio": "Cardio",
}

def fetch_activities(client):
    print(f"Fetching last {MAX_ACTIVITIES} activities...", flush=True)
    all_acts = client.get_activities(0, MAX_ACTIVITIES)

    cutoff = (datetime.date.today() - datetime.timedelta(weeks=WEEKS_BACK)).isoformat()

    runs = []
    strength = []
    for act in all_acts:
        atype = act.get("activityType", {}).get("typeKey", "")
        date = act.get("startTimeLocal", "")[:10]
        if date < cutoff:
            continue

        if "running" in atype.lower():
            dist_m = act.get("distance", 0)
            dist_mi = meters_to_miles(dist_m)
            if dist_mi < 0.5:
                continue  # skip tiny GPS blips

            duration_secs = act.get("duration", 0)
            duration_mins = round(duration_secs / 60, 1) if duration_secs else 0
            avg_hr = act.get("averageHR")
            max_hr = act.get("maxHR")
            avg_speed = act.get("averageSpeed", 0)  # m/s
            elev_gain = round(act.get("elevationGain", 0), 0)

            pace_str = pace_from_speed(avg_speed)
            run_type = categorize_run(dist_mi, avg_hr, duration_mins, date)

            runs.append({
                "id": act.get("activityId"),
                "name": act.get("activityName", "Run"),
                "date": date,
                "week": week_start(date),
                "distance_mi": dist_mi,
                "duration_mins": duration_mins,
                "avg_pace": pace_str,
                "avg_pace_secs": pace_to_seconds(pace_str),
                "avg_hr": avg_hr,
                "max_hr": max_hr,
                "elev_gain_ft": round(elev_gain * 3.28084, 0) if elev_gain else 0,
                "run_type": run_type,
                "calories": act.get("calories", 0),
            })

        elif atype.lower() in STRENGTH_TYPE_KEYS:
            duration_secs = act.get("duration", 0)
            duration_mins = round(duration_secs / 60, 1) if duration_secs else 0
            if duration_mins < 5:
                continue  # skip trivial entries
            strength.append({
                "id": act.get("activityId"),
                "name": act.get("activityName", "Workout"),
                "date": date,
                "week": week_start(date),
                "activity_type": STRENGTH_TYPE_KEYS[atype.lower()],
                "type_key": atype.lower(),
                "duration_mins": duration_mins,
                "calories": act.get("calories", 0),
                "avg_hr": act.get("averageHR"),
                "max_hr": act.get("maxHR"),
            })

    runs.sort(key=lambda r: r["date"])
    strength.sort(key=lambda s: s["date"])
    print(f"Found {len(runs)} qualifying runs, {len(strength)} strength sessions.", flush=True)

    # Fetch per-mile splits for recent runs (last 12 weeks)
    splits_cutoff = (datetime.date.today() - datetime.timedelta(weeks=12)).isoformat()
    recent_runs = [r for r in runs if r['date'] >= splits_cutoff]
    if recent_runs:
        print(f"Fetching mile splits for {len(recent_runs)} recent runs", flush=True, end="")
        for r in recent_runs:
            r['splits'] = fetch_run_splits(client, r['id'])
            print(".", end="", flush=True)
        print()

    return runs, strength

def fetch_training_status(client):
    """Fetch VO2max and training load if available."""
    try:
        today = datetime.date.today().isoformat()
        metrics = client.get_max_metrics(today)
        vo2 = None
        if metrics and isinstance(metrics, list):
            for m in metrics:
                v = m.get("generic", {}).get("vo2MaxValue") or \
                    m.get("cycling", {}).get("vo2MaxValue")
                if v:
                    vo2 = round(v, 1)
                    break
        return {"vo2_max": vo2}
    except Exception:
        return {"vo2_max": None}

def fetch_run_splits(client, activity_id):
    """Fetch per-mile lap data for a single run."""
    try:
        data = client.get_activity_splits(activity_id)
        laps = data.get('lapDTOs', [])
        miles = []
        for lap in laps:
            dist_m = lap.get('distance', 0)
            if dist_m < 400:  # skip partial laps shorter than 400m
                continue
            speed_ms = lap.get('averageSpeed', 0)
            pace_str = pace_from_speed(speed_ms) if speed_ms else None
            hr = lap.get('averageHR')
            miles.append({
                'mile':         lap.get('lapIndex', len(miles) + 1),
                'dist_mi':      round(dist_m / METERS_PER_MILE, 2),
                'pace':         pace_str,
                'pace_secs':    pace_to_seconds(pace_str),
                'hr':           round(hr) if hr else None,
                'elev_gain_ft': round((lap.get('elevationGain') or 0) * 3.28084),
            })
        return miles
    except Exception:
        return []

def fetch_sleep_and_recovery(client):
    """
    Fetch daily sleep stats and body-battery/recovery scores for the
    last WEEKS_BACK weeks.  Returns two lists:
      sleep_days  – one entry per night with score, duration, stages
      recovery_days – one entry per day with body-battery and HRV status
    """
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(weeks=WEEKS_BACK)
    sleep_days = []
    recovery_days = []

    print(f"Fetching sleep & recovery data ({WEEKS_BACK} weeks)...", flush=True, end="")
    day = cutoff
    while day <= today:
        date_str = day.isoformat()
        day += datetime.timedelta(days=1)

        # ── Sleep ──────────────────────────────────────────────────────────
        try:
            sd = client.get_sleep_data(date_str)
            if sd:
                daily = sd.get("dailySleepDTO") or sd  # shape varies by library version
                # Try to pull the core fields defensively
                score_obj = daily.get("sleepScores") or {}
                score_val = (
                    score_obj.get("overall", {}).get("value") if isinstance(score_obj.get("overall"), dict)
                    else score_obj.get("overall")
                )
                total_secs = daily.get("sleepTimeSeconds") or daily.get("totalSleepSeconds")
                deep_secs  = daily.get("deepSleepSeconds")
                rem_secs   = daily.get("remSleepSeconds")
                light_secs = daily.get("lightSleepSeconds")
                awake_secs = daily.get("awakeSleepSeconds") or daily.get("awakingCount")

                if total_secs and total_secs > 3600:  # at least 1 hr = real night
                    sleep_days.append({
                        "date":        date_str,
                        "score":       int(score_val) if score_val is not None else None,
                        "total_hrs":   round(total_secs / 3600, 2),
                        "deep_hrs":    round(deep_secs  / 3600, 2) if deep_secs  else None,
                        "rem_hrs":     round(rem_secs   / 3600, 2) if rem_secs   else None,
                        "light_hrs":   round(light_secs / 3600, 2) if light_secs else None,
                        "awake_mins":  round(awake_secs / 60,   1) if isinstance(awake_secs, (int,float)) and awake_secs > 60 else None,
                    })
        except Exception:
            pass  # day not available — skip silently

        # ── Body Battery / Recovery ────────────────────────────────────────
        try:
            bb = client.get_body_battery(date_str)
            # Returns list of {date, charged, drained, bodyBatteryStatList:[{bodyBatteryLevel,...}]}
            if bb and isinstance(bb, list):
                for entry in bb:
                    if entry.get("date") == date_str:
                        charged  = entry.get("charged")
                        drained  = entry.get("drained")
                        # End-of-day battery level = max level in statList
                        stat_list = entry.get("bodyBatteryStatList") or []
                        levels = [s.get("bodyBatteryLevel") for s in stat_list if s.get("bodyBatteryLevel") is not None]
                        end_level = max(levels) if levels else charged
                        recovery_days.append({
                            "date":       date_str,
                            "charged":    charged,
                            "drained":    drained,
                            "end_level":  end_level,
                        })
                        break
        except Exception:
            pass

    print(f" {len(sleep_days)} nights, {len(recovery_days)} recovery days.", flush=True)
    return sleep_days, recovery_days

def compute_sleep_kpis(sleep_days):
    if not sleep_days:
        return {}
    cutoff = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    recent = [s for s in sleep_days if s["date"] >= cutoff]
    scores = [s["score"] for s in sleep_days if s["score"] is not None]
    hrs    = [s["total_hrs"] for s in sleep_days]
    return {
        "avg_score":     round(sum(scores)/len(scores)) if scores else None,
        "avg_hrs":       round(sum(hrs)/len(hrs), 1) if hrs else None,
        "last_score":    sleep_days[-1]["score"] if sleep_days else None,
        "last_total_hrs":sleep_days[-1]["total_hrs"] if sleep_days else None,
        "this_week_avg_score": round(sum(s["score"] for s in recent if s["score"] is not None) /
                                     max(1, sum(1 for s in recent if s["score"] is not None))) if recent else None,
    }

# ── Aggregation ───────────────────────────────────────────────────────────────
def build_weekly_summary(runs):
    weeks = defaultdict(lambda: {
        "miles": 0, "runs": 0, "long_run_mi": 0,
        "pace_secs_list": [], "avg_hr_list": []
    })
    for r in runs:
        w = r["week"]
        weeks[w]["miles"] = round(weeks[w]["miles"] + r["distance_mi"], 2)
        weeks[w]["runs"] += 1
        weeks[w]["long_run_mi"] = round(max(weeks[w]["long_run_mi"], r["distance_mi"]), 2)
        if r["avg_pace_secs"]:
            weeks[w]["pace_secs_list"].append(r["avg_pace_secs"])
        if r["avg_hr"]:
            weeks[w]["avg_hr_list"].append(r["avg_hr"])

    result = []
    for week_dt in sorted(weeks.keys()):
        w = weeks[week_dt]
        avg_pace_secs = (sum(w["pace_secs_list"]) / len(w["pace_secs_list"])
                         if w["pace_secs_list"] else None)
        avg_hr = (round(sum(w["avg_hr_list"]) / len(w["avg_hr_list"]))
                  if w["avg_hr_list"] else None)
        result.append({
            "week": week_dt,
            "miles": w["miles"],
            "runs": w["runs"],
            "long_run_mi": w["long_run_mi"],
            "avg_pace": seconds_to_pace(avg_pace_secs),
            "avg_pace_secs": round(avg_pace_secs) if avg_pace_secs else None,
            "avg_hr": avg_hr,
        })
    return result

def compute_kpis(runs, weekly):
    if not runs:
        return {}
    recent_runs = [r for r in runs if r["date"] >= (
        datetime.date.today() - datetime.timedelta(days=7)).isoformat()]
    this_week_miles = round(sum(r["distance_mi"] for r in recent_runs), 1)

    all_paces = [r["avg_pace_secs"] for r in runs if r["avg_pace_secs"]]
    avg_pace = seconds_to_pace(sum(all_paces) / len(all_paces)) if all_paces else None

    peak_week = max(weekly, key=lambda w: w["miles"]) if weekly else {}
    longest_run = max(runs, key=lambda r: r["distance_mi"]) if runs else {}

    hrs = [r["avg_hr"] for r in runs if r["avg_hr"]]
    avg_hr_all = round(sum(hrs) / len(hrs)) if hrs else None

    return {
        "this_week_miles": this_week_miles,
        "total_runs": len(runs),
        "avg_pace": avg_pace,
        "peak_week_miles": peak_week.get("miles"),
        "longest_run_mi": longest_run.get("distance_mi"),
        "avg_hr": avg_hr_all,
    }

def compute_strength_kpis(strength):
    """Summary stats for strength sessions."""
    if not strength:
        return {"total_sessions": 0, "avg_duration_mins": None, "avg_calories": None, "this_week_sessions": 0}
    cutoff = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    this_week = [s for s in strength if s["date"] >= cutoff]
    durations = [s["duration_mins"] for s in strength if s["duration_mins"]]
    cals = [s["calories"] for s in strength if s["calories"]]
    return {
        "total_sessions": len(strength),
        "avg_duration_mins": round(sum(durations) / len(durations), 1) if durations else None,
        "avg_calories": round(sum(cals) / len(cals)) if cals else None,
        "this_week_sessions": len(this_week),
    }

def hr_zone_distribution(runs):
    """Count time in HR zones (rough estimate from avg HR distribution)."""
    zones = {"Zone 1 (<120)": 0, "Zone 2 (120-139)": 0,
             "Zone 3 (140-154)": 0, "Zone 4 (155-169)": 0, "Zone 5 (170+)": 0}
    for r in runs:
        hr = r.get("avg_hr")
        mins = r.get("duration_mins", 0)
        if not hr:
            continue
        if hr < 120:
            zones["Zone 1 (<120)"] += mins
        elif hr < 140:
            zones["Zone 2 (120-139)"] += mins
        elif hr < 155:
            zones["Zone 3 (140-154)"] += mins
        elif hr < 170:
            zones["Zone 4 (155-169)"] += mins
        else:
            zones["Zone 5 (170+)"] += mins
    return [{"zone": k, "minutes": round(v)} for k, v in zones.items()]

# ── Dashboard embed ───────────────────────────────────────────────────────────
def embed_data_in_dashboard(data, script_dir):
    """
    Rewrite the GARMIN_DATA constant in marathon_dashboard.html,
    then copy it to index.html so the Netlify PWA home-screen icon works.
    """
    import re, shutil
    html_path   = os.path.join(script_dir, "marathon_dashboard.html")
    index_path  = os.path.join(script_dir, "index.html")

    if not os.path.exists(html_path):
        print(f"  (Skipping dashboard embed — {html_path} not found)")
        return

    with open(html_path, "r") as f:
        html = f.read()

    new_const = f"const GARMIN_DATA = {json.dumps(data, separators=(',', ':'))};"
    updated = re.sub(r"const GARMIN_DATA = \{.*?\};", new_const, html, count=1, flags=re.DOTALL)

    if updated == html:
        print("  (Dashboard embed marker not found — HTML not updated)")
        return

    with open(html_path, "w") as f:
        f.write(updated)
    print(f"  Dashboard updated: {html_path}")

    # Mirror to index.html — this is what iOS/Netlify serves at "/"
    shutil.copy2(html_path, index_path)
    print(f"  Copied to:         {index_path}")

# ── GitHub Pages deploy ───────────────────────────────────────────────────────
def deploy_to_github(script_dir):
    """
    Commit updated dashboard files and push to GitHub Pages.
    The repo remote must already be configured with credentials baked into the URL.
    """
    import subprocess

    print("Deploying to GitHub Pages...", flush=True)
    try:
        # Stage the files that change on every sync
        files = ["index.html", "marathon_dashboard.html"]
        subprocess.run(
            ["git", "add"] + files,
            cwd=script_dir, check=True, capture_output=True
        )
        # Only commit if there are actual changes staged
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=script_dir, capture_output=True
        )
        if diff.returncode == 0:
            print("  No changes to deploy.")
            return
        subprocess.run(
            ["git", "commit", "-m", f"Auto-sync: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"],
            cwd=script_dir, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "push"],
            cwd=script_dir, check=True, capture_output=True
        )
        print("  Deployed: https://vishalkiritnaik-a11y.github.io/marathon-dashboard/")
    except subprocess.CalledProcessError as e:
        print(f"  GitHub deploy error: {e.stderr.decode().strip()}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    client = login()

    runs, strength = fetch_activities(client)
    ts = fetch_training_status(client)
    weekly = build_weekly_summary(runs)
    kpis = compute_kpis(runs, weekly)
    hr_zones = hr_zone_distribution(runs)
    strength_kpis = compute_strength_kpis(strength)
    sleep_days, recovery_days = fetch_sleep_and_recovery(client)
    sleep_kpis = compute_sleep_kpis(sleep_days)

    output = {
        "generated_at": datetime.datetime.now().isoformat(),
        "kpis": {**kpis, "vo2_max": ts.get("vo2_max")},
        "runs": runs,
        "weekly_summary": weekly,
        "hr_zones": hr_zones,
        "strength_sessions": strength,
        "strength_kpis": strength_kpis,
        "sleep_days": sleep_days,
        "recovery_days": recovery_days,
        "sleep_kpis": sleep_kpis,
    }

    script_dir = os.path.dirname(__file__)
    out_path = os.path.join(script_dir, "garmin_data.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nData saved to: {out_path}")
    print(f"  {len(runs)} runs | {len(weekly)} weeks of history")
    print(f"  {len(strength)} strength sessions")
    print(f"  {len(sleep_days)} nights of sleep tracked")
    print(f"  This week: {kpis.get('this_week_miles', 0)} miles")
    if ts.get("vo2_max"):
        print(f"  VO2 Max: {ts['vo2_max']}")

    embed_data_in_dashboard(output, script_dir)
    deploy_to_github(script_dir)
    print("\nDone. Open: https://vishalkiritnaik-a11y.github.io/marathon-dashboard/")

if __name__ == "__main__":
    main()
