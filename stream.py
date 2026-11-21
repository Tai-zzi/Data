"""실시간(스트리밍) 처리 코어.
입력 조각(EMG/EEG 2채널 + 오디오)을 받을 때마다:
 필터링(인과 필터) -> 20Hz 특징 -> 이갈이/코골이 상태 판정 -> 자극 제어 -> 수면 단계(30초마다)
결과는 대시보드가 읽는 배열(proc.xxx)에 쌓인다."""
import numpy as np
from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, tf2sos, welch

import config as C
from detect import mains_notch_sos

HOP_S = C.AUDIO_HOP_S                       # 0.05 s -> 특징은 20Hz로 계산
EMG_HOP = int(round(C.EMG_FS * HOP_S))      # 홉당 EMG 샘플 수
AUD_HOP = int(round(C.AUDIO_FS * HOP_S))    # 홉당 오디오 샘플 수

STAGE_W, STAGE_R, STAGE_L, STAGE_D = 3, 2, 1, 0   # 값이 클수록 '깨어 있음' (수면 그래프 위쪽)
STAGE_LABEL = {3: "각성", 2: "REM", 1: "얕은 수면", 0: "깊은 수면"}


class CausalFilter:
    """청크 사이에 상태를 유지하는 실시간용 필터."""
    def __init__(self, sos):
        self.sos, self.zi = sos, None

    def __call__(self, x):
        if self.zi is None:
            self.zi = sosfilt_zi(self.sos) * x[0]
        y, self.zi = sosfilt(self.sos, x, zi=self.zi)
        return y


class WindowRMS:
    """홉별 평균제곱을 받아 최근 w홉 이동 RMS를 돌려준다 (실시간용 포락선)."""
    def __init__(self, w):
        self.w, self.hist = w, None

    def __call__(self, ms):
        if self.hist is None:
            self.hist = np.full(self.w - 1, ms[0])
        x = np.concatenate((self.hist, ms))
        self.hist = x[len(x) - (self.w - 1):]
        return np.sqrt(np.convolve(x, np.ones(self.w) / self.w, mode="valid"))


class RunDetector:
    """값이 임계값(기준선 x ratio)을 min_dur 이상 넘으면 '활동 중'으로 판정.
    hold 초 이상 조용해지면 종료. 기준선은 처음 init_s초(조용한 구간)로 학습."""
    def __init__(self, ratio, min_dur, hold):
        self.ratio, self.min_dur, self.hold = ratio, min_dur, hold
        self.n_init = int(C.BASELINE_INIT_S / HOP_S)
        self._init, self.baseline = [], None
        self.active, self.cand, self.last_above = False, None, None
        self.thr = np.nan

    def step(self, t, v):
        """반환: (활동중?, 방금 확정된 시작시각 or None, 방금 끝난 시각 or None)"""
        onset = end = None
        if self.baseline is None:
            self._init.append(v)
            if len(self._init) >= self.n_init:
                self.baseline = float(np.median(self._init))
            return False, None, None
        self.thr = self.baseline * self.ratio
        if v > self.thr:
            self.last_above = t
            if self.cand is None:
                self.cand = t
            if not self.active and t - self.cand >= self.min_dur:
                self.active, onset = True, self.cand
        else:
            if self.cand is not None and t - self.last_above >= self.hold:
                if self.active:
                    self.active, end = False, self.last_above
                self.cand = None
            if self.cand is None:                       # 조용할 때만 기준선 갱신
                self.baseline += C.BASELINE_ADAPT * (v - self.baseline)
        return self.active, onset, end


