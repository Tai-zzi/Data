"""자극 '요청 마커': 이갈이가 확정되면 안전 조건을 검사해 '이때 자극을 요청했을 것'을 기록만 한다.

- 실제 자극은 내보내지 않는다 (장치로 아무것도 보내지 않음, armed=False 고정).
- 안전 조건은 snore_ai_1/snore_ai/trigger/safety.py 의 SafetyGate 를 그대로 쓴다
  (시작 유예, 최소 간격, 시간당/밤당 최대, 신호 품질) + 수면 단계가 '각성'이면 보류.
- 모든 판단(마커/보류와 이유)은 stim_requests.csv 에 남는다 -> "언제, 왜 요청했는지/안 했는지" 설명 가능.
"""
import importlib.util
import os

import config as C
from stream import STAGE_LABEL, STAGE_W

_SAFETY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snore_ai_1", "snore_ai", "trigger", "safety.py")
_spec = importlib.util.spec_from_file_location("cogoaltis_safety", _SAFETY)
_safety = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_safety)
SafetyConfig, SafetyGate = _safety.SafetyConfig, _safety.SafetyGate


class StimMarker:
    def __init__(self, out_dir, warmup_min=None):
        cfg = SafetyConfig(armed=False, warmup_min=C.MARK_WARMUP_MIN if warmup_min is None else warmup_min,
                           min_conf=0.0, gap_s=C.MARK_GAP_S, max_per_hour=C.MARK_MAX_PER_HOUR,
                           max_per_night=C.MARK_MAX_PER_NIGHT, stop_file="")
        self.gate = SafetyGate(cfg=cfg, log_path=os.path.join(out_dir, "stim_requests.csv"))
        self.marks = []                       # 마커가 찍힌 시각(초)

    def request(self, t, stage, quality_ok, info=None):
        """반환: True = 마커 기록(이때 자극을 요청했을 것), False = 보류"""
        info = dict(info or {})
        info["수면"] = STAGE_LABEL.get(stage, "판정 전")
        if C.MARK_SKIP_AWAKE and stage == STAGE_W:
            self.gate._write(t, "보류", "각성 상태(수면 단계 추정)", "BRUX", 1.0, info)
            return False
        ok = self.gate.request(t, "BRUX", 1.0, quality_ok=quality_ok, info=info)
        if ok:
            self.marks.append(t)
        return ok

    def last_reason(self):
        return self.gate.log[-1]["reason"] if self.gate.log else ""

    def summary(self):
        reasons = {}
        for r in self.gate.log:
            if r["action"] == "보류":
                key = r["reason"].split(" ")[0] if r["reason"].startswith("시작 유예") else r["reason"]
                reasons[key] = reasons.get(key, 0) + 1
        return dict(마커=len(self.marks), 보류=sum(reasons.values()), 보류_이유=reasons)
