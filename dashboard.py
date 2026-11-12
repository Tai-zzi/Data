"""실시간 4단 대시보드 (모든 그래프가 같은 시간축을 공유하며 계속 이어서 그려짐)
 ① 수면 단계(Hypnogram)  ② 턱근육 EMG(이갈이=빨강)  ③ 코골이 소리(규칙 패턴=빨강)  ④ 자극 세기/결과

실행:  python dashboard.py                      (가짜 데이터로 20분 밤을 10배속 재생)
       python dashboard.py --minutes 30 --speed 20 --window 8
       python dashboard.py --save out/dashboard.png   (창 없이 끝까지 돌리고 그림만 저장)
       python dashboard.py --source fake              (가짜 ESP32 -> 패킷 -> 로거 전체 경로 시험, sessions/ 에 저장)
       python dashboard.py --source serial --port COM5   (실제 장치. 포트 목록: python serial_source.py --list)"""
import argparse
import time

import numpy as np

import config as C
from mock_source import MockNight
from stream import HOP_S, STAGE_LABEL, StreamProcessor


def setup_font(mpl):
    try:
        import koreanize_matplotlib  # noqa: F401  (설치돼 있으면 한글 폰트 자동 설정)
    except ImportError:
        pass
    from matplotlib import font_manager as fm
    names = {f.name for f in fm.fontManager.ttflist}
    for n in ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR"]:
        if n in names:
            mpl.rcParams["font.family"] = n
            break
    mpl.rcParams["axes.unicode_minus"] = False


def build_figure(plt):
    fig, ax = plt.subplots(4, 1, figsize=(13, 9.5), sharex=True)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.95, bottom=0.07, hspace=0.35)
    A = {}
    ax[0].set_title("① 수면 단계 (EEG 대역 파워 기반 추정 - 의료용 판독 아님)", loc="left", fontsize=11)
    ax[0].set_yticks([0, 1, 2, 3])
    ax[0].set_yticklabels([STAGE_LABEL[0], STAGE_LABEL[1], STAGE_LABEL[2], STAGE_LABEL[3]])
    ax[0].set_ylim(-0.5, 3.5)
    A["hyp"], = ax[0].plot([], [], "k", drawstyle="steps-post", lw=2)

    ax[1].set_title("② 턱근육(EMG) 움직임 - 위아래로 흔들림 / 이갈이 감지 시 빨간색", loc="left", fontsize=11)
    A["emg"], = ax[1].plot([], [], color="tab:blue", lw=0.8)
    A["emg_hot"], = ax[1].plot([], [], color="tab:red", lw=1.2)
    A["env_p"], = ax[1].plot([], [], color="gray", lw=0.6, alpha=0.7)
    A["env_n"], = ax[1].plot([], [], color="gray", lw=0.6, alpha=0.7)
    A["thr_p"], = ax[1].plot([], [], "r--", lw=0.7, alpha=0.6)
    A["thr_n"], = ax[1].plot([], [], "r--", lw=0.7, alpha=0.6)

    ax[2].set_title("③ 코골이 소리(마이크 저주파 에너지) - 일정한 호흡 패턴이 잡히면 빨간색", loc="left", fontsize=11)
    A["aud"], = ax[2].plot([], [], color="tab:blue", lw=0.9)
    A["aud_hot"], = ax[2].plot([], [], color="tab:red", lw=1.4)
    A["aud_thr"], = ax[2].plot([], [], "r--", lw=0.7, alpha=0.6)

    ax[3].set_title("④ 턱밑 패치 자극 - 세기(단계) / 초록 원=이갈이 중단 성공, 빨간 X=계속됨", loc="left", fontsize=11)
    A["stim"], = ax[3].plot([], [], color="tab:orange", drawstyle="steps-post", lw=2)
    A["ok"], = ax[3].plot([], [], "o", color="tab:green", ms=9, ls="none")
    A["ng"], = ax[3].plot([], [], "x", color="tab:red", ms=9, mew=2.5, ls="none")
    ax[3].set_ylim(0, C.STIM_MAX_LEVEL + 0.8)
    ax[3].set_yticks(range(0, C.STIM_MAX_LEVEL + 1))
    ax[3].set_yticklabels(["꺼짐"] + [f"{i}단계" for i in range(1, C.STIM_MAX_LEVEL + 1)])
    ax[3].set_xlabel("시간 (분)")
    for a in ax:
        a.grid(alpha=0.25)
    box = dict(boxstyle="round", fc="white", ec="gray", alpha=0.9)
    A["txt"] = [a.text(0.99, 0.88, "", transform=a.transAxes, ha="right", va="top",
                       fontsize=12, bbox=box) for a in ax]
    return fig, ax, A


