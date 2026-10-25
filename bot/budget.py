#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
省 token：花得越多 回得越少（递减模型）

  回复意愿  w(u) = 底线 + (1 - 底线) / (1 + (u / u半)^k)

  u     最近 24 小时花了多少钱（美元：DeepSeek token + MiniMax 语音 + 搜索，按 config → panel.prices 折算）
  u半   花到这么多 意愿就掉到一半左右（config → budget.half_usd，默认 0.3）
  k     形状，小于 1：一开始掉得很快（很快到一半），之后越来越慢地往下掉（默认 0.6）
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
"""
import random
import time

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


def usage():
    return stats.spent(float(_cfg.get("window_hours", 24)))


def status():
    u = usage()
    return {"enabled": bool(_cfg.get("enabled", True)), "spent": round(u, 4), "will": round(curve(u), 3),
            "half_usd": _cfg["half_usd"], "shape": _cfg["shape"], "floor": _cfg["floor"],
            "window_hours": _cfg.get("window_hours", 24), "powers": dict(_cfg["powers"]), "full": control.full_state()}


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
