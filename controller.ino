// WEMOS D1 MINI - PIR -> Serial, Relay control via serial commands
#define RELAY_PIN D6     // transistor input that drives the relay coil
#define LED_PIN   D5     // optional status LED
#define PIR_PIN   D7     // PIR input

unsigned long lastPirTime = 0;
const unsigned long PIR_DEBOUNCE_MS = 1000; // debounce interval

String cmd = "";

void setup() {
  Serial.begin(9600);
  pinMode(RELAY_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  pinMode(PIR_PIN, INPUT);

  digitalWrite(RELAY_PIN, LOW); // relay off (locked) by default
  digitalWrite(LED_PIN, LOW);

  Serial.println("WEMOS_READY");
}

void loop() {
  // PIR -> notify PC
  int pir = digitalRead(PIR_PIN);
  if (pir == HIGH && (millis() - lastPirTime) > PIR_DEBOUNCE_MS) {
    lastPirTime = millis();
    Serial.println("MOTION");
    // quick blink for local feedback
    digitalWrite(LED_PIN, HIGH);
    delay(120);
    digitalWrite(LED_PIN, LOW);
  }

  // Serial commands handling
  if (Serial.available()) {
    cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.equalsIgnoreCase("open")) {
      digitalWrite(RELAY_PIN, HIGH); // energize relay -> power servo/open
      Serial.println("RELAY_OPEN");
    } else if (cmd.equalsIgnoreCase("close")) {
      digitalWrite(RELAY_PIN, LOW);  // de-energize relay -> lock/close
      Serial.println("RELAY_CLOSE");
    } else if (cmd.equalsIgnoreCase("ping")) {
      Serial.println("PONG");
    } else {
      Serial.print("UNKNOWN_CMD:");
      Serial.println(cmd);
    }
  }
}
