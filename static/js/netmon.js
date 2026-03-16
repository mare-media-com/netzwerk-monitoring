document.addEventListener("DOMContentLoaded", function () {

    let hosts = [];

    const renderedEvents = {};
    const cpuGauges = {};

    /* ===================== */
    /* Host-Initialisierung  */
    /* ===================== */
    function initHosts(hostList) {

        hosts = hostList;

        hostList.forEach(host => {
            renderedEvents[host] = new Set();
        });

        initGauges();
    }

    function initGauges() {
        hosts.forEach(host => {
            const el = document.getElementById("cpuGauge" + capitalize(host));
            if (el) {
                cpuGauges[host] = new JustGage({
                    id: "cpuGauge" + capitalize(host),
                    value: 0,
                    min: 30,
                    max: 90,
                    title: "CPU Temperature",
                    label: "°C",
                    decimals: 2,
                    valueFontColor: "#ffffff",
                    titleFontColor: "#ffffff",
                    labelFontColor: "#ffffff",
                    gaugeColor: "#808080",
                    levelColors: ["#33ff57", "#ffc107", "#ff5733"]
                });
            }
        });
    }

    function capitalize(str) {
        return str.charAt(0).toUpperCase() + str.slice(1);
    }

    /* ===================== */
    /* UI-Updates            */
    /* ===================== */
    function updateCardState(cardId, isOk) {

        const card = document.getElementById(cardId);
        if (!card) return;

        card.classList.remove("ok", "fail");
        card.classList.add(isOk ? "ok" : "fail");
    }

    function setLatency(id, value) {

        const el = document.getElementById(id);
        if (!el) return;

        if (value === null || value === undefined) {
            el.innerHTML = "–";
            return;
        }

        let color = value < 50 ? "green" :
                    value <= 100 ? "yellow" :
                                "red";

        el.className = `latency ${color}`;
        el.innerHTML = `⏱ ${value} ms`;
    }

    function updateRam(host, percent, usedMB, totalMB, warn, crit) {
        const freeMB = totalMB - usedMB;

        const dotColor = percent >= crit ? "#ff5733" : percent >= warn ? "#ffc107" : "#33ff57";

        const textEl = document.getElementById("ramText" + capitalize(host));
        const barEl = document.getElementById("ramBar" + capitalize(host));
        if (textEl && barEl) {
            textEl.innerHTML = `
                <span class="status-dot" style="background:${dotColor}"></span>
                Used: ${usedMB.toFixed(2)}MB (${percent.toFixed(2)}%)<br>
                Free: ${freeMB.toFixed(2)}MB | Total: ${totalMB.toFixed(2)}MB
            `;
            barEl.style.width = percent + "%";
        }
    }

    function updateSd(host, percent, usedGB, totalGB, warn, crit) {
        const freeGB = totalGB - usedGB;
        const dotColor = percent >= crit ? "#ff5733" : percent >= warn ? "#ffc107" : "#33ff57";

        const textEl = document.getElementById("sdText" + capitalize(host));
        const barEl = document.getElementById("sdBar" + capitalize(host));
        if (textEl && barEl) {
            textEl.innerHTML = `
                <span class="status-dot" style="background:${dotColor}"></span>
                Used: ${usedGB.toFixed(2)}GB (${percent.toFixed(2)}%)<br>
                Free: ${freeGB.toFixed(2)}GB | Total: ${totalGB.toFixed(2)}GB
            `;
            barEl.style.width = percent + "%";
        }
    }

    function renderFailure(blockId, stats) {

        const block = document.getElementById(blockId);
        if (!block) return;

        if (!stats || !stats.fail_start) {
            block.innerHTML = `<small>✅ Noch kein Ausfall</small>`;
            return;
        }

        block.innerHTML = `
            <small>
                ❌ Zuletzt ausgefallen: ${stats.fail_start}<br>
                🕒 Dauer: ${stats.fail_duration}
            </small>
        `;
    }

    function renderStats(blockId, stats) {

        const block = document.getElementById(blockId);
        if (!block) return;

        if (!stats) {
        block.innerHTML = `<small>Keine Daten</small>`;
        return;
    }

        const availability = stats.availability_7d;

        let dotColor = "#33ff57";

        if (availability !== null && availability !== undefined) {
            if (availability < 99) dotColor = "#ff5733";
            else if (availability < 99.9) dotColor = "#ffc107";
        }

        block.innerHTML = `
            <small>
                ⚡ Ausfälle 24h: ${stats.outages_24h ?? "-"}<br>
                📅 Ausfälle 7 Tage: ${stats.outages_7d ?? "-"}<br>
                ❌ Downtime 7 Tage: ${stats.downtime_7d ?? "-"}<br>
                <span class="status-dot" style="background:${dotColor}"></span>
                Verfügbarkeit 7 Tage: ${stats.availability_7d ?? "-"} %
            </small>
        `;
    }

    function renderTimeline(containerId, events, key) {

        const el = document.getElementById(containerId);
        if (!el || !events) return;

        events.forEach(event => {

            const eventId = `${event.time}-${event.state}`;

            // Bereits vorhanden? → überspringen
            if (renderedEvents[key].has(eventId)) return;

            renderedEvents[key].add(eventId);

            const div = document.createElement("div");
            div.className = `event ${event.state.toLowerCase()} new-event`;

            div.innerHTML = `${event.time} | ${event.state}`;

            el.prepend(div); // NEUE EVENTS OBEN ⭐
        });
    }

    /* ===================== */
    /* Main Update */
    /* ===================== */

    function updateServerStatus(data) {

        if (!data.servers) return;

        hosts.forEach(host => {

            // Status OK / FAIL
            const server = data.servers?.[host];
            const ok = server?.online;

            const statusEl = document.getElementById(host + "Status");
            if (statusEl)
                statusEl.innerHTML = ok ? "🟢 OK" : "🔴 FAIL";

            // Card-Farbe
            updateCardState(host + "Card", ok);

            // Latenz
            setLatency(host + "Latency", server?.latency);

            // Failure + Stats
            const stats = data.server_stats?.[host];

            renderFailure(host + "FailureBlock", stats);
            renderStats(host + "StatsBlock", stats);

            // Timeline
            renderTimeline(host + "Timeline", data.timelines?.[host], host);
        });

    }

    /* ===================== */
    /* Fetch-Funktion        */
    /* ===================== */

    function fetchStats() {
        fetch("/debug")
            .then(res => res.json())
            .then(data => {

                const thresholds = data.thresholds;

                hosts.forEach(host => {

                    const config = hostConfig[host];
                    const rpi = data.raspberries?.[host];

                    if (!rpi) return;

                    // CPU
                    if (rpi.cpu != null) {
                        cpuGauges[host]?.refresh(parseFloat(rpi.cpu));
                    }

                    // RAM
                    if (rpi.ram_percent != null && rpi.ram_used != null) {
                        updateRam(
                            host,
                            rpi.ram_percent,
                            rpi.ram_used,
                            rpi.ram_total,
                            thresholds.ram.warn,
                            thresholds.ram.crit
                        );
                    }

                    // SD
                    if (rpi.sd_percent != null && rpi.sd_used != null) {
                        updateSd(
                            host,
                            rpi.sd_percent,
                            rpi.sd_used,
                            rpi.sd_total,
                            thresholds.sd.warn,
                            thresholds.sd.crit
                        );
                    }
                });

                updateServerStatus(data);
            })
            .catch(err => console.error("Fetch /debug failed:", err));
    }

    /* ===================== */
    /* Start: Hosts & Fetch  */
    /* ===================== */
    initHosts(Object.keys(hostConfig)); // Init Hosts + Gauges
    fetchStats();                        // Erste Werte laden
    setInterval(fetchStats, 5000);       // alle 5 Sekunden aktualisieren

});