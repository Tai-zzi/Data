"""가짜 ESP32: MockNight 데이터를 실제와 똑같은 바이너리 패킷으로 만들어 '시리얼처럼' 흘려준다.
장치 없이 SerialSource -> 로거 -> 대시보드 전체 경로를 시험하는 용도.
 speed=None 이면 즉시 끝까지 생성(테스트용), 숫자면 그 배속으로 실시간 흉내
 drop/corrupt: 패킷 유실/손상 확률 (복구 동작 시험용)"""
import time

import numpy as np

import config as C
from mock_source import MockNight
from packet import pack
from serial_source import uv_per_count
from stream import HOP_S


class FakeLink:
    def __init__(self, minutes=5, seed=0, speed=1.0, with_audio=True, drop=0.0, corrupt=0.0):
        self.night = MockNight(minutes, seed)
        self.speed, self.with_audio, self.drop, self.corrupt = speed, with_audio, drop, corrupt
        self.rng = np.random.default_rng(seed + 1000)
        self._seq, self._made, self._t0 = 0, 0, None

    @property
    def finished(self):
        return self.night.finished

    def read_bytes(self):
        if self.night.finished:
            return b""
        if self.speed is None:
            due = 200
        else:
            if self._t0 is None:
                self._t0 = time.perf_counter()
            due = min(int((time.perf_counter() - self._t0) * self.speed / HOP_S) - self._made, 400)
        out = bytearray()
        for _ in range(max(due, 0)):
            r = self.night.read(1)                       # 1홉 = EMG 50프레임 + 오디오 800샘플
            if r is None:
                break
            self._made += 1
            emg2, aud = r                                # (50,2): [EEG, EMG] µV
            counts = np.zeros((len(emg2), 2), np.int64)
            if C.CH_EEG == C.CH_EMG:                      # 전극 한 쌍: 뇌파와 근전도가 한 채널에 섞여 들어옴
                counts[:, C.CH_EMG] = np.round((emg2[:, 0] + emg2[:, 1]) / uv_per_count(C.ADS_GAIN_EMG))
            else:
                counts[:, C.CH_EEG] = np.round(emg2[:, 0] / uv_per_count(C.ADS_GAIN_EEG))
                counts[:, C.CH_EMG] = np.round(emg2[:, 1] / uv_per_count(C.ADS_GAIN_EMG))
            counts = np.clip(counts, -2 ** 23, 2 ** 23 - 1)
            a16 = (np.clip(np.round(aud * 32768), -32768, 32767).astype(np.int16)
                   if self.with_audio else np.zeros(0, np.int16))
            t_us = int((self.night.t - HOP_S) * 1e6)
            pkt = pack(self._seq, t_us, counts, a16)
            self._seq += 1
            if self.rng.random() < self.drop:
                continue
            if self.rng.random() < self.corrupt:
                pkt = pkt[:30] + b"\x00" + pkt[31:]
            out += pkt
        return bytes(out)

    def on_stimulus(self, t, level):
        self.night.apply_stimulus(t, level)

    def close(self):
        pass
