"""마이크(INMP441) 소리 점검 (30초). 조용할 때와 박수/말소리 때를 비교해서 마이크가 진짜 소리에 반응하는지 본다.
사용:  python check_audio.py COM4            (기본 921600 baud, 펌웨어는 소리를 보내는 cogoaltis_fw2)
       python check_audio.py --selftest       (장치 없이 분석 코드만 시험)
순서: 처음 8초는 조용히 -> 그 뒤 22초 동안 박수를 치거나 말을 한다.
저장: sessions/audio_check_날짜_시각/  (audio.wav, result.txt, audio_check.png) - 그래프 그림은 자동으로 열린다.
참고: 값은 펌웨어가 int16으로 바꾼 소리다. 진짜 마이크는 조용한 방에서 수십~수백, 박수에는 수천 이상이 나온다.
      마이크 선이 허공에 떠 있으면 조용해도 -32768~32767 끝까지 꽉 차게 흔들린다.
주의: Serial Monitor를 먼저 닫을 것.   필요: pip install pyserial"""
import os
import sys
import time
import wave

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import config as C
from packet import PacketParser

QUIET_END, TOTAL = 8.0, 30.0
FS = C.AUDIO_FS


def window_rms(a, fs, win=0.1):
    n = int(win * fs)
    m = len(a) // n
    return np.sqrt(np.mean(a[:m * n].reshape(m, n).astype(np.float64) ** 2, axis=1)), win


def analyze(a, fs=FS):
    """a: int16 소리. 반환: 숫자들과 판정 글."""
    r = dict(n=len(a))
    if len(a) < fs * 10:
        r["verdict"] = "소리가 충분히 안 들어왔습니다. 펌웨어가 소리를 보내는 cogoaltis_fw2인지, 포트/속도를 확인하세요."
        return r
    rms, win = window_rms(a, fs)
    t = np.arange(len(rms)) * win
    q = (t >= 1) & (t < QUIET_END - 1)
    l = t >= QUIET_END + 1
    seg = a[int(1 * fs):int((QUIET_END - 1) * fs)]
    r.update(rms_curve=rms, t=t, quiet_rms=float(np.median(rms[q])), loud_rms=float(np.percentile(rms[l], 95)),
             quiet_sat=float(np.mean(np.abs(seg) >= 32000) * 100), max_abs=int(np.max(np.abs(a))))
    r["ratio"] = r["loud_rms"] / r["quiet_rms"] if r["quiet_rms"] > 0 else float("inf")
    if r["max_abs"] == 0:
        r["verdict"] = "모두 0입니다: 마이크가 아무것도 안 보냅니다 (전원 VDD->3V3, GND, L/R->GND, 핀 배선 확인)."
    elif r["quiet_sat"] > 5 or r["quiet_rms"] > 3000:
        r["verdict"] = ("조용할 때도 값이 매우 큽니다: 진짜 마이크 소리가 아니라 선이 허공에 떠 있거나(배선/연결 안 됨) 핀 순서가 틀린 모양입니다.")
    elif r["ratio"] >= 5 and r["quiet_rms"] < 1500:
        r["verdict"] = "정상: 조용할 때는 작고, 박수/말소리에는 크게 반응합니다. 마이크가 제대로 동작합니다."
    else:
        r["verdict"] = "애매함: 소리에 반응이 약합니다. 마이크 구멍 쪽을 소리 가까이(20~30cm) 두고 더 크게 박수를 쳐 다시 해보세요."
    return r


def lines(r):
    if "rms_curve" not in r:
        return ["=== 결과 ===", r["verdict"]]
    return ["=== 결과 ===",
            f"조용할 때 크기(중앙값): {r['quiet_rms']:.0f} | 박수/말소리 때(상위5%): {r['loud_rms']:.0f} | 비율 {r['ratio']:.1f}배",
            f"조용한 구간에서 끝값(±32000 이상)에 붙은 비율: {r['quiet_sat']:.1f}% | 전체 최대 {r['max_abs']}",
            "판정: " + r["verdict"]]


