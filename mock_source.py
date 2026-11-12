"""가짜 '밤' 생성기 (실시간 스트림처럼 조각 단위로 데이터를 내보냄).
- 채널0: EEG(수면 단계별 뇌파 모사), 채널1: 턱 EMG(이갈이 포함), 오디오: 코골이 포함
- 자극을 주면 일정 확률로 이갈이가 멈추는 '반응'도 흉내 낸다 (단계가 높을수록 확률 높음)
실제 장치가 준비되면 이 클래스 대신 시리얼 소스를 끼우면 된다 (read/apply_stimulus 인터페이스 동일)."""
import numpy as np
from scipy.signal import butter, sosfilt

import config as C
from stream import AUD_HOP, EMG_HOP, HOP_S, STAGE_D, STAGE_L, STAGE_R, STAGE_W

W, R, L, D = STAGE_W, STAGE_R, STAGE_L, STAGE_D
PATTERN = [(W, 3), (L, 4), (D, 5), (L, 3), (R, 3), (L, 3), (D, 3), (L, 3),
           (W, 1), (L, 3), (R, 4), (L, 3), (W, 2)]            # 합계 40에폭 = 20분
EEG_AMP = {W: dict(delta=5, theta=5, alpha=25, beta=12),
           L: dict(delta=10, theta=20, alpha=6, beta=5),
           D: dict(delta=50, theta=8, alpha=3, beta=2),
           R: dict(delta=6, theta=12, alpha=6, beta=10)}
P_STOP = {1: 0.15, 2: 0.35, 3: 0.60, 4: 0.80, 5: 0.90}      # 자극 단계별 '이갈이 멈춤' 확률(가정)
PULSE_S, PERIOD_S = 1.5, 4.0


class _BandNoise:
    def __init__(self, rng, lo, hi, fs):
        self.rng = rng
        self.sos = butter(4, [lo, hi], "bandpass", fs=fs, output="sos")
        y, self.zi = sosfilt(self.sos, rng.normal(0, 1, int(fs * 20)),
                             zi=np.zeros((self.sos.shape[0], 2)))
        self.scale = y[int(fs * 2):].std()

    def __call__(self, n):
        y, self.zi = sosfilt(self.sos, self.rng.normal(0, 1, n), zi=self.zi)
        return y / self.scale


