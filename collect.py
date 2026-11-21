"""이갈이(턱 EMG) + 코골이(마이크) 실시간 데이터 수집기: 그래프를 계속 보여주면서 모든 것을 저장한다.
사용:  python collect.py COM4                     (실시간 그래프 창 + 저장. 창을 닫으면 종료)
       python collect.py COM4 --minutes 60        (60분 뒤 자동 종료)
       python collect.py COM4 --no-gui            (창 없이 저장만: 밤새 측정용, Ctrl+C로 종료)
       python collect.py --fake --minutes 8       (장치 없이 가짜 ESP32로 코드만 시험)
옵션: --window 5 (그래프에 보이는 분, 기본 5)  --baud 921600  --out collections  --hours 10 (최대 기록 시간)
하는 일:
  - 데이터를 받는 일(별도 스레드)과 그래프 그리기를 분리해서, 그래프가 느려져도 데이터가 빠지지 않는다.
  - 원본 신호(EEG열, 턱 EMG열, 소리)를 계속 파일로 저장한다.  -> session/날짜_시각/
  - 이갈이/코골이/무호흡 의심 구간(비진단)이 생길 때마다 events.csv 에 바로 한 줄씩 추가한다 (엑셀로 열림).
  - 1분마다 summary.md/json 을 갱신하고, 끝나면 최종 요약과 전체 그래프(screen_final.png)를 저장한다.
저장 위치: collections/날짜_시각/   (run_log.txt, events.csv, summary.md, summary.json, screen_final.png, session/)
주의: 시작 후 처음 10초는 조용히(평소 잡음 학습). 전극을 몸에 붙이기 전에 USB 아이솔레이터와 노트북 배터리를 확인한다.
      무호흡 의심 구간은 의료 진단이 아니며, 코골이가 끝난 뒤 10~60초 무음이 있다가 다시 코골이가 시작될 때만 표시한다.
필요: pip install pyserial"""
import argparse
import csv
import json
import os
import sys
import threading
import time

import numpy as np

import config as C
from stream import HOP_S, STAGE_LABEL, StreamProcessor

APNEA_MIN, APNEA_MAX = 10.0, 60.0       # 코골이 사이 무음이 이 범위면 '무호흡 의심'(비진단)
SNORE_END_EST = 1.5                      # 마지막 호흡 소리 시작 + 약 1.5초를 코골이 끝으로 추정


class Tee:
    def __init__(self, path):
        self.f, self.o = open(path, "w", encoding="utf-8"), sys.stdout

    def write(self, s):
        self.o.write(s)
        self.f.write(s)

    def flush(self):
        self.o.flush()
        self.f.flush()


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


class Stats:
    """감지된 사건 목록과 events.csv 기록."""
    def __init__(self, out, marker=None):
        self.t0 = time.time()
        self.grind, self.snore, self.apnea = [], [], []
        self.g_idx = self.s_idx = self.o_idx = 0
        self.marker = marker
        self.prev_snore_end = None
        self.f = open(os.path.join(out, "events.csv"), "w", newline="", encoding="utf-8-sig")
        self.w = csv.writer(self.f)
        self.w.writerow(["종류", "시작(경과 시:분:초)", "끝(경과 시:분:초)", "길이(초)", "시작 시계시각", "비고"])
        self.f.flush()

    def add(self, kind, s, e, note=""):
        self.w.writerow([kind, hms(s), hms(e), round(e - s, 1), time.strftime("%H:%M:%S", time.localtime(self.t0 + s)), note])
        self.f.flush()
        print(f"[{hms(e)}] {kind} {e - s:.1f}초 {note}", flush=True)

    def poll(self, proc):
        while self.o_idx < len(proc.grind_onsets):                 # 이갈이가 방금 확정됨 -> 자극 요청 마커 판단
            t, g0 = proc.grind_onsets[self.o_idx]
            self.o_idx += 1
            if self.marker is None:
                continue
            n = proc.n
            sat = bool(n and float(np.median(proc.emg_env[max(0, n - 1200):n])) > C.EMG_SATURATION_UV)
            if self.marker.request(t, proc.current_stage(), quality_ok=not sat, info={"이갈이_시작": hms(g0)}):
                self.add("자극 요청 마커(실제 출력 없음)", t, t, f"수면 {STAGE_LABEL.get(proc.current_stage(), '판정 전')}")
        while self.g_idx < len(proc.grind_events):
            s, e = proc.grind_events[self.g_idx]
            self.g_idx += 1
            self.grind.append((s, e))
            self.add("이갈이(턱 EMG)", s, e)
        while self.s_idx < len(proc.snore_episodes):
            s, e = proc.snore_episodes[self.s_idx]
            self.s_idx += 1
            end = max(s + 1.0, e - C.SNORE_HOLD_S + SNORE_END_EST)
            if self.prev_snore_end is not None and APNEA_MIN <= s - self.prev_snore_end <= APNEA_MAX:
                self.apnea.append((self.prev_snore_end, s))
                self.add("무호흡 의심 구간(비진단)", self.prev_snore_end, s, "코골이 후 무음, 이어서 다시 시작")
            self.snore.append((s, end))
            self.add("코골이(소리)", s, end)
            self.prev_snore_end = end

    def close_active(self, proc):
        """끝나는 시점에 아직 진행 중이던 사건도 기록."""
        self.poll(proc)
        n = proc.n
        if n and proc.emg_flag[n - 1]:
            self.grind.append((proc._g_start, n * HOP_S))
            self.add("이갈이(턱 EMG)", proc._g_start, n * HOP_S, "측정 종료 시 진행 중")
        if n and proc.snore_flag[n - 1]:
            self.snore.append((proc._s_start, n * HOP_S))
            self.add("코골이(소리)", proc._s_start, n * HOP_S, "측정 종료 시 진행 중")