class SnoreDetector:
    """코골이 판정 (둘 중 하나면 ON):
     1) 규칙 반복: 소리 시작 간격이 SNORE_INTERVAL 안에 있는 것이 연속으로 SNORE_MIN_INTERVALS번 이어짐
        (예: 2번 = 소리 3번. 한두 번 난 소리가 우연히 규칙적인 경우를 걸러냄)
     2) 긴 소리: 큰 저주파 소리가 SNORE_SUSTAIN_S 이상 끊기지 않고 이어짐 (계속 드르르륵)
    마지막 코골이 소리 후 SNORE_HOLD_S 동안 없으면 OFF."""
    def __init__(self):
        self.pulse = RunDetector(C.SNORE_RATIO, C.SNORE_PULSE_MIN, 0.3)
        self.on, self.last_onset, self.last_seen, self.thr = False, None, None, np.nan
        self.chain, self.chain_start = 0, None

    def step(self, t, v):
        """반환: (코골이 상태, 이번에 ON으로 바뀌며 소급 표시할 시작시각 or None)"""
        active, onset, _ = self.pulse.step(t, v)
        self.thr = self.pulse.thr
        retro = None
        if onset is not None:
            lo, hi = C.SNORE_INTERVAL
            if self.last_onset is not None and lo <= onset - self.last_onset <= hi:
                if self.chain == 0:
                    self.chain_start = self.last_onset
                self.chain += 1
                if not self.on and self.chain >= C.SNORE_MIN_INTERVALS:   # 1) 규칙 반복
                    self.on, retro = True, self.chain_start
            else:
                self.chain = 0
            self.last_onset = onset
            if self.on:
                self.last_seen = onset
        if active and self.pulse.cand is not None and t - self.pulse.cand >= C.SNORE_SUSTAIN_S:
            if not self.on:                                  # 2) 긴 소리
                self.on, retro = True, self.pulse.cand
        if self.on and active:
            self.last_seen = t
        if self.on and t - self.last_seen > C.SNORE_HOLD_S:
            self.on, self.chain = False, 0
        return self.on, retro


class Stimulator:
    """하드웨어 연결 지점. 지금은 아무 출력도 하지 않는 가짜(mock).
    실제 장치에서는 이 함수 안에서 MCP4725 지령 전압을 쓰도록 구현한다."""
    def set_level(self, level, dur_s):
        pass


class StimController:
    """이갈이가 확정되면 낮은 단계부터 자극 -> 3초 안에 멈췄는지로 성공/실패 판정.
    실패하면 한 단계 올리고, 2번 연속 성공하면 한 단계 내려 '최소 유효 단계'를 찾는다."""
    def __init__(self, stimulator=None):
        self.hw = stimulator or Stimulator()
        self.level, self.streak = 1, 0
        self.state, self.t_end, self.t_ready = "idle", 0.0, 0.0
        self.pulse_level = 0
        self.pending, self.results, self.history = [], [], []
        self.counts = {lv: [0, 0] for lv in range(1, C.STIM_MAX_LEVEL + 1)}  # [시도, 성공]

    def _allowed(self, t):
        return sum(1 for h in self.history if t - h < 3600) < C.STIM_MAX_PER_HOUR

    def step(self, t, grind_active):
        """반환: 지금 자극 중이면 그 단계, 아니면 0"""
        if self.state == "idle":
            if grind_active and t >= self.t_ready and self._allowed(t):
                lvl = min(self.level, C.STIM_MAX_LEVEL)          # 절대 상한 보장
                self.hw.set_level(lvl, C.STIM_PULSE_S)
                self.pending.append((t, lvl))
                self.history.append(t)
                self.pulse_level, self.state = lvl, "pulse"
                self.t_end = t + C.STIM_PULSE_S
        elif self.state == "pulse":
            if t >= self.t_end:
                self.state, self.t_end = "wait", t + C.STIM_RESPONSE_WINDOW_S
        elif self.state == "wait":
            if t >= self.t_end:
                ok = not grind_active
                self.results.append((t, ok, self.pulse_level))
                self.counts[self.pulse_level][0] += 1
                self.counts[self.pulse_level][1] += int(ok)
                if ok:
                    self.streak += 1
                    if self.streak >= 2 and self.level > 1:
                        self.level, self.streak = self.level - 1, 0
                else:
                    self.level, self.streak = min(self.level + 1, C.STIM_MAX_LEVEL), 0
                self.state, self.t_ready = "idle", t + C.STIM_COOLDOWN_S
        return self.pulse_level if self.state == "pulse" else 0


