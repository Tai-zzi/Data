#include <Arduino.h>
#include <SPI.h>

// ADS1292R ID test with progress markers [1]..[6]
// Pins (first wiring): SCK 12, MOSI 11, MISO 13, CS 10, START 8, RESET 7
constexpr int PIN_SCK   = 12;
constexpr int PIN_MOSI  = 11;
constexpr int PIN_MISO  = 13;
constexpr int PIN_CS    = 10;
constexpr int PIN_START = 8;
constexpr int PIN_RESET = 7;   // PWDN/RESET pin of the AFE

constexpr uint8_t CMD_RESET  = 0x06;
constexpr uint8_t CMD_SDATAC = 0x11;
constexpr uint8_t CMD_RREG   = 0x20;
constexpr uint8_t REG_ID     = 0x00;

// slow start: 100 kHz, MSB first, SPI mode 1
SPISettings afeSettings(100000, MSBFIRST, SPI_MODE1);

unsigned long attempts = 0;
unsigned long matches = 0;

void sendCommand(uint8_t command) {
  SPI.beginTransaction(afeSettings);
  digitalWrite(PIN_CS, LOW);
  delayMicroseconds(10);
  SPI.transfer(command);
  delayMicroseconds(50);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  delayMicroseconds(50);
}

uint8_t readRegister(uint8_t address) {
  SPI.beginTransaction(afeSettings);
  digitalWrite(PIN_CS, LOW);
  delayMicroseconds(10);
  SPI.transfer(CMD_RREG | (address & 0x1F));   // RREG + register address
  delayMicroseconds(50);
  SPI.transfer(0x00);                          // number of registers - 1
  delayMicroseconds(50);
  uint8_t value = SPI.transfer(0x00);
  delayMicroseconds(50);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  delayMicroseconds(50);
  return value;
}

void setup() {
  Serial.begin(115200);

  // wait for the monitor (max 5 s so it never blocks forever)
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 5000) {
    delay(100);
  }
  delay(1000);

  Serial.println("\n[1] ID TEST START");
  Serial.flush();

  digitalWrite(PIN_CS, HIGH);
  pinMode(PIN_CS, OUTPUT);

  digitalWrite(PIN_START, LOW);
  pinMode(PIN_START, OUTPUT);

  digitalWrite(PIN_RESET, HIGH);
  pinMode(PIN_RESET, OUTPUT);

  Serial.println("[2] GPIO READY");
  Serial.flush();

  // CS is controlled manually, so pass -1 here (the library must not toggle it by itself)
  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, -1);

  Serial.println("[3] SPI BEGIN DONE");
  Serial.flush();
  delay(1000);

  digitalWrite(PIN_RESET, LOW);
  delay(10);
  digitalWrite(PIN_RESET, HIGH);
  delay(1000);

  Serial.println("[4] HARDWARE RESET DONE");
  Serial.flush();

  sendCommand(CMD_RESET);
  delay(100);

  Serial.println("[5] RESET COMMAND SENT");
  Serial.flush();

  sendCommand(CMD_SDATAC);
  delay(10);

  Serial.println("[6] START READING ID");
  Serial.flush();
}

void loop() {
  uint8_t id = readRegister(REG_ID);

  attempts++;
  bool recognized = (id == 0x53 || id == 0x73);
  if (recognized) {
    matches++;
  }

  Serial.print("#");
  Serial.print(attempts);
  Serial.print(" ID=0x");
  if (id < 0x10) {
    Serial.print("0");
  }
  Serial.print(id, HEX);

  if (id == 0x53) {
    Serial.print(" -> ADS1292 ID MATCH");
  } else if (id == 0x73) {
    Serial.print(" -> ADS1292R ID MATCH");
  } else if (id == 0x00 || id == 0xFF) {
    Serial.print(" -> NO VALID ID: check wiring/power/reset/clock");
  } else {
    Serial.print(" -> UNEXPECTED ID: verify chip and SPI");
  }

  Serial.print(" | Matches: ");
  Serial.print(matches);
  Serial.print("/");
  Serial.println(attempts);

  delay(1000);
}
