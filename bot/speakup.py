#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
群里主动冒泡（借鉴 dsh-speak-up 的掷骰子思路）
  每隔一段时间给每个群掷一次骰子 —— 掷骰子本身不花 token，只有命中了才去想要说什么
  命中线 = 基础概率（忙时低 闲时高）× 时机因子 + 连续没中的加成
    时机因子：她 20 分钟内刚说过话 ×0.15；她 6 小时以上没说话 ×1.5
    群里还在热聊（几分钟内有人说话）不冒泡 —— 冒泡是给冷场准备的
    她上次冒泡没人理 → 下次更难开口（不对着空气连发）
  试运行（dry_run）：只把「本来要说的话」写进日志 不真发
"""
import random
import time
from pathlib import Path

import clock
import safeio

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "speakup.json"


def load():
    try:
        d = safeio.read_json(STATE, None, print, busy_raise=True)   # 读坏了先另存 .bad
    except safeio.Busy:            # 一直打不开：这次当没有、不存（10-28 Gemini 复查）
        return safeio.Unsaved({})
    return d if isinstance(d, dict) else {}


def save(d):
    if isinstance(d, safeio.Unsaved):
        return
    safeio.write_json(STATE, d)   # 原子写（10-28 Gemini 复查：以前直接覆盖着写 重启时被硬杀在半中间就坏了）


def peak(now=None):
    """忙时：工作日 9-12 14-18（大家在上课上班）"""
    n = now or clock.now()
    return n.weekday() < 5 and (9 <= n.hour < 12 or 14 <= n.hour < 18)


def roll(gid, mem, cfg):
    """掷骰子：返回 (要不要说, 说明)"""
    d = load()
    if isinstance(d, safeio.Unsaved):      # 冒泡记录一时打不开：今天冒了几次都不知道 → 这次不冒（10-28 Gemini 复查）
        return False, "冒泡记录一时打不开"
    st = d.get(gid, {})
    now = time.time()
    hist = mem.get("history") or []
    last_any = max((h.get("ts", 0) for h in hist), default=0)
    last_me = max((h.get("ts", 0) for h in hist if h.get("role") == "assistant"), default=0)
    quiet_min = float(cfg.get("quiet_min", 30))
    if now - last_any < quiet_min * 60:
        return False, f"群里{int((now - last_any) / 60)}分钟前还有人说话 不算冷场"
    today = clock.today()
    if st.get("day") == today and st.get("count", 0) >= int(cfg.get("daily_max", 6)):
        return False, "今天冒泡次数用完了"
    base = float(cfg.get("peak_base", 0.10) if peak() else cfg.get("off_base", 0.30))
    since_me = now - last_me if last_me else 1e9
    factor = 0.15 if since_me < 20 * 60 else (1.5 if since_me > 6 * 3600 else 1.0)
    # 上次是她冒的泡、之后没人接 → 别对着空气连发
    if hist and hist[-1].get("role") == "assistant" and st.get("last_bubble", 0) >= last_me - 1:
        factor *= 0.2 if since_me < 12 * 3600 else 0.6
    bonus = min(float(cfg.get("miss_max", 0.4)), st.get("misses", 0) * float(cfg.get("miss_bonus", 0.1)))
    line = min(1.0, base * factor + bonus)
    r = random.random()
    return r < line, f"命中线 {line:.0%}（基础{base:.0%}×{factor:.2f}+连空{bonus:.0%}）掷出 {r:.2f}"


def record(gid, hit, spoke=False):
    d = load()
    st = d.setdefault(gid, {})
    today = clock.today()
    if st.get("day") != today:
        st["day"], st["count"] = today, 0
    if hit:
        st["misses"] = 0
        if spoke:
            st["count"] = st.get("count", 0) + 1
            st["last_bubble"] = time.time()
    else:
        st["misses"] = st.get("misses", 0) + 1
    st["last_roll"] = time.time()
    save(d)


def scene(mem):
    hist = mem.get("history") or []
    last_any = max((h.get("ts", 0) for h in hist), default=0)
    mins = int((time.time() - last_any) / 60) if last_any else 0
    quiet = f"{mins // 60}个多小时" if mins >= 90 else f"{mins}分钟"
    return (f"群里已经安静了{quiet}。没人叫你 是你自己想冒个泡。"
            "找一个由头开口：你刚在 B 站刷到的东西、你的日记里的事、群里之前没聊完的话题、"
            "你记得的某个群友的近况（问一句）、或者你此刻在干嘛。"
            "一两句短的 像随手发的群消息 可以配表情包。"
            "别说「好安静」「有人吗」「大家在干嘛」这种空话 别自我介绍。"
            "实在没什么想说的 就只写 [[不说]]。")
