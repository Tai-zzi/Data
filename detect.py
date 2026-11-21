"""신호처리 + 규칙 기반 감지 (임계값 방식). 입력이 합성이든 실측이든 동일하게 동작."""
import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, iirnotch, sosfiltfilt, tf2sos

import config as C


def find_events(env, fs_env, thr, min_dur, merge_gap):
    """포락선이 thr 이상인 구간 -> [(start_s, end_s)]. 가까운 구간은 합치고 짧은 구간은 버린다."""
    above = np.concatenate(([0], (env > thr).astype(np.int8), [0]))
    d = np.diff(above)
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    runs = [[s / fs_env, e / fs_env] for s, e in zip(starts, ends)]
    merged = []
    for s, e in runs:
        if merged and s - merged[-1][1] <= merge_gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if e - s >= min_dur]


def mains_notch_sos(fs, f_max=None):
    """전원 잡음 60Hz와 그 배수(120, 180, ...)를 모두 깎는 노치 필터 묶음 (sos).
    실측(2026-11-16): 60Hz만 깎으면 배수 잡음에 묻혀 이를 물어도 1.3배, 배수까지 깎으면 5배 이상 차이."""
    top = min(f_max or fs / 2, fs / 2) - 5
    return np.vstack([tf2sos(*iirnotch(C.MAINS_HZ * k, Q=30, fs=fs))
                      for k in range(1, int(top // C.MAINS_HZ) + 1)])


def emg_envelope(x, fs=C.EMG_FS):
    x = np.asarray(x, dtype=np.float64)
    sos = butter(4, C.EMG_BAND, "bandpass", fs=fs, output="sos")
    x = sosfiltfilt(sos, x)
    x = sosfiltfilt(mains_notch_sos(fs, C.EMG_BAND[1]), x)
    win = max(1, int(C.EMG_WIN_S * fs))
    return np.sqrt(uniform_filter1d(x ** 2, win, mode="nearest"))   # 이동 RMS


def detect_grind(emg, fs=C.EMG_FS):
    env = emg_envelope(emg, fs)
    thr = np.median(env) * C.EMG_RATIO
    ev = find_events(env, fs, thr, C.GRIND_MIN_DUR, C.GRIND_MERGE_GAP)
    return ev, env, thr, fs


def audio_envelope(x, fs=C.AUDIO_FS):
    x = np.asarray(x, dtype=np.float64)
    sos = butter(4, C.AUDIO_BAND, "bandpass", fs=fs, output="sos")
    x = sosfiltfilt(sos, x)
    frame, hop = int(C.AUDIO_FRAME_S * fs), int(C.AUDIO_HOP_S * fs)
    cs = np.concatenate(([0.0], np.cumsum(x ** 2)))
    idx = np.arange(0, len(x) - frame + 1, hop)
    env = np.sqrt((cs[idx + frame] - cs[idx]) / frame)               # 프레임 RMS
    return env, 1.0 / C.AUDIO_HOP_S


def detect_snore(audio, fs=C.AUDIO_FS):
    env, fs_env = audio_envelope(audio, fs)
    thr = np.median(env) * C.SNORE_RATIO
    ev = find_events(env, fs_env, thr, C.SNORE_MIN_DUR, C.SNORE_MERGE_GAP)
    return ev, env, thr, fs_env


def snore_gaps(snore_events):
    """코골이 이벤트 사이의 무음 구간(초) -> B/C안의 '무호흡 의심 구간' 후보 계산용 (의료 진단 아님)."""
    return [(a[1], b[0]) for a, b in zip(snore_events, snore_events[1:])]
