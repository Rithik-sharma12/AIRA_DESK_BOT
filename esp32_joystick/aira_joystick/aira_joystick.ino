// AIRA joystick: ESP32 (WROOM) + KY-023 -> Wi-Fi -> AIRA's Raspberry Pi screen.
//
//   KY-023  ->  ESP32           VCC -> 3.3V   GND -> GND   VRX -> GPIO32   VRY -> GPIO33   SW -> GPIO27
//
// Moves become focus moves on the Pi (up/down: menu, right: into a screen's buttons), a short
// press selects, a long press goes Home. The ESP32 finds the Pi by itself: the Pi announces
// {"service":"aira-pi","port":8771} on UDP 8767 every 2 s, so no IP address is needed even when
// the hotspot hands out new ones. It then opens ws://<pi>:8771/ and sends the pairing token.
//
// Build: Arduino IDE 2 + "esp32 by Espressif" boards + library "WebSockets" by Markus Sattler.
// Copy secrets.h.example to secrets.h and fill it in (secrets.h is git-ignored).
// Stage 1 (joystick only, no Wi-Fi): set AIRA_SERIAL_ONLY to 1 in secrets.h, open Serial Monitor at 115200.

#include <WiFi.h>
#include <WiFiUdp.h>
#include <WebSocketsClient.h>
#include "secrets.h"

#ifndef AIRA_SERIAL_ONLY
#define AIRA_SERIAL_ONLY 0
#endif
#ifndef AIRA_PI_HOST          // optional fixed address, e.g. "10.140.240.10"; empty = find the Pi
#define AIRA_PI_HOST ""
#endif

// ---------------------------------------------------------------- pins and feel
const int PIN_X = 32, PIN_Y = 33, PIN_SW = 27;   // ADC1 pins: they keep working while Wi-Fi is on
const bool INVERT_X = false, INVERT_Y = false, SWAP_XY = false;   // flip if your module is mounted turned
const int DEAD_ZONE = 900;        // of 0..4095 around the centre: ignore small wobbles
const int RELEASE_ZONE = 600;     // must come back this close to centre to count as released (hysteresis)
const uint32_t REPEAT_FIRST_MS = 450, REPEAT_MS = 180;   // holding a direction repeats it
const uint32_t DEBOUNCE_MS = 30, LONG_PRESS_MS = 800;
const uint16_t PI_PORT_DEFAULT = 8771, BEACON_PORT = 8767;

WebSocketsClient ws;
WiFiUDP udp;
int centreX = 2048, centreY = 2048;
String piHost = AIRA_PI_HOST;
uint16_t piPort = PI_PORT_DEFAULT;
bool linked = false, wsStarted = false;
uint32_t lastWifiTry = 0, lastBeaconLog = 0;

int readAxis(int pin) {                              // average 8 readings: the ESP32 ADC is noisy
  long sum = 0;
  for (int i = 0; i < 8; i++) sum += analogRead(pin);
  return sum / 8;
}

void calibrate() {                                   // the stick rests at its centre during start-up
  long sx = 0, sy = 0;
  for (int i = 0; i < 32; i++) { sx += analogRead(PIN_X); sy += analogRead(PIN_Y); delay(5); }
  centreX = sx / 32;
  centreY = sy / 32;
  Serial.printf("[joy] centre x=%d y=%d\n", centreX, centreY);
}

void sendEvent(const char *event, const char *direction) {
  char msg[96];
  if (direction) snprintf(msg, sizeof msg, "{\"event\":\"%s\",\"direction\":\"%s\"}", event, direction);
  else snprintf(msg, sizeof msg, "{\"event\":\"%s\"}", event);
  Serial.printf("[joy] %s\n", msg);
  if (!AIRA_SERIAL_ONLY && linked) ws.sendTXT(msg);
}

// ---------------------------------------------------------------- joystick
const char *currentDirection() {
  int dx = readAxis(PIN_X) - centreX, dy = readAxis(PIN_Y) - centreY;
  if (SWAP_XY) { int t = dx; dx = dy; dy = t; }
  if (INVERT_X) dx = -dx;
  if (INVERT_Y) dy = -dy;
  static const char *held = nullptr;
  int ax = abs(dx), ay = abs(dy);
  if (held) {                                        // stay "held" until it is back near the centre
    if (ax < RELEASE_ZONE && ay < RELEASE_ZONE) held = nullptr;
    else if (ax > DEAD_ZONE || ay > DEAD_ZONE)
      held = (ax > ay) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up");
    return held;
  }
  if (ax < DEAD_ZONE && ay < DEAD_ZONE) return nullptr;
  held = (ax > ay) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up");
  return held;
}

void pollStick() {
  static const char *last = nullptr;
  static uint32_t since = 0, nextRepeat = 0;
  const char *dir = currentDirection();
  uint32_t now = millis();
  if (dir != last) {
    last = dir;
    if (dir) { sendEvent("navigate", dir); since = now; nextRepeat = now + REPEAT_FIRST_MS; }
  } else if (dir && now >= nextRepeat) {
    sendEvent("navigate", dir);
    nextRepeat = now + REPEAT_MS;
  }
  (void)since;
}