def redraw(proc, A, ax, window_s=None, full=False):
    n = proc.n
    if n < 2:
        return
    i0 = 0 if (full or window_s is None) else max(0, n - int(window_s / HOP_S))
    t = np.arange(i0, n) * HOP_S / 60.0
    now = proc.now / 60.0

    # ① 수면 단계: 마지막 에폭 값을 현재 시각까지 이어 그림
    if proc.stage_t:
        st = np.array(proc.stage_t) / 60.0
        sv = np.array(proc.stage_v, float)
        A["hyp"].set_data(np.append(st, now), np.append(sv, sv[-1]))
        A["txt"][0].set_text("현재: " + STAGE_LABEL[int(sv[-1])])
    else:
        A["txt"][0].set_text("수면 판정 대기 중 (30초 단위)")

    # ② EMG
    pk, fl = proc.emg_peak[i0:n], proc.emg_flag[i0:n]
    env, thr = proc.emg_env[i0:n], proc.emg_thr[i0:n]
    A["emg"].set_data(t, pk)
    A["emg_hot"].set_data(t, np.where(fl, pk, np.nan))
    A["env_p"].set_data(t, env); A["env_n"].set_data(t, -env)
    A["thr_p"].set_data(t, thr); A["thr_n"].set_data(t, -thr)
    m = max(8.0, 1.15 * float(np.nanmax(np.abs(pk))))
    ax[1].set_ylim(-m, m)
    A["txt"][1].set_text("이갈이 감지!" if proc.emg_flag[n - 1] else "정상")
    A["txt"][1].set_color("tab:red" if proc.emg_flag[n - 1] else "black")

    # ③ 코골이
    ae, sf = proc.aud_env[i0:n], proc.snore_flag[i0:n]
    A["aud"].set_data(t, ae)
    A["aud_hot"].set_data(t, np.where(sf, ae, np.nan))
    A["aud_thr"].set_data(t, proc.aud_thr[i0:n])
    thr_a = proc.aud_thr[i0:n]
    thr_top = float(np.nanmax(thr_a)) * 2 if np.isfinite(thr_a).any() else 0.0
    ax[2].set_ylim(0, max(float(np.nanmax(ae)) * 1.15, thr_top, 1e-3))
    A["txt"][2].set_text("코골이 중" if proc.snore_flag[n - 1] else "정상")
    A["txt"][2].set_color("tab:red" if proc.snore_flag[n - 1] else "black")

    # ④ 자극
    A["stim"].set_data(t, proc.stim_level[i0:n])
    res = [(rt / 60.0, ok, lv) for rt, ok, lv in proc.stim.results if rt / 60.0 >= t[0]]
    A["ok"].set_data([r[0] for r in res if r[1]], [r[2] for r in res if r[1]])
    A["ng"].set_data([r[0] for r in res if not r[1]], [r[2] for r in res if not r[1]])
    cur = int(proc.stim_level[n - 1])
    A["txt"][3].set_text(f"자극 중: {cur}단계" if cur else f"대기 (다음 시작 단계: {proc.stim.level})")

    if full or window_s is None:
        ax[3].set_xlim(0, max(now, 1e-3))
    else:
        ax[3].set_xlim(max(0.0, now - window_s / 60.0), max(window_s / 60.0, now))


def summary(proc, night=None):
    tried = sum(c[0] for c in proc.stim.counts.values())
    won = sum(c[1] for c in proc.stim.counts.values())
    if night is not None:
        st = np.array(proc.stage_v)
        acc = np.mean(st == night.stages[:len(st)]) if len(st) else float("nan")
        print(f"[요약] 수면 단계 일치율 {acc:.0%} ({len(st)}에폭) | 이갈이 정답 {len(night.grind)} / 감지 {len(proc.grind_events)}"
              f" | 코골이 정답 {len(night.snore)} / 감지 {len(proc.snore_episodes)}")
    else:
        print(f"[요약] 측정 {proc.now / 60:.1f}분 | 이갈이 감지 {len(proc.grind_events)}회"
              f" | 코골이 감지 {len(proc.snore_episodes)}회 | 수면 판정 {len(proc.stage_v)}에폭")
    print(f"       자극 {tried}회 중 이갈이 중단 {won}회, 단계별[시도,성공]={proc.stim.counts}")