def save(r, a, out):
    os.makedirs(out, exist_ok=True)
    with wave.open(os.path.join(out, "audio.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(FS)
        w.writeframes(a.astype("<i2").tobytes())
    with open(os.path.join(out, "result.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines(r)) + "\n")
    if "rms_curve" in r:
        try:
            import koreanize_matplotlib  # noqa: F401
        except ImportError:
            pass
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.plot(r["t"], r["rms_curve"], color="tab:blue", lw=1.2)
        ax.axvspan(1, QUIET_END - 1, color="0.8", alpha=0.4, label="조용한 구간 (기준)")
        ax.axvspan(QUIET_END + 1, TOTAL, color="tab:red", alpha=0.08, label="박수/말소리 구간")
        ax.set_xlabel("시간 (초)"); ax.set_ylabel("소리 크기 (int16 RMS)")
        ax.set_title(r["verdict"][:60], loc="left", fontsize=10)
        ax.legend(loc="upper right"); ax.grid(alpha=0.25)
        fig.tight_layout()
        png = os.path.join(out, "audio_check.png")
        fig.savefig(png, dpi=110)
        plt.close(fig)
        try:
            os.startfile(png)
        except (AttributeError, OSError):
            print("그래프 파일:", png)
    print("\n".join(lines(r)))
    print("저장 폴더:", os.path.abspath(out))


def acquire(port, baud):
    try:
        import serial
    except ImportError:
        raise SystemExit("pyserial이 필요합니다:  pip install pyserial")
    try:
        ser = serial.Serial(port, baud, timeout=0.2)
    except serial.SerialException as e:
        raise SystemExit(f"포트를 열 수 없습니다 ({port}): {e}\nSerial Monitor 등이 포트를 쓰고 있지 않은지 확인하세요.")
    ser.reset_input_buffer()
    parser, chunks, n, ci = PacketParser(), [], 0, 0
    cues = [(0, "시작! 8초 동안 조용히 계세요"), (QUIET_END, "지금부터 박수를 치거나 말하세요 (계속)\a"), (TOTAL, "끝")]
    t0 = time.time()
    while n < TOTAL * FS:
        for p in parser.feed(ser.read(4096)):
            chunks.append(p.audio)
            n += len(p.audio)
        while ci < len(cues) and n / FS >= cues[ci][0]:
            print(cues[ci][1], flush=True)
            ci += 1
        if time.time() - t0 > TOTAL + 30:                        # 데이터가 안 들어오거나 너무 느리면 포기
            print("시간 초과: 소리 데이터가 충분히 들어오지 않았습니다 (펌웨어, 포트, 속도 확인).")
            break
    ser.close()
    return np.concatenate(chunks)[:int(TOTAL * FS)] if chunks else np.zeros(0, np.int16)


def selftest():
    rng = np.random.default_rng(0)
    n = int(TOTAL * FS)
    t = np.arange(n) / FS
    good = rng.normal(0, 60, n)
    for s in (9, 12, 15, 19, 24):
        m = (t >= s) & (t < s + 0.3)
        good[m] += rng.normal(0, 9000, m.sum())
    floating = np.clip(rng.normal(0, 1e5, n), -32768, 32767)
    for name, a in (("정상 마이크 흉내", good), ("허공에 뜬 선 흉내", floating)):
        print(f"\n[{name}]")
        print("\n".join(lines(analyze(np.clip(a, -32768, 32767).astype(np.int16)))))


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if sys.argv[1] == "--selftest":
        return selftest()
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else C.SERIAL_BAUD
    a = acquire(sys.argv[1], baud)
    save(analyze(a), a, os.path.join("sessions", "audio_check_" + time.strftime("%Y%m%d_%H%M%S")))


if __name__ == "__main__":
    main()