def classify_epoch(x, fs, low_only=False):
    """30초 EEG -> 수면 단계. 대역 파워 비율 규칙: 알파↑=각성, 델타↑=깊은 수면, 베타↑=REM, 나머지=얕은 수면.
    low_only=True: 근전도가 섞인 에폭용. 근전도에 덮이는 베타를 빼고 0.5~15 Hz 안의 비율로만 각성/깊은/얕은 판정 (REM 판정 안 함)."""
    f, p = welch(x, fs, nperseg=int(4 * fs))
    tot = p[(f >= 0.5) & (f < (15 if low_only else 30))].sum()
    rel = {k: p[(f >= lo) & (f < hi)].sum() / tot for k, (lo, hi) in C.BANDS.items()}
    if low_only:
        rel["beta"] = float("nan")
        if rel["alpha"] > C.WAKE_ALPHA_REL:
            return STAGE_W, rel
        return (STAGE_D if rel["delta"] > C.DEEP_DELTA_REL else STAGE_L), rel
    if rel["alpha"] > C.WAKE_ALPHA_REL:
        return STAGE_W, rel
    if rel["delta"] > C.DEEP_DELTA_REL:
        return STAGE_D, rel
    if rel["beta"] > C.REM_BETA_REL:
        return STAGE_R, rel
    return STAGE_L, rel


