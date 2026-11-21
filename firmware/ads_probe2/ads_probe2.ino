// ads_probe2.ino : ADS1292R 칩이 "살아 있는지"를 먼저 확인하는 진단 스케치 (ads_pin_probe 후속)
//
// 1단계(통신 없이): 칩을 깨우고 START를 켠 뒤 1초 동안 DRDY 선이 몇 번 깜빡이는지 센다.
//    ADS1292R은 전원/리셋/클럭이 정상이면 기본 설정(약 500회/초)으로 DRDY를 계속 내보낸다.
//      - 약 500회 -> 칩은 살아 있음 (전원, PWDN, START, DRDY 선이 정상)
//      - 0회      -> 칩이 안 깨어나거나 DRDY 선이 안 이어짐 (전원/PWDN/START/DRDY/클럭 확인)
// 2단계(DRDY가 확인된 배정에서만): ID 레지스터 읽기 (정상이면 0x73)
//
// 사용법: Arduino IDE에서 열기 -> 보드 ESP32S3 Dev Module -> 업로드 -> 시리얼 모니터 115200
#include <SPI.h>

struct PinSet { const char* name; int sck, mosi, miso, cs, drdy, start, pwdn; };

const PinSet SETS[] = {
  {"A: 처음 배선 (7~13번)",             12, 11, 13, 10,  9,  8,  7},
  {"B: 표대로 꽂은 배선 (4~7, 15~17번)",  4,  5,  6,  7, 15, 16, 17},
  {"C: B와 반대 방향 (17~15, 7, 6~4번)", 17, 16, 15,  7,  6,  5,  4},
};
const int N_SETS = sizeof(SETS) / sizeof(SETS[0]);

volatile uint32_t drdyCount = 0;
void IRAM_ATTR onDrdy() { drdyCount++; }

void releasePins(const PinSet& s) {
  int p[] = {s.sck, s.mosi, s.miso, s.cs, s.drdy, s.start, s.pwdn};
  for (int i = 0; i < 7; i++) pinMode(p[i], INPUT);
}

void wakeChip(const PinSet& s) {
  pinMode(s.cs, OUTPUT);    digitalWrite(s.cs, HIGH);
  pinMode(s.start, OUTPUT); digitalWrite(s.start, LOW);
  pinMode(s.pwdn, OUTPUT);  digitalWrite(s.pwdn, HIGH);
  pinMode(s.drdy, INPUT);
  delay(1000);                                   // 전원 안정 대기
  digitalWrite(s.pwdn, LOW); delay(5);           // 리셋 펄스
  digitalWrite(s.pwdn, HIGH); delay(200);
}

uint32_t countDrdy(const PinSet& s) {
  wakeChip(s);
  drdyCount = 0;
  attachInterrupt(digitalPinToInterrupt(s.drdy), onDrdy, FALLING);
  digitalWrite(s.start, HIGH);                   // 변환 시작
  delay(1000);
  detachInterrupt(digitalPinToInterrupt(s.drdy));
  digitalWrite(s.start, LOW);
  return drdyCount;
}

int readId(const PinSet& s, bool swapMosiMiso) {
  int mosi = swapMosiMiso ? s.miso : s.mosi;
  int miso = swapMosiMiso ? s.mosi : s.miso;
  wakeChip(s);
  SPI.begin(s.sck, miso, mosi, -1);              // CS는 직접 제어
  SPI.beginTransaction(SPISettings(200000, MSBFIRST, SPI_MODE1));
  digitalWrite(s.cs, LOW);  delayMicroseconds(10);
  SPI.transfer(0x11);                            // SDATAC: 연속 읽기 모드 해제
  delayMicroseconds(20);
  digitalWrite(s.cs, HIGH); delayMicroseconds(20);
  digitalWrite(s.cs, LOW);  delayMicroseconds(10);
  SPI.transfer(0x20);                            // RREG: ID 레지스터부터
  delayMicroseconds(20);
  SPI.transfer(0x00);                            // 1개 읽기
  delayMicroseconds(20);
  uint8_t id = SPI.transfer(0x00);
  delayMicroseconds(10);
  digitalWrite(s.cs, HIGH);
  SPI.endTransaction();
  SPI.end();
  return id;
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { }
  delay(500);
  Serial.println("=== ADS1292R 진단 시작 (1단계: DRDY 신호 확인) ===");

  bool alive = false;
  for (int i = 0; i < N_SETS && !alive; i++) {
    uint32_t n = countDrdy(SETS[i]);
    Serial.printf("[%s] DRDY 1초간 %lu회", SETS[i].name, (unsigned long)n);
    if (n >= 100) {
      Serial.println("  <== 칩이 살아 있음!");
      alive = true;
      Serial.println("=== 2단계: ID 읽기 ===");
      for (int sw = 0; sw < 2; sw++) {
        int id = readId(SETS[i], sw == 1);
        Serial.printf("  ID 0x%02X%s", id, sw ? " (MOSI/MISO 맞바꿈)" : "");
        Serial.println((id == 0x73 || id == 0x53) ? "  <== 정상 응답! 이 배선이 맞습니다" : "  (응답 없음)");
      }
      Serial.printf("  배선: SCK=%d MOSI=%d MISO=%d CS=%d DRDY=%d START=%d PWDN=%d\n",
                    SETS[i].sck, SETS[i].mosi, SETS[i].miso, SETS[i].cs,
                    SETS[i].drdy, SETS[i].start, SETS[i].pwdn);
    } else {
      Serial.println("  (신호 없음)");
    }
    releasePins(SETS[i]);
  }

  if (!alive) {
    Serial.println("DRDY 신호를 못 찾음 -> 칩이 안 깨어나거나 DRDY 선이 연결되지 않았습니다.");
    Serial.println("확인: VDD=3V3, GND, PWDN/RESET, START, DRDY 선, 모듈 핀 접촉, 클럭(CLK) 설정");
  }
  Serial.println("=== 끝 (결과를 그대로 복사해서 알려주세요) ===");
}

void loop() { }
