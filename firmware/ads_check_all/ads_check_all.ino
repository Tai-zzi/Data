#include <Arduino.h>
#include <SPI.h>

// ads_check_all : one sketch that checks the CJMCU-1292 (ADS1292R) after re-soldering.
// It tries 3 candidate pin sets (A, B, C). For each set:
//   step 1: DRDY test  (reset chip, START on, count DRDY falling edges for 1 s; about 500 = chip alive)
//   step 2: ID test    (SPI 100 kHz, RESET command, SDATAC, read ID; 0x73 = ADS1292R, 0x53 = ADS1292)
// It stops at the first set where the ID matches. Press RST to run it again.
// Output is English only (no encoding problems).

struct PinSet { const char* name; int sck, mosi, miso, cs, drdy, start, pwdn; };

const PinSet SETS[] = {
  {"A: first wiring (12,11,13,10 / DRDY 9, START 8, PWDN 7)",       12, 11, 13, 10,  9,  8,  7},
  {"B: table wiring (4,5,6,7 / DRDY 15, START 16, PWDN 17)",         4,  5,  6,  7, 15, 16, 17},
  {"C: reverse of B (17,16,15,7 / DRDY 6, START 5, PWDN 4)",        17, 16, 15,  7,  6,  5,  4},
};
const int N_SETS = sizeof(SETS) / sizeof(SETS[0]);

constexpr uint8_t CMD_RESET  = 0x06;
constexpr uint8_t CMD_SDATAC = 0x11;
constexpr uint8_t CMD_RREG   = 0x20;

SPISettings afeSettings(100000, MSBFIRST, SPI_MODE1);

volatile uint32_t drdyCount = 0;
void IRAM_ATTR onDrdy() { drdyCount++; }

void releasePins(const PinSet& s) {
  int p[] = {s.sck, s.mosi, s.miso, s.cs, s.drdy, s.start, s.pwdn};
  for (int i = 0; i < 7; i++) pinMode(p[i], INPUT);
}

void wakeChip(const PinSet& s) {
  digitalWrite(s.cs, HIGH);    pinMode(s.cs, OUTPUT);
  digitalWrite(s.start, LOW);  pinMode(s.start, OUTPUT);
  digitalWrite(s.pwdn, HIGH);  pinMode(s.pwdn, OUTPUT);
  pinMode(s.drdy, INPUT_PULLUP);
  delay(1000);                         // power settle
  digitalWrite(s.pwdn, LOW);  delay(10);   // hardware reset pulse
  digitalWrite(s.pwdn, HIGH); delay(1000);
}

uint32_t countDrdy(const PinSet& s) {
  wakeChip(s);
  drdyCount = 0;
  attachInterrupt(digitalPinToInterrupt(s.drdy), onDrdy, FALLING);
  digitalWrite(s.start, HIGH);
  delay(1000);
  detachInterrupt(digitalPinToInterrupt(s.drdy));
  digitalWrite(s.start, LOW);
  return drdyCount;
}

void spiCommand(const PinSet& s, uint8_t cmd) {
  SPI.beginTransaction(afeSettings);
  digitalWrite(s.cs, LOW);  delayMicroseconds(10);
  SPI.transfer(cmd);        delayMicroseconds(50);
  digitalWrite(s.cs, HIGH);
  SPI.endTransaction();
  delayMicroseconds(50);
}

uint8_t readIdRegister(const PinSet& s) {
  SPI.beginTransaction(afeSettings);
  digitalWrite(s.cs, LOW);  delayMicroseconds(10);
  SPI.transfer(CMD_RREG | 0x00);   delayMicroseconds(50);
  SPI.transfer(0x00);              delayMicroseconds(50);
  uint8_t v = SPI.transfer(0x00);  delayMicroseconds(50);
  digitalWrite(s.cs, HIGH);
  SPI.endTransaction();
  delayMicroseconds(50);
  return v;
}

uint8_t readId(const PinSet& s, bool swapMosiMiso) {
  int mosi = swapMosiMiso ? s.miso : s.mosi;
  int miso = swapMosiMiso ? s.mosi : s.miso;
  wakeChip(s);
  SPI.begin(s.sck, miso, mosi, -1);    // CS is controlled by hand
  spiCommand(s, CMD_RESET);  delay(100);
  spiCommand(s, CMD_SDATAC); delay(10);
  uint8_t id = readIdRegister(s);
  SPI.end();
  return id;
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 5000) { delay(100); }
  delay(1000);
  Serial.println("\n=== ads_check_all start (takes about 30 s) ===");

  bool anyAlive = false, matched = false;
  for (int i = 0; i < N_SETS && !matched; i++) {
    Serial.printf("[%s]\n", SETS[i].name);
    uint32_t n = countDrdy(SETS[i]);
    bool alive = (n >= 400 && n <= 600);
    if (alive) anyAlive = true;
    Serial.printf("  step 1 DRDY: %lu per sec -> %s\n", (unsigned long)n,
                  n == 0 ? "NO SIGNAL" : (alive ? "ALIVE (about 500)" : "UNSTABLE (noise or bad contact)"));
    releasePins(SETS[i]);

    for (int sw = 0; sw < 2 && !matched; sw++) {
      uint8_t id = readId(SETS[i], sw == 1);
      bool ok = (id == 0x73 || id == 0x53);
      Serial.printf("  step 2 ID%s: 0x%02X -> %s\n", sw ? " (MOSI/MISO swapped)" : "", id,
                    ok ? "MATCH! chip answered" : "no valid answer");
      if (ok) {
        matched = true;
        Serial.printf("  >>> CORRECT WIRING: SCK=%d MOSI=%d MISO=%d CS=%d DRDY=%d START=%d PWDN=%d\n",
                      SETS[i].sck, sw ? SETS[i].miso : SETS[i].mosi, sw ? SETS[i].mosi : SETS[i].miso,
                      SETS[i].cs, SETS[i].drdy, SETS[i].start, SETS[i].pwdn);
      }
      releasePins(SETS[i]);
    }
  }

  Serial.println("--- summary ---");
  if (matched)       Serial.println("ID matched: the AFE works. Next step: real firmware.");
  else if (anyAlive) Serial.println("DRDY is alive but no ID: chip runs, check SCK/MOSI/MISO/CS wires and solder joints.");
  else               Serial.println("No DRDY and no ID: chip not running. Check LOGIC_SEL, CLKSEL, VDD, GND, PWDN/START wires.");
  Serial.println("=== done (press RST to run again) ===");
}

void loop() { }
