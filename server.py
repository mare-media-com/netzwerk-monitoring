#!/usr/bin/env python3
import subprocess
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
import logging
from threading import Thread
import time
import os
import sys
import glob
from zoneinfo import ZoneInfo
from collections import defaultdict, deque
import platform
import requests
from config import (
    RASPIBERND_IP,
    FRITZBOX_IP,
    RASPI4B_IP,
    PING_TARGETS
)

logging.getLogger('werkzeug').setLevel(logging.ERROR)

# =====================
# PORT AUTOMATISCH BESTIMMEN
# =====================

# Prüfen, ob wir in einer virtuellen Umgebung arbeiten
# sys.prefix zeigt auf das .venv-Verzeichnis, wenn aktiviert
if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
    # Lokale Entwicklung in .venv → Port 5001
    PORT = 5001
    IS_DEV = True
else:
    # Container / Prod → Port 5000
    PORT = 5000
    IS_DEV = False

print(f"Starte Flask auf Port {PORT} ({'Entwicklung' if IS_DEV else 'Produktiv'})")

# =====================
# SYSTEM & PFADE
# =====================
IS_WINDOWS = platform.system().lower() == "windows"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # Ordner von server.py
LOG_DIR = os.path.join(BASE_DIR, "logs")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
LOG_PATH = os.path.join(LOG_DIR, "monitor.log")

# =====================
# KONFIGURATION
# =====================

TIMEOUT = 2
AGENT_TIMEOUT = 90  # Sekunden
LOCAL_TZ = ZoneInfo("Europe/Berlin")

RAM_WARN_THRESHOLD = 75
RAM_CRIT_THRESHOLD = 85

RAM_CHECK_INTERVAL = 15          # Sekunden
RAM_TRIGGER_TIME = 300           # 5 Minuten

RAM_TRIGGER_COUNT = RAM_TRIGGER_TIME // RAM_CHECK_INTERVAL

ram_warn_counter = 0
ram_crit_counter = 0

# =====================
# LOGGING
# =====================
logger = logging.getLogger("monitor")
logger.setLevel(logging.INFO)
handler = logging.FileHandler(LOG_PATH)
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.info("Logging gestartet")

# =====================
# STATUS
# =====================
status = {
    "server": {
        "fritzbox": None,
        "internet": None,
        "raspi4b": None,
        "fritzbox_latency": None,
        "internet_latency": None,
        "raspi4b_latency": None
    },
    "raspibernd": {
        "cpu_temp": None,
        "ram": None,
        "sd": None
    }
}

# =====================
# HILFSFUNKTIONEN
# =====================
def ping(host):
    try:
        if IS_WINDOWS:
            cmd = ["ping", "-n", "1", "-w", str(TIMEOUT * 1000), host]
        else:
            cmd = ["ping", "-c", "1", "-W", str(TIMEOUT), host]

        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except Exception:
        return False

def ping_with_latency(host):
    try:
        start = time.time()
        if IS_WINDOWS:
            cmd = ["ping", "-n", "1", "-w", str(TIMEOUT * 1000), host]
        else:
            cmd = ["ping", "-c", "1", "-W", str(TIMEOUT), host]

        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            latency_ms = int((time.time() - start) * 1000)
            return True, latency_ms
        return False, None
    except Exception as e:
        print("PING ERROR:", e)
        return False, None

def check_internet():
    successes, latencies = 0, []
    for host in PING_TARGETS:
        ok, latency = ping_with_latency(host)
        if ok:
            successes += 1
            latencies.append(latency)
    if successes >= 2:
        return True, int(sum(latencies)/len(latencies))
    return False, None

def log_change(source, component, old, new):
    if old is None:
        return
    if old != new:
        state = "OK" if new else "FAIL"
        logger.info(f"{source} | {component} | {state}")

def check_raspibernd_limits(cpu_temp, ram, sd):
    if cpu_temp is not None and cpu_temp >= 75:
        logger.warning(f"RASPIBERND | CPU_TEMP | HIGH | {cpu_temp:.2f}")
        logger.info(f"SERVER | RASPIBERND | TEMP_WARN")

    global ram_warn_counter, ram_crit_counter

    if ram is not None:

        if ram >= RAM_CRIT_THRESHOLD:
            ram_crit_counter += 1
            ram_warn_counter += 1   # logisch auch warn-level

        elif ram >= RAM_WARN_THRESHOLD:
            ram_warn_counter += 1
            ram_crit_counter = 0

        else:
            ram_warn_counter = 0
            ram_crit_counter = 0

    if ram_crit_counter == RAM_TRIGGER_COUNT:
        logger.warning(f"RASPIBERND | RAM | CRITICAL | {ram:.1f}")
        logger.info("SERVER | RASPIBERND | RAM_CRITICAL")
        ram_crit_counter = RAM_TRIGGER_COUNT   # Deckeln

    elif ram_warn_counter == RAM_TRIGGER_COUNT:
        logger.warning(f"RASPIBERND | RAM | HIGH | {ram:.1f}")
        logger.info("SERVER | RASPIBERND | RAM_WARN")
        ram_warn_counter = RAM_TRIGGER_COUNT   # Deckeln

    if sd is not None:
        if sd >= 80:
            logger.warning(f"RASPIBERND | SD | CRITICAL | {sd:.2f}")
            logger.info(f"SERVER | RASPIBERND | SD_CRITICAL")
        elif sd >= 60:
            logger.warning(f"RASPIBERND | SD | HIGH | {sd:.2f}")
            logger.info(f"SERVER | RASPIBERND | SD_WARN")

