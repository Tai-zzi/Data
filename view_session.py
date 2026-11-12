"""저장된 측정 폴더(sessions\\날짜_시각)를 다시 열어 그래프와 숫자로 확인한다. (다른 파일 없이 이 파일 하나로 실행됨)
사용:  python view_session.py "세션폴더 경로"
예:    python view_session.py "C:\\Users\\xskrl\\OneDrive\\바탕 화면\\CoGoalTis\\sessions\\20261019_042023"
하는 일: eeg.npy, emg.npy(이미 uV 단위)와 audio.wav를 읽어서
  - 채널별 신호와 포락선(0.25초 RMS) 그래프를 view_session.png 로 저장하고 자동으로 연다
  - 평소 대비 3배 넘게 커진 구간(이갈이 후보) 목록, 소리가 들어왔는지 요약을 화면에 출력한다
주의: 어느 채널이 턱 신호인지는 그때의 config 설정에 따라 달라서, 두 채널을 모두 보여준다.
필요: numpy, scipy, matplotlib (이미 설치돼 있음)"""
import json
import os
import sys
import wave

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, iirnotch, sosfiltfilt, tf2sos


def font():
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


def envelope(x, fs):
    sos = butter(4, [20, min(450, fs / 2 - 5)], "bandpass", fs=fs, output="sos")
    y = sosfiltfilt(sos, x)
    b, a = iirnotch(60, Q=30, fs=fs)
    y = sosfiltfilt(tf2sos(b, a), y)
    return y, np.sqrt(uniform_filter1d(y ** 2, max(1, int(0.25 * fs)), mode="nearest"))


def bursts(env, fs, ratio=3.0, min_dur=0.8, gap=1.0):
    base = float(np.median(env))
    above = np.concatenate(([0], (env > base * ratio).astype(np.int8), [0]))
    d = np.diff(above)
    runs = [[s / fs, e / fs] for s, e in zip(np.where(d == 1)[0], np.where(d == -1)[0])]
    merged = []
    for s, e in runs:
        if merged and s - merged[-1][1] <= gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return base, [(s, e) for s, e in merged if e - s >= min_dur]


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    folder = sys.argv[1]
    try:
        eeg = np.load(os.path.join(folder, "eeg.npy")).astype(np.float64)
        emg = np.load(os.path.join(folder, "emg.npy")).astype(np.float64)
    except OSError as e:
        raise SystemExit(f"파일을 열 수 없습니다: {e}\n경로를 확인하거나, 폴더를 C:\\ 아래(영어 경로)로 복사해서 다시 해보세요.")
    fs = 1000
    mp = os.path.join(folder, "meta.json")
    if os.path.exists(mp):
        meta = json.load(open(mp, encoding="utf-8"))
        fs = int(meta.get("emg_fs", 1000))
        print("기록 시각:", meta.get("start_wall", "?"), "| 길이(초):", meta.get("duration_s", "?"))
    dur = len(emg) / fs
    print(f"데이터 길이 {dur:.1f}초 ({len(emg)}샘플, {fs}Hz)")

    font()
    fig, ax = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    t = np.arange(len(emg)) / fs
    for k, (name, x) in enumerate([("emg.npy (그때 설정의 '턱 EMG' 열)", emg), ("eeg.npy (나머지 열)", eeg)]):
        y, env = envelope(x, fs)
        base, ev = bursts(env, fs)
        p95 = float(np.percentile(env, 95))
        ax[k].plot(t, y, color="0.7", lw=0.5, label="필터링한 신호 (20~450Hz)")
        ax[k].plot(t, env, color="tab:blue", lw=1.5, label="포락선(0.25초 RMS)")
        ax[k].axhline(base * 3, color="r", ls="--", lw=0.8, label="평소의 3배 선")
        for s, e in ev:
            ax[k].axvspan(s, e, color="tab:red", alpha=0.2)
        ax[k].set_title(f"{name}: 평소 {base:.1f} uV | 상위5% {p95:.1f} uV | 커진 구간 {len(ev)}개", loc="left", fontsize=11)
        ax[k].set_ylabel("uV")
        ax[k].grid(alpha=0.25)
        ax[k].legend(loc="upper right", fontsize=8)
        print(f"\n[{name}] 평소 {base:.1f} uV, 상위5% {p95:.1f} uV (비율 {p95 / base if base else 0:.1f}배), 3배 넘게 커진 구간 {len(ev)}개")
        for s, e in ev[:15]:
            print(f"   {s:6.1f}초 ~ {e:6.1f}초  ({e - s:.1f}초)")
    ax[-1].set_xlabel("시간 (초)")
    fig.tight_layout()

    wp = os.path.join(folder, "audio.wav")
    if os.path.exists(wp):
        with wave.open(wp, "rb") as w:
            a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)
            wfs = w.getframerate()
        print(f"\n[소리 audio.wav] 길이 {len(a) / wfs:.1f}초, 최대 {np.max(np.abs(a)):.0f}, 평균크기 {np.mean(np.abs(a)):.1f}")
        print("   -> 소리가 전혀 안 들어왔습니다 (모두 0: 마이크 미연결이거나 펌웨어가 소리를 안 보냄)" if np.max(np.abs(a)) == 0
              else "   -> 소리가 들어온 흔적이 있습니다")

    png = os.path.join(folder, "view_session.png")
    try:
        fig.savefig(png, dpi=110)
    except OSError:
        png = "view_session.png"
        fig.savefig(png, dpi=110)
    print("\n그래프 저장:", os.path.abspath(png))
    try:
        os.startfile(png)
    except (AttributeError, OSError):
        print("그래프 파일을 직접 열어 보세요.")


if __name__ == "__main__":
    main()
