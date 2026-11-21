// stim_bench : 자극 회로(MCP4725 -> Howland 전류원) 출력 시험용 펌웨어.  ** 더미 저항 전용 **
//
// 사용 전 확인 (하나라도 아니면 업로드하지 말 것)
//   - 리드선 자리 J/44-J/48 에는 더미 저항(1k / 4.7k / 10k)만. 전극/사람 몸 연결 금지
//   - 첫 업로드 때 MCP4725는 전원 직후 약 50%(1.65V)를 낸다. 이 펌웨어가 EEPROM 전원투입값을 0으로 저장하면
//     다음 전원부터는 0V로 시작한다 (부팅 메시지에서 "EEPROM power-up code = 0" 확인)
//
// 배선: MCP4725 SDA/SCL 은 GPIO1/GPIO2 (어느 쪽이 SDA인지 자동으로 찾음), VOUT -> G/24 + GPIO14(모니터)
// 시리얼: 115200 baud. Serial Monitor 에서 직접 입력하거나 PC의 stim_bench.py 사용
//
// 명령 (줄 끝 Enter)
//   L<n>   n단계 전류를 HOLD_MS 동안 유지(직류) 후 자동 0  -> 멀티미터로 Rs(J/30-J/36) 양단 전압 측정용
//          ※ 직류 시험은 G/40-G/44 커패시터 자리를 잠깐 전선으로 바꾼 상태에서 (커패시터가 있으면 전류가 금방 0으로 줄어듦)
//   P<n>   n단계 펄스열 (PULSE_US 폭, PULSE_HZ, BURST_MS 동안) -> 오실로스코프로 Rs 양단 확인. 커패시터 있는 상태
//   M      모니터(GPIO14 = DAC 출력 전압) 읽기
//   S      즉시 0 (언제든)
//   ?      설정/단계표 출력
// 출력은 어떤 경우에도 HOLD_MS(최대 3초) 뒤 자동으로 0이 된다.
#include <Wire.h>

// ---- 회로 값 (빵판과 같아야 함) ----
constexpr float R1 = 10000.0f, R2 = 11000.0f, RS = 1000.0f;   // I = Vin * R2 / (R1 * RS)
constexpr float DAC_VDD = 3.3f;                                 // MCP4725 VDD (3V3)
constexpr int PIN_A = 1, PIN_B = 2;                             // MCP4725 I2C (SDA/SCL 자동 판별)
constexpr int PIN_MON = 14;                                     // VOUT 모니터 (ADC)

// ---- 단계표 (mA). 상한은 펌웨어에 고정. PC가 바꿀 수 없음 ----
constexpr int N_LEVEL = 5;
constexpr float LEVEL_MA[N_LEVEL + 1] = {0.0f, 0.25f, 0.5f, 0.75f, 1.0f, 1.5f};
constexpr float MAX_MA = 1.5f;

constexpr uint32_t HOLD_MS = 3000;     // 직류 유지 최대 시간
constexpr uint32_t PULSE_US = 300;     // 펄스 폭
constexpr uint32_t PULSE_HZ = 50;      // 펄스 반복
constexpr uint32_t BURST_MS = 1000;    // 펄스열 길이

uint8_t dacAddr = 0;
int pinSda = -1, pinScl = -1;
uint16_t curCode = 0;
uint32_t holdUntil = 0;
String line;

uint16_t codeFor(float mA) {
  if (mA > MAX_MA) mA = MAX_MA;
  if (mA < 0) mA = 0;
  float vin = mA / 1000.0f * R1 * RS / R2;          // 필요한 DAC 전압
  long c = lroundf(vin / DAC_VDD * 4095.0f);
  return (uint16_t)constrain(c, 0, 4095);
}

float mAFor(uint16_t code) { return code / 4095.0f * DAC_VDD * R2 / (R1 * RS) * 1000.0f; }

bool dacFast(uint16_t code) {                        // fast write, power-down 비트 00
  Wire.beginTransmission(dacAddr);
  Wire.write((uint8_t)((code >> 8) & 0x0F));
  Wire.write((uint8_t)(code & 0xFF));
  bool ok = Wire.endTransmission() == 0;
  if (ok) curCode = code;
  return ok;
}

bool dacRead(uint8_t b[5]) {
  if (Wire.requestFrom(dacAddr, (uint8_t)5) != 5) return false;
  for (int i = 0; i < 5; i++) b[i] = Wire.read();
  return true;
}

uint16_t eepromCode(const uint8_t b[5]) { return ((uint16_t)(b[3] & 0x0F) << 8) | b[4]; }

bool writeEepromZero() {                             // DAC + EEPROM 에 0 저장 (전원투입값 0V)
  Wire.beginTransmission(dacAddr);
  Wire.write(0x60);
  Wire.write(0x00);
  Wire.write(0x00);
  if (Wire.endTransmission() != 0) return false;
  for (int i = 0; i < 20; i++) {                     // EEPROM 쓰기 완료(RDY=1) 대기, 최대 약 100 ms
    delay(5);
    uint8_t b[5];
    if (dacRead(b) && (b[0] & 0x80)) return eepromCode(b) == 0 && ((b[3] >> 5) & 0x03) == 0;
  }
  return false;
}

