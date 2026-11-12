"""가짜(합성) 데이터 생성기: EMG + 오디오 + 정답 라벨.
장치 없이 감지/평가 코드를 개발하기 위한 용도. 방해 요소(삼킴, 기침)도 섞어 둠."""
import numpy as np
from scipy.signal import butter, sosfilt
from scipy.signal.windows import tukey

from config import AUDIO_FS, EMG_FS

PULSE_S = 1.5    # 코골이 1회(들숨) 길이
PERIOD_S = 4.0   # 호흡 주기


def _place(rng, n, duration, dur_range, margin=10.0, gap=5.0):
    events, tries = [], 0
    while len(events) < n and tries < 20000:
        tries += 1
        d = rng.uniform(*dur_range)
        s = rng.uniform(margin, duration - margin - d)
        e = s + d
        if all(e + gap <= a or s >= b + gap for a, b in events):
            events.append((s, e))
    return sorted(events)


def _emg_burst(rng, m, fs, gain, phasic=False):
    sos = butter(4, [30, 250], "bandpass", fs=fs, output="sos")
    b = sosfilt(sos, rng.normal(0, 1, m))
    b /= b.std()
    b *= tukey(m, 0.2)
    if phasic:  # 이갈이는 약 1~1.5Hz로 세기가 오르내림
        tt = np.arange(m) / fs
        b *= 0.8 + 0.2 * np.sin(2 * np.pi * rng.uniform(1.0, 1.5) * tt)
    return gain * b


def make_emg(duration, rng, grind_events, artifact_times, fs=EMG_FS):
    n = int(duration * fs)
    t = np.arange(n) / fs
    x = rng.normal(0, 1, n)                                   # 기본 노이즈
    x += 2.0 * np.sin(2 * np.pi * 60 * t + rng.uniform(0, 6.28))  # 전원 잡음
    x += 5.0 * np.sin(2 * np.pi * 0.3 * t)                    # 느린 드리프트
    for s, e in grind_events:
        i0, m = int(s * fs), int((e - s) * fs)
        x[i0:i0 + m] += _emg_burst(rng, m, fs, rng.uniform(6, 10), phasic=True)
    for ts in artifact_times:                                 # 삼킴/움직임 (짧음)
        i0, m = int(ts * fs), int(0.3 * fs)
        x[i0:i0 + m] += _emg_burst(rng, m, fs, 6.0)
    return x.astype(np.float32)


def make_audio(duration, rng, snore_events, cough_times, fs=AUDIO_FS):
    n = int(duration * fs)
    x = rng.normal(0, 0.005, n)
    actual = []
    for s, e in snore_events:
        f0 = rng.uniform(90, 140)
        n_pulse = int((e - s - PULSE_S) // PERIOD_S) + 1
        m = int(PULSE_S * fs)
        tt = np.arange(m) / fs
        wave = sum(np.sin(2 * np.pi * k * f0 * tt) / k for k in range(1, 9))
        wave = wave / np.abs(wave).max() * np.hanning(m) * 0.2
        for p in range(n_pulse):
            i0 = int((s + p * PERIOD_S) * fs)
            x[i0:i0 + m] += wave + rng.normal(0, 0.01, m) * np.hanning(m)
        actual.append((s, s + (n_pulse - 1) * PERIOD_S + PULSE_S))
    for tc in cough_times:                                    # 기침/잡음 (짧고 광대역)
        i0, m = int(tc * fs), int(0.3 * fs)
        x[i0:i0 + m] += rng.normal(0, 0.3, m) * tukey(m, 0.5)
    return x.astype(np.float32), actual


def generate_session(duration=600, seed=0, n_grind=12, n_snore=6,
                     n_artifact=15, n_cough=10):
    """반환: dict(emg, audio, labels=[(start, end, kind)], emg_fs, audio_fs)"""
    rng = np.random.default_rng(seed)
    grind = _place(rng, n_grind, duration, (2.0, 8.0))
    snore = _place(rng, n_snore, duration, (10.0, 30.0))
    art = rng.uniform(5, duration - 5, n_artifact)
    cough = rng.uniform(5, duration - 5, n_cough)
    emg = make_emg(duration, rng, grind, art)
    audio, snore_actual = make_audio(duration, rng, snore, cough)
    labels = [(s, e, "grind") for s, e in grind] + [(s, e, "snore") for s, e in snore_actual]
    labels.sort()
    return dict(emg=emg, audio=audio, labels=labels, emg_fs=EMG_FS, audio_fs=AUDIO_FS)
