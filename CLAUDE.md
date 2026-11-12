# CoGoalTis (코골티스) - 프로젝트 메모 (Claude Code가 시작할 때 읽는 파일)

작업 폴더: 저장소 루트 (Windows에서는 `C:\CoGoalTis` 같은 **영어 경로의 로컬 폴더**를 권장. OneDrive/한글 경로에서 파일을 못 읽는 문제가 있었다)

## 목적
이어버드형 EEG/EMG 센서 + 마이크로 이갈이·코골이를 감지하는 캡스톤디자인 프로젝트.
PC 쪽은 Python(루트의 .py), 장치 쪽은 ESP32-S3 + Arduino 스케치(`firmware\`).
현황과 배선은 `docs\STATUS.md`, `docs\HARDWARE.md`, 시험 기록은 `docs\TEST_LOG.md`, 안전 원칙은 `docs\SAFETY.md`를 먼저 읽는다.

## 폴더 구조 (평평함)
- 루트의 `*.py`: config, stream, collect, serial_source, session_logger, check_stream, check_audio, emg_clench_test, report_test, view_session, dashboard ...
- `firmware\<스케치이름>\`: Arduino 스케치. 본 펌웨어는 `firmware\cogoaltis_fw2`(EMG + 마이크). 나머지는 점검·진단용
- `docs\`: 문서와 시험 기록. 측정 원본(`sessions`, `collections`)은 저장소에 올리지 않는다(.gitignore)

## 하드웨어 현재 상태 (확인된 것 / 확인 안 된 것)
- ADS1292R(CJMCU-1292): **정상 동작** (ID 0x73, 1 kSPS, 내부 클럭). LOGIC_SEL은 3.3V 쪽으로 납땜. CLK 선은 필요 없음.
- 표 배선: SCK 4, MOSI 5, MISO 6, CS 7, DRDY 15, START 16, PWDN/RESET 17, VDD->3V3, GND->GND
- 데이터 경로 확인: 약 1000 Hz, CRC 오류 0 (check_stream.py, collect.py 마지막 수집)
- 턱 EMG는 패킷 **열 0** (config.py: CH_EMG = 0, CH_EEG = 1). 열 1은 전극 없는 비어 있는 채널. 힘주기 테스트에서 열 0이 5.2배 반응.
- **최근 실시간 수집(11/11~11/12)에서 턱 EMG 입력이 포화**(+-403 mV = 게인 6 최대 입력)되어 이갈이 감지 못함. 전극 접촉/연결 확인이 필요하다.
- 마이크 INMP441: 배선 SCK 40, WS 41, SD 39, VDD 3V3, GND, L/R GND (마이크 뒷면 글자 기준). 수집 그래프에서 소리에 반응하는 것으로 보이나, `check_audio.py` 정식 확인 기록은 없다.
- 자극(MCP4725): 연결 안 함. 펌웨어에 자극 출력 없음. `Stimulator.set_level()`은 비어 있다.
  MCP4725는 전원만 켜도 약 50%(약 1.65V)를 내보낸다는 것이 사용자 세션에서 확인되었다. **VOUT은 몸/전극/패치에 절대 연결하지 않는다.** 책상 점검은 `firmware\dac_test`(VOUT -> ESP32 14번만).
- 전극을 몸에 붙일 때는 USB 아이솔레이터(PC -> 아이솔레이터 -> ESP32)와 노트북 배터리가 전제.
- 보드에 지금 어떤 스케치가 올라가 있는지는 사용자가 직접 확인한다(측정 전 `cogoaltis_fw2`가 올라가 있어야 한다).

## 자주 쓰는 명령 (루트에서 실행)
- 포트 목록: `python serial_source.py --list`  (COM 번호는 바뀐다: COM4/COM5)
- 스트림 점검: `python check_stream.py COM4 10`
- 마이크 소리 점검(30초, 조용히 8초 + 박수): `python check_audio.py COM4`
- 턱 힘주기 테스트(40초): `python emg_clench_test.py COM4`
- **실시간 수집(그래프 + 모든 것 저장)**: `python collect.py COM4` (`--minutes 60`, `--no-gui` 밤샘, `--fake` 코드 시험) -> collections\날짜_시각\
- 통합 간이 테스트(150초): `python report_test.py COM4`
- 저장된 기록 다시 보기: `python view_session.py "sessions\날짜_시각"`
- 명령 프롬프트에서 한 번에: `fw help` (fw.bat). 보드에 올리거나 전극을 쓰는 명령은 fw.bat이 먼저 YES를 묻는다.
  Claude Code는 이런 명령을 대신 실행하지 말고 사용자가 직접 실행하게 안내한다.
- Arduino IDE 설정: 보드 ESP32S3 Dev Module, USB CDC On Boot = Disabled, 케이블은 "UART"로 인쇄된 포트, 업로드는 사용자가 직접 한다.

## 규칙 (꼭 지킬 것)
1. 권한 모드는 manual(허락을 묻는 기본 모드)을 유지한다. auto 모드는 쓰지 않는다.
2. 보드에 올리는 명령(upload 등)은 하지 말고 사용자에게 방법만 안내한다. 하게 된다면 전극이 몸에서 떨어져 있는지 먼저 확인받는다.
3. 자극 출력(MCP4725 -> 패치) 코드는 사용자가 명시적으로 요청하기 전까지 만들거나 켜지 않는다. 몸에 닿는 전기 자극은 지도교수 승인과 안전 설계가 먼저다(docs\SAFETY.md).
4. VDD를 5V로 옮기거나 납땜 패드(LOGIC_SEL, CLKSEL)를 바꾸는 일은 코드로 하지 말고 안내만 한다. (LOGIC_SEL이 5V 쪽이면 ESP32 핀에 5V가 들어갈 수 있다)
5. 저장소 바깥의 파일은 고치거나 지우지 않는다. 파일을 지우기 전에는 반드시 사용자에게 목록을 보여주고 확인받는다.
6. 실제 장치로 확인하지 못한 코드는 "문법만 점검했고 실제 보드 미검증"이라고 정직하게 말한다.
7. 교수님께 보고하는 결과는 부풀리지 않는다. 간이 테스트는 깨어 있는 상태에서 동작을 흉내 낸 것이라 수면 중 정확도를 보여주지 않는다. 의료 진단이 아니라고 밝힌다.
8. 개인정보(전화번호, 이메일, 학번)와 측정 원본(녹음 포함)은 저장소에 올리지 않는다.
9. 사용자는 한국어로 대화한다. 설명은 쉬운 말로, 한 번에 한 단계씩.

## 알려진 함정
- 턱 EMG가 +-403,000 uV에 붙어 있으면 입력 포화다: 전극이 떨어졌거나 접촉이 나쁘다. 정상은 휴식 때 수십 uV, 이를 물면 수백 uV (collect.py가 경고를 띄운다).
- 코골이 판정은 규칙적인 소리가 연속 SNORE_MIN_INTERVALS번(기본 2) 이어져야 ON이다. 오탐이 많으면 config.py에서 3으로 올린다.
- Serial Monitor가 열려 있으면 포트가 막힌다 (업로드, check_stream, collect 모두).
- 보드의 시리얼 속도는 921600 (cogoaltis_fw/fw2). Serial Monitor로 열려면 속도를 맞춰야 읽힌다 (데이터 패킷은 어차피 깨져 보인다).
- Arduino IDE는 함수 선언을 첫 함수 앞에 자동으로 끼워 넣는다. 함수 인자로 쓰는 struct는 첫 함수보다 위에서 선언해야 한다.
- 한글 경로/OneDrive 경로에서 파이썬이 파일을 못 읽는 일이 있었다. 영어 경로의 로컬 폴더에서만 작업한다.
