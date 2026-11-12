"""감지 결과 vs 정답 라벨 비교: 이벤트 단위 / 프레임 단위 Precision, Recall, F1."""
import numpy as np

import config as C


def _overlap(a, b):
    return min(a[1], b[1]) - max(a[0], b[0]) > 0


def _prf(tp_p, n_pred, tp_t, n_true):
    p = tp_p / n_pred if n_pred else 0.0
    r = tp_t / n_true if n_true else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def event_metrics(pred, true):
    """예측이 정답과 조금이라도 겹치면 맞춘 것으로 인정."""
    tp_t = sum(any(_overlap(t, p) for p in pred) for t in true)
    tp_p = sum(any(_overlap(p, t) for t in true) for p in pred)
    p, r, f = _prf(tp_p, len(pred), tp_t, len(true))
    return dict(precision=p, recall=r, f1=f, n_pred=len(pred), n_true=len(true),
                tp=tp_t, fp=len(pred) - tp_p, fn=len(true) - tp_t)


def frame_metrics(pred, true, duration, res=C.FRAME_EVAL_S):
    n = int(duration / res)

    def mask(events):
        m = np.zeros(n, bool)
        for s, e in events:
            m[int(s / res):int(np.ceil(e / res))] = True
        return m

    mp, mt = mask(pred), mask(true)
    tp = (mp & mt).sum()
    p = tp / mp.sum() if mp.sum() else 0.0
    r = tp / mt.sum() if mt.sum() else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return dict(precision=p, recall=r, f1=f)