class StreamProcessor:
    def __init__(self, stimulator=None, capacity_hours=10):
        fs, afs = C.EMG_FS, C.AUDIO_FS
        self.f_eeg = CausalFilter(butter(2, C.EEG_BAND, "bandpass", fs=fs, output="sos"))
        band = C.EMG_BAND_SHARED if C.SINGLE_CHANNEL else C.EMG_BAND     # 한 채널 공유면 뇌파 대역과 겹치지 않게
        self.f_emg = CausalFilter(butter(4, band, "bandpass", fs=fs, output="sos"))
        self.f_notch = CausalFilter(mains_notch_sos(fs, C.EMG_BAND[1]))      # 60Hz + 배수
        self.f_aud = CausalFilter(butter(4, C.AUDIO_BAND, "bandpass", fs=afs, output="sos"))
        self.env_emg = WindowRMS(int(round(C.EMG_WIN_S / HOP_S)))
        self.env_aud = WindowRMS(int(round(C.AUDIO_FRAME_S / HOP_S)))
        self.grind = RunDetector(C.EMG_RATIO, C.GRIND_MIN_DUR, C.GRIND_MERGE_GAP)
        self.snore = SnoreDetector()
        self.stim = StimController(stimulator)

        cap = int(capacity_hours * 3600 / HOP_S)
        self.n = 0
        self.emg_peak = np.zeros(cap, np.float32)
        self.emg_env = np.zeros(cap, np.float32)
        self.emg_thr = np.full(cap, np.nan, np.float32)
        self.emg_flag = np.zeros(cap, bool)
        self.aud_env = np.zeros(cap, np.float32)
        self.aud_thr = np.full(cap, np.nan, np.float32)
        self.snore_flag = np.zeros(cap, bool)
        self.stim_level = np.zeros(cap, np.int8)
        self.stage_t, self.stage_v = [], []        # 에폭 시작 시각(초), 단계
        self.stage_artifact = []                   # True = 근전도가 많아 판정 불가(직전 단계 유지)
        self._eeg_chunks, self._eeg_n, self._epoch_i = [], 0, 0
        self.grind_events, self.snore_episodes = [], []
        self.grind_onsets = []                     # (확정 시각, 시작 시각): 이갈이가 '지금' 확정된 순간 (자극 요청 판단용)
        self._g_start = self._s_start = 0.0

    @property
    def now(self):
        return self.n * HOP_S

    def process(self, emg2, audio):
        """emg2: (n,2) ch0=EEG, ch1=턱 EMG  /  audio: (m,)  (홉 길이의 정수배)"""
        K = len(audio) // AUD_HOP
        emg2 = np.asarray(emg2, float)[:K * EMG_HOP]
        audio = np.asarray(audio, float)[:K * AUD_HOP]
        eeg = self.f_eeg(emg2[:, 0])
        emg = self.f_notch(self.f_emg(emg2[:, 1]))
        aud = self.f_aud(audio)
        self._eeg_chunks.append(eeg)
        self._eeg_n += len(eeg)

        eh = emg.reshape(K, EMG_HOP)
        peak = eh[np.arange(K), np.abs(eh).argmax(axis=1)]       # 부호 있는 최대값 -> 위아래로 흔들리는 파형
        env_e = self.env_emg((eh ** 2).mean(axis=1))
        env_a = self.env_aud((aud.reshape(K, AUD_HOP) ** 2).mean(axis=1))
        for k in range(K):
            self._step(peak[k], env_e[k], env_a[k])
        self._epochs()

    def _step(self, peak, env_e, env_a):
        i, t = self.n, self.n * HOP_S
        g_on, g_start, g_end = self.grind.step(t, env_e)
        if g_start is not None:
            self.emg_flag[int(round(g_start / HOP_S)):i + 1] = True
        if g_start is not None:
            self._g_start = g_start
            self.grind_onsets.append((t, g_start))
        if g_end is not None:
            self.grind_events.append((self._g_start, g_end))
        s_on, s_retro = self.snore.step(t, env_a)
        if s_retro is not None:
            self.snore_flag[int(round(s_retro / HOP_S)):i + 1] = True
            self._s_start = s_retro
        if i > 0 and self.snore_flag[i - 1] and not s_on:
            self.snore_episodes.append((self._s_start, t))
        self.emg_peak[i], self.emg_env[i], self.emg_thr[i] = peak, env_e, self.grind.thr
        self.emg_flag[i] = g_on
        self.aud_env[i], self.aud_thr[i] = env_a, self.snore.thr
        self.snore_flag[i] = s_on
        self.stim_level[i] = self.stim.step(t, g_on)
        self.n += 1

    def _epochs(self):
        n_ep = int(C.EPOCH_S * C.EMG_FS)
        while self._eeg_n >= n_ep:
            x = np.concatenate(self._eeg_chunks)
            ep, rest = x[:n_ep], x[n_ep:]
            self._eeg_chunks, self._eeg_n = [rest], len(rest)
            stage, _ = classify_epoch(ep, C.EMG_FS)
            # 전극 한 쌍으로 뇌파+근전도를 같이 보면, 이를 가는 동안 근전도(20~35 Hz)가 뇌파의 베타 대역을 덮는다.
            # 그런 에폭은 베타를 빼고 저주파(0.5~15 Hz)만으로 판정한다 (REM은 판정 안 함, stage_artifact=True로 표시).
            a = int(round(self._epoch_i * C.EPOCH_S / HOP_S))
            b = min(int(round((self._epoch_i + 1) * C.EPOCH_S / HOP_S)), self.n)
            frac = float(self.emg_flag[a:b].mean()) if b > a else 0.0
            bad = frac > C.EPOCH_EMG_MAX_FRAC
            if bad:
                stage, _ = classify_epoch(ep, C.EMG_FS, low_only=True)
            self.stage_t.append(self._epoch_i * C.EPOCH_S)
            self.stage_v.append(stage)
            self.stage_artifact.append(bad)
            self._epoch_i += 1

    def current_stage(self):
        """가장 최근 에폭의 수면 단계 (아직 없으면 None)"""
        return self.stage_v[-1] if self.stage_v else None
