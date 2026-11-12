"""ESP32 시리얼 출력을 N초만 읽어서 화면에 찍고 끝낸다 (Claude Code 같은 도구가 결과를 읽기 좋게).
사용:  python serial_peek.py COM5 15        (COM5 포트를 15초 동안 읽기, 기본 115200)
       python serial_peek.py COM5 15 921600
주의: Arduino IDE의 Serial Monitor가 열려 있으면 포트가 사용 중이라 열리지 않는다. 먼저 닫을 것.
필요: pip install pyserial"""
import sys
import time

try:
    import serial
except ImportError:
    raise SystemExit("pyserial이 필요합니다:  pip install pyserial")


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    port, seconds = sys.argv[1], float(sys.argv[2])
    baud = int(sys.argv[3]) if len(sys.argv) > 3 else 115200
    try:
        ser = serial.Serial(port, baud, timeout=0.5)
    except serial.SerialException as e:
        raise SystemExit(f"포트를 열 수 없습니다 ({port}): {e}\n"
                         "Serial Monitor 등 다른 프로그램이 포트를 쓰고 있지 않은지 확인하세요.")
    ser.reset_input_buffer()
    t0 = time.time()
    while time.time() - t0 < seconds:
        line = ser.readline()
        if line:
            print(line.decode("utf-8", errors="replace").rstrip(), flush=True)
    ser.close()


if __name__ == "__main__":
    main()