def write_summary(out, proc, st, stats, args, started, final):
    dur = proc.now
    g_sec = sum(e - s for s, e in st.grind)
    s_sec = sum(e - s for s, e in st.snore)
    info = dict(상태="최종" if final else "진행 중(1분마다 갱신)", 시작=started, 측정길이_초=round(dur, 1),
                이갈이_횟수=len(st.grind), 이갈이_총시간_초=round(g_sec, 1),
                이갈이_시간당_횟수=round(len(st.grind) / (dur / 3600), 1) if dur > 60 else None,
                코골이_구간수=len(st.snore), 코골이_총시간_분=round(s_sec / 60, 1),
                코골이_비율_퍼센트=round(100 * s_sec / dur, 1) if dur > 0 else None,
                무호흡_의심_구간수=len(st.apnea),
                무호흡_의심_최장_초=round(max([e - s for s, e in st.apnea], default=0), 1),
                수면_단계_분={STAGE_LABEL[k]: round(proc.stage_v.count(k) * C.EPOCH_S / 60, 1)
                           for k in sorted(STAGE_LABEL, reverse=True)},
                수면_판정불가_에폭=int(sum(proc.stage_artifact)), 수면_에폭=len(proc.stage_v),
                자극_요청_마커=st.marker.summary() if st.marker else None,
                한채널_공유=bool(C.SINGLE_CHANNEL), 수신=stats, 가짜데이터=bool(args.fake))
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    rows = []
    for h in range(max(1, int(np.ceil(dur / 3600)))):
        a, b = h * 3600, (h + 1) * 3600
        g = sum(1 for s, _ in st.grind if a <= s < b)
        sn = sum(max(0.0, min(e, b) - max(s, a)) for s, e in st.snore) / 60
        ap = sum(1 for s, _ in st.apnea if a <= s < b)
        rows.append(f"| {h + 1}시간째 | {g} | {sn:.1f} | {ap} |")
    fake = "**주의: 가짜 데이터(코드 시험용)입니다. 보고에 쓰지 마세요.**\n\n" if args.fake else ""
    md = f"""# 이갈이·코글이 실시간 수집 요약

{fake}- 상태: {info['상태']}
- 시작: {started} | 측정 길이: {hms(dur)} ({dur:.0f}초)
- 수신: 패킷 {stats.get('packets')}개, CRC 오류 {stats.get('bad_crc')}, 유실 {stats.get('lost_packets')}, 실제 EMG 샘플링 약 {stats.get('measured_emg_fs', 'N/A')} Hz

## 이갈이 (턱 EMG, 패킷 열 {C.CH_EMG})
- 감지 횟수 {info['이갈이_횟수']}회, 총 {g_sec:.0f}초, 시간당 {info['이갈이_시간당_횟수']}회

## 코골이 (마이크)
- 코골이 구간 {info['코골이_구간수']}개, 총 {info['코골이_총시간_분']}분 (측정 시간의 {info['코골이_비율_퍼센트']}%)
- 무호흡 의심 구간(의료 진단 아님) {info['무호흡_의심_구간수']}개, 최장 {info['무호흡_의심_최장_초']}초
  (코골이가 끝난 뒤 {APNEA_MIN:.0f}~{APNEA_MAX:.0f}초 무음이 있다가 다시 코골이가 시작된 경우)

## 수면 상태 (EEG 대역 파워 기반 추정, 의료용 판독 아님)
- {' / '.join(f'{k} {v}분' for k, v in info['수면_단계_분'].items())}
- 30초 에폭 {info['수면_에폭']}개 중 근전도가 섞여 저주파만으로 판정(REM 제외) {info['수면_판정불가_에폭']}개
- 전극: {'한 쌍(이어클립 + 패치)으로 뇌파와 근전도를 같은 채널에서 주파수로 나눠 봄' if C.SINGLE_CHANNEL else f'EEG 열 {C.CH_EEG}, EMG 열 {C.CH_EMG}'}

## 자극 요청 마커 (실제 자극 출력 없음, 기록만)
- {st.marker.summary() if st.marker else '꺼짐'}
- 조건: 시작 후 {st.marker.gate.cfg.warmup_min if st.marker else '-'}분 유예, 최소 {C.MARK_GAP_S:.0f}초 간격, 시간당 {C.MARK_MAX_PER_HOUR}회, 밤당 {C.MARK_MAX_PER_NIGHT}회, 신호 포화 시 보류{', 각성이면 보류' if C.MARK_SKIP_AWAKE else ''}
- 모든 판단(마커/보류와 이유): stim_requests.csv

## 시간대별
| 구간 | 이갈이 횟수 | 코골이 시간(분) | 무호흡 의심 |
|---|---|---|---|
""" + "\n".join(rows) + f"""

## 사용한 감지 기준 (config.py)
- 이갈이: 평소 대비 {C.EMG_RATIO}배 이상이 {C.GRIND_MIN_DUR}초 이상 지속
- 코골이: 평소 대비 {C.SNORE_RATIO}배 이상인 호흡 소리가 {C.SNORE_INTERVAL[0]}~{C.SNORE_INTERVAL[1]}초 간격으로 반복
- 기준은 기본값이며 아직 실제 데이터로 튜닝하지 않았습니다.

## 한계
- 감지 결과는 정답(라벨)과 비교하기 전의 값입니다. 정확도(F1)는 별도로 라벨링해서 평가해야 합니다.
- 소리와 EMG의 시간축은 서로 다른 시계라서 몇 시간 단위로 조금씩 어긋날 수 있습니다.
- 원본 신호는 session/ 폴더에 저장되어 나중에 다시 분석할 수 있습니다.
"""
    md = md.replace("이갈이·코글이", "이갈이·코골이")
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f:
        f.write(md)
    return info


