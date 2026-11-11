#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大肥鱼的时间感
  让她知道「现在几点、星期几、她自己这会儿在干嘛」
  11-02 v1.17.5 小明：「睡觉是全局 但是目前有个问题是太爱睡觉 不用睡得很久 可能北京时间凌晨3点到早上8点差不多了」
    → 真睡只在北京时间 3–8 点（前面半小时「该睡了」、后面一小时「刚起床」）；她的一天从 3 点算起（睡着那会儿翻篇 跟以前 5 点一样是入睡的点）
    → 她在哪个对话里说了要睡 = 全局睡着（commit.py 记；SLEEP_SRC 接进来 state() 照它说「在睡觉」：在线状态、日程、空间都跟着）
"""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:                              # utf-8-sig：记事本存的「UTF-8 带 BOM」也认（10-28 审查 panel-7 复查：以前读不出来就悄悄按电脑的时间）
    _CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
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


DAY0_H = 3                        # 她的一天从北京时间几点算起（11-02 v1.17.5：5 → 3 = 她入睡的点；日程文件 / 日记 / 梦 / 整首名额都按这个分天）


def her_day(ts=None):
    """她的「一天」从凌晨 3 点（她睡着的点）算起：凌晨 2 点还算前一天（11-02 v1.17.5 以前是 5 点）"""
    return (dt(ts) - timedelta(hours=DAY0_H)).strftime("%Y-%m-%d")


# (起, 止, 状态, 说话的样子)：起止是北京时间的钟点 可以带小数（2.5 = 2:30）
# 11-02 v1.17.5：真睡只在 3–8 点（以前 03–05 该睡了 / 05–12 在睡觉 / 12–14 刚起床 —— 小明：「太爱睡觉」）
SCHEDULE = [
    (0, 2.5, "熬夜中", "精神反而挺好 在刷视频或者打游戏"),
    (2.5, 3, "该睡了", "困了 回得慢 随时可能去睡"),
    (3, 8, "在睡觉", "被消息吵醒的 迷糊 字少 可能带点起床气"),
    (8, 9, "刚起床", "还没完全醒 在想吃啥 第一顿算早饭"),
    (9, 12, "上午", "醒了 在摸鱼 刷视频"),
    (12, 18, "下午", "醒了 在摸鱼 刷视频"),
    (18, 24, "晚上", "一天里最精神的时候 话会多一点"),
]
# 夜猫子模式（11-07 v1.17.25 小明：「设置一个开关控制睡觉 就叫夜猫子模式 默认开启」）：开着 = 她不睡 ——
#   该睡了 / 在睡觉 / 刚起床那几个钟点都算熬夜中（只给状态 E1）；她说了要睡（commit）也不算睡着；控制台「叫醒她」用不上
OWL_SCHEDULE = [
    (0, 8, "熬夜中", "夜猫子模式：不睡觉 精神还行 在刷视频或者打游戏"),
    (8, 12, "上午", "醒着 在摸鱼 刷视频"),
    (12, 18, "下午", "醒着 在摸鱼 刷视频"),
    (18, 24, "晚上", "一天里最精神的时候 话会多一点"),
]


def owl():
    """夜猫子模式开着吗（control.night_owl；读不了 → 当开着：默认就是开的）"""
    try:
        import control                  # 在函数里 import：control 也 import 了 clock
        return bool(control.night_owl())
    except Exception:
        return True


SLEEP_SRC = None      # 11-02 v1.17.5：bot.py 接 commit.sleep_state —— 她说了要睡（全局）→ (状态, 样子)；没睡 → None


def span(name):
    """SCHEDULE 里某个状态的 (起, 止)（钟点 可带小数）；没有 → None"""
    for a, b, nm, _ in SCHEDULE:
        if nm == name:
            return float(a), float(b)
    return None


# （11-10 v1.17.35 审查 V8-1：「她这个钟点在不在睡」只留这一个出口 —— 以前自拍 selfie / 晚回 catchup / 说到做到的规矩 commit.RULES /
#   日程 life 各自按「北京 3–8 点 = 睡」写死，夜猫子开着（她不睡）也照说「你是被叫醒的」「可以说刚睡醒」「3–8 点是真睡」「准备睡」，
#   跟 state() 给的「熬夜中」在同一轮提示里打架。现在那几处都来问下面两个；夜猫子关着时答案跟它们以前各自写死的一样）
def _in_sleep(ts=None):
    """这个钟点落在 SCHEDULE「在睡觉」那段里（默认 3–8 点；不看夜猫子）"""
    d = dt(ts)
    h = d.hour + d.minute / 60
    a, b = span("在睡觉") or (3.0, 8.0)
    return (a <= h < b) if a <= b else (h >= a or h < b)


def asleep_at(ts=None):
    """她这个钟点按作息在不在睡：夜猫子开着 → False（她不睡）；关着 → 钟点在「在睡觉」那段（默认 3–8 点）
    只看钟点 —— 她嘴上说了要睡（SLEEP_SRC）、控制台「叫醒她」照旧看 state()"""
    return (not owl()) and _in_sleep(ts)


def up_late(ts=None):
    """夜猫子开着、这个钟点本来是睡觉那段：她没睡、在熬夜 → OWL_SCHEDULE 里这个钟点的状态名（「熬夜中」跟 state() 给的一样）；不是 → 空字符串"""
    if not (owl() and _in_sleep(ts)):
        return ""
    d = dt(ts)
    h = d.hour + d.minute / 60
    return next((name for a, b, name, _ in OWL_SCHEDULE if a <= h < b), "熬夜中")


def night_span():
    """夜里「真睡」那段：(该睡了的起点, 在睡觉的终点)，默认 (2.5, 8)。在这段里说要睡 = 睡到早上（commit 用）"""
    s, p = span("在睡觉") or (3.0, 8.0), span("该睡了")
    return (p[0] if p and p[1] == s[0] else s[0]), s[1]


def in_night(ts=None):
    """这个时间在不在夜里真睡那段（含入睡前的「该睡了」）"""
    d = dt(ts)
    h = d.hour + d.minute / 60 + d.second / 3600
    a, b = night_span()
    return (a <= h < b) if a <= b else (h >= a or h < b)


def wake_ts(ts=None):
    """从 ts 起下一次「在睡觉」结束的时间戳（默认早上 8 点）"""
    d = dt(ts)
    end = (span("在睡觉") or (3.0, 8.0))[1]
    hh, mm = int(end), int(round((end - int(end)) * 60))
    w = d.replace(hour=hh % 24, minute=mm, second=0, microsecond=0)
    if w <= d:
        w += timedelta(days=1)
    return w.timestamp()


AWAKE = ("被叫醒了", "本来在睡 被老板叫醒了 有点迷糊 但醒着 能正常聊 会嘟囔两句困")


def awake_until():
    """控制台「叫醒她」（10-27）：data/control.json 里 awake.until（到点自动回去按作息睡）"""
    try:
        import control                  # 在函数里 import：control 也 import 了 clock
        return float((control.rules().get("awake") or {}).get("until") or 0)
    except Exception:
        return 0.0


def state():
    if owl():                          # 夜猫子模式（11-07 v1.17.25）：不睡 —— 说过要睡、控制台叫醒都不看
        n = now()
        h = n.hour + n.minute / 60
        for a, b, name, how in OWL_SCHEDULE:
            if a <= h < b:
                return name, how
        return "晚上", ""
    if callable(SLEEP_SRC):            # 她说了要睡（全局 11-02 v1.17.5）：哪个钟点都算在睡觉
        try:
            st = SLEEP_SRC()
            if st:
                return st
        except Exception:
            pass
    n = now()
    h = n.hour + n.minute / 60
    for a, b, name, how in SCHEDULE:
        if a <= h < b:
            if name in ("在睡觉", "该睡了") and awake_until() > time.time():
                return AWAKE
            return name, how
    return "晚上", ""


PERIODS = [(0, 5, "凌晨"), (5, 8, "清早"), (8, 11, "上午"), (11, 13, "中午"), (13, 17, "下午"), (17, 19, "傍晚"), (19, 23, "晚上"),
           (23, 24, "深夜")]


def period(h):
    return next((w for a, b, w in PERIODS if a <= int(h) < b), "")


def block(doing=None, time_said=None, exact=True):
    """doing：醒着、有日程时「你这会儿」换成日程里这段（11-01 v1.16.4 每日生活 life.doing()；None = 照作息）
    time_said：这段对话里她最近几句提过时间的说法（11-02 v1.17.3 C74 问题 4：「现在」块里的时间成了她最顺手的话头 ——
      48 条私聊回复 24 条拿「一点多还不睡」开头）→ 告诉她已经提过几次（只给状态 引的是她自己说过的）
    exact=False：她这段对话里已经提过时间、他这几句又跟时间无关 → 只给「凌晨 / 下午…」不给几点几分（时间只在跟话题有关时给）"""
    n = now()
    name, how = state()
    wk = WEEK[n.weekday()]
    hm = n.strftime('%H:%M') if exact else period(n.hour)
    s = (f"\n# 现在\n{n.strftime('%m月%d日')} 星期{wk} {hm}" + (f"（{TZ_LABEL}）" if TZ_LABEL else "") + "。"
         + (f"你这会儿：{doing}。\n" if doing else f"你这会儿：{name}（{how}）。\n")
         + "时间是真的 别说跟时间对不上的话（比如晚上十点说早安）。\n")
    try:  # 群友多在国内：问「北京时间」时别报自己这边的钟点
        from datetime import timezone
        bj = datetime.now(timezone(timedelta(hours=8)))
        if exact and bj.strftime('%H:%M') != n.strftime('%H:%M'):
            s += f"（北京时间是 {bj.strftime('%m月%d日 %H:%M')}：有人问北京时间/国内几点就报这个）\n"
    except Exception:
        pass
    if n.weekday() >= 5:
        s += "今天周末 群里人一般多一点。\n"
    if time_said:
        s += (f"（这段对话里你已经提过 {len(time_said)} 次时间（" + "".join(f"「{x}」" for x in list(time_said)[:2])
              + "）：时间是背景 他没提就别再说" + ("" if exact else "；具体几点他问了再给你") + "）\n")
    elif not exact:                        # 11-04 v1.17.18 人格回归：他没聊时间 → 只给时段（以前新对话第一轮总给几点几分 → 爱拿「这么晚」开头）
        s += "（他没聊时间：几点几分没给你 —— 时间是背景 别拿它开话头；他问了再给你）\n"
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
    """粗一点的「多久以前」：按她的一天（凌晨 3 点起算 11-02 v1.17.5 以前 5 点）说 今天 / 昨天 / 前天 / 几天前 —— 一天只变一次
    放进系统提示前半段的用（10-26 前缀缓存：提示词前面的字不变 DeepSeek 才能命中缓存 便宜 50 倍）"""
    if not ts:
        return "很久以前"
    try:
        then = datetime.fromtimestamp(float(ts), _tz) if _tz else datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError, OverflowError):
        return "很久以前"
    n = (datetime.strptime(her_day(), "%Y-%m-%d").date() - (then - timedelta(hours=DAY0_H)).date()).days
    if n <= 0:
        return "今天"
    return "昨天" if n == 1 else "前天" if n == 2 else f"{n}天前" if n < 7 else "挺久以前"
