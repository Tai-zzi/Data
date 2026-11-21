// ads_live.ino : DRDY 신호를 1초마다 계속 보여주는 "실시간 모니터" (납땜 없이 점검할 때 사용)
//
// 핀은 표대로(B 배선) 고정:
//   SCK=4  MOSI=5  MISO=6  CS=7  DRDY=15  START=16  PWDN/RESET=17
// 매 주기마다 칩을 리셋하고 START를 켠 뒤 1초 동안 DRDY 하강 횟수를 센다.
//   약 500회(400~600) -> 칩이 살아 있음 / 0회 -> 신호 없음 / 그 밖의 값 -> 불안정
// 시리얼 모니터(115200)를 켜 둔 채로 배선이나 패드를 만지면, 숫자가 바뀌는 순간을 바로 볼 수 있다.
// 진짜 신호는 매초 거의 같은 값(약 500)이 나온다. 0~수천으로 들쭉날쭉하면 접촉 불량이나 노이즈다.
// (v2: DRDY 입력에 풀업을 켜서, 선이 떠 있으면 노이즈 대신 0으로 보이게 함 + 값 판정 추가)
const int PIN_SCK = 4, PIN_MOSI = 5, PIN_MISO = 6, PIN_CS = 7;
const int PIN_DRDY = 15, PIN_START = 16, PIN_PWDN = 17;

volatile uint32_t drdyCount = 0;
void IRAM_ATTR onDrdy() { drdyCount++; }

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { }
  pinMode(PIN_SCK, INPUT); pinMode(PIN_MOSI, INPUT); pinMode(PIN_MISO, INPUT);   // SPI 선은 이번엔 쓰지 않음
  pinMode(PIN_CS, OUTPUT);    digitalWrite(PIN_CS, HIGH);
  pinMode(PIN_START, OUTPUT); digitalWrite(PIN_START, LOW);
  pinMode(PIN_PWDN, OUTPUT);  digitalWrite(PIN_PWDN, HIGH);
  pinMode(PIN_DRDY, INPUT_PULLUP);       // 선이 떠 있어도 노이즈가 안 잡히게 풀업
  attachInterrupt(digitalPinToInterrupt(PIN_DRDY), onDrdy, FALLING);
  Serial.println("=== DRDY 실시간 모니터 시작 (1초마다 한 줄) ===");
  Serial.println("약 500이면 칩 정상, 0이면 신호 없음. 패드/배선을 만지면서 숫자 변화를 보세요.");
}

void loop() {
  digitalWrite(PIN_START, LOW);
  digitalWrite(PIN_PWDN, LOW);  delay(5);          // 리셋 펄스
  digitalWrite(PIN_PWDN, HIGH); delay(200);
  noInterrupts(); drdyCount = 0; interrupts();
  digitalWrite(PIN_START, HIGH);                   // 변환 시작
  delay(1000);
  noInterrupts(); uint32_t n = drdyCount; interrupts();
  const char* verdict = (n == 0) ? "신호 없음"
                      : (n >= 400 && n <= 600) ? "정상 범위 (약 500)"
                      : "불안정 (접촉 불량/노이즈 의심, 진짜 신호 아님)";
  Serial.printf("DRDY %lu회/초  ->  %s\n", (unsigned long)n, verdict);
}