# =====================
# LOG- & TIMELINE-FUNKTIONEN
# =====================

def get_log_files():
    files = glob.glob(os.path.join(LOG_DIR, "monitor.log*"))

    def extract_number(path):
        name = os.path.basename(path)
        if name == "monitor.log":
            return 0
        try:
            return int(name.split(".")[-1])
        except:
            return 0

    # älteste zuerst: höchste Nummer zuerst
    return sorted(files, key=extract_number, reverse=True)

def read_recent_logs(lines=100):
    entries = []
    for log_file in get_log_files():
        if not os.path.exists(log_file):
            continue
        with open(log_file, "r") as f:
            for line in f:
                line = line.strip()
                if "|" not in line:
                    continue
                try:
                    ts_str, rest = line.split(" | ", 1)
                    ts_local = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    line = f"{ts_local.strftime('%d.%m.%Y %H:%M:%S')} | {rest}"
                except Exception:
                    pass
                entries.append(line)
    return entries[-lines:]

def read_server_timeline(max_events=15):
    timeline = defaultdict(lambda: deque(maxlen=max_events))
    for log_file in get_log_files():
        if not os.path.exists(log_file):
            continue
        with open(log_file, "r") as f:
            for line in f:
                if "SERVER |" not in line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 5:
                    continue
                try:
                    ts_local = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                server = parts[3]
                state = parts[4]
                timeline[server].append({"time": ts_local.strftime("%d.%m.%Y %H:%M:%S"),
                                         "state": state})
    return {server: list(events) for server, events in timeline.items()}

def last_server_outage_duration(component):
    fail_times = []
    for log_file in get_log_files():
        if not os.path.exists(log_file):
            continue
        with open(log_file, "r") as f:
            for line in f:
                if f"SERVER | {component} | FAIL" in line:
                    try:
                        ts = datetime.strptime(line.split(" | ", 1)[0], "%Y-%m-%d %H:%M:%S")
                        fail_times.append(ts)
                    except Exception:
                        continue
    if not fail_times:
        return None, None
    latest_fail = max(fail_times)
    ok_time = None
    for log_file in get_log_files():
        if not os.path.exists(log_file):
            continue
        with open(log_file, "r") as f:
            for line in f:
                if f"SERVER | {component} | OK" in line:
                    try:
                        ts = datetime.strptime(line.split(" | ", 1)[0], "%Y-%m-%d %H:%M:%S")
                        if ts > latest_fail:
                            ok_time = ts
                            break
                    except Exception:
                        continue
        if ok_time:
            break
    return latest_fail, ok_time - latest_fail if ok_time else None

