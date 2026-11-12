"""ESP32가 보내는 패킷 스트림이 정상인지 N초 동안 점검한다. (대시보드 전에 먼저 이걸로 확인)
사용:  python check_stream.py COM5 10           (COM5를 10초 동안 읽기, 기본 921600 baud)
       python check_stream.py COM5 10 921600
       python check_stream.py --selftest         (장치 없이 가짜 ESP32로 이 점검 도구 자체를 시험)
확인하는 것: 수신 패킷 수, CRC 오류/유실, 실제 샘플링 속도(약 1000이어야 함), 채널별 크기(µV).
펌웨어가 TEST_SIGNAL=1 이면 약 +-1000 uV(=+-1 mV)의 1 Hz 사각파가 보여야 정상이다.
주의: Arduino IDE의 Serial Monitor를 먼저 닫을 것.   필요: pip install pyserial"""
import sys
import time

import numpy as np

import config as C
from packet import PacketParser
from serial_source import uv_per_count


def read_packets(port, seconds, baud):
    parser, pkts = PacketParser(), []
    if port == "--selftest":
        from fake_device import FakeLink
        link = FakeLink(minutes=1, speed=None, with_audio=False)
        while not link.finished and len(pkts) < 200:
            pkts += parser.feed(link.read_bytes())
        return parser, pkts
    try:
        import serial
    except ImportError:
        raise SystemExit("pyserial이 필요합니다:  pip install pyserial")
    try:
        ser = serial.Serial(port, baud, timeout=0.2)
    except serial.SerialException as e:
        raise SystemExit(f"포트를 열 수 없습니다 ({port}): {e}\nSerial Monitor 등이 포트를 쓰고 있지 않은지 확인하세요.")
    ser.reset_input_buffer()
    t0 = time.time()
    while time.time() - t0 < seconds:
        data = ser.read(4096)
        if data:
            pkts += parser.feed(data)
    ser.close()
    return parser, pkts


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    port = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    baud = int(sys.argv[3]) if len(sys.argv) > 3 else C.SERIAL_BAUD
    parser, pkts = read_packets(port, seconds, baud)

    print(f"수신 패킷 {len(pkts)}개 | CRC 오류 {parser.bad_crc} | 순번 유실 {parser.lost}")
    if len(pkts) < 3:
        print("패킷이 거의 안 들어왔습니다. 포트 번호, 속도(baud), 펌웨어의 [STREAMING] 상태, Serial Monitor가 닫혀 있는지 확인하세요.")
        return
    emg = np.vstack([p.emg for p in pkts]).astype(np.float64)       # (프레임, 채널)
    frames = len(emg)
    dt = (pkts[-1].t_us - pkts[0].t_us) / 1e6
    if dt > 0:
        fs = (frames - len(pkts[-1].emg)) / dt
        flag = "정상" if abs(fs - C.EMG_FS) < 0.03 * C.EMG_FS else "설정 EMG_FS와 다름 -> config.py 또는 펌웨어 확인"
        print(f"실제 샘플링 속도 약 {fs:.1f} Hz (설정 {C.EMG_FS}) -> {flag}")
    gains = {C.CH_EEG: C.ADS_GAIN_EEG, C.CH_EMG: C.ADS_GAIN_EMG}
    names = {C.CH_EEG: "EEG 채널", C.CH_EMG: "EMG 채널"}
    for ch in (C.CH_EEG, C.CH_EMG):
        uv = emg[:, ch] * uv_per_count(gains[ch])
        print(f"{names[ch]}(열 {ch}): 최소 {uv.min():9.1f} uV | 최대 {uv.max():9.1f} uV | 표준편차 {uv.std():8.1f} uV")
        p2p = uv.max() - uv.min()
        if 1500 <= p2p <= 2600 and uv.std() > 500:
            print("   -> 약 +-1 mV 사각파로 보입니다 (펌웨어 TEST_SIGNAL=1 이라면 정상).")
    print("(TEST_SIGNAL=1 이면 두 채널 모두 약 -1000 ~ +1000 uV로 오가야 합니다.)")


if __name__ == "__main__":
    main()
