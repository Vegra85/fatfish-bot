#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大肥鱼的时间感
  让她知道「现在几点、星期几、她自己这会儿在干嘛」
  作息跟 persona 一致：下午起床，凌晨三点还醒着
"""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:
    _CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
except Exception:                 # 控制台 / 小工具在没有 config 的地方 import 也别崩
    _CFG = {}
_TZ_NAME = (_CFG.get("soul") or {}).get("timezone") or ""
# 中国 1991 年以后没有夏令时：北京时间就是固定的 UTC+8 —— 不用装 tzdata（Windows 上 zoneinfo 没有 tzdata 会加载失败）
FIXED = {"Asia/Shanghai": 8, "Asia/Chongqing": 8, "Asia/Harbin": 8, "Asia/Urumqi": 8, "PRC": 8, "Asia/Hong_Kong": 8,
         "Asia/Macau": 8, "Asia/Taipei": 8, "Asia/Singapore": 8, "Asia/Tokyo": 9}

_tz = None
if _TZ_NAME in FIXED:
    _tz = timezone(timedelta(hours=FIXED[_TZ_NAME]), "北京时间" if FIXED[_TZ_NAME] == 8 else _TZ_NAME)
elif _TZ_NAME:
    try:
        from zoneinfo import ZoneInfo
        _tz = ZoneInfo(_TZ_NAME)
    except Exception as e:  # Windows 上没装 tzdata 会走到这里
        print(f"[时间] 时区 {_TZ_NAME} 加载失败（pip install tzdata 可修）: {e}，改用本机时间")
        _tz = None
TZ_LABEL = ("北京时间" if FIXED.get(_TZ_NAME) == 8 else _TZ_NAME) if _tz else ""

WEEK = "一二三四五六日"


def now():
    return datetime.now(_tz) if _tz else datetime.now()


def dt(ts=None):
    """时间戳 → 她那边的时间（10-27 起是北京时间）；None = 现在"""
    if ts is None:
        return now()
    return datetime.fromtimestamp(float(ts), _tz) if _tz else datetime.fromtimestamp(float(ts))


def strf(fmt, ts=None):
    """time.strftime 的替身：按她的时区（10-27 小明：以后总时区以北京时间为准 —— 日志、按天算的数、文件夹名都用这个）"""
    return dt(ts).strftime(fmt)


def hour():
    return now().hour


def today():
    return now().strftime("%Y-%m-%d")


def her_day():
    """她的「一天」从早上 5 点算起：凌晨 3 点还算前一天"""
    return (now() - timedelta(hours=5)).strftime("%Y-%m-%d")


# (起, 止, 状态, 说话的样子)
SCHEDULE = [
    (0, 3, "熬夜中", "精神反而挺好 在刷视频或者打游戏"),
    (3, 5, "该睡了", "困到不行 回得慢 随时会说「睡了」"),
    (5, 12, "在睡觉", "被消息吵醒的 迷糊 字少 可能带点起床气"),
    (12, 14, "刚起床", "还没完全醒 在想吃啥 第一顿算早饭"),
    (14, 18, "下午", "醒了 在摸鱼 刷视频"),
    (18, 24, "晚上", "一天里最精神的时候 话会多一点"),
]


def state():
    h = hour()
    for a, b, name, how in SCHEDULE:
        if a <= h < b:
            return name, how
    return "晚上", ""


def block():
    n = now()
    name, how = state()
    wk = WEEK[n.weekday()]
    s = (f"\n# 现在\n{n.strftime('%m月%d日')} 星期{wk} {n.strftime('%H:%M')}" + (f"（{TZ_LABEL}）" if TZ_LABEL else "") + "。"
         f"你这会儿：{name}（{how}）。\n"
         "时间是真的 别说跟时间对不上的话（比如晚上十点说早安）。\n")
    try:  # 群友多在国内：问「北京时间」时别报自己这边的钟点
        from datetime import timezone
        bj = datetime.now(timezone(timedelta(hours=8)))
        if bj.strftime('%H:%M') != n.strftime('%H:%M'):
            s += f"（北京时间是 {bj.strftime('%m月%d日 %H:%M')}：有人问北京时间/国内几点就报这个）\n"
    except Exception:
        pass
    if n.weekday() >= 5:
        s += "今天周末 群里人一般多一点。\n"
    return s


def ago(ts):
    """把时间戳说成人话"""
    if not ts:
        return "很久以前"
    d = time.time() - ts
    if d < 600:
        return "刚刚"
    if d < 3600:
        return f"{int(d / 60)}分钟前"
    if d < 86400:
        return f"{int(d / 3600)}小时前"
    if d < 2 * 86400:
        return "昨天"
    if d < 7 * 86400:
        return f"{int(d / 86400)}天前"
    return "挺久以前"


def day_ago(ts):
    """粗一点的「多久以前」：按她的一天（早上 5 点起算）说 今天 / 昨天 / 前天 / 几天前 —— 一天只变一次
    放进系统提示前半段的用（10-26 前缀缓存：提示词前面的字不变 DeepSeek 才能命中缓存 便宜 50 倍）"""
    if not ts:
        return "很久以前"
    try:
        then = datetime.fromtimestamp(float(ts), _tz) if _tz else datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError, OverflowError):
        return "很久以前"
    n = (datetime.strptime(her_day(), "%Y-%m-%d").date() - (then - timedelta(hours=5)).date()).days
    if n <= 0:
        return "今天"
    return "昨天" if n == 1 else "前天" if n == 2 else f"{n}天前" if n < 7 else "挺久以前"
