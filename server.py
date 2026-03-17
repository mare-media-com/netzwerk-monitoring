#!/usr/bin/env python3
# region imports
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
from config import HOSTS, RASPBERRY_THRESHOLDS
# endregion imports


# region settings
logging.getLogger('werkzeug').setLevel(logging.ERROR)


# =====================
# PORT AUTOMATISCH BESTIMMEN
# =====================
# Prüfen, ob in einer virtuellen Umgebung gearbeitet wird
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


# ---------------------
# PING und Raspberry Hosts
# ---------------------
PING_HOSTS = [
    host for host, cfg in HOSTS.items()
    if "ping" in cfg.get("types", [])
]

RASPBERRY_HOSTS = [
    host for host, cfg in HOSTS.items()
    if "raspberry" in cfg.get("types", [])
]


# ---------------------
# IPs, RAM, SD
# ---------------------
HOST_IPS = {
    host: cfg["ip"]
    for host, cfg in HOSTS.items()
    if "ip" in cfg
}

TOTAL_RAM = {
    host: HOSTS[host]["total_ram"]
    for host in RASPBERRY_HOSTS
}

TOTAL_SD = {
    host: HOSTS[host]["total_sd"]
    for host in RASPBERRY_HOSTS
}


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
RAM_CHECK_INTERVAL = 15          # Sekunden
RAM_TRIGGER_TIME = 300           # 5 Minuten
RAM_TRIGGER_COUNT = RAM_TRIGGER_TIME // RAM_CHECK_INTERVAL


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
sensor_states = {} # speichert aktuellen Status pro Host/Sensor, z.B. "OK", "WARN", "CRITICAL"
timelines = defaultdict(lambda: deque(maxlen=50))  # speichert die Timeline, max 50 Einträge pro Host
# endregion settings


# region status
# =====================
# STATUS
# =====================
status = {
    "server": {},
}

# Fehlversuche zählen (Ping-Stabilisierung)
ping_fail_counter = {host: 0 for host in HOSTS}
PING_FAIL_THRESHOLD = 3

ram_warn_counter = {}
ram_crit_counter = {}

# Ping Hosts vorbereiten
for host, cfg in HOSTS.items():
    if "ping" in cfg.get("types", []):
        status["server"][host] = None
        status["server"][f"{host}_latency"] = None

# Raspberry Hosts vorbereiten
for host, cfg in HOSTS.items():
    if "raspberry" in cfg.get("types", []):
        status[host] = {
            "cpu_temp": None,

            "ram_percent": None,
            "ram_used": None,
            "ram_total": None,

            "sd_percent": None,
            "sd_used": None,
            "sd_total": None            
        }
# endregion status


# region functions
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

def check_multiple_ping(targets):
    successes, latencies = 0, []
    for target in targets:
        ok, latency = ping_with_latency(target)
        if ok:
            successes += 1
            latencies.append(latency)
    if successes >= 2:  # z. B. mindestens 2 Erfolge
        return True, int(sum(latencies)/len(latencies))
    return False, None

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

def log_change(source, component, old, new):
    if old is None:
        return
    if old != new:
        state = "OK" if new else "FAIL"
        logger.info(f"{source} | {component} | {state}")

