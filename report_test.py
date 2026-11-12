"""간이 통합 테스트(이갈이 + 코골이): 실행 화면을 띄우고, 실행한 모든 것을 한 폴더에 저장한다.
사용:  python report_test.py COM4            (실제 장치. 포트 번호는 지금 번호)
       python report_test.py COM4 --baud 921600
       python report_test.py --fake           (장치 없이 가짜 ESP32로 코드만 시험. 창 없이 빠르게 끝까지 돌림)
시간표 (총 150초, 화면 위에 안내가 뜬다):
   0~15초   가만히 (기준선 학습)
  15~75초   이갈이 흉내: 안내가 뜨면 2초 꽉 물기, 4초 쉬기 x 10번
  75~135초  코골이: 코골이 소리를 내거나 스피커로 재생 (마이크에서 20~50cm)
 135~150초  가만히 (마무리)
저장 위치: reports/test_날짜_시각/
  run_log.txt     실행하는 동안 화면에 나온 글 전체
  summary.md      교수님께 보고할 요약 (설정, 시간표, 결과 숫자, 한계)
  events.csv      감지한 이갈이/코골이 구간 목록
  screen_*.png    실행 화면 캡처 (각 단계가 끝날 때마다 + 최종)
  session/날짜/   원본 신호 (eeg.npy, emg.npy, audio.wav, events.log, meta.json)
안전: 전극을 몸에 붙이기 전에 USB 아이솔레이터와 노트북 배터리를 확인한다. 자극 출력은 이 테스트에서 꺼져 있다.
필요: pip install pyserial"""
import argparse
import csv
import json
import os
import sys
import time

import numpy as np

import config as C
from evaluate import event_metrics
from stream import HOP_S, StreamProcessor

# ---------------- 시간표 ----------------
PHASES = [("baseline", 0, 15, "가만히 있으세요 (평소 잡음 학습)"),
          ("grind", 15, 75, "이갈이 흉내: 안내가 뜨면 2초 꽉 물고 4초 쉬세요 (10번)"),
          ("snore", 75, 135, "코골이: 코골이 소리를 내거나 스피커로 재생하세요 (마이크 20~50cm)"),
          ("end", 135, 150, "가만히 있으세요 (마무리)")]
TOTAL = 150.0
CLENCH_STARTS = [16 + 6 * k for k in range(10)]     # 이를 무는 시작 시각 (초), 각 2초
CLENCH_LEN = 2.0


class Tee:
    """화면에 찍는 글을 파일에도 같이 저장."""
    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")
        self.o = sys.stdout

    def write(self, s):
        self.o.write(s)
        self.f.write(s)

    def flush(self):
        self.o.flush()
        self.f.flush()


def current_message(t):
    for k in CLENCH_STARTS:
        if k <= t < k + CLENCH_LEN:
            return "지금! 이를 꽉 무세요!"
    for _, a, b, msg in PHASES:
        if a <= t < b:
            return msg
    return "끝"


def span_flagged_seconds(flag, t0, t1):
    i0, i1 = int(t0 / HOP_S), min(len(flag), int(t1 / HOP_S))
    return float(np.sum(flag[i0:i1])) * HOP_S if i1 > i0 else 0.0


def evaluate_run(proc, stats, fake):
    n = proc.n
    grind = [(round(a, 2), round(b, 2)) for a, b in proc.grind_events]
    if proc.emg_flag[n - 1]:                                     # 끝까지 이어진 구간
        grind.append((round(proc._g_start, 2), round(n * HOP_S, 2)))
    snore = [(round(a, 2), round(b, 2)) for a, b in proc.snore_episodes]
    if proc.snore_flag[n - 1]:
        snore.append((round(proc._s_start, 2), round(n * HOP_S, 2)))
    true_g = [(k, k + CLENCH_LEN) for k in CLENCH_STARTS]
    gm = event_metrics(grind, true_g)
    sp = [p for p in PHASES if p[0] == "snore"][0]
    in_snore = span_flagged_seconds(proc.snore_flag, sp[1], sp[2])
    out_snore = span_flagged_seconds(proc.snore_flag, 0, sp[1]) + span_flagged_seconds(proc.snore_flag, sp[2], TOTAL)
    in_grind_phase = [e for e in grind if 15 <= e[0] < 75]
    out_grind = [e for e in grind if not (15 <= e[0] < 75)]
    aud = proc.aud_env[:n]
    return dict(grind=grind, snore=snore, gm=gm, in_snore=in_snore, out_snore=out_snore,
                in_grind_phase=in_grind_phase, out_grind=out_grind,
                aud_median=float(np.median(aud)) if n else 0.0, aud_max=float(np.max(aud)) if n else 0.0,
                emg_median=float(np.median(proc.emg_env[:n])) if n else 0.0, stats=stats, fake=fake)


