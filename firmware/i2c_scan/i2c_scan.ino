// i2c_scan : finds I2C devices (MCP4725 check).  READ ONLY - it never writes a DAC value.
// Wiring: SDA -> GPIO 2, SCL -> GPIO 1.   Output: 115200 baud, repeats every 3 s.
// For an MCP4725 it also READS (does not change) the current output value and the
// value stored in its EEPROM, which is what it outputs right after power-up.
#include <Wire.h>

constexpr int PIN_SDA = 2;
constexpr int PIN_SCL = 1;

void readMcp4725(uint8_t addr) {
  // MCP4725 read: 5 bytes = status, DAC hi, DAC lo, EEPROM hi, EEPROM lo
  if (Wire.requestFrom(addr, (uint8_t)5) != 5) {
    Serial.println("    (could not read 5 bytes)");
    return;
  }
  uint8_t b[5];
  for (int i = 0; i < 5; i++) b[i] = Wire.read();
  uint16_t dac = ((uint16_t)b[1] << 4) | (b[2] >> 4);
  uint16_t eep = ((uint16_t)(b[3] & 0x0F) << 8) | b[4];
  uint8_t pdDac = (b[0] >> 1) & 0x03;
  uint8_t pdEep = (b[3] >> 5) & 0x03;
  Serial.printf("    MCP4725 now   : code %4u / 4095 (%.0f%% of VDD), power-down bits %u\n", dac, dac * 100.0 / 4095, pdDac);
  Serial.printf("    MCP4725 EEPROM: code %4u / 4095 (%.0f%% of VDD), power-down bits %u  <- value at power-up\n", eep, eep * 100.0 / 4095, pdEep);
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Wire.begin(PIN_SDA, PIN_SCL, 100000);
}

void loop() {
  Serial.printf("\nI2C scan (SDA=%d, SCL=%d)\n", PIN_SDA, PIN_SCL);
  int found = 0;
  for (uint8_t a = 0x08; a < 0x78; a++) {
    Wire.beginTransmission(a);              // address only, no data bytes
    if (Wire.endTransmission() == 0) {
      found++;
      Serial.printf("  found 0x%02X%s\n", a, (a >= 0x60 && a <= 0x67) ? "  <- looks like MCP4725" : "");
      if (a >= 0x60 && a <= 0x67) readMcp4725(a);
    }
  }
  if (!found) Serial.println("  nothing found (check SDA/SCL/VCC/GND, pull-ups)");
  delay(3000);
}