def check_raspberry_limits(host, cpu_temp, ram, sd):

    thresholds = RASPBERRY_THRESHOLDS
    
    # =====================
    # CPU Temperatur
    # =====================

    if cpu_temp is not None:
        if cpu_temp >= thresholds["cpu"]["crit"]:
            logger.warning(f"{host.upper()} | CPU_TEMP | CRITICAL | {cpu_temp:.2f}")
            logger.info(f"SERVER | {host.upper()} | TEMP_CRITICAL")
        elif cpu_temp >= thresholds["cpu"]["warn"]:
            logger.warning(f"{host.upper()} | CPU_TEMP | HIGH | {cpu_temp:.2f}")
            logger.info(f"SERVER | {host.upper()} | TEMP_WARN")

    # =====================
    # RAM Überwachung
    # =====================

    if host not in ram_warn_counter:
        ram_warn_counter[host] = 0
        ram_crit_counter[host] = 0

    if ram is not None:

        if ram >= thresholds["ram"]["crit"]:
            ram_crit_counter[host] += 1
            ram_warn_counter[host] += 1

        elif ram >= thresholds["ram"]["warn"]:
            ram_warn_counter[host] += 1
            ram_crit_counter[host] = 0

        else:
            ram_warn_counter[host] = 0
            ram_crit_counter[host] = 0

    if ram_crit_counter[host] == RAM_TRIGGER_COUNT:

        logger.warning(f"{host.upper()} | RAM | CRITICAL | {ram:.1f}")
        logger.info(f"SERVER | {host.upper()} | RAM_CRITICAL")

        ram_crit_counter[host] = RAM_TRIGGER_COUNT

    elif ram_warn_counter[host] == RAM_TRIGGER_COUNT:

        logger.warning(f"{host.upper()} | RAM | HIGH | {ram:.1f}")
        logger.info(f"SERVER | {host.upper()} | RAM_WARN")

        ram_warn_counter[host] = RAM_TRIGGER_COUNT

    # =====================
    # SD Karte
    # =====================

    if sd is not None:

        if sd >= thresholds["sd"]["crit"]:

            logger.warning(f"{host.upper()} | SD | CRITICAL | {sd:.2f}")
            logger.info(f"SERVER | {host.upper()} | SD_CRITICAL")

        elif sd >= thresholds["sd"]["warn"]:

            logger.warning(f"{host.upper()} | SD | HIGH | {sd:.2f}")
            logger.info(f"SERVER | {host.upper()} | SD_WARN")


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


# =====================
# SENSOR STATUS & TIMELINE
# =====================

def update_sensor_state(host, sensor, new_state):
    """ Aktualisiert die Timeline nur bei Statusänderung """
    if host not in sensor_states:
        sensor_states[host] = {}

    old_state = sensor_states[host].get(sensor, "OK")

    if new_state != old_state:
        timelines[host].append({
            "time": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
            "state": f"{sensor.upper()}_{new_state}"
        })
        sensor_states[host][sensor] = new_state
        print(f"Timeline aktualisiert: {host} | {sensor.upper()} -> {new_state}")

def evaluate_sensor(host, sensor, value, warn, crit):
    """ Bewertet den Sensorwert und aktualisiert den Status """
    if value >= crit:
        state = "CRITICAL"
    elif value >= warn:
        state = "WARN"
    else:
        state = "OK"
    update_sensor_state(host, sensor, state)

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

def format_duration(delta):
    if not delta:
        return None

    seconds = int(delta.total_seconds())

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    return f"{h}h {m}m {s}s"
# endregion functions


# region flask app & routen
# =====================
# FLASK APP / Routen
# =====================
app = Flask(__name__)

@app.route("/")
def index():
   
    logs = read_recent_logs()
    server_timeline = read_server_timeline()

    server_stats = {}

    for host in PING_HOSTS:

        fail_start, fail_duration = last_server_outage_duration(host.upper())

        server_stats[host] = {
            "fail_start": fail_start,
            "fail_duration": fail_duration,
            "outages_24h": count_recent_outages(host.upper(), 24),
            "outages_7d": count_recent_outages(host.upper(), 24*7),
            "downtime_7d": format_duration(calculate_downtime(host.upper(), 24*7)),
            "availability_7d": calculate_availability(host.upper(), 24*7)
        }

    return render_template(
        "index.html",
        HOSTS=HOSTS,
        status=status,
        logs=logs,
        server_timeline=server_timeline,
        server_stats=server_stats,

            IS_DEV=IS_DEV
        )

@app.route("/config")
def get_config():
    return jsonify(HOSTS)