void pollButton() {
  static bool stable = HIGH, lastRaw = HIGH, longSent = false;
  static uint32_t changedAt = 0, downAt = 0;
  bool raw = digitalRead(PIN_SW);                    // KY-023 SW pulls to GND when pressed
  uint32_t now = millis();
  if (raw != lastRaw) { lastRaw = raw; changedAt = now; }
  if (now - changedAt >= DEBOUNCE_MS && raw != stable) {
    stable = raw;
    if (stable == LOW) { downAt = now; longSent = false; }
    else if (!longSent) sendEvent("select", nullptr);            // released before the long-press time
  }
  if (stable == LOW && !longSent && now - downAt >= LONG_PRESS_MS) {
    sendEvent("back", nullptr);                                  // held: Home, sent while still held
    longSent = true;
  }
}

// ---------------------------------------------------------------- network
void onWs(WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED: {
      char hello[128];
      snprintf(hello, sizeof hello, "{\"event\":\"hello\",\"device\":\"aira-joystick\",\"token\":\"%s\"}", AIRA_TOKEN);
      ws.sendTXT(hello);
      Serial.printf("[ws] connected to %s:%u, sent hello\n", piHost.c_str(), piPort);
      break;
    }
    case WStype_TEXT:
      if (length && strstr((const char *)payload, "\"ok\":true")) { linked = true; Serial.println("[ws] paired with AIRA"); }
      else if (strstr((const char *)payload, "\"ok\":false")) {
        Serial.printf("[ws] refused: %.*s  (check AIRA_TOKEN: on the Pi run  python3 -m aira_pi controller)\n",
                      (int)length, (const char *)payload);
      }
      break;
    case WStype_DISCONNECTED:
      if (linked) Serial.println("[ws] disconnected; retrying");
      linked = false;
      break;
    default:
      break;
  }
}

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED || millis() - lastWifiTry < 10000) return;
  lastWifiTry = millis();
  Serial.printf("[wifi] connecting to %s ...\n", AIRA_WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                              // lower latency for joystick events
  WiFi.begin(AIRA_WIFI_SSID, AIRA_WIFI_PASSWORD);
}

void listenForPi() {                                 // the Pi's UDP announcement tells us where it is
  int size = udp.parsePacket();
  if (size <= 0) return;
  char buf[160];
  int n = udp.read(buf, sizeof buf - 1);
  buf[n > 0 ? n : 0] = 0;
  if (!strstr(buf, "\"aira-pi\"")) return;
  const char *p = strstr(buf, "\"port\":");
  uint16_t port = p ? (uint16_t)atoi(p + 7) : PI_PORT_DEFAULT;
  String host = udp.remoteIP().toString();
  if (host != piHost || port != piPort || !wsStarted) {
    Serial.printf("[net] found AIRA at %s:%u\n", host.c_str(), port);
    piHost = host;
    piPort = port;
    if (wsStarted) ws.disconnect();
    ws.begin(piHost.c_str(), piPort, "/");
    ws.onEvent(onWs);
    ws.setReconnectInterval(3000);
    ws.enableHeartbeat(15000, 3000, 2);              // notice a vanished Pi within ~20 s
    wsStarted = true;
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  pinMode(PIN_SW, INPUT_PULLUP);
  analogReadResolution(12);
  analogSetPinAttenuation(PIN_X, ADC_11db);          // full 0..3.3 V range
  analogSetPinAttenuation(PIN_Y, ADC_11db);
  Serial.println("\nAIRA joystick" + String(AIRA_SERIAL_ONLY ? " (serial test mode)" : ""));
  calibrate();
  if (AIRA_SERIAL_ONLY) return;
  connectWifi();
  udp.begin(BEACON_PORT);
  if (piHost.length()) {                             // fixed address given: no need to wait for the beacon
    ws.begin(piHost.c_str(), piPort, "/");
    ws.onEvent(onWs);
    ws.setReconnectInterval(3000);
    ws.enableHeartbeat(15000, 3000, 2);
    wsStarted = true;
  }
}

void loop() {
  pollStick();
  pollButton();
  if (!AIRA_SERIAL_ONLY) {
    connectWifi();
    if (WiFi.status() == WL_CONNECTED) {
      if (!String(AIRA_PI_HOST).length()) listenForPi();
      if (wsStarted) ws.loop();
      if (!wsStarted && millis() - lastBeaconLog > 5000) {
        lastBeaconLog = millis();
        Serial.printf("[net] on Wi-Fi as %s, waiting for AIRA's announcement (UDP %u)...\n",
                      WiFi.localIP().toString().c_str(), BEACON_PORT);
      }
    }
  }
  delay(10);
}