class MockNight:
    def __init__(self, minutes=20, seed=0):
        self.rng = rng = np.random.default_rng(seed)
        self.total = minutes * 60.0
        n_ep = int(self.total // C.EPOCH_S)
        st = []
        while len(st) < n_ep:
            for s, k in PATTERN:
                st += [s] * k
        self.stages = np.array(st[:n_ep])
        self.t = 0.0
        self.noise = {b: _BandNoise(rng, lo, hi, C.EMG_FS) for b, (lo, hi) in C.BANDS.items()}
        self.emg_noise = _BandNoise(rng, 30, 250, C.EMG_FS)

        self.grind, self.snore = [], []                       # 정답(truth)
        last_g = last_s = 0.0
        for e, s in enumerate(self.stages):
            t0 = e * C.EPOCH_S
            if t0 < 40:
                continue
            if s == L and rng.random() < 0.7:                  # 이갈이는 얕은 수면에서
                for _ in range(int(rng.integers(1, 3))):
                    st_ = max(last_g + 6, t0 + rng.uniform(0, 12))
                    dur = rng.uniform(6, 14)
                    if st_ + dur < self.total - 5:
                        self.grind.append(dict(s=st_, e=st_ + dur, G=rng.uniform(6, 10),
                                               f=rng.uniform(1.0, 1.5), stopped=False))
                        last_g = st_ + dur
            if s in (L, D, R) and rng.random() < 0.5:         # 코골이
                st_ = max(last_s + 6, t0 + rng.uniform(0, 5))
                n_p = int(rng.integers(3, 8))
                if st_ + (n_p - 1) * PERIOD_S + PULSE_S < self.total - 5:
                    self.snore.append(dict(s=st_, n=n_p, f0=rng.uniform(90, 140)))
                    last_s = st_ + (n_p - 1) * PERIOD_S + PULSE_S
        self.artifacts = list(rng.uniform(25, self.total - 5, int(self.total / 40)))   # 삼킴(짧은 EMG)
        self.coughs = list(rng.uniform(25, self.total - 5, int(self.total / 60)))      # 기침(짧은 소음)

    @property
    def finished(self):
        return self.t >= self.total - 1e-9

    def read(self, K):
        K = min(K, int(round((self.total - self.t) / HOP_S)))
        if K <= 0:
            return None
        rng = self.rng
        ne, na = K * EMG_HOP, K * AUD_HOP
        te = self.t + np.arange(ne) / C.EMG_FS
        ta = self.t + np.arange(na) / C.AUDIO_FS

        # 채널0: EEG
        stage = self.stages[np.minimum((te // C.EPOCH_S).astype(int), len(self.stages) - 1)]
        eeg = rng.normal(0, 2, ne) + 3 * np.sin(2 * np.pi * 60 * te) + 20 * np.sin(2 * np.pi * 0.05 * te)
        for b in C.BANDS:
            amp = np.array([EEG_AMP[c][b] for c in (D, L, R, W)])[stage]   # 코드 0..3 -> D,L,R,W 순
            eeg += amp * self.noise[b](ne)

        # 채널1: 턱 EMG
        emg = rng.normal(0, 1, ne) + 2 * np.sin(2 * np.pi * 60 * te) + 5 * np.sin(2 * np.pi * 0.3 * te)
        amp = np.zeros(ne)
        for g in self.grind:
            if g["e"] + 0.2 < te[0] or g["s"] > te[-1]:
                continue
            ramp = np.clip((te - g["s"]) / 0.15, 0, 1) * np.clip((g["e"] - te) / 0.15, 0, 1)
            amp += g["G"] * ramp * (0.8 + 0.2 * np.sin(2 * np.pi * g["f"] * (te - g["s"])))
        for ts in self.artifacts:
            m = (te >= ts) & (te < ts + 0.3)
            amp[m] += 6 * 0.5 * (1 - np.cos(2 * np.pi * (te[m] - ts) / 0.3))
        emg += amp * self.emg_noise(ne)

        # 오디오: 코골이 + 기침
        aud = rng.normal(0, 0.005, na)
        for sn in self.snore:
            for p in range(sn["n"]):
                ps = sn["s"] + p * PERIOD_S
                m = (ta >= ps) & (ta < ps + PULSE_S)
                if m.any():
                    tl = ta[m] - ps
                    wave = sum(np.sin(2 * np.pi * k * sn["f0"] * tl) / k for k in range(1, 9)) / 2.0
                    hann = np.hanning(int(PULSE_S * C.AUDIO_FS))
                    idx = np.clip(((tl / PULSE_S) * (len(hann) - 1)).astype(int), 0, len(hann) - 1)
                    aud[m] += 0.2 * wave * hann[idx] + rng.normal(0, 0.01, m.sum()) * hann[idx]
        for tc in self.coughs:
            m = (ta >= tc) & (ta < tc + 0.3)
            if m.any():
                aud[m] += rng.normal(0, 0.3, m.sum()) * 0.5 * (1 - np.cos(2 * np.pi * (ta[m] - tc) / 0.3))

        self.t += K * HOP_S
        return np.column_stack((eeg, emg)).astype(np.float32), aud.astype(np.float32)

    def apply_stimulus(self, t, level):
        """자극이 들어가면 진행 중인 이갈이가 확률적으로 곧 멈춘다 (이미 생성된 과거는 못 바꾸므로 현재 시각 이후부터)."""
        for g in self.grind:
            if g["s"] <= t < g["e"] and self.rng.random() < P_STOP[level]:
                g["e"] = min(g["e"], max(self.t, t + self.rng.uniform(0.3, 1.0)))
                g["stopped"] = True