def write_outputs(out, proc, src, fake, started):
    stats = src.stats()
    ev = evaluate_run(proc, stats, fake)
    with open(os.path.join(out, "events.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["종류", "시작(초)", "끝(초)", "길이(초)"])
        for a, b in ev["grind"]:
            w.writerow(["이갈이(턱 EMG)", a, b, round(b - a, 2)])
        for a, b in ev["snore"]:
            w.writerow(["코골이(소리)", a, b, round(b - a, 2)])
    gm, silent = ev["gm"], ev["aud_max"] <= 0 or ev["aud_max"] < ev["aud_median"] * 1.5
    fake_note = "**주의: 이 결과는 가짜 데이터(코드 시험용)입니다. 보고에 쓰지 마세요.**\n\n" if fake else ""
    md = f"""# 간이 통합 테스트 결과 (이갈이 + 코골이)

{fake_note}- 실행 시각: {started}
- 목적: 센서(ADS1292R 턱 EMG, INMP441 마이크) -> ESP32 -> PC 전체 경로에서 실제 신호가 들어와 감지·저장되는지 확인
- 방법: **깨어 있는 상태**에서 동작을 시간표대로 흉내 냄 (실제 수면 측정이 아님)
- 자극 출력: 이번 테스트에서는 꺼 둠 (전극에 전류를 흘리지 않음)

## 시간표
| 구간(초) | 내용 |
|---|---|
| 0~15 | 가만히 (평소 잡음 학습) |
| 15~75 | 이갈이 흉내: 2초 꽉 물기 + 4초 쉬기, 10번 |
| 75~135 | 코골이 소리 내기/스피커 재생 |
| 135~150 | 가만히 (마무리) |

## 연결 상태
- 수신 패킷 {stats.get('packets')}개, CRC 오류 {stats.get('bad_crc')}, 유실 {stats.get('lost_packets')}
- 실제 EMG 샘플링 약 {stats.get('measured_emg_fs', 'N/A')} Hz (설정 {C.EMG_FS})
- 채널 배정: 턱 EMG = 패킷 열 {C.CH_EMG}, 나머지 열 {C.CH_EEG}은 이번에 사용하지 않음 (수면 단계 그래프는 의미 없음)
- 마이크 소리: {"**거의 들어오지 않음(무음/미연결 의심)**" if silent else "들어옴"} (소리 포락선 중앙값 {ev['aud_median']:.4g}, 최대 {ev['aud_max']:.4g})

## 결과 1: 이갈이 흉내 (턱 EMG)
- 이를 문 횟수(안내 기준): 10번
- 감지된 이갈이 구간: {len(ev['grind'])}개 (이갈이 구간 안 {len(ev['in_grind_phase'])}개, 바깥 {len(ev['out_grind'])}개)
- 안내 시간표와 겹친 정도: 정답 {gm['n_true']}개 중 {gm['tp']}개 맞춤, 오탐 {gm['fp']}개, 놓침 {gm['fn']}개
  (정밀도 {gm['precision']:.2f}, 재현율 {gm['recall']:.2f}, F1 {gm['f1']:.2f})

## 결과 2: 코골이 (마이크)
- 감지된 코골이 구간: {len(ev['snore'])}개
- 코골이 구간(75~135초) 중 "코골이"로 표시된 시간: {ev['in_snore']:.1f}초 / 60초
- 그 밖의 시간에 코골이로 잘못 표시된 시간: {ev['out_snore']:.1f}초

## 실행 화면
- `screen_1_baseline.png`, `screen_2_grind.png`, `screen_3_snore.png`, `screen_final.png` (각 단계가 끝날 때 캡처)
- 그래프 설명: ② 턱근육 EMG(이갈이=빨강), ③ 코골이 소리(규칙적인 호흡 패턴=빨강), ①④는 이번 시험에서 사용하지 않음

## 한계 (보고할 때 같이 말할 것)
- 깨어 있는 상태에서 흉내 낸 간이 시험이라 **실제 수면 중 정확도를 보여주지 않습니다.** 수면 단계는 다루지 않았습니다.
- "정답"은 사람이 시간표대로 동작한 시각입니다. 반응 시간 때문에 시작/끝이 조금 어긋날 수 있습니다.
- 감지 기준(`config.py`의 임계값)은 기본값이며 아직 튜닝하지 않았습니다.
- 코골이는 스피커 재생이나 흉내를 쓰면 실제 코골이와 소리 특성이 다를 수 있습니다.
- 원본 데이터는 `session/` 폴더에 저장되어 있어 나중에 다시 분석할 수 있습니다.
"""
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(dict(started=started, fake=fake, phases=PHASES, clench_starts=CLENCH_STARTS, stats=stats,
                       config=dict(EMG_FS=C.EMG_FS, AUDIO_FS=C.AUDIO_FS, CH_EMG=C.CH_EMG, CH_EEG=C.CH_EEG,
                                   EMG_RATIO=C.EMG_RATIO, SNORE_RATIO=C.SNORE_RATIO, GRIND_MIN_DUR=C.GRIND_MIN_DUR,
                                   SNORE_INTERVAL=C.SNORE_INTERVAL, BASELINE_INIT_S=C.BASELINE_INIT_S)),
                  f, ensure_ascii=False, indent=2)
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", help="시리얼 포트 (예: COM4)")
    ap.add_argument("--baud", type=int, default=C.SERIAL_BAUD)
    ap.add_argument("--fake", action="store_true", help="장치 없이 가짜 ESP32로 코드 시험")
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()
    if not args.fake and not args.port:
        raise SystemExit(__doc__)

    started = time.strftime("%Y-%m-%d %H:%M:%S")
    out = os.path.join(args.out, "test_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    sys.stdout = Tee(os.path.join(out, "run_log.txt"))
    print(f"[시작] {started}  저장 폴더: {os.path.abspath(out)}")
    print(f"[설정] 턱 EMG 열={C.CH_EMG}, 포트={'(가짜 ESP32)' if args.fake else args.port}, baud={args.baud}")

    import matplotlib
    if args.fake:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import dashboard
    from serial_source import SerialLink, SerialSource
    from session_logger import SessionLogger
    dashboard.setup_font(matplotlib)

    logger = SessionLogger(os.path.join(out, "session"), dict(source="fake" if args.fake else "serial", test="report_test"))
    if args.fake:
        from fake_device import FakeLink
        link = FakeLink(minutes=TOTAL / 60, seed=3, speed=None, with_audio=True)
    else:
        link = SerialLink(args.port, args.baud)
    src = SerialSource(link, logger)
    proc = StreamProcessor()
    proc.stim.t_ready = 1e18                                     # 이 테스트에서는 자극 판정/출력을 끈다
    fig, ax, A = dashboard.build_figure(plt)

    snaps = {"baseline": ("screen_1_baseline.png", 15), "grind": ("screen_2_grind.png", 75),
             "snore": ("screen_3_snore.png", 135)}
    snapped, printed, state = set(), set(), {"done": False}

    def refresh(title):
        dashboard.redraw(proc, A, ax, full=True)
        A["hyp"].set_data([], [])
        A["txt"][0].set_text("이번 시험에서는 사용 안 함 (EEG 전극 없음)")
        A["txt"][3].set_text("자극 꺼짐 (이번 시험에서는 사용 안 함)")
        ax[3].set_xlim(0, TOTAL / 60)
        fig.suptitle(title, fontsize=13)

    def tick():
        d = src.read_available()
        if d is None:
            return False
        proc.process(*d)
        return True

    def after_tick():
        t = proc.now
        for k in CLENCH_STARTS:                                  # 안내 문구를 콘솔에도 (벨 소리 포함)
            if t >= k and ("c", k) not in printed:
                printed.add(("c", k))
                print(f"[{t:5.1f}초] 이를 꽉 무세요 (2초)\a")
        for name, a, b, msg in PHASES:
            if t >= a and ("p", name) not in printed:
                printed.add(("p", name))
                print(f"[{t:5.1f}초] {msg}")
        for name, (fn, t_end) in snaps.items():
            if t >= t_end and name not in snapped:
                snapped.add(name)
                refresh(f"{current_message(t_end - 0.1)}   (캡처: {name} 단계 끝)")
                fig.savefig(os.path.join(out, fn), dpi=110)
                print(f"[캡처] {fn}")

    def finish():
        if state["done"]:
            return
        state["done"] = True
        refresh("측정 종료 - 결과가 저장되었습니다")
        fig.savefig(os.path.join(out, "screen_final.png"), dpi=110)
        ev = write_outputs(out, proc, src, args.fake, started)
        print("\n=== 결과 요약 ===")
        print(f"이갈이: 안내 10번 중 {ev['gm']['tp']}번 맞춤, 오탐 {ev['gm']['fp']}개 (F1 {ev['gm']['f1']:.2f})")
        print(f"코골이: 코골이 구간 60초 중 {ev['in_snore']:.1f}초 감지, 그 밖의 시간 오탐 {ev['out_snore']:.1f}초")
        print(f"수신: 패킷 {ev['stats'].get('packets')}개, CRC 오류 {ev['stats'].get('bad_crc')}, 유실 {ev['stats'].get('lost_packets')}")
        print(f"저장 폴더: {os.path.abspath(out)}")
        print("  summary.md / events.csv / screen_*.png / run_log.txt / session/ (원본)")

    if args.fake:
        while proc.now < TOTAL:
            got = tick()
            after_tick()
            if not got and src.finished:
                break
            if not got:
                time.sleep(0.001)
        finish()
        src.close()
        return

    from matplotlib.animation import FuncAnimation
    print("창이 뜨면 안내 문구를 따라 하세요. (안내는 창 위쪽에도 나옵니다)")

    def update(_):
        if state["done"]:
            return
        tick()
        after_tick()
        t = proc.now
        if t >= TOTAL:
            finish()
            anim.event_source.stop()
            return
        refresh(f"[{t:5.1f} / {TOTAL:.0f}초]  {current_message(t)}")

    anim = FuncAnimation(fig, update, interval=100, cache_frame_data=False)
    try:
        plt.show()
    finally:
        if not state["done"]:
            print("[중단] 시간표가 끝나기 전에 창을 닫았습니다. 지금까지의 결과만 저장합니다.")
            finish()
        src.close()


if __name__ == "__main__":
    main()