@app.route("/api/status")
def api_status():
    raspberry_stats = {}
    for host, cfg in HOSTS.items():
        if "raspberry" not in cfg.get("types", []):
            continue
        s = status.get(host, {})
        raspberry_stats[host] = {
            "cpu": s.get("cpu_temp"),
            "ram_percent": s.get("ram_percent"),
            "ram_used": s.get("ram_used"),
            "ram_total": s.get("ram_total"),
            "sd_percent": s.get("sd_percent"),
            "sd_used": s.get("sd_used"),
            "sd_total": s.get("sd_total")
        }

    return jsonify({
        "status": status,
        "raspberries": raspberry_stats,
        "thresholds": RASPBERRY_THRESHOLDS
    })

@app.route("/debug")
def debug():
    now = datetime.now(LOCAL_TZ)
    server_timeline = read_server_timeline()

    # =====================
    # SERVER STATUS
    # =====================
    servers = {}
    for host, cfg in HOSTS.items():
        if "ping" in cfg.get("types", []):
            servers[host] = {
                "online": status["server"].get(host),
                "latency": status["server"].get(f"{host}_latency")
            }

    # =====================
    # SERVER STATISTIKEN
    # =====================
    server_stats = {}
    for host, cfg in HOSTS.items():
        if "ping" not in cfg.get("types", []):
            continue

        fail_start, fail_duration = last_server_outage_duration(host.upper())

        server_stats[host] = {
            "fail_start": (
                fail_start.strftime("%d.%m.%Y %H:%M:%S")
                if fail_start else None
            ),
            "fail_duration": format_duration(fail_duration),
            "outages_24h": count_recent_outages(host.upper(), 24),
            "outages_7d": count_recent_outages(host.upper(), 24*7),
            "downtime_7d": format_duration(
                calculate_downtime(host.upper(), 24*7)
            ),
            "availability_7d": calculate_availability(host.upper(), 24*7)
        }

    # =====================
    # RASPBERRY SYSTEMWERTE
    # =====================

    raspberry_stats = {}
    for host, cfg in HOSTS.items():
        if "raspberry" not in cfg.get("types", []):
            continue
        s = status.get(host, {})
        raspberry_stats[host] = {
            "cpu": s.get("cpu_temp"),
            "ram_percent": s.get("ram_percent"),
            "ram_used": s.get("ram_used"),
            "ram_total": s.get("ram_total"),
            "sd_percent": s.get("sd_percent"),
            "sd_used": s.get("sd_used"),
            "sd_total": s.get("sd_total")
        }

    # =====================
    # TIMELINES (SERVER + SENSOR), auf letzte 5 Einträge limitiert
    # =====================
    timelines_combined = {}
    for host in HOSTS:
        server_events = server_timeline.get(host.upper(), [])
        sensor_events = list(timelines.get(host, []))
        timelines_combined[host] = server_events + sensor_events

    # =====================
    # RÜCKGABE ALS JSON
    # =====================
    return {
        "time": now.strftime("%d.%m.%Y %H:%M:%S"),
        "servers": servers,
        "server_stats": server_stats,
        "raspberries": raspberry_stats,
        "timelines": timelines_combined,
        "thresholds": RASPBERRY_THRESHOLDS
    }
# endregion routes


