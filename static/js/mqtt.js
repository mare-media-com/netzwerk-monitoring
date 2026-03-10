// mqtt.js
// Verbindung zum MQTT-Broker über WebSockets herstellen, Werte aktualisieren

const broker = MQTT_HOST;  // aus secrets.js
const brokerport = MQTT_PORT;
const clientId = "webclient_" + Math.floor(Math.random() * 1000);
const topicTemp = "esp32/bme280/temperature";
const topicHum = "esp32/bme280/humidity";

let client;
let lastTempUpdate = Date.now();
let lastHumUpdate = Date.now();

// MQTT-Verbindung aufbauen
function connectMQTT() {
    client = new Paho.Client(broker, brokerport, clientId);

    client.onConnectionLost = onConnectionLost;
    client.onMessageArrived = onMessageArrived;

    client.connect({
        onSuccess: onConnect,
        onFailure: function (err) {
            console.error("MQTT connection failed:", err);
            setTimeout(connectMQTT, 3000); // erneut verbinden bei Fehler
        },
        useSSL: false,
        reconnect: true
    });
}

function onConnect() {
    console.log("Connected to MQTT broker via WebSockets");
    client.subscribe(topicTemp);
    client.subscribe(topicHum);
}

// Bei Verbindungsverlust
function onConnectionLost(responseObject) {
    if (responseObject.errorCode !== 0) {
        console.warn("Connection lost:", responseObject.errorMessage);
        setTimeout(connectMQTT, 3000); // erneut verbinden
    }
}

function onMessageArrived(message) {
    const topic = message.destinationName;
    const payload = parseFloat(message.payloadString);

    if (topic === topicTemp) {

        const tempDisplay = document.getElementById("temp-display");
        const tempLed = document.getElementById("temp-led");

        tempDisplay.textContent = `🌡️ ${payload} °C`;

        lastTempUpdate = Date.now();

        tempLed.classList.remove("led-green", "led-red", "led-blue");

        if (payload < 20) {
            tempLed.classList.add("led-blue");
        } else if (payload <= 24) {
            tempLed.classList.add("led-green");
        } else {
            tempLed.classList.add("led-red");
        }

    } else if (topic === topicHum) {

        const humDisplay = document.getElementById("hum-display");
        const humLed = document.getElementById("hum-led");

        humDisplay.textContent = `💧 ${payload} %`;

        lastHumUpdate = Date.now();

        humLed.classList.remove("led-green", "led-red", "led-blue");

        if (payload < 40) {
            humLed.classList.add("led-red");
        } else if (payload <= 60) {
            humLed.classList.add("led-green");
        } else {
            humLed.classList.add("led-blue");
        }
    }
}

// Initialisierung nach Laden der Seite
window.addEventListener("load", () => {
    connectMQTT();
});

// ===============================
// Überwachung der letzten MQTT-Daten
// ===============================

setInterval(() => {

    const now = Date.now();

    const tempLed = document.getElementById("temp-led");
    const humLed = document.getElementById("hum-led");

    if (now - lastTempUpdate > 60000) {
        tempLed.classList.remove("led-green", "led-red", "led-blue");
    }

    if (now - lastHumUpdate > 60000) {
        humLed.classList.remove("led-green", "led-red", "led-blue");
    }

}, 5000);