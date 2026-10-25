#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
上线补回：下线期间（重启、掉线）没看到的消息 上线后补看一遍、该回的回
  - 每个对话按聊天记录里最后一条的时间 从 NapCat 拉之后的历史消息（一页 40 条 一直往回翻到下线以前）
    （get_recent_contact 找哪些对话有新消息 → get_friend_msg_history / get_group_msg_history）
  - 私聊：对方在她下线时发了消息 就回（太久以前的不管）
  - 群聊：下线期间有人 @ 她 / 叫她名字 / 引用她 才回 每个找过她的人各回一条（引用他最后那句）；没人找她的只记下 当聊天背景
  - 私聊里所有没回的（不只是下线期间：重启时正在攒的、生成失败的、没发出去的…）开机时和之后每 3 分钟扫一遍 都回
    （bot.py _sweep_private；sweep_* 这几项）
  - 回得晚：提示词里告诉她「这是多久之前的消息 你刚上线才看到」
      几分钟 → 开头带一句「刚看到」「刚才掉线了」
      几小时 → 先说「才看到」 事情可能已经过去了 还要紧再回
      隔天   → 简短说一句才看到 + 一句回应
      那段时间正好是她的睡觉时间 → 也可以说刚睡醒
"""
import time
from datetime import datetime

DEFAULTS = {
    "enabled": True,
    "wait_sec": 5,              # 连上以后等几秒 让 QQ 把离线消息同步下来
    "recent_contacts": 50,      # 看最近多少个会话
    "fetch_count": 40,          # 每次拉多少条
    "fetch_pages_group": 15,    # 群聊最多往回翻几次（40×15 = 600 条 忙的群下线一小时就几百条）
    "fetch_pages_private": 5,   # 私聊最多往回翻几次
    "max_private_hours": 168,   # 私聊：这么久以前的消息不管了（一周：电脑关了几天回来也补）
    "max_group_hours": 6,       # 群聊：这么久以前的消息不管了
    "max_people_per_group": 6,  # 一个群里找过她的人 最多各回几个
    "max_replies": 20,          # 一次上线最多补回几条（私聊优先）
    "late_min": 3,              # 晚了这么多分钟才算「晚回」
    "sweep_private": True,      # 私聊里她没回的消息 开机时和之后定时都扫一遍 都回
    "sweep_every_sec": 180,     # 多久扫一次
    "sweep_min_age_sec": 60,    # 发来多久了还没回才算（刚来的正在攒 别抢）
    "sweep_hours": 168,         # 这么久以前没回的就不管了
    "sweep_max_tries": 5,       # 同一批消息回了几次都没回成（生成失败/发不出去）就先放下 等他再发新的
}
_cfg = dict(DEFAULTS)


def configure(cfg):
    _cfg.update({k: v for k, v in (cfg or {}).items() if not str(k).startswith("_")})


def cfg():
    return _cfg


def _span(sec):
    sec = max(0, int(sec))
    if sec < 3600:
        return f"{max(1, sec // 60)} 分钟"
    if sec < 86400:
        h = sec / 3600
        return f"{int(h)} 个多小时" if h - int(h) >= 0.3 else f"{int(h)} 小时"
    return f"{sec // 86400} 天"


def _slept(t_from, t_to, tz=None, sleep=(5, 12)):
    """这段时间里有没有她的睡觉时间（按她的时区）"""
    t = t_from
    while t <= t_to:
        h = datetime.fromtimestamp(t, tz).hour if tz else datetime.fromtimestamp(t).hour
        if sleep[0] <= h < sleep[1]:
            return True
        t += 1800
    return False


def late_note(oldest, newest, now=None, scene="private", tz=None, cause="offline"):
    """回得晚的时候加进提示词的一段话（没晚就返回空）
    cause：offline = 下线期间的（可以说刚才掉线了）；missed = 在线但漏回的（别说掉线 就说刚看到）"""
    now = now or time.time()
    wait = now - oldest
    if wait < float(_cfg["late_min"]) * 60:
        return ""
    when = f"是{_span(wait)}前发的"
    if newest and now - newest >= 60 and _span(now - newest) != _span(wait):
        when = f"最早的是{_span(wait)}前、最近的是{_span(now - newest)}前发的"
    if wait < 3600 and cause == "missed":
        s = (f"\n【晚回了】这几条消息{when} 你刚才没顾上 现在才看到。"
             "像真人晚回消息那样：开头随口带一句（「刚看到」「才看到」这种 一句就够 别道歉半天 别说掉线）再正常回。")
    elif wait < 3600:
        s = (f"\n【晚回了】这几条消息{when} 你刚才掉线了一会儿 现在才看到。"
             "像真人晚回消息那样：开头随口带一句（「刚看到」「刚才掉线了」这种 一句就够 别道歉半天）再正常回。")
    elif wait < 12 * 3600:
        s = (f"\n【晚回了很久】这几条消息{when} " + ("你一直没顾上看" if cause == "missed" else "你下线了这么久") + " 现在才看到。"
             "先说一句「才看到」之类的 再回；那时候的事可能已经过去了 还要紧就回 不要紧就简单接一句。")
    else:
        s = (f"\n【隔了很久才回】这几条消息{when} 现在才看到。"
             "简短说一句才看到 再回一句就好 别像刚收到一样热情地展开。")
    if wait >= 1800 and _slept(oldest, now, tz):
        s += "那段时间正好是你睡觉的点 也可以说刚睡醒。"
    s += "别说具体是什么故障。"
    if scene == "group":
        s += "群里的话题可能早就过去了 只回找你的那句 别把下线期间群里聊的都回一遍。"
    return s + "\n"


def summary(rows):
    """日志用：[(对话, 新消息数, 结论)] → 一行"""
    return " / ".join(f"{k} {n}条→{why}" for k, n, why in rows)
