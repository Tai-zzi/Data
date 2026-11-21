#include <Arduino.h>
#include <SPI.h>
#include "esp_timer.h"
#include "protocol.h"

// CoGoalTis firmware v1 : ADS1292R (CJMCU-1292) -> 1 kSPS, 2 channels -> binary packets over USB serial.
// Matches the PC side:  python dashboard.py --source serial --port COMx --baud 921600
//
// FIRST RUN: keep TEST_SIGNAL = 1. The ADS generates its own 1 Hz test square wave, so NO electrodes are needed.
//            Then check it with:  python check_stream.py COMx 10
// ELECTRODES: set TEST_SIGNAL = 0 and re-upload. Use the USB isolator, and a battery laptop if possible.
//
// There is NO stimulation output in this firmware.
#define TEST_SIGNAL 1

// Pin wiring (the table wiring that worked in ads_b_test)
constexpr int PIN_SCK   = 4;
constexpr int PIN_MOSI  = 5;
constexpr int PIN_MISO  = 6;
constexpr int PIN_CS    = 7;
constexpr int PIN_DRDY  = 15;
constexpr int PIN_START = 16;
constexpr int PIN_PWDN  = 17;

constexpr uint32_t SERIAL_BAUD = 921600;     // must match SERIAL_BAUD in config.py

// ADS1292R commands / registers
constexpr uint8_t CMD_RESET = 0x06, CMD_SDATAC = 0x11, CMD_RDATAC = 0x10, CMD_RREG = 0x20, CMD_WREG = 0x40;
constexpr uint8_t REG_ID = 0x00, REG_CONFIG1 = 0x01, REG_CONFIG2 = 0x02, REG_CH1SET = 0x04, REG_CH2SET = 0x05;

constexpr uint8_t CONFIG1_1KSPS = 0x03;      // continuous mode, 1000 SPS
#if TEST_SIGNAL
constexpr uint8_t CONFIG2_VAL = 0xA3;        // reference buffer on + internal test signal on (1 Hz square)
constexpr uint8_t CHSET_VAL   = 0x05;        // gain 6 + MUX = test signal
#else
constexpr uint8_t CONFIG2_VAL = 0xA0;        // reference buffer on
constexpr uint8_t CHSET_VAL   = 0x00;        // gain 6 + normal electrode input
#endif

SPISettings adsSpi(1000000, MSBFIRST, SPI_MODE1);

volatile uint32_t drdyCount = 0;
void IRAM_ATTR onDrdy() { drdyCount++; }

uint32_t handled = 0, lostSamples = 0, seqNo = 0;
uint16_t frameCount = 0;
uint64_t packetStartUs = 0;
int32_t frames[PKT_FRAMES * PKT_CH];
uint8_t packetBuf[PKT_MAX_BYTES];

void adsCommand(uint8_t cmd) {
  SPI.beginTransaction(adsSpi);
  digitalWrite(PIN_CS, LOW);  delayMicroseconds(10);
  SPI.transfer(cmd);          delayMicroseconds(50);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  delayMicroseconds(50);
}

uint8_t adsRead(uint8_t reg) {
  SPI.beginTransaction(adsSpi);
  digitalWrite(PIN_CS, LOW);  delayMicroseconds(10);
  SPI.transfer(CMD_RREG | reg);   delayMicroseconds(50);
  SPI.transfer(0x00);             delayMicroseconds(50);
  uint8_t v = SPI.transfer(0x00); delayMicroseconds(50);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  delayMicroseconds(50);
  return v;
}

void adsWrite(uint8_t reg, uint8_t val) {
  SPI.beginTransaction(adsSpi);
  digitalWrite(PIN_CS, LOW);  delayMicroseconds(10);
  SPI.transfer(CMD_WREG | reg);   delayMicroseconds(50);
  SPI.transfer(0x00);             delayMicroseconds(50);
  SPI.transfer(val);              delayMicroseconds(50);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  delayMicroseconds(100);
}

bool writeVerify(uint8_t reg, uint8_t val, const char* name) {
  adsWrite(reg, val);
  uint8_t back = adsRead(reg);
  Serial.printf("  %s: wrote 0x%02X read 0x%02X %s\n", name, val, back, back == val ? "OK" : "MISMATCH");
  return back == val;
}

bool initAds() {
  digitalWrite(PIN_START, LOW);
  digitalWrite(PIN_PWDN, HIGH);
  delay(500);
  digitalWrite(PIN_PWDN, LOW);  delay(10);       // hardware reset
  digitalWrite(PIN_PWDN, HIGH); delay(500);
  adsCommand(CMD_RESET);  delay(100);
  adsCommand(CMD_SDATAC); delay(10);              // registers can only be written outside RDATAC

  uint8_t id = adsRead(REG_ID);
  Serial.printf("ADS ID = 0x%02X %s\n", id, (id == 0x73 || id == 0x53) ? "(OK)" : "(NOT FOUND)");
  if (id != 0x73 && id != 0x53) return false;

  bool ok = true;
  ok &= writeVerify(REG_CONFIG1, CONFIG1_1KSPS, "CONFIG1");
  ok &= writeVerify(REG_CONFIG2, CONFIG2_VAL, "CONFIG2");
  delay(300);                                     // internal reference settling
  ok &= writeVerify(REG_CH1SET, CHSET_VAL, "CH1SET");
  ok &= writeVerify(REG_CH2SET, CHSET_VAL, "CH2SET");
  return ok;
}

void setup() {
  Serial.setTxBufferSize(4096);                   // lets Serial.write return at once (no sample loss)
  Serial.begin(SERIAL_BAUD);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { delay(10); }
  delay(500);
  Serial.println("\nCOGOALTIS-FW v1 boot");
  Serial.println(TEST_SIGNAL ? "mode: TEST SIGNAL (no electrodes needed)" : "mode: ELECTRODES");

  digitalWrite(PIN_CS, HIGH);    pinMode(PIN_CS, OUTPUT);
  digitalWrite(PIN_START, LOW);  pinMode(PIN_START, OUTPUT);
  digitalWrite(PIN_PWDN, HIGH);  pinMode(PIN_PWDN, OUTPUT);
  pinMode(PIN_DRDY, INPUT_PULLUP);
  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, -1);     // CS is controlled by hand

  while (!initAds()) {
    Serial.println("ADS init failed, retrying in 2 s (check wiring / solder)");
    delay(2000);
  }

  attachInterrupt(digitalPinToInterrupt(PIN_DRDY), onDrdy, FALLING);
  adsCommand(CMD_RDATAC);                         // continuous data mode
  delay(10);
  digitalWrite(PIN_START, HIGH);                  // start conversions
  Serial.println("STREAMING binary packets from now on (do not use Serial Monitor)");
  Serial.flush();
  handled = drdyCount;
}

void loop() {
  uint32_t seen = drdyCount;
  if (seen == handled) return;
  if (seen - handled > 1) lostSamples += seen - handled - 1;
  handled = seen;

  uint8_t tx[9] = {0}, rx[9];
  SPI.beginTransaction(adsSpi);
  digitalWrite(PIN_CS, LOW);
  SPI.transferBytes(tx, rx, 9);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();

  if (frameCount == 0) packetStartUs = (uint64_t)esp_timer_get_time();
  parseAdsFrame(rx, &frames[frameCount * PKT_CH], &frames[frameCount * PKT_CH + 1]);
  frameCount++;

  if (frameCount == PKT_FRAMES) {
    size_t n = buildPacket(packetBuf, seqNo++, packetStartUs, frames, frameCount);
    Serial.write(packetBuf, n);
    frameCount = 0;
  }
}
