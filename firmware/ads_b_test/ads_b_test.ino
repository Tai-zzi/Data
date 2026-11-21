#include <Arduino.h>
#include <SPI.h>

// ads_b_test : check for the TABLE wiring only (no other pin sets are touched)
//   CJMCU SCK -> ESP32 4      CJMCU MOSI -> ESP32 5     CJMCU MISO -> ESP32 6
//   CJMCU CS  -> ESP32 7      CJMCU DRDY -> ESP32 15
//   CJMCU START -> ESP32 16   CJMCU PWDN/RESET -> ESP32 17
//   CJMCU VDD -> 3V3          CJMCU GND -> GND
// Each cycle (about 5 s) prints one line:
//   DRDY per second (about 500 = chip alive) | ID read normally | ID read with MOSI/MISO swapped
//   ID 0x73 = ADS1292R, 0x53 = ADS1292
constexpr int PIN_SCK   = 4;
constexpr int PIN_MOSI  = 5;
constexpr int PIN_MISO  = 6;
constexpr int PIN_CS    = 7;
constexpr int PIN_DRDY  = 15;
constexpr int PIN_START = 16;
constexpr int PIN_PWDN  = 17;

constexpr uint8_t CMD_RESET  = 0x06;
constexpr uint8_t CMD_SDATAC = 0x11;
constexpr uint8_t CMD_RREG   = 0x20;

SPISettings afeSettings(100000, MSBFIRST, SPI_MODE1);

volatile uint32_t drdyCount = 0;
void IRAM_ATTR onDrdy() { drdyCount++; }

unsigned long cycles = 0;

void hardwareReset(int settleMs) {
  digitalWrite(PIN_START, LOW);
  digitalWrite(PIN_PWDN, HIGH);
  delay(settleMs);                       // let the supply settle
  digitalWrite(PIN_PWDN, LOW);  delay(10);   // reset pulse
  digitalWrite(PIN_PWDN, HIGH); delay(300);
}

uint32_t countDrdy() {
  hardwareReset(300);
  drdyCount = 0;
  attachInterrupt(digitalPinToInterrupt(PIN_DRDY), onDrdy, FALLING);
  digitalWrite(PIN_START, HIGH);
  delay(1000);
  detachInterrupt(digitalPinToInterrupt(PIN_DRDY));
  digitalWrite(PIN_START, LOW);
  return drdyCount;
}

void spiCommand(uint8_t cmd) {
  SPI.beginTransaction(afeSettings);
  digitalWrite(PIN_CS, LOW);  delayMicroseconds(10);
  SPI.transfer(cmd);          delayMicroseconds(50);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  delayMicroseconds(50);
}

uint8_t readIdRegister() {
  SPI.beginTransaction(afeSettings);
  digitalWrite(PIN_CS, LOW);  delayMicroseconds(10);
  SPI.transfer(CMD_RREG | 0x00);   delayMicroseconds(50);
  SPI.transfer(0x00);              delayMicroseconds(50);
  uint8_t v = SPI.transfer(0x00);  delayMicroseconds(50);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  delayMicroseconds(50);
  return v;
}

uint8_t readId(bool swapMosiMiso) {
  int mosi = swapMosiMiso ? PIN_MISO : PIN_MOSI;
  int miso = swapMosiMiso ? PIN_MOSI : PIN_MISO;
  hardwareReset(300);
  SPI.begin(PIN_SCK, miso, mosi, -1);    // CS is controlled by hand
  spiCommand(CMD_RESET);  delay(100);
  spiCommand(CMD_SDATAC); delay(10);
  uint8_t id = readIdRegister();
  SPI.end();
  pinMode(PIN_SCK, INPUT); pinMode(PIN_MOSI, INPUT); pinMode(PIN_MISO, INPUT);
  return id;
}

const char* idText(uint8_t id) {
  if (id == 0x73) return "MATCH (ADS1292R)";
  if (id == 0x53) return "MATCH (ADS1292)";
  if (id == 0x00 || id == 0xFF) return "no answer";
  return "unexpected";
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 5000) { delay(100); }
  delay(1000);

  digitalWrite(PIN_CS, HIGH);    pinMode(PIN_CS, OUTPUT);
  digitalWrite(PIN_START, LOW);  pinMode(PIN_START, OUTPUT);
  digitalWrite(PIN_PWDN, HIGH);  pinMode(PIN_PWDN, OUTPUT);
  pinMode(PIN_DRDY, INPUT_PULLUP);
  pinMode(PIN_SCK, INPUT); pinMode(PIN_MOSI, INPUT); pinMode(PIN_MISO, INPUT);

  Serial.println("\n=== ads_b_test start (table wiring: SCK4 MOSI5 MISO6 CS7 DRDY15 START16 PWDN17) ===");
  delay(1000);                           // first power-up settle
}

void loop() {
  uint32_t n = countDrdy();
  uint8_t idA = readId(false);
  uint8_t idB = readId(true);
  cycles++;

  const char* d = (n == 0) ? "NO SIGNAL" : ((n >= 400 && n <= 600) ? "ALIVE" : "UNSTABLE");
  Serial.printf("#%lu  DRDY %lu/s (%s) | ID 0x%02X %s | swapped 0x%02X %s\n",
                cycles, (unsigned long)n, d, idA, idText(idA), idB, idText(idB));
  delay(500);
}