class Plotter:
    """3단 그래프: ① 턱 EMG ② 코골이 소리(+무호흡 의심 음영) ③ 누적 기록. 실시간 창과 최종 저장 그림에 같이 쓴다."""
    def __init__(self, fig):
        self.fig = fig
        self.ax = fig.subplots(3, 1, sharex=True)
        fig.subplots_adjust(left=0.07, right=0.93, top=0.93, bottom=0.07, hspace=0.32)
        a0, a1, a2 = self.ax
        a0.set_title("① 턱근육(EMG): 이를 물면 위아래로 크게 흔들림 / 이갈이로 판정되면 빨간색", loc="left", fontsize=11)
        self.emg, = a0.plot([], [], color="tab:blue", lw=0.7)
        self.emg_hot, = a0.plot([], [], color="tab:red", lw=1.2)
        self.env_p, = a0.plot([], [], color="gray", lw=0.6, alpha=0.7)
        self.env_n, = a0.plot([], [], color="gray", lw=0.6, alpha=0.7)
        self.thr_p, = a0.plot([], [], "r--", lw=0.7, alpha=0.6)
        self.thr_n, = a0.plot([], [], "r--", lw=0.7, alpha=0.6)
        a1.set_title("② 코골이 소리(마이크 저주파): 규칙적인 호흡 패턴이 잡히면 빨간색 / 주황 음영 = 무호흡 의심 구간(비진단)", loc="left", fontsize=11)
        self.aud, = a1.plot([], [], color="tab:blue", lw=0.9)
        self.aud_hot, = a1.plot([], [], color="tab:red", lw=1.4)
        self.aud_thr, = a1.plot([], [], "r--", lw=0.7, alpha=0.6)
        self.apnea_patches = []
        a2.set_title("③ 누적 기록: 이갈이 횟수(주황, 왼쪽 축) / 코골이 시간(보라, 오른쪽 축)", loc="left", fontsize=11)
        self.cum_g, = a2.plot([], [], color="tab:orange", lw=2, drawstyle="steps-post")
        self.a2b = a2.twinx()
        self.cum_s, = self.a2b.plot([], [], color="tab:purple", lw=2)
        a2.set_ylabel("이갈이 횟수 (누적)")
        self.a2b.set_ylabel("코골이 시간 (분, 누적)")
        a2.set_xlabel("경과 시간 (분)")
        for a in self.ax:
            a.grid(alpha=0.25)
        box = dict(boxstyle="round", fc="white", ec="gray", alpha=0.9)
        self.tx = [a.text(0.99, 0.88, "", transform=a.transAxes, ha="right", va="top", fontsize=12, bbox=box) for a in self.ax]
        self.tx[2].set_position((0.01, 0.9))
        self.tx[2].set_ha("left")

    def update(self, S, full=False, window_min=5.0):
        t, now = S["t"], S["now_min"]
        if len(t) < 2:
            return
        self.emg.set_data(t, S["emg_peak"])
        self.emg_hot.set_data(t, np.where(S["emg_flag"], S["emg_peak"], np.nan))
        self.env_p.set_data(t, S["emg_env"]); self.env_n.set_data(t, -S["emg_env"])
        self.thr_p.set_data(t, S["emg_thr"]); self.thr_n.set_data(t, -S["emg_thr"])
        m = max(8.0, 1.15 * float(np.nanmax(np.abs(S["emg_peak"]))))
        self.ax[0].set_ylim(-m, m)
        self.aud.set_data(t, S["aud_env"])
        self.aud_hot.set_data(t, np.where(S["snore_flag"], S["aud_env"], np.nan))
        self.aud_thr.set_data(t, S["aud_thr"])
        thr_top = float(np.nanmax(S["aud_thr"])) * 2 if np.isfinite(S["aud_thr"]).any() else 0.0
        self.ax[1].set_ylim(0, max(float(np.nanmax(S["aud_env"])) * 1.15, thr_top, 1e-3))
        for p in self.apnea_patches:
            p.remove()
        self.apnea_patches = [self.ax[1].axvspan(s / 60, e / 60, color="orange", alpha=0.35) for s, e in S["apnea"]]
        self.apnea_patches += [self.ax[0].axvline(m / 60, color="tab:green", lw=1.5, ls=":") for m in S["marks"]]
        gy = len(S["grind_y"])
        self.cum_g.set_data(np.concatenate(([0.0], S["grind_t"], [now])), np.concatenate(([0], S["grind_y"], [gy])))
        self.cum_s.set_data(S["cum_t"], S["cum_snore"])
        self.ax[2].set_ylim(0, max(3, len(S["grind_y"]) * 1.15))
        self.a2b.set_ylim(0, max(1.0, float(S["cum_snore"][-1]) * 1.15 if len(S["cum_snore"]) else 1.0))
        if S["emg_warn"]:
            self.tx[0].set_text("경고: 신호가 너무 큼(포화) - 전극 접촉/연결 확인")
            self.tx[0].set_color("tab:red")
        else:
            self.tx[0].set_text("이갈이 감지!" if S["emg_flag"][-1] else "정상")
            self.tx[0].set_color("tab:red" if S["emg_flag"][-1] else "black")
        self.tx[1].set_text("코골이 중" if S["snore_flag"][-1] else "정상")
        self.tx[1].set_color("tab:red" if S["snore_flag"][-1] else "black")
        self.tx[2].set_text(S["summary_text"])
        if full:
            self.ax[2].set_xlim(0, max(now, 1e-3))
        else:
            self.ax[2].set_xlim(max(0.0, now - window_min), max(window_min, now))


