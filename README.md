# 🌐 Netzwerk-Monitoring Dashboard

Ein leichtgewichtiges, webbasiertes Monitoring-Dashboard für ein Heimnetzwerk auf einem Raspberry Pi.
Das System überwacht zentrale Netzwerkkomponenten (Internet, Router, Server) und stellt Status, Latenzen, Ausfälle und Systemressourcen in einem übersichtlichen Webinterface dar.

Das Projekt ist speziell für **Home-Lab-Umgebungen** konzipiert und läuft komplett lokal.

---

# ✨ Features

### Netzwerküberwachung

* Statusüberwachung von:

  * Internetverbindung
  * FRITZ!Box
  * Raspberry Pi Server
* Anzeige der aktuellen Latenz
* automatische Erkennung von Ausfällen

### Ausfallanalyse

* letzter Ausfall mit Startzeit
* Dauer des Ausfalls
* Anzahl der Ausfälle

  * letzte 24 Stunden
  * letzte 7 Tage
* Gesamt-Downtime der letzten 7 Tage
* berechnete Verfügbarkeit (Availability)

### Systemüberwachung (Raspberry Pi)

Anzeige von:

* CPU-Temperatur
* RAM-Auslastung
* SD-Card Speicher

inklusive:

* Gauge-Anzeige für CPU-Temperatur
* Fortschrittsbalken für RAM und Speicher

### Event-Timeline

Für jedes überwachte System:

* Status-Historie
* automatische Live-Updates
* farblich markierte Events

| Status  | Bedeutung               |
| ------- | ----------------------- |
| 🟢 OK   | System erreichbar       |
| 🔴 FAIL | System nicht erreichbar |

### Live-Dashboard

Das Webinterface aktualisiert sich automatisch:

* Live-Status
* Timelines
* Systemwerte

ohne Seiten-Reload.

---

# 🖥 Beispiel Dashboard

Das Dashboard zeigt:

* Netzwerkstatus
* Systemressourcen
* Ausfallstatistiken
* Ereignis-Timeline

Alle Daten werden lokal verarbeitet und im Browser dargestellt.

---

# 🏗 Architektur

```
Raspberry Pi
│
├── server.py
│
├── logs/
│   ├── monitor.log
│   ├── monitor.log.1
│   └── ...
│
├── static/
│   ├── style.css
│   └── favicon.png
│
└── templates/
    └── index.html
```

### Backend

* Python
* Flask Webserver
* Ping-Monitoring
* Log-Analyse für Ausfallstatistiken

### Frontend

* HTML / CSS
* JavaScript
* Live-Updates via Fetch API
* JustGage für Temperaturanzeige

---

# ⚙️ Installation

## Repository klonen

```
git clone https://github.com/USERNAME/netzwerk-monitoring.git
cd netzwerk-monitoring
```

---

## Python Abhängigkeiten installieren

```
pip install -r requirements.txt
```

---

## Datei secrets.js

```
/static/config/secrets.example
secrets.example umbenennen in secrets.js
MQTT-IP und MQTT-Port eintragen
```

---

## 3️⃣ Server starten

```
python3 server.py
```

Das Dashboard ist anschließend erreichbar unter:

```
http://localhost:5000
```

oder im Netzwerk z.B.:

```
http://raspberrypi:5000
```

---

# 🔍 API Endpoint

Das Frontend ruft regelmäßig den Debug-Endpoint auf:

```
/debug
```

Dieser liefert JSON-Daten für:

* Netzwerkstatus
* Latenzen
* Ausfallstatistiken
* Timelines
* Systemressourcen

---

# 📊 Log-Auswertung

Alle Monitoring-Events werden in Logdateien gespeichert:

```
SERVER | INTERNET | OK
SERVER | INTERNET | FAIL
```

Diese Logs werden ausgewertet für:

* Ausfallstatistiken
* Downtime-Berechnung
* Timeline-Anzeige

---

# 🎨 Benutzeroberfläche

Das Dashboard verwendet:

* Kartenlayout für Systeme
* Status-Farben
* Timeline-Events
* Fortschrittsbalken
* Live-Aktualisierung

Farbschema:

| Farbe | Bedeutung |
| ----- | --------- |
| Grün  | System OK |
| Gelb  | Warnung   |
| Rot   | Fehler    |

---

# 📜 Lizenz

Dieses Projekt ist ein persönliches Home-Lab Monitoring Tool.

Verwendung und Anpassung für private Projekte ist erlaubt.

---

# 👨‍💻 Autor

by **codewerkstatt @ mare-media.com**
