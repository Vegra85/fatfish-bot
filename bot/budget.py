#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
省 token：花得越多 回得越少（递减模型）

  回复意愿  w(u) = 底线 + (1 - 底线) / (1 + (u / u半)^k)

  u     最近 24 小时花了多少钱（美元：DeepSeek token + MiniMax 语音 + 搜索，按 config → panel.prices 折算）
  u半   花到这么多 意愿就掉到一半左右（config → budget.half_usd，默认 0.3）
  k     形状，小于 1：一开始掉得很快（很快到一半），之后越来越慢地往下掉（默认 0.6 省着花）
        大于 1：前面几乎不掉 快花到 u半 才往下掉（按预算花）—— 作者 10-28 按每天 ¥15 的预算填 u半 1.69（≈ ¥12）、k 4：
        花 ¥5 → 97%   ¥7 → 91%   ¥10 → 71%   ¥12 → 55%   ¥15 → 36%（那时被 @ 的还回 90%、接话 8%、插话 5%）
  底线  再怎么花也留着的意愿（默认 0.1）

  例（默认参数）：花 $0.05 → 77%   $0.1 → 69%   $0.3 → 55%   $0.6 → 46%   $1 → 39%   $2 → 32%   $5 → 24%

不同场合用同一条曲线的不同次方（次方越小越不受影响）：
  老板              不限
  私聊              w^0.5
  群里 @她/叫她/引用她  w^0.1   （被找的时候几乎都回：意愿 63% 时 95%、掉到底线也有 79%）
  群里没 @ 的接话      w^2.5   （省 token 主要省在这：猜「是不是在跟她说」要多花一次调用）
  群里自己插话        w^3
  冒泡/冷场试探/收尾/主动找人  w^3
同一个人被跳过之后又来找她：每被跳过一次 概率开一次方（越执着越容易回）
火力全开（控制台 / 老板私聊「火力全开」）：私聊、找她、接话、插话 都不掷了（插话还是 bot.py 那边挑着接）；冒泡/主动 照常
控制台「回复意愿 从现在重新算」（10-27）：花费只从点的那一刻算起（统计里的钱不动）；过了统计窗口（24 小时）自动失效
"""
import random
import time

import clock
import control
import stats

DEFAULTS = {
    "enabled": True,
    "half_usd": 0.3,
    "shape": 0.6,
    "floor": 0.1,
    "window_hours": 24,
    "powers": {"private": 0.5, "direct": 0.1, "follow": 2.5, "chat": 3.0, "extra": 3.0},
}
FULL_KINDS = ("private", "direct", "follow", "chat")   # 火力全开时不省的
KIND_NAME = {"private": "私聊", "direct": "找她", "follow": "接话", "chat": "插话", "extra": "冒泡/主动"}
_cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
_skips = {}          # (对话, QQ) -> [被跳过的时间]


def configure(cfg):
    for k, v in (cfg or {}).items():
        if str(k).startswith("_"):
            continue
        if k == "powers" and isinstance(v, dict):
            _cfg["powers"].update({a: float(b) for a, b in v.items() if isinstance(b, (int, float))})
        elif k in DEFAULTS:
            _cfg[k] = v


def cfg():
    return _cfg


def curve(u, half=None, shape=None, floor=None):
    """回复意愿 0~1"""
    half = float(_cfg["half_usd"] if half is None else half)
    shape = float(_cfg["shape"] if shape is None else shape)
    floor = float(_cfg["floor"] if floor is None else floor)
    u = max(0.0, float(u))
    if half <= 0:
        return 1.0
    return floor + (1.0 - floor) / (1.0 + (u / half) ** shape)


def reset_state(now=None, window=None):
    """控制台「回复意愿 从现在重新算」（10-27）：{"ts": 点的时间, "base": 那一小时已经花了的} / None（没点 或者已经过了统计窗口）"""
    r = control.rules().get("budget_reset") or {}
    try:
        ts = float(r.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    now = now or time.time()
    if ts <= 0 or now - ts > float(window or _cfg.get("window_hours", 24)) * 3600:
        return None
    return {"ts": ts, "base": float(r.get("base") or 0), "by": r.get("by") or ""}


def usage(now=None, hours_dict=None, window=None):
    """最近 window_hours 小时花了多少（美元）；控制台点过「从现在重新算」→ 只算点了以后花的
    （统计按整点记：点的那一小时 减掉点的时候这一小时已经花了的）。hours_dict：控制台自己读的 stats.json 里的 hours"""
    now = now or time.time()
    hours = float(window or _cfg.get("window_hours", 24))
    hs = hours_dict if hours_dict is not None else (stats._load().get("hours") or {})
    keys = {clock.strf("%Y-%m-%d %H", now - i * 3600) for i in range(max(1, int(hours)))}
    r = reset_state(now, hours)
    if not r:
        return sum(v for k, v in hs.items() if k in keys)
    hk = clock.strf("%Y-%m-%d %H", r["ts"])
    return sum(v for k, v in hs.items() if k in keys and k > hk) + max(0.0, float(hs.get(hk, 0)) - r["base"])


def reset_now(by="控制台", hours_dict=None):
    """回复意愿从现在起重新算（不动统计里的花费 只是省 token 这条曲线从 0 开始）"""
    now = time.time()
    hs = hours_dict if hours_dict is not None else (stats._load().get("hours") or {})
    base = float(hs.get(clock.strf("%Y-%m-%d %H", now), 0))
    control._edit(lambda d: d.__setitem__("budget_reset", {"ts": now, "base": base, "by": str(by)[:20]}))
    return status()


def reset_clear():
    control._edit(lambda d: d.pop("budget_reset", None))
    return status()


def status():
    u = usage()
    r = reset_state()
    return {"enabled": bool(_cfg.get("enabled", True)), "spent": round(u, 4), "will": round(curve(u), 3),
            "half_usd": _cfg["half_usd"], "shape": _cfg["shape"], "floor": _cfg["floor"],
            "window_hours": _cfg.get("window_hours", 24), "powers": dict(_cfg["powers"]), "full": control.full_state(),
            "reset": r, "spent_all": round(stats.spent(float(_cfg.get("window_hours", 24))), 4) if r else round(u, 4)}


def allow(kind, owner=False, conv="", uid="", now=None):
    """这次回不回 → (要不要回, 说明)。kind 见上面的表"""
    if owner or not _cfg.get("enabled", True):
        return True, ""
    if kind in FULL_KINDS and control.full_on(now):
        return True, "火力全开"
    now = now or time.time()
    u = usage()
    w = curve(u)
    p = w ** float(_cfg["powers"].get(kind, 1.0))
    k = (conv, str(uid))
    skipped = [t for t in _skips.get(k, []) if now - t < 900] if uid else []
    if skipped and kind in ("private", "direct", "follow"):
        p = p ** (1.0 / (1 + len(skipped)))
    roll = random.random()
    why = f"最近{int(_cfg.get('window_hours', 24))}小时花了 ${u:.2f} · 意愿 {w:.0%} · {KIND_NAME.get(kind, kind)} {p:.0%} · 掷出 {roll:.2f}"
    if roll < p:
        _skips.pop(k, None)
        return True, why
    if uid:
        _skips[k] = skipped + [now]
    return False, why