def make_snapshot(proc, st, window_s):
    n = proc.n
    i0 = 0 if window_s is None else max(0, n - int(window_s / HOP_S))
    sl = slice(i0, n)
    step = max(1, n // 3000)
    idx = np.arange(0, n, step)
    ends = np.array([e / 60 for _, e in st.grind])
    s_sec = sum(e - s for s, e in st.snore) + (n * HOP_S - proc._s_start if n and proc.snore_flag[n - 1] else 0)
    return dict(t=np.arange(i0, n) * HOP_S / 60, now_min=n * HOP_S / 60,
                emg_peak=proc.emg_peak[sl].copy(), emg_flag=proc.emg_flag[sl].copy(), emg_env=proc.emg_env[sl].copy(),
                emg_thr=proc.emg_thr[sl].copy(), aud_env=proc.aud_env[sl].copy(), aud_thr=proc.aud_thr[sl].copy(),
                snore_flag=proc.snore_flag[sl].copy(), apnea=list(st.apnea),
                grind_t=ends, grind_y=np.arange(1, len(ends) + 1),
                cum_t=idx * HOP_S / 60, cum_snore=np.cumsum(proc.snore_flag[:n])[idx] * HOP_S / 60,
                emg_warn=bool(len(proc.emg_env[sl]) and float(np.median(proc.emg_env[sl])) > C.EMG_SATURATION_UV),
                marks=list(st.marker.marks) if st.marker else [],
                summary_text=f"이갈이 {len(st.grind)}회 | 코골이 {s_sec / 60:.1f}분 | 무호흡 의심 {len(st.apnea)}회(비진단)"
                             f"\n수면(추정): {STAGE_LABEL.get(proc.current_stage(), '판정 전(30초마다)')}"
                             f" | 자극 요청 마커 {len(st.marker.marks) if st.marker else 0}회(초록 점선, 출력 없음)")


def setup_font(mpl):
    try:
        import koreanize_matplotlib  # noqa: F401
    except ImportError:
        pass
    from matplotlib import font_manager as fm
    names = {f.name for f in fm.fontManager.ttflist}
    for n in ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR"]:
        if n in names:
            mpl.rcParams["font.family"] = n
            break
    mpl.rcParams["axes.unicode_minus"] = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?")
    ap.add_argument("--baud", type=int, default=C.SERIAL_BAUD)
    ap.add_argument("--minutes", type=float, default=0, help="이 시간(분)이 지나면 자동 종료 (0=계속)")
    ap.add_argument("--window", type=float, default=5, help="그래프에 보이는 시간(분)")
    ap.add_argument("--hours", type=float, default=10, help="메모리에 잡아둘 최대 기록 시간")
    ap.add_argument("--no-gui", action="store_true", help="창 없이 저장만")
    ap.add_argument("--fake", action="store_true", help="가짜 ESP32로 코드 시험(창 없이 끝까지)")
    ap.add_argument("--out", default="collections")
    ap.add_argument("--mark-warmup", type=float, default=None,
                    help=f"자극 요청 마커 시작 유예(분, 기본 {C.MARK_WARMUP_MIN}). 시연 때 0.5 등으로 줄임")
    ap.add_argument("--no-marker", action="store_true", help="자극 요청 마커 끔")
    args = ap.parse_args()
    if not args.fake and not args.port:
        raise SystemExit(__doc__)
    if args.fake:
        args.no_gui = True
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    out = os.path.join(args.out, time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    sys.stdout = Tee(os.path.join(out, "run_log.txt"))
    sys.stderr = sys.stdout                                       # 오류(traceback)도 run_log.txt에 남긴다
    print(f"[시작] {started}  저장 폴더: {os.path.abspath(out)}")
    print(f"[설정] 턱 EMG 열={C.CH_EMG}, EEG 열={C.CH_EEG}{' (한 채널 공유: 이어클립 + 패치)' if C.SINGLE_CHANNEL else ''}, "
          f"포트={'(가짜 ESP32)' if args.fake else args.port}, baud={args.baud}")
    print("[설정] 자극: 실제 출력 없음. 이갈이 확정 시 안전 조건을 통과하면 '자극 요청 마커'만 기록 (stim_requests.csv)")
    print("[안내] 처음 10초는 조용히 계세요 (평소 잡음 학습). 전극을 몸에 붙이기 전에 아이솔레이터와 노트북 배터리를 확인했나요?")

    import matplotlib
    if args.no_gui:
        matplotlib.use("Agg")
    setup_font(matplotlib)
    from serial_source import SerialLink, SerialSource
    from session_logger import SessionLogger

    logger = SessionLogger(os.path.join(out, "session"), dict(source="fake" if args.fake else "serial", tool="collect"))
    if args.fake:
        from fake_device import FakeLink
        link = FakeLink(minutes=args.minutes or 8, seed=5, speed=None, with_audio=True)
    else:
        link = SerialLink(args.port, args.baud)
    src = SerialSource(link, logger)
    proc = StreamProcessor(capacity_hours=args.hours)
    proc.stim.t_ready = 1e18                                      # stream.py의 자극 제어(StimController)는 꺼 둠
    marker = None
    if not args.no_marker:
        from stim_marker import StimMarker
        marker = StimMarker(out, args.mark_warmup)
    st = Stats(out, marker)
    lock, stop, state = threading.Lock(), threading.Event(), dict(done=False, first=True, last_save=time.time())

    def worker():
        while not stop.is_set():
            d = src.read_available()
            if d is None:
                if src.finished:
                    break
                time.sleep(0.005)
                continue
            with lock:
                proc.process(*d)
                if state["first"]:
                    st.t0, state["first"] = time.time() - proc.now, False
                st.poll(proc)
                over = args.minutes and proc.now >= args.minutes * 60
            if over:
                break
        state["done"] = True

    th = threading.Thread(target=worker, daemon=True)
    th.start()

    def periodic():
        if time.time() - state["last_save"] >= 60 and proc.n:
            state["last_save"] = time.time()
            with lock:
                write_summary(out, proc, st, src.stats(), args, started, False)

    try:
        if args.no_gui:
            last = 0
            while not state["done"]:
                time.sleep(0.5)
                periodic()
                if time.time() - last >= 60:
                    last = time.time()
                    print(f"[{hms(proc.now)}] 이갈이 {len(st.grind)}회, 코골이 {len(st.snore)}구간 | {src.status()}", flush=True)
                    if proc.n and float(np.median(proc.emg_env[max(0, proc.n - 1200):proc.n])) > C.EMG_SATURATION_UV:
                        print("   경고: 턱 EMG 신호가 너무 큽니다(포화). 전극 접촉/연결을 확인하세요.", flush=True)
        else:
            import matplotlib.pyplot as plt
            from matplotlib.animation import FuncAnimation
            fig = plt.figure(figsize=(13, 8.5))
            pl = Plotter(fig)

            def update(_):
                periodic()
                with lock:
                    if not proc.n:
                        return
                    S = make_snapshot(proc, st, args.window * 60)
                pl.update(S, window_min=args.window)
                fig.suptitle(f"경과 {hms(proc.now)} | {src.status().split(' | 저장')[0]} | 창을 닫으면 종료·저장", fontsize=11)
                if state["done"]:
                    anim.event_source.stop()

            anim = FuncAnimation(fig, update, interval=500, cache_frame_data=False)
            plt.show()
    except KeyboardInterrupt:
        print("\n[중단] Ctrl+C: 지금까지의 결과를 저장합니다.")
    finally:
        stop.set()
        th.join(timeout=5)
        with lock:
            st.close_active(proc)
            stats = src.stats()
            info = write_summary(out, proc, st, stats, args, started, True)
            if proc.n > 1:
                from matplotlib.backends.backend_agg import FigureCanvasAgg
                from matplotlib.figure import Figure
                fig2 = Figure(figsize=(14, 9))
                FigureCanvasAgg(fig2)
                Plotter(fig2).update(make_snapshot(proc, st, None), full=True)
                fig2.suptitle(f"측정 종료 | 총 {hms(proc.now)} | 이갈이 {len(st.grind)}회, 코골이 {info['코골이_총시간_분']}분", fontsize=12)
                fig2.savefig(os.path.join(out, "screen_final.png"), dpi=110)
        print(f"\n=== 결과 요약 ===\n측정 {hms(proc.now)} | 이갈이 {info['이갈이_횟수']}회 | 코골이 {info['코골이_구간수']}구간 {info['코골이_총시간_분']}분 | 무호흡 의심 {info['무호흡_의심_구간수']}개(비진단)")
        print(f"수면(추정) {info['수면_단계_분']} | 판정 불가 에폭 {info['수면_판정불가_에폭']}/{info['수면_에폭']} | "
              f"자극 요청 마커 {info['자극_요청_마커']}")
        print(f"저장 폴더: {os.path.abspath(out)}\n  summary.md / events.csv / stim_requests.csv / screen_final.png / run_log.txt / session/ (원본)")
        st.f.close()
        try:                                                        # events.csv를 시간순으로 정렬해서 다시 저장
            p = os.path.join(out, "events.csv")
            with open(p, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
            head, body = rows[0], sorted(rows[1:], key=lambda r: (r[1], r[0]))
            with open(p, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(head)
                w.writerows(body)
        except OSError:
            pass
        src.close()


if __name__ == "__main__":
    main()
