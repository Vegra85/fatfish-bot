#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
群聊节奏：观望 → 在线（跟着聊一阵）→ 收尾潜水（借鉴 qq-bridge 的「一代仿真」状态机）
  观望（lurk）：平时的样子 —— 被叫了才回 偶尔插一嘴（bot.py 里原来的 should_interject）
  在线（active）：她在群里说上话之后 会「在线」一阵子（默认 8~15 分钟）
      这期间没点她名的话也会挑着接（有概率、两句之间有间隔、每轮有上限）
      别人在 @ 别人 / 引用别人说话 = 不是跟她说的 不抢话
  冷场：在线时大家突然都不说话了 → 有一定概率顺着话题再试探一句；还是没人理 → 安静回观望
  到点：聊天还在继续 → 自然说句收尾话（「我先潜水了」「去吃饭了 你们聊」）再回观望
        收尾后一段时间内不主动插话（被叫了照样回）
  状态只在内存里 重启就回到观望
"""
import random
import time

import control

DEFAULTS = {
    "enabled": True,
    "minutes": [8, 15],        # 每次在线多久（随机）
    "max_minutes": 40,         # 被一直 @ 着也最多在线这么久
    "reply_prob": 0.3,         # 在线时 没点她名的消息 接话的概率
    "pic_factor": 0.3,         # 光发图/表情包的消息 概率再打个折（不然她见图就接 显得话痨）
    "min_gap_sec": 45,         # 在线时主动接话 两句之间至少隔多久
    "max_replies": 6,          # 每次在线 主动接话（没被点名的）最多几次
    "idle_min": 5,             # 在线时多久没人说话算冷场
    "probe_prob": 0.3,         # 冷场时试探一句的概率
    "probe_wait_min": 2,       # 试探后等多久没人理就回观望
    "exit_line": True,         # 到点时说句收尾话
    "exit_line_prob": 0.7,
    "exit_if_chat_within_min": 3,   # 到点时 最近几分钟还有人在聊 才说收尾话（没人聊就默默走）
    "cooldown_min": 10,        # 收尾后多久内不主动插话
}

_g = {}          # gid -> 状态
_cfg = dict(DEFAULTS)


def configure(cfg):
    _cfg.update({k: v for k, v in (cfg or {}).items() if not k.startswith("_")})


def _eff():
    """火力全开时：在线更久、没点名的话更爱接、两句间隔更短、收尾后冷却更短"""
    if not control.full_on():
        return _cfg
    f = control.full_cfg()
    c = dict(_cfg)
    c["reply_prob"] = max(float(_cfg.get("reply_prob", 0.3)), float(f["rhythm_reply_prob"]))
    c["min_gap_sec"] = min(float(_cfg.get("min_gap_sec", 45)), float(f["rhythm_min_gap_sec"]))
    c["max_replies"] = max(int(_cfg.get("max_replies", 6)), int(f["rhythm_max_replies"]))
    lo, hi = (_cfg.get("minutes") or [8, 15])[:2]
    flo, fhi = (f["rhythm_minutes"] or [15, 30])[:2]
    c["minutes"] = [max(lo, flo), max(hi, fhi)]
    c["max_minutes"] = max(float(_cfg.get("max_minutes", 40)), float(fhi) * 2)
    c["cooldown_min"] = min(float(_cfg.get("cooldown_min", 10)), float(f["rhythm_cooldown_min"]))
    return c


def enabled():
    return bool(_cfg.get("enabled", True))


def _st(gid):
    return _g.setdefault(str(gid), {"mode": "lurk", "since": 0, "until": 0, "last_other": 0, "last_me": 0,
                                    "free_replies": 0, "probed": 0, "cool_until": 0})


def heard(gid, now=None):
    """群里有人（不是她）说话了"""
    _st(gid)["last_other"] = now or time.time()


def spoke(gid, addressed, now=None):
    """她在群里说了话。addressed=别人点名找她（@/叫名字/引用她/刚叫过她）"""
    _c = _eff()
    if not enabled():
        return
    now = now or time.time()
    st = _st(gid)
    st["last_me"] = now
    st["probed"] = 0
    if st["mode"] != "active":
        lo, hi = (_c.get("minutes") or [8, 15])[:2]
        st.update(mode="active", since=now, until=now + random.uniform(lo, hi) * 60, free_replies=0)
        return "enter"
    if addressed:            # 有人一直找她 就多待一会儿（有上限）
        cap = st["since"] + float(_c.get("max_minutes", 40)) * 60
        st["until"] = min(cap, max(st["until"], now + 5 * 60))
    else:
        st["free_replies"] += 1
    return None


def is_active(gid):
    return enabled() and _st(gid)["mode"] == "active"


def cooling(gid, now=None):
    return (now or time.time()) < _st(gid).get("cool_until", 0)


def minutes_in(gid, now=None):
    st = _st(gid)
    return int(((now or time.time()) - st["since"]) / 60) if st["mode"] == "active" else 0


def want_reply(gid, to_other=False, now=None, only_pic=False):
    """在线时 一条没点她名的消息 要不要接"""
    _c = _eff()
    now = now or time.time()
    st = _st(gid)
    if st["mode"] != "active" or to_other:
        return False
    if now - max(st["last_me"], st.get("last_try", 0)) < float(_c.get("min_gap_sec", 45)):
        return False
    if st["free_replies"] >= int(_c.get("max_replies", 6)):
        return False
    p = float(_c.get("reply_prob", 0.3)) * (float(_c.get("pic_factor", 0.3)) if only_pic else 1)
    if random.random() < p:
        st["last_try"] = now          # 想接（哪怕最后她选择不说）也算一次 免得连着想
        return True
    return False


def tick(gid, now=None):
    """后台定时看一眼 返回要做的事：None / "probe"（冷场试探） / "exit"（收尾潜水） / "quiet"（默默回观望）"""
    if not enabled():
        return None
    now = now or time.time()
    st = _st(gid)
    if st["mode"] != "active":
        return None
    last_any = max(st["last_other"], st["last_me"])
    if now >= st["until"]:
        recent = now - st["last_other"] <= float(_cfg.get("exit_if_chat_within_min", 3)) * 60
        _to_lurk(st, now)
        if recent and _cfg.get("exit_line", True) and random.random() < float(_cfg.get("exit_line_prob", 0.7)):
            return "exit"
        return "quiet"
    if st["probed"]:
        if st["last_other"] > st["probed"]:
            st["probed"] = 0                  # 有人接话了 接着聊
        elif now - st["probed"] >= float(_cfg.get("probe_wait_min", 2)) * 60:
            _to_lurk(st, now, cool=False)     # 试探了也没人理 安静回观望
            return "quiet"
        else:
            return None
    if now - last_any >= float(_cfg.get("idle_min", 5)) * 60:
        if random.random() < float(_cfg.get("probe_prob", 0.3)):
            st["probed"] = now
            return "probe"
        _to_lurk(st, now, cool=False)
        return "quiet"
    return None


def _to_lurk(st, now, cool=True):
    _c = _eff()
    st["mode"] = "lurk"
    st["probed"] = 0
    if cool:
        st["cool_until"] = now + float(_c.get("cooldown_min", 10)) * 60


def active_groups():
    return [g for g, st in _g.items() if st["mode"] == "active"]


def status(gid):
    st = _st(gid)
    if st["mode"] != "active":
        return "观望" + ("（刚收尾 冷却中）" if cooling(gid) else "")
    return f"在线 已聊{minutes_in(gid)}分钟 还剩{max(0, int((st['until'] - time.time()) / 60))}分钟 主动接了{st['free_replies']}句"


PROBE_SCENE = ("群里刚才还在聊（你也在聊） 现在突然安静了几分钟。"
               "你可以顺着刚才的话题再补一句、抛个相关的小问题、或者吐槽一句 —— 就一句 短的 像真人随口一说。"
               "别说「人呢」「怎么不说话了」「好安静」这种。没什么想说的就只写 [[不说]]。")

EXIT_SCENE = ("你在群里已经跟大家聊了一阵子 现在想下线/潜水了（去吃饭、去睡觉、去摸鱼、被老板叫走…随你 看现在几点）。"
              "自然地说一句很短的收尾话 像真人退出聊天那样 可以带一点刚才聊的内容。"
              "只说一句 别提问 别总结 别说「下次再聊」这种客套。不想说就只写 [[不说]]。")
