"""턱에 힘주기 간이 테스트 (40초). 어느 채널이 턱 근육 신호에 반응하는지 알려준다.
순서: 처음 10초는 가만히 -> (2초 꽉 물기 + 4초 쉬기) x 5번.   안내 문구가 화면에 뜬다.
사용:  python emg_clench_test.py COM4            (기본 921600 baud)
       python emg_clench_test.py COM4 921600
       python emg_clench_test.py --load sessions\\clench_날짜_시각   (저장된 결과를 다시 보기: 그래프 다시 열기)
       python emg_clench_test.py --selftest      (장치 없이 분석 코드만 시험)
끝나면 sessions/clench_날짜_시각/ 폴더에 원본(emg_counts.npy), 결과(result.txt), 그래프(clench_result.png)가 저장되고
그래프 그림이 자동으로 열린다.
먼저: 펌웨어를 TEST_SIGNAL=0 으로 올려 두고, Serial Monitor는 닫을 것.   필요: pip install pyserial
안전: 전극을 몸에 붙이기 전에 USB 아이솔레이터 연결과 노트북 배터리 사용을 확인할 것."""
import os
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, iirnotch, sosfiltfilt, tf2sos

import config as C
from packet import PacketParser
from serial_source import uv_per_count

RELAX0, CLENCH, REST, REPEAT = 10, 2, 4, 5
TOTAL = RELAX0 + REPEAT * (CLENCH + REST)          # 40 초
FULL_SCALE = 8_388_607


def column_gain(col):
    return C.ADS_GAIN_EEG if col == C.CH_EEG else C.ADS_GAIN_EMG


def filtered(x_uv, fs):
    sos = butter(4, [20, min(450, fs / 2 - 5)], "bandpass", fs=fs, output="sos")
    y = sosfiltfilt(sos, x_uv)
    b, a = iirnotch(C.MAINS_HZ, Q=30, fs=fs)
    return sosfiltfilt(tf2sos(b, a), y)


def envelope(x_uv, fs):
    y = filtered(x_uv, fs)
    return np.sqrt(uniform_filter1d(y ** 2, max(1, int(0.25 * fs)), mode="nearest"))


def analyze(counts, fs=C.EMG_FS):
    """counts: (프레임, 2) 정수 코드. 채널(열)별 휴식 대비 힘줄 때 신호 비율을 계산해 출력한다."""
    out = []
    for col in range(counts.shape[1]):
        x = counts[:, col].astype(np.float64)
        sat = float(np.mean(np.abs(x) > 0.95 * FULL_SCALE)) * 100
        env = envelope(x * uv_per_count(column_gain(col)), fs)
        base = float(np.median(env[int(1 * fs):int((RELAX0 - 1) * fs)]))
        levels = []
        for k in range(REPEAT):
            s = RELAX0 + k * (CLENCH + REST)
            levels.append(float(np.median(env[int((s + 0.4) * fs):int((s + CLENCH) * fs)])))
        clench = float(np.median(levels))
        ratio = clench / base if base > 0 else float("inf")
        out.append(dict(col=col, base=base, clench=clench, ratio=ratio, sat=sat))
    return out


def report(res):
    lines = ["=== 결과 ==="]
    for r in res:
        if r["sat"] > 5:
            verdict = "신호가 한계값에 붙어 있음(포화): 전극 접촉/기준 전극 문제"
        elif r["ratio"] >= 3:
            verdict = "반응 확실"
        elif r["ratio"] >= 1.5:
            verdict = "약한 반응"
        else:
            verdict = "반응 없음"
        r["verdict"] = verdict
        lines.append(f"열 {r['col']}: 휴식 {r['base']:7.1f} uV | 힘줄 때 {r['clench']:7.1f} uV | 비율 {r['ratio']:5.1f}배 | 포화 {r['sat']:4.1f}% -> {verdict}")
    best = max(res, key=lambda r: r["ratio"] if r["sat"] <= 5 else 0)
    lines.append("")
    if best["ratio"] >= 1.5 and best["sat"] <= 5:
        lines.append(f"턱 신호는 열 {best['col']}에서 보입니다.")
        if best["col"] != C.CH_EMG:
            lines.append(f"-> config.py 에서 CH_EMG = {best['col']}, CH_EEG = {C.CH_EMG} 로 바꾸세요 (지금은 CH_EMG = {C.CH_EMG}).")
        else:
            lines.append("-> config.py 의 채널 배정(CH_EMG)과 같습니다. 그대로 쓰면 됩니다.")
    else:
        lines.append("뚜렷한 반응이 없습니다. 전극이 근육 위에 닿아 있는지, 터미널 연결, 피부 상태를 확인하고 다시 해보세요.")
    print("\n" + "\n".join(lines))
    return lines


def _font():
    try:
        import koreanize_matplotlib  # noqa: F401
    except ImportError:
        pass
    from matplotlib import font_manager as fm
    names = {f.name for f in fm.fontManager.ttflist}
    for n in ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR"]:
        if n in names:
            plt.rcParams["font.family"] = n
            break
    plt.rcParams["axes.unicode_minus"] = False


