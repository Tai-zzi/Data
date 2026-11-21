// ads_live2 : prints DRDY count every second (ADS1292R alive check)
// pins: SCK=4 MOSI=5 MISO=6 CS=7 DRDY=15 START=16 PWDN/RESET=17
// about 500 per second = chip alive, 0 = no signal, other = unstable/noise
const int PIN_SCK = 4, PIN_MOSI = 5, PIN_MISO = 6, PIN_CS = 7;
const int PIN_DRDY = 15, PIN_START = 16, PIN_PWDN = 17;

volatile uint32_t drdyCount = 0;
void IRAM_ATTR onDrdy() { drdyCount++; }

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { }
  pinMode(PIN_SCK, INPUT); pinMode(PIN_MOSI, INPUT); pinMode(PIN_MISO, INPUT);
  pinMode(PIN_CS, OUTPUT);    digitalWrite(PIN_CS, HIGH);
  pinMode(PIN_START, OUTPUT); digitalWrite(PIN_START, LOW);
  pinMode(PIN_PWDN, OUTPUT);  digitalWrite(PIN_PWDN, HIGH);
  pinMode(PIN_DRDY, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_DRDY), onDrdy, FALLING);
  Serial.println("=== DRDY monitor start (one line per second) ===");
}

void loop() {
  digitalWrite(PIN_START, LOW);
  digitalWrite(PIN_PWDN, LOW);  delay(5);
  digitalWrite(PIN_PWDN, HIGH); delay(200);
  noInterrupts(); drdyCount = 0; interrupts();
  digitalWrite(PIN_START, HIGH);
  delay(1000);
  noInterrupts(); uint32_t n = drdyCount; interrupts();
  const char* verdict = (n == 0) ? "NO SIGNAL"
                      : (n >= 400 && n <= 600) ? "OK (about 500)"
                      : "UNSTABLE (bad contact or noise, not a real signal)";
  Serial.printf("DRDY %lu per sec  ->  %s\n", (unsigned long)n, verdict);
}