# region main loop
# =====================
# HAUPTSCHLEIFE
# =====================
def background_checks():
    while True:

        # =====================
        # PING HOSTS
        # =====================

        for host, cfg in HOSTS.items():
            if "ping" not in cfg.get("types", []):
                continue

            old_state = status["server"].get(host)

            # Ping durchführen
            if "ip" in cfg:
                ok, latency = ping_with_latency(cfg["ip"])
            elif "targets" in cfg:
                ok, latency = check_multiple_ping(cfg["targets"])
            else:
                continue

            # Status setzen
            status["server"][host] = ok
            status["server"][f"{host}_latency"] = latency

            # Ping-Fail-Counter erhöhen, falls fehlgeschlagen
            if not ok:
                ping_fail_counter[host] += 1
                if ping_fail_counter[host] >= PING_FAIL_THRESHOLD:
                    status["server"][host] = False
                    status["server"][f"{host}_latency"] = None
                    log_change("SERVER", host.upper(), old_state, False)
            else:
                ping_fail_counter[host] = 0
                log_change("SERVER", host.upper(), old_state, ok)

        # =====================
        # RASPBERRY STATS
        # =====================

        for host, cfg in HOSTS.items():

            if "raspberry" in cfg.get("types", []):

                try:

                    r = requests.get(
                        f"http://{cfg['ip']}:8888/dynamic.json",
                        timeout=5
                    )

                    data = r.json()

                    # CPU Temperatur
                    cpu_temp = float(data.get("soc_temp", 0))

                    # RAM
                    memory_available = float(data.get("memory_available"))
                    memory_total = cfg.get("total_ram")

                    memory_used = memory_total - memory_available
                    ram_percent = (memory_used / memory_total) * 100

                    # SD (rpimonitor liefert MB)
                    sd_used = float(data.get("sdcard_root_used")) / 1024
                    sd_total = cfg.get("total_sd")

                    sd_percent = (sd_used / sd_total) * 100

                    status[host] = {
                        "cpu_temp": cpu_temp,

                        "ram_percent": ram_percent,
                        "ram_used": memory_used,
                        "ram_total": memory_total,

                        "sd_percent": sd_percent,
                        "sd_used": sd_used,
                        "sd_total": sd_total
                    }

                    check_raspberry_limits(host, cpu_temp, ram_percent, sd_percent)

                    # =====================
                    # SENSOR STATUS (Timeline)
                    # =====================

                    evaluate_sensor(
                        host,
                        "temp",
                        cpu_temp,
                        RASPBERRY_THRESHOLDS["cpu"]["warn"],
                        RASPBERRY_THRESHOLDS["cpu"]["crit"]
                    )

                    evaluate_sensor(
                        host,
                        "ram",
                        ram_percent,
                        RASPBERRY_THRESHOLDS["ram"]["warn"],
                        RASPBERRY_THRESHOLDS["ram"]["crit"]
                    )

                    evaluate_sensor(
                        host,
                        "sd",
                        sd_percent,
                        RASPBERRY_THRESHOLDS["sd"]["warn"],
                        RASPBERRY_THRESHOLDS["sd"]["crit"]
                    )

                # except Exception as e:
                    # print(f"{host} fetch error:", e)
                except requests.exceptions.RequestException:
                    # 👉 Host ist offline / nicht erreichbar

                    status[host]["cpu_temp"] = None
                    status[host]["ram"] = None
                    status[host]["ram_used"] = None
                    status[host]["sd"] = None
                    status[host]["sd_used"] = None

                    # print(f"{host} offline")

        time.sleep(30)


# =====================
# TESTFUNKTION FÜR SENSOR-STATE HANDLING
# =====================
def test_sensor_state_handling():
    test_host = "test_rpi"
    test_values = [
        {"cpu": 70, "ram": 60, "sd": 80},  # WARN/CRIT
        {"cpu": 85, "ram": 75, "sd": 90},  # steigt auf CRIT/WARN
        {"cpu": 85, "ram": 75, "sd": 90},  # gleiche Werte, sollte nichts neues erzeugen
        {"cpu": 60, "ram": 50, "sd": 70},  # fällt auf OK/WARN
        {"cpu": 90, "ram": 80, "sd": 95},  # wieder CRIT/WARN
    ]

    thresholds = {
        "cpu": {"warn": 65, "crit": 80},
        "ram": {"warn": 70, "crit": 85},
        "sd": {"warn": 75, "crit": 90},
    }

    for i, vals in enumerate(test_values, 1):
        print(f"\nLoop {i}: Werte = {vals}")
        for sensor, value in vals.items():
            evaluate_sensor(test_host, sensor, value,
                            warn=thresholds[sensor]["warn"],
                            crit=thresholds[sensor]["crit"])
        print("Timeline aktuell:")
        for entry in timelines[test_host]:
            print(entry)
        print("-" * 50)


# =====================
# Aufruf HAUPTSCHLEIFE
# =====================
if __name__ == "__main__":
    # Test starten
    # test_sensor_state_handling()

    from threading import Thread
    Thread(target=background_checks, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=True, use_reloader=False)
# endregion main loop