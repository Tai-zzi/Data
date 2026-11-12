"""저장/불러오기. 실측 데이터도 같은 형식(emg.npy, audio.wav, labels.csv)으로 저장하면 그대로 분석 가능."""
import csv
import json
import os

import numpy as np
from scipy.io import wavfile


def save_session(out_dir, emg, audio, labels, emg_fs, audio_fs):
    os.makedirs(out_dir, exist_ok=True)
    np.save(os.path.join(out_dir, "emg.npy"), emg)
    wavfile.write(os.path.join(out_dir, "audio.wav"), audio_fs,
                  (np.clip(audio, -1, 1) * 32767).astype(np.int16))
    with open(os.path.join(out_dir, "labels.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["start_s", "end_s", "kind"])
        for s, e, k in labels:
            w.writerow([f"{s:.3f}", f"{e:.3f}", k])
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(dict(emg_fs=emg_fs, audio_fs=audio_fs), f)


def load_session(d):
    emg = np.load(os.path.join(d, "emg.npy"))
    audio_fs, a = wavfile.read(os.path.join(d, "audio.wav"))
    audio = a.astype(np.float32) / 32768.0
    with open(os.path.join(d, "meta.json")) as f:
        meta = json.load(f)
    return dict(emg=emg, audio=audio, labels=read_labels(os.path.join(d, "labels.csv")),
                emg_fs=meta["emg_fs"], audio_fs=audio_fs)


def read_labels(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return [(float(r["start_s"]), float(r["end_s"]), r["kind"]) for r in rows]


def read_audacity_labels(path):
    """Audacity '라벨 내보내기' 텍스트(시작<TAB>끝<TAB>이름)를 읽는다. 이름은 grind / snore 로 붙이세요."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and not parts[0].startswith("\\"):
                out.append((float(parts[0]), float(parts[1]), parts[2].strip()))
    return out