def make_source(args):
    """--source fake / serial 일 때 입력 소스(+원본 저장 로거)를 만든다. mock 이면 None."""
    if args.source == "mock":
        return None
    from serial_source import SerialLink, SerialSource
    if args.source == "serial" and not args.port:
        raise SystemExit("--port 가 필요합니다. 포트 목록: python serial_source.py --list")
    logger = None
    if not args.no_log:
        from session_logger import SessionLogger
        logger = SessionLogger(args.log_dir, dict(source=args.source, ch_eeg=C.CH_EEG, ch_emg=C.CH_EMG,
                                                  gain_eeg=C.ADS_GAIN_EEG, gain_emg=C.ADS_GAIN_EMG,
                                                  vref=C.ADS_VREF))
    if args.source == "fake":
        from fake_device import FakeLink
        link = FakeLink(args.minutes, args.seed, None if args.save else args.speed,
                        with_audio=not args.no_audio,
                        drop=0.01 if args.glitch else 0.0, corrupt=0.005 if args.glitch else 0.0)
    else:
        link = SerialLink(args.port, args.baud)
    return SerialSource(link, logger)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["mock", "fake", "serial"], default="mock",
                    help="mock=가짜 데이터 직접 재생 / fake=가짜 ESP32 패킷 경로 / serial=실제 장치")
    ap.add_argument("--port", help="시리얼 포트 (예: COM5)")
    ap.add_argument("--baud", type=int, default=C.SERIAL_BAUD)
    ap.add_argument("--no-audio", action="store_true", help="(fake) 오디오 없이 턱 EMG만 보냄")
    ap.add_argument("--glitch", action="store_true", help="(fake) 패킷 유실/손상을 일부러 섞음")
    ap.add_argument("--no-log", action="store_true", help="원본 저장 안 함")
    ap.add_argument("--log-dir", default="sessions", help="원본 저장 폴더")
    ap.add_argument("--minutes", type=float, default=20, help="가짜 데이터 길이(분)")
    ap.add_argument("--speed", type=float, default=10, help="가짜 데이터 재생 배속 (1=실시간)")
    ap.add_argument("--window", type=float, default=10, help="화면에 보이는 시간 폭(분)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--save", help="지정하면 창 없이 끝까지 처리 후 이 경로에 PNG 저장")
    args = ap.parse_args()

    import matplotlib
    if args.save:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    setup_font(matplotlib)

    source = make_source(args)
    night = MockNight(args.minutes, args.seed) if source is None else None
    proc = StreamProcessor()
    fig, ax, A = build_figure(plt)

    def advance(K):                      # 가짜 데이터 직접 재생
        r = night.read(K)
        if r is None:
            return False
        proc.process(*r)
        for t, lv in proc.stim.pending:
            night.apply_stimulus(t, lv)
        proc.stim.pending.clear()
        return True

    def advance_live():                  # 장치(또는 가짜 ESP32)에서 도착한 만큼 처리
        d = source.read_available()
        if d is None:
            return False
        proc.process(*d)
        for t, lv in proc.stim.pending:
            source.apply_stimulus(t, lv)
        proc.stim.pending.clear()
        return True

    truth = source.link.night if (source is not None and args.source == "fake") else night

    if args.save:
        if source is None:
            while advance(20):
                pass
        else:
            while True:
                got = advance_live()
                if not got and source.finished:
                    break
                if not got:
                    time.sleep(0.001)
        redraw(proc, A, ax, full=True)
        fig.suptitle(f"측정 종료 ({proc.now / 60:.0f}분)", fontsize=12)
        import os
        os.makedirs(os.path.dirname(args.save) or ".", exist_ok=True)
        fig.savefig(args.save, dpi=100)
        summary(proc, truth)
        print("저장:", args.save)
        if source is not None:
            source.close()
        return

    from matplotlib.animation import FuncAnimation
    last = [time.perf_counter()]
    done = [False]

    def update(_):
        if source is None:
            now = time.perf_counter()
            K = int(np.clip((now - last[0]) * args.speed / HOP_S, 1, 400))
            last[0] = now
            ended = not advance(K)
        else:
            got = advance_live()
            ended = (not got) and source.finished
            if not ended:
                fig.suptitle(source.status(), fontsize=10)
        if ended and not done[0]:
            done[0] = True
            fig.suptitle("측정 종료", fontsize=12)
            anim.event_source.stop()
            summary(proc, truth)
        redraw(proc, A, ax, window_s=args.window * 60)

    anim = FuncAnimation(fig, update, interval=100, cache_frame_data=False)
    try:
        plt.show()
    finally:
        if source is not None:
            source.close()               # 창을 닫으면 원본 저장을 마무리


if __name__ == "__main__":
    main()