def plot(counts, res, path, fs=C.EMG_FS):
    """열별로 (회색) 필터링한 신호와 (파랑) 포락선을 그리고, 빨간 띠 = 이를 문 구간, 회색 띠 = 휴식 기준 구간."""
    _font()
    t = np.arange(len(counts)) / fs
    fig, ax = plt.subplots(counts.shape[1], 1, figsize=(13, 3.6 * counts.shape[1]), sharex=True)
    ax = np.atleast_1d(ax)
    for col, a in enumerate(ax):
        x = counts[:, col].astype(np.float64) * uv_per_count(column_gain(col))
        y = filtered(x, fs)
        env = envelope(x, fs)
        a.plot(t, y, color="0.65", lw=0.5, label="필터링한 신호 (20~450Hz)")
        a.plot(t, env, color="tab:blue", lw=1.6, label="포락선(0.25초 RMS)")
        a.axvspan(1, RELAX0 - 1, color="0.8", alpha=0.35, label="휴식 기준 구간")
        for k in range(REPEAT):
            s0 = RELAX0 + k * (CLENCH + REST)
            a.axvspan(s0, s0 + CLENCH, color="tab:red", alpha=0.18, label="이를 문 구간" if k == 0 else None)
        r = res[col]
        a.set_title(f"열 {col}: 휴식 {r['base']:.1f} uV -> 힘줄 때 {r['clench']:.1f} uV ({r['ratio']:.1f}배)  {r.get('verdict', '')}",
                    loc="left", fontsize=11)
        a.set_ylabel("uV")
        a.grid(alpha=0.25)
        a.legend(loc="upper right", fontsize=8)
    ax[-1].set_xlabel("시간 (초)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def finish(counts, outdir, save_raw=True):
    res = analyze(counts)
    lines = report(res)
    os.makedirs(outdir, exist_ok=True)
    if save_raw:
        np.save(os.path.join(outdir, "emg_counts.npy"), counts)
    with open(os.path.join(outdir, "result.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    png = os.path.join(outdir, "clench_result.png")
    plot(counts, res, png)
    print(f"\n저장 폴더: {os.path.abspath(outdir)}\n  emg_counts.npy (원본) / result.txt (결과 글) / clench_result.png (그래프)")
    try:
        os.startfile(png)            # Windows: 그래프 그림을 바로 연다
    except (AttributeError, OSError):
        print("그래프 파일을 직접 열어 보세요:", png)


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
    parser, chunks, frames = PacketParser(), [], 0
    cues = [(0, "시작! 가만히 계세요 (10초)")]
    for k in range(REPEAT):
        s = RELAX0 + k * (CLENCH + REST)
        cues += [(s, f"[{k + 1}/{REPEAT}] 지금! 이를 꽉 무세요 (2초)\a"), (s + CLENCH, "      힘 빼고 쉬세요")]
    cues.append((TOTAL, "끝"))
    ci = 0
    while frames < TOTAL * C.EMG_FS:
        for p in parser.feed(ser.read(4096)):
            chunks.append(p.emg)
            frames += len(p.emg)
        while ci < len(cues) and frames / C.EMG_FS >= cues[ci][0]:
            print(cues[ci][1], flush=True)
            ci += 1
    ser.close()
    print(f"수신 패킷 CRC 오류 {parser.bad_crc} | 순번 유실 {parser.lost}")
    return np.vstack(chunks)[:TOTAL * C.EMG_FS]


def selftest():
    rng = np.random.default_rng(1)
    n, fs = TOTAL * C.EMG_FS, C.EMG_FS
    counts = np.zeros((n, 2))
    t = np.arange(n) / fs
    base_uv = [rng.normal(0, 4, n), rng.normal(0, 4, n)]          # 휴식 잡음
    for k in range(REPEAT):
        s = RELAX0 + k * (CLENCH + REST)
        m = (t >= s + 0.2) & (t < s + CLENCH)
        base_uv[0][m] += rng.normal(0, 120, m.sum())              # 열 0에서만 근육 신호
    for c in range(2):
        counts[:, c] = np.round(base_uv[c] / uv_per_count(column_gain(c)))
    finish(counts, os.path.join("sessions", "clench_selftest"))


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if sys.argv[1] == "--selftest":
        return selftest()
    if sys.argv[1] == "--load":
        if len(sys.argv) < 3:
            raise SystemExit("사용:  python emg_clench_test.py --load sessions\\clench_날짜_시각")
        folder = sys.argv[2]
        return finish(np.load(os.path.join(folder, "emg_counts.npy")), folder, save_raw=False)
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else C.SERIAL_BAUD
    counts = acquire(sys.argv[1], baud)
    finish(counts, os.path.join("sessions", "clench_" + time.strftime("%Y%m%d_%H%M%S")))


if __name__ == "__main__":
    main()