def count_recent_outages(component, hours):
    cutoff = datetime.now() - timedelta(hours=hours)
    count = 0
    for log_file in get_log_files():
        if not os.path.exists(log_file):
            continue
        with open(log_file, "r") as f:
            for line in f:
                if f"SERVER | {component} | FAIL" not in line:
                    continue
                try:
                    ts_utc = datetime.strptime(line.split(" | ", 1)[0], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                if ts_utc >= cutoff:
                    count += 1
    return count

def calculate_downtime(component, hours):
    cutoff = datetime.now() - timedelta(hours=hours)
    downtime = timedelta()
    fail_start = None
    for log_file in get_log_files():
        if not os.path.exists(log_file):
            continue
        with open(log_file, "r") as f:
            for line in f:
                if f"SERVER | {component} |" not in line:
                    continue
                try:
                    ts = datetime.strptime(line.split(" | ", 1)[0], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                if ts < cutoff:
                    continue
                if "FAIL" in line:
                    if fail_start is None:
                        fail_start = ts
                elif "OK" in line:
                    if fail_start is not None:
                        downtime += ts - fail_start
                        fail_start = None
    if fail_start is not None:
        downtime += datetime.now() - fail_start
    return downtime

def calculate_availability(component, hours):
    total_time = timedelta(hours=hours)
    downtime = calculate_downtime(component, hours)
    uptime = total_time - downtime
    return round((uptime / total_time) * 100, 3)

def format_duration(td):
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"

def format_duration(delta):
    if not delta:
        return None

    seconds = int(delta.total_seconds())

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    return f"{h}h {m}m {s}s"

# =====================
# RASPIBERND STATS
# =====================
def get_raspibernd_stats():
    try:
        r = requests.get(f"http://{RASPIBERND_IP}:8888/dynamic.json", timeout=5)
        data = r.json()

        # =====================
        # CPU Temperatur
        # =====================
        cpu_temp = float(data.get("soc_temp"))

        # =====================
        # RAM Nutzung berechnen
        # =====================
        memory_available = float(data.get("memory_available"))
        memory_total = 909.60   # ← EINMALIG aus RPi-Monitor übernommen

        memory_used = memory_total - memory_available
        ram_usage = (memory_used / memory_total) * 100

        # =====================
        # SD Nutzung
        # =====================
        sd_used = float(data.get("sdcard_root_used"))

        # Falls du Prozent willst (optional)
        sd_total = 28950        # ← EINMALIG aus RPi-Monitor übernommen
        sd_usage = (sd_used / sd_total) * 100

        return cpu_temp, ram_usage, sd_usage

    except Exception as e:
        print("RPi-Monitor fetch error:", e)
        return None, None, None

# =====================
# FLASK APP
# =====================
app = Flask(__name__)

@app.route("/")
def index():
   
    logs = read_recent_logs()
    server_timeline = read_server_timeline()

    inet_downtime_7d = calculate_downtime("INTERNET", 24*7)
    inet_availability_7d = calculate_availability("INTERNET", 24*7)

    fritz_downtime_7d = calculate_downtime("FRITZBOX", 24*7)
    fritz_availability_7d = calculate_availability("FRITZBOX", 24*7)

    raspi4b_downtime_7d = calculate_downtime("RASPI4B", 24*7)
    raspi4b_availability_7d = calculate_availability("RASPI4B", 24*7)

    return render_template(
        "index.html",
        status=status,
        logs=logs,
        server_timeline=server_timeline,

        inet_fail_start=last_server_outage_duration("INTERNET")[0],
        inet_fail_duration=last_server_outage_duration("INTERNET")[1],
        inet_outages_7d=count_recent_outages("INTERNET", 24*7),
        inet_outages_24h=count_recent_outages("INTERNET", 24),
        inet_downtime_7d=format_duration(inet_downtime_7d),
        inet_availability_7d=inet_availability_7d,

        fritz_fail_start=last_server_outage_duration("FRITZBOX")[0],
        fritz_fail_duration=last_server_outage_duration("FRITZBOX")[1],
        fritz_outages_7d=count_recent_outages("FRITZBOX", 24*7),
        fritz_outages_24h=count_recent_outages("FRITZBOX", 24),
        fritz_downtime_7d=format_duration(fritz_downtime_7d),
        fritz_availability_7d=fritz_availability_7d,

        raspi4b_fail_start=last_server_outage_duration("RASPI4B")[0],
        raspi4b_fail_duration=last_server_outage_duration("RASPI4B")[1],
        raspi4b_outages_7d=count_recent_outages("RASPI4B", 24*7),
        raspi4b_outages_24h=count_recent_outages("RASPI4B", 24),
        raspi4b_downtime_7d=format_duration(raspi4b_downtime_7d),
        raspi4b_availability_7d=raspi4b_availability_7d,

        raspibernd_cpu=status["raspibernd"]["cpu_temp"],
        raspibernd_ram=status["raspibernd"]["ram"],
        raspibernd_sd=status["raspibernd"]["sd"],

        IS_DEV=IS_DEV
    )

@app.route("/api/status")
def api_status():
    return jsonify(status)

@app.route("/debug")
def debug():

    now = datetime.now(LOCAL_TZ)

    # =====================
    # Outage Daten
    # =====================

    inet_fail_start, inet_fail_duration = last_server_outage_duration("INTERNET")
    fritz_fail_start, fritz_fail_duration = last_server_outage_duration("FRITZBOX")
    raspi4b_fail_start, raspi4b_fail_duration = last_server_outage_duration("RASPI4B")

    # =====================
    # Statistiken
    # =====================

    inet_outages_24h = count_recent_outages("INTERNET", 24)
    inet_outages_7d  = count_recent_outages("INTERNET", 24*7)
    inet_downtime_7d = format_duration(calculate_downtime("INTERNET", 24*7))
    inet_availability_7d = calculate_availability("INTERNET", 24*7)

    fritz_outages_24h = count_recent_outages("FRITZBOX", 24)
    fritz_outages_7d  = count_recent_outages("FRITZBOX", 24*7)
    fritz_downtime_7d = format_duration(calculate_downtime("FRITZBOX", 24*7))
    fritz_availability_7d = calculate_availability("FRITZBOX", 24*7)

    raspi4b_outages_24h = count_recent_outages("RASPI4B", 24)
    raspi4b_outages_7d  = count_recent_outages("RASPI4B", 24*7)
    raspi4b_downtime_7d = format_duration(calculate_downtime("RASPI4B", 24*7))
    raspi4b_availability_7d = calculate_availability("RASPI4B", 24*7)

    # =====================
    # Timelines
    # =====================

    server_timeline = read_server_timeline()

    return {
        # =====================
        # Systemwerte
        # =====================
        "cpu": status["raspibernd"]["cpu_temp"],
        "ram": status["raspibernd"]["ram"],
        "sd":  status["raspibernd"]["sd"],

        # =====================
        # Serverstatus
        # =====================
        "servers": {
            "internet": status["server"]["internet"],
            "internet_latency": status["server"]["internet_latency"],

            "fritzbox": status["server"]["fritzbox"],
            "fritzbox_latency": status["server"]["fritzbox_latency"],

            "raspi4b": status["server"]["raspi4b"],
            "raspi4b_latency": status["server"]["raspi4b_latency"]
        },

        # =====================
        # Internet
        # =====================
        "inet_fail_start": (
            inet_fail_start.strftime("%d.%m.%Y %H:%M:%S")
            if inet_fail_start else None
        ),
        "inet_fail_duration_text": format_duration(inet_fail_duration),

        "inet_outages_24h": inet_outages_24h,
        "inet_outages_7d": inet_outages_7d,
        "inet_downtime_7d": inet_downtime_7d,
        "inet_availability_7d": inet_availability_7d,

        # =====================
        # FritzBox
        # =====================
        "fritz_fail_start": (
            fritz_fail_start.strftime("%d.%m.%Y %H:%M:%S")
            if fritz_fail_start else None
        ),
        "fritz_fail_duration_text": format_duration(fritz_fail_duration),

        "fritz_outages_24h": fritz_outages_24h,
        "fritz_outages_7d": fritz_outages_7d,
        "fritz_downtime_7d": fritz_downtime_7d,
        "fritz_availability_7d": fritz_availability_7d,

        # =====================
        # Raspi4b
        # =====================
        "raspi4b_fail_start": (
            raspi4b_fail_start.strftime("%d.%m.%Y %H:%M:%S")
            if raspi4b_fail_start else None
        ),
        "raspi4b_fail_duration_text": format_duration(raspi4b_fail_duration),

        "raspi4b_outages_24h": raspi4b_outages_24h,
        "raspi4b_outages_7d": raspi4b_outages_7d,
        "raspi4b_downtime_7d": raspi4b_downtime_7d,
        "raspi4b_availability_7d": raspi4b_availability_7d,

        # =====================
        # Timelines
        # =====================
        "timelines": {
            "internet": server_timeline.get("INTERNET", []),
            "fritzbox": server_timeline.get("FRITZBOX", []),
            "raspi4b": server_timeline.get("RASPI4B", []),
            "raspibernd": server_timeline.get("RASPIBERND", [])
        }
    }


# =====================
# HAUPTSCHLEIFE
# =====================
def background_checks():
    while True:
        # FritzBox
        old_fritz = status["server"]["fritzbox"]
        ok_fritz, latency_fritz = ping_with_latency(FRITZBOX_IP)
        status["server"]["fritzbox"] = ok_fritz
        status["server"]["fritzbox_latency"] = latency_fritz
        log_change("SERVER", "FRITZBOX", old_fritz, ok_fritz)

        # Internet
        old_net = status["server"]["internet"]
        ok_inet, latency_inet = check_internet()
        status["server"]["internet"] = ok_inet
        status["server"]["internet_latency"] = latency_inet
        log_change("SERVER", "INTERNET", old_net, ok_inet)

        # Raspi4b
        old_raspi = status["server"]["raspi4b"]
        ok_raspi, latency_raspi = ping_with_latency(RASPI4B_IP)
        status["server"]["raspi4b"] = ok_raspi
        status["server"]["raspi4b_latency"] = latency_raspi
        log_change("SERVER", "RASPI4B", old_raspi, ok_raspi)

        # raspibernd
        cpu_temp, ram, sd = get_raspibernd_stats()

        status["raspibernd"]["cpu_temp"] = cpu_temp
        status["raspibernd"]["ram"] = ram
        status["raspibernd"]["sd"] = sd

        check_raspibernd_limits(cpu_temp, ram, sd)

        time.sleep(30)

if __name__ == "__main__":
    from threading import Thread
    Thread(target=background_checks, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=True, use_reloader=False)