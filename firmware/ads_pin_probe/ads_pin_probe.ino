// ads_pin_probe.ino : CJMCU-1292(ADS1292R)가 ESP32-S3의 어느 핀에 연결됐는지 찾는 확인용 스케치
//
// 후보 핀 배정으로 ADS1292R의 ID 레지스터를 읽어서, 응답이 오는 배정을 시리얼 모니터에 알려준다.
//  - 정상이면 ID = 0x73 (ADS1292R)  /  0x53 이면 ADS1292
//  - 0x00 또는 0xFF 는 응답 없음 (그 배정이 아님)
// 처음 맞는 배정을 찾으면 거기서 멈춘다. 후보에 없는 핀에 꽂았다면 "배정을 못 찾음"이 나온다.
//
// 사용법: Arduino IDE에서 보드를 ESP32S3 Dev Module 로 고르고 업로드 -> 시리얼 모니터 115200
//  - 보드의 "UART"로 인쇄된 USB-C 포트로 연결하면 그대로 동작
//  - "USB"로 인쇄된 포트로 연결하면 Tools > USB CDC On Boot > Enabled 로 바꿔야 글이 보임
#include <SPI.h>

struct PinSet { const char* name; int sck, mosi, miso, cs, drdy, start, pwdn; };

const PinSet SETS[] = {
  {"A: 처음 배선 (7~13번)",             12, 11, 13, 10,  9,  8,  7},
  {"B: 옮긴 배선 (4~7, 15~17번)",        4,  5,  6,  7, 15, 16, 17},
  {"C: B와 반대 방향 (17~15, 7, 6~4번)", 17, 16, 15,  7,  6,  5,  4},
};
const int N_SETS = sizeof(SETS) / sizeof(SETS[0]);

void releasePins(const PinSet& s) {            // 확인이 끝난 핀은 입력으로 돌려놓음
  int p[] = {s.sck, s.mosi, s.miso, s.cs, s.drdy, s.start, s.pwdn};
  for (int i = 0; i < 7; i++) pinMode(p[i], INPUT);
}

int readId(const PinSet& s, bool swapMosiMiso) {
  int mosi = swapMosiMiso ? s.miso : s.mosi;
  int miso = swapMosiMiso ? s.mosi : s.miso;

  pinMode(s.cs, OUTPUT);    digitalWrite(s.cs, HIGH);
  pinMode(s.start, OUTPUT); digitalWrite(s.start, LOW);
  pinMode(s.pwdn, OUTPUT);  digitalWrite(s.pwdn, HIGH);
  pinMode(s.drdy, INPUT);
  delay(1000);                                              // 전원 안정 대기
  digitalWrite(s.pwdn, LOW); delay(5);                      // 리셋 펄스
  digitalWrite(s.pwdn, HIGH); delay(200);

  SPI.begin(s.sck, miso, mosi, -1);                         // CS는 직접 제어
  SPI.beginTransaction(SPISettings(500000, MSBFIRST, SPI_MODE1));

  digitalWrite(s.cs, LOW);  delayMicroseconds(10);
  SPI.transfer(0x11);                                       // SDATAC: 연속 읽기 모드 해제
  delayMicroseconds(20);
  digitalWrite(s.cs, HIGH); delayMicroseconds(20);

  digitalWrite(s.cs, LOW);  delayMicroseconds(10);
  SPI.transfer(0x20);                                       // RREG: 레지스터 0(ID)부터
  delayMicroseconds(20);
  SPI.transfer(0x00);                                       // 1개만 읽기
  delayMicroseconds(20);
  uint8_t id = SPI.transfer(0x00);                          // ID 값 수신
  delayMicroseconds(10);
  digitalWrite(s.cs, HIGH);

  SPI.endTransaction();
  SPI.end();
  releasePins(s);
  return id;
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { }
  delay(500);
  Serial.println("=== ADS1292R 배선 확인 시작 ===");

  bool found = false;
  for (int i = 0; i < N_SETS && !found; i++) {
    for (int sw = 0; sw < 2 && !found; sw++) {
      int id = readId(SETS[i], sw == 1);
      Serial.printf("[%s]%s -> ID 0x%02X", SETS[i].name, sw ? " (MOSI/MISO 맞바꿈)" : "", id);
      if (id == 0x73 || id == 0x53) {
        Serial.println("  <== 응답 확인! 이 배정이 맞습니다");
        Serial.printf("   SCK=%d MOSI=%d MISO=%d CS=%d DRDY=%d START=%d PWDN=%d\n",
                      SETS[i].sck, sw ? SETS[i].miso : SETS[i].mosi, sw ? SETS[i].mosi : SETS[i].miso,
                      SETS[i].cs, SETS[i].drdy, SETS[i].start, SETS[i].pwdn);
        found = true;
      } else {
        Serial.println("  (응답 없음)");
      }
    }
  }
  if (!found) {
    Serial.println("배정을 못 찾음: 후보에 없는 핀이거나, 전원(VDD/GND)·납땜·선 접촉을 확인하세요.");
  }
  Serial.println("=== 끝 (결과를 그대로 복사해서 알려주세요) ===");
}

void loop() { }
