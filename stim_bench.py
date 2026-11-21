"""자극 회로 더미 저항 시험 (firmware/stim_bench 와 짝).  ** 전극/사람 몸 연결 금지 **

순서
  1) 보드에 firmware/stim_bench 업로드 (리드선 자리 J/44-J/48 에는 더미 저항만)
  2) G/40-G/44 커패시터 자리를 잠깐 전선으로 바꿈 (직류 측정용. 끝나면 커패시터로 복귀)
  3) python stim_bench.py COM4
     - 저항 1k -> 4.7k -> 10k 순서로 바꿔 꽂으라고 안내
     - 단계마다 3초 동안 전류가 흐름 -> 그동안 멀티미터로 더미 저항 양단 전압(mV)을 읽어 입력
     - 결과: 목표 전류 vs 실제 전류, 통과/실패, 전압 부족(포화) 여부 -> sessions/stim_bench_날짜_시각.csv
  펄스 확인(오실로스코프, 커패시터 복귀 후):  python stim_bench.py COM4 --pulse 3
"""
import argparse
import csv
import os
import re
import sys
import time

LEVEL_MA = {1: 0.25, 2: 0.5, 3: 0.75, 4: 1.0, 5: 1.5}   # 펌웨어 LEVEL_MA 와 같아야 함
R_BLEED = 100e3          # H/44-H/48 100k (더미 저항과 병렬)
RS = 1000.0              # J/30-J/36
V_SUPPLY = 5.0           # ESP32 5V
OUT_HEADROOM = 0.2       # rail-to-rail op-amp 출력이 전원에서 떨어지는 정도 (V, 대략)
TOL = 0.10               # 통과 기준 +-10%
DUMMIES = (1000, 4700, 10000)


def ask(msg, default=None):
    s = input(msg).strip()
    return s if s else default


def ask_float(msg):
    while True:
        s = input(msg).strip().replace(",", "")
        try:
            return float(s)
        except ValueError:
            print("  숫자로 입력하세요 (예: 512)")


def predict_out_v(i_ma, r_load, rf):
    """op-amp 출력에 필요한 전압(대략). 전원 - 여유보다 크면 전류가 다 못 나간다(포화)."""
    r_eff = r_load * R_BLEED / (r_load + R_BLEED)
    return i_ma / 1000.0 * (RS + rf + r_eff)


class Board:
    def __init__(self, port, baud=115200, ser=None):
        if ser is None:
            import serial
            ser = serial.Serial(port, baud, timeout=0.2)
        self.ser = ser

    def lines(self, seconds):
        out, end = [], time.time() + seconds
        while time.time() < end:
            raw = self.ser.readline()
            if raw:
                s = raw.decode(errors="ignore").strip()
                if s:
                    print("  [보드]", s)
                    out.append(s)
        return out

    def cmd(self, text, wait=0.8):
        self.ser.reset_input_buffer()
        self.ser.write((text + "\n").encode())
        return self.lines(wait)

    def close(self):
        try:
            self.ser.write(b"S\n")
            time.sleep(0.1)
            self.ser.close()
        except Exception:
            pass


def boot(board):
    print("\n보드 부팅 메시지를 읽는 중 (4초)...")
    got = board.lines(4.0)
    if not any("READY" in s for s in got):
        print("  READY가 안 보입니다. 보드 리셋(RST 버튼) 후 다시 읽습니다.")
        got += board.lines(5.0)
    text = "\n".join(got)
    if "FATAL" in text:
        sys.exit("MCP4725를 못 찾았습니다. I2C 배선(GPIO1/2), VDD 3V3를 확인하세요.")
    if "WARN EEPROM" in text:
        print("!! EEPROM 0 저장 실패: 전원을 켤 때 출력이 0이 아닙니다. 다음 단계로 가지 마세요.")
    if not any("READY" in s for s in got):
        print("  (READY를 못 받았지만 명령으로 확인해 봅니다)")
        board.cmd("?", 1.0)


