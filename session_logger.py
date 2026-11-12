"""측정 중 원본 신호를 계속 파일에 기록한다 (프로그램이 꺼져도 데이터가 남도록 바로바로 씀).

세션 폴더 구조 (sessions/20261020_231500/):
  emg_raw.f32   측정 중 계속 이어 쓰는 원본 [EEG, 턱EMG] float32 (µV)   <- 임시 파일 (변환 성공하면 삭제)
  audio_raw.i16 측정 중 계속 이어 쓰는 원본 오디오 int16               <- 임시 파일 (변환 성공하면 삭제)
  events.log    패킷 유실/CRC 오류/시작·종료 기록
  meta.json     샘플링 속도, 채널 배정, 스케일, 측정 통계
종료 시 자동 변환:
  eeg.npy, emg.npy(턱), audio.wav, labels.csv(빈 라벨)  -> run_demo.py --data 로 바로 분석 가능
비정상 종료 시 복구:  python session_logger.py finalize sessions/폴더명"""
import json
import os
import sys
import time
import wave

import numpy as np

import config as C


class SessionLogger:
    def __init__(self, root="sessions", meta=None):
        self.dir = os.path.join(root, time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(self.dir, exist_ok=True)
        self._emg = open(os.path.join(self.dir, "emg_raw.f32"), "wb")
        self._aud = open(os.path.join(self.dir, "audio_raw.i16"), "wb")
        self._log = open(os.path.join(self.dir, "events.log"), "w", encoding="utf-8", buffering=1)
        self.meta = dict(meta or {})
        self.meta.update(emg_fs=C.EMG_FS, audio_fs=C.AUDIO_FS,
                         start_wall=time.strftime("%Y-%m-%d %H:%M:%S"))
        self._save_meta()
        self.n_emg = self.n_aud = 0
        self._flush_t = time.time()
        self.event("측정 시작")

    def _save_meta(self):
        with open(os.path.join(self.dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(self.meta, f, ensure_ascii=False, indent=2)

    def event(self, text):
        self._log.write(f"[{self.n_emg / C.EMG_FS:9.2f}s] {text}\n")

    def write(self, emg_uv, audio_i16):
        np.asarray(emg_uv, "<f4").tofile(self._emg)
        np.asarray(audio_i16, "<i2").tofile(self._aud)
        self.n_emg += len(emg_uv)
        self.n_aud += len(audio_i16)
        if time.time() - self._flush_t > 2.0:      # 2초마다 디스크에 확정
            self._emg.flush(); self._aud.flush()
            self._flush_t = time.time()

    def close(self, extra=None):
        self.event("측정 종료")
        self._emg.close(); self._aud.close(); self._log.close()
        self.meta.update(extra or {})
        self.meta.update(n_emg_samples=self.n_emg, n_audio_samples=self.n_aud,
                         duration_s=round(self.n_emg / C.EMG_FS, 2))
        self._save_meta()
        finalize_session(self.dir)
        return self.dir


def finalize_session(d):
    """임시 원본 파일을 분석용 형식(eeg.npy, emg.npy, audio.wav, labels.csv)으로 변환."""
    mp = os.path.join(d, "meta.json")
    meta = json.load(open(mp, encoding="utf-8")) if os.path.exists(mp) else {}
    audio_fs = meta.get("audio_fs", C.AUDIO_FS)

    raw = np.fromfile(os.path.join(d, "emg_raw.f32"), dtype="<f4")
    raw = raw[: len(raw) // 2 * 2].reshape(-1, 2)
    np.save(os.path.join(d, "eeg.npy"), raw[:, 0])
    np.save(os.path.join(d, "emg.npy"), raw[:, 1])

    src = os.path.join(d, "audio_raw.i16")
    with wave.open(os.path.join(d, "audio.wav"), "wb") as w, open(src, "rb") as f:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(audio_fs)
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            w.writeframes(chunk[: len(chunk) // 2 * 2])

    lp = os.path.join(d, "labels.csv")
    if not os.path.exists(lp):
        with open(lp, "w", encoding="utf-8") as f:
            f.write("start_s,end_s,kind\n")
    meta.setdefault("emg_fs", C.EMG_FS)
    meta["n_emg_samples"] = len(raw)
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.remove(os.path.join(d, "emg_raw.f32"))      # 변환이 모두 끝난 뒤에만 임시 파일 삭제
    os.remove(src)
    return d


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "finalize":
        print("변환 완료:", finalize_session(sys.argv[2]))
    else:
        print("사용법: python session_logger.py finalize sessions/폴더명")