bool findDac() {
  const int pairs[2][2] = {{PIN_A, PIN_B}, {PIN_B, PIN_A}};
  for (auto& p : pairs) {
    Wire.begin(p[0], p[1], 400000);
    for (uint8_t a = 0x60; a <= 0x67; a++) {
      Wire.beginTransmission(a);
      if (Wire.endTransmission() == 0) {
        dacAddr = a; pinSda = p[0]; pinScl = p[1];
        return true;
      }
    }
    Wire.end();
  }
  return false;
}

void outZero() { dacFast(0); holdUntil = 0; }

int monitorMv() {
  long s = 0;
  for (int i = 0; i < 16; i++) s += analogReadMilliVolts(PIN_MON);
  return (int)(s / 16);
}

void printTable() {
  Serial.printf("CFG R1=%.0f R2=%.0f RS=%.0f DAC_VDD=%.2f gain=%.3f mA/V max=%.2f mA hold=%lu ms pulse=%lu us %lu Hz %lu ms\n",
                R1, R2, RS, DAC_VDD, R2 / (R1 * RS) * 1000.0f, MAX_MA, (unsigned long)HOLD_MS,
                (unsigned long)PULSE_US, (unsigned long)PULSE_HZ, (unsigned long)BURST_MS);
  for (int n = 1; n <= N_LEVEL; n++) {
    uint16_t c = codeFor(LEVEL_MA[n]);
    Serial.printf("LEVEL %d target=%.2f mA code=%u vin=%.3f V\n", n, LEVEL_MA[n], c, c / 4095.0f * DAC_VDD);
  }
}

bool stopRequested() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == 'S' || ch == 's') return true;
  }
  return false;
}

void doHold(int n) {
  uint16_t c = codeFor(LEVEL_MA[n]);
  if (!dacFast(c)) { Serial.println("ERR i2c"); outZero(); return; }
  holdUntil = millis() + HOLD_MS;
  delay(300);                                        // 모니터 안정화
  Serial.printf("OK L%d target=%.2f mA code=%u mon=%d mV expect_mon=%.0f mV (auto 0 in %lu ms)\n",
                n, LEVEL_MA[n], c, monitorMv(), c / 4095.0f * DAC_VDD * 1000.0f, (unsigned long)HOLD_MS);
}

void doPulse(int n) {
  uint16_t c = codeFor(LEVEL_MA[n]);
  uint32_t period = 1000000UL / PULSE_HZ, count = BURST_MS * PULSE_HZ / 1000, done = 0;
  for (; done < count; done++) {
    uint32_t t0 = micros();
    if (!dacFast(c)) break;
    while (micros() - t0 < PULSE_US) {}
    dacFast(0);
    if (stopRequested()) break;
    while (micros() - t0 < period) {}
  }
  outZero();
  Serial.printf("OK P%d target=%.2f mA code=%u pulses=%lu/%lu (width %lu us incl. I2C ~60 us)\n", n, LEVEL_MA[n], c,
                (unsigned long)done, (unsigned long)count, (unsigned long)PULSE_US);
}

void handle(String s) {
  s.trim();
  if (!s.length()) return;
  char cmd = toupper(s[0]);
  int n = s.substring(1).toInt();
  if (cmd == 'S') { outZero(); Serial.println("OK S (0 V)"); return; }
  if (cmd == 'M') { Serial.printf("OK M mon=%d mV code=%u\n", monitorMv(), curCode); return; }
  if (cmd == '?') { printTable(); return; }
  if ((cmd == 'L' || cmd == 'P') && (n < 1 || n > N_LEVEL)) { Serial.printf("ERR level 1..%d\n", N_LEVEL); return; }
  if (cmd == 'L') { doHold(n); return; }
  if (cmd == 'P') { doPulse(n); return; }
  Serial.println("ERR unknown (L<n> P<n> M S ?)");
}

void setup() {
  Serial.begin(115200);
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);
  delay(300);
  Serial.println("\nSTIM-BENCH boot  ** dummy resistor only, no electrodes **");
  analogReadResolution(12);
  if (!findDac()) {
    Serial.println("FATAL MCP4725 not found on GPIO1/GPIO2 (check wiring, VDD 3V3). Output stays off.");
    for (;;) delay(1000);
  }
  dacFast(0);                                        // 가장 먼저 0으로
  Serial.printf("MCP4725 addr=0x%02X SDA=GPIO%d SCL=GPIO%d\n", dacAddr, pinSda, pinScl);
  uint8_t b[5];
  if (dacRead(b)) {
    uint16_t e = eepromCode(b);
    Serial.printf("EEPROM power-up code = %u (pd=%u)\n", e, (b[3] >> 5) & 0x03);
    if (e != 0) {
      Serial.println(writeEepromZero() ? "EEPROM power-up code -> 0 saved (next power-on starts at 0 V)"
                                       : "WARN EEPROM write failed: power-on still outputs non-zero!");
    }
  }
  Serial.printf("idle mon=%d mV (should be ~0)\n", monitorMv());
  printTable();
  Serial.println("READY  commands: L<n> hold | P<n> pulses | M monitor | S stop | ? table");
}

void loop() {
  if (holdUntil && (int32_t)(millis() - holdUntil) >= 0) { outZero(); Serial.println("AUTO 0 (hold time over)"); }
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') { handle(line); line = ""; }
    else if (line.length() < 32) line += ch;
  }
}
