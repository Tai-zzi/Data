"""합성 데이터 생성 -> 저장 -> 감지 -> 평가 -> 그래프.   실행: python run_demo.py
실측 데이터로 돌릴 때:  python run_demo.py --data 폴더경로  (emg.npy, audio.wav, labels.csv, meta.json)"""
import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from dataio import load_session, save_session
from detect import detect_grind, detect_snore, snore_gaps
from evaluate import event_metrics, frame_metrics
from synth import generate_session


def report(name, pred, true, duration):
    em, fm = event_metrics(pred, true), frame_metrics(pred, true, duration)
    print(f"[{name}] 정답 {em['n_true']}개 / 예측 {em['n_pred']}개  (맞춤 {em['tp']}, 오탐 {em['fp']}, 놓침 {em['fn']})")
    print(f"   이벤트 단위  P={em['precision']:.2f} R={em['recall']:.2f} F1={em['f1']:.2f}")
    print(f"   프레임 단위  P={fm['precision']:.2f} R={fm['recall']:.2f} F1={fm['f1']:.2f}")


def plot(path, t_emg, env_e, thr_e, t_aud, env_a, thr_a, true_g, pred_g, true_s, pred_s):
    fig, ax = plt.subplots(2, 1, figsize=(14, 6), sharex=True)
    for a, t, env, thr, tr, pr, title in [
        (ax[0], t_emg, env_e, thr_e, true_g, pred_g, "EMG envelope (grind)"),
        (ax[1], t_aud, env_a, thr_a, true_s, pred_s, "Audio low-band envelope (snore)")]:
        a.plot(t, env, lw=0.6, color="k")
        a.axhline(thr, color="r", ls="--", lw=0.8)
        for s, e in tr:
            a.axvspan(s, e, ymin=0.5, ymax=1, color="tab:green", alpha=0.35)
        for s, e in pr:
            a.axvspan(s, e, ymin=0, ymax=0.5, color="tab:blue", alpha=0.35)
        a.set_title(title + "   (green=truth top, blue=detected bottom)")
    ax[1].set_xlabel("time [s]")
    fig.tight_layout()
    fig.savefig(path, dpi=110)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=600)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--data", help="저장된 세션 폴더(실측 데이터)")
    ap.add_argument("--out", default="out")
    args = ap.parse_args()

    if args.data:
        s = load_session(args.data)
    else:
        s = generate_session(args.duration, args.seed)
        save_session(os.path.join(args.out, "synthetic"), s["emg"], s["audio"],
                     s["labels"], s["emg_fs"], s["audio_fs"])
    dur = len(s["emg"]) / s["emg_fs"]
    true_g = [(a, b) for a, b, k in s["labels"] if k == "grind"]
    true_s = [(a, b) for a, b, k in s["labels"] if k == "snore"]

    pred_g, env_e, thr_e, fs_e = detect_grind(s["emg"], s["emg_fs"])
    pred_s, env_a, thr_a, fs_a = detect_snore(s["audio"], s["audio_fs"])

    print(f"세션 길이 {dur:.0f}s")
    report("이갈이", pred_g, true_g, dur)
    report("코골이", pred_s, true_s, dur)
    print(f"코골이 사이 무음 구간 후보 {len(snore_gaps(pred_s))}개 (B/C안 무호흡 의심 구간 계산에 사용 예정)")

    os.makedirs(args.out, exist_ok=True)
    plot(os.path.join(args.out, "result.png"),
         np.arange(len(env_e)) / fs_e, env_e, thr_e,
         np.arange(len(env_a)) / fs_a, env_a, thr_a, true_g, pred_g, true_s, pred_s)
    print(f"그래프 저장: {os.path.join(args.out, 'result.png')}")


if __name__ == "__main__":
    main()