def run_dc(board, rf, out_dir):
    rows = []
    v0 = ask_float("\n[0] 대기 상태(출력 0)에서 더미 저항 양단 전압 mV (0 근처여야 함): ")
    rows.append(dict(r_ohm="", level=0, target_ma=0, measured_mv=v0, measured_ma=0, mon_mv="",
                     expect_mon_mv="", predicted_out_v="", saturated="", result="PASS" if abs(v0) < 20 else "FAIL"))
    for r in DUMMIES:
        input(f"\n=== J/44-J/48 에 {r/1000:g}k 더미 저항을 꽂고 Enter (전극 연결 금지) ===")
        r_eff = r * R_BLEED / (r + R_BLEED)
        for lv, ma in LEVEL_MA.items():
            need = predict_out_v(ma, r, rf)
            sat = need > V_SUPPLY - OUT_HEADROOM
            print(f"\n- {lv}단계 목표 {ma:.2f} mA  (예상 더미 전압 {ma * r_eff:.0f} mV"
                  f"{', 전압 부족 예상' if sat else ''})  -> 3초 출력")
            resp = board.cmd(f"L{lv}", 0.6)
            m = re.search(r"mon=(-?\d+) mV expect_mon=(\d+)", " ".join(resp))
            mon, exp_mon = (int(m.group(1)), int(m.group(2))) if m else ("", "")
            v = ask_float("  3초 안에 멀티미터로 읽은 더미 저항 양단 전압 mV: ")
            i_meas = v / r_eff
            err = (i_meas - ma) / ma
            ok = abs(err) <= TOL
            res = "PASS" if ok else ("SAT(전압부족)" if sat else "FAIL")
            print(f"  -> 실제 {i_meas:.3f} mA ({err:+.0%})  {res}"
                  + (f"   DAC 모니터 {mon} mV / 기대 {exp_mon} mV" if mon != "" else ""))
            rows.append(dict(r_ohm=r, level=lv, target_ma=ma, measured_mv=v, measured_ma=round(i_meas, 4),
                             mon_mv=mon, expect_mon_mv=exp_mon, predicted_out_v=round(need, 2),
                             saturated=int(sat), result=res))
            board.lines(2.6)                  # AUTO 0 메시지 대기
    path = os.path.join(out_dir, "stim_bench_" + time.strftime("%Y%m%d_%H%M%S") + ".csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("\n=== 결과 요약 ===")
    print(f"{'저항':>6} {'단계':>4} {'목표mA':>7} {'실제mA':>7}  결과")
    for r in rows:
        print(f"{str(r['r_ohm']):>6} {r['level']:>4} {r['target_ma']:>7} {r['measured_ma']:>7}  {r['result']}")
    n_fail = sum(1 for r in rows if r["result"] == "FAIL")
    print(f"\nFAIL {n_fail}개 / SAT(전압부족)는 5V 전원의 한계로 예상된 것  ->  저장: {path}")
    print("끝나면 G/40-G/44 를 다시 커패시터로 바꾸세요.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port")
    ap.add_argument("--rf", type=float, default=None, help="I/36-I/40 Rf 값(옴). 안 주면 물어봄")
    ap.add_argument("--pulse", type=int, default=0, help="이 단계로 펄스열만 내보냄 (오실로스코프 확인용)")
    ap.add_argument("--out", default="sessions")
    a = ap.parse_args()

    print("※ 이 시험은 더미 저항 전용입니다. 리드선 자리에 전극이나 사람 몸이 연결되어 있으면 지금 멈추세요.")
    if ask("리드선 자리에 더미 저항만 있고 전극은 연결 안 됨 -> YES 입력: ", "").upper() != "YES":
        sys.exit("취소했습니다.")
    os.makedirs(a.out, exist_ok=True)
    board = Board(a.port)
    try:
        boot(board)
        if a.pulse:
            if a.pulse not in LEVEL_MA:
                sys.exit("단계는 1~5")
            print(f"\n{a.pulse}단계 펄스열 1초 (오실로스코프: Rs J/30-J/36 양단 또는 더미 저항 양단)")
            board.cmd(f"P{a.pulse}", 2.0)
            return
        rf = a.rf if a.rf is not None else float(ask("Rf(I/36-I/40) 값 옴 [1000]: ", "1000"))
        run_dc(board, rf, a.out)
    finally:
        board.close()


if __name__ == "__main__":
    main()
