#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
群聊接话：没 @ 她、但其实是在接她话的消息 也要接上
  她在群里说完话后的一小会儿里（默认 150 秒），有人发了一条没 @ 别人、没引用别人的消息：
    先看几个信号打分 —— 紧跟着她说的、用「你」、在提问、跟她刚说的话有重合的词、
                        是她刚回过的人、她刚点名的人；中间又有别人插进来、光发张图 就扣分
    分高 → 当成在跟她说（不用 @ 也接）
    分低 → 不是跟她说的（还是按原来的节奏 偶尔插一嘴）
    拿不准 → 让模型看一眼最近几条判断「是不是在跟她说话」（几十个 token 很便宜）
  防止跟另一个机器人对聊停不下来：同一个人不 @ 她、她连着接话最多几轮（默认 5 轮）
    之后要有人 @ 她 / 叫她名字 / 引用她 才重新算；10 分钟没接也重新算
"""
import random
import re
import time

import control

DEFAULTS = {
    "enabled": True,
    "window_sec": 150,       # 她说完话之后多久之内的消息 才考虑是不是在接她的话
    "yes_score": 4.0,        # 分数到这个 直接当成在跟她说
    "no_score": 1.5,         # 分数不到这个 直接当成不是
    "ask_model": True,       # 中间的让模型判断
    "max_turns": 5,          # 同一个人不 @ 她 她最多连着接几轮（防两个机器人对聊停不下来）
    "owner_max_turns": 10,   # 老板多一点
    "turn_reset_min": 10,    # 多久没接 轮数清零
    "pic_prob": 0.4,         # 光发图/表情包 就算在接她 也只按这个概率回
}
_cfg = dict(DEFAULTS)
_g = {}        # gid -> {"t0","sent","text","answered": 她最近一轮说的话, "turns": {uid: [轮数, 上次时间]}}

_CJK = re.compile(r"[㐀-鿿]")
_STOP = set("什么 怎么 这个 那个 不是 没有 就是 你们 我们 他们 一下 一个 还是 可以 知道 觉得 现在 真的 这么 那么 "
            "然后 但是 因为 所以 已经 还有 这样 那样 有点 一点 大肥 肥鱼 鲸鱼 哈哈 呵呵 嘿嘿 啊啊 嗯嗯 哦哦 "
            "好的 是的 对的 还好 好吧 行吧 啊这 我们 咱们 一样".split())
_Q = re.compile(r"[?？]|吗|呢|啥|什么|怎么|怎样|如何|为什么|为啥|咋|哪|几|多少|是不是|有没有|行不行|好不好|能不能|要不要")


def configure(cfg):
    _cfg.update({k: v for k, v in (cfg or {}).items() if not str(k).startswith("_")})


def _eff():
    """火力全开时：窗口更长、打分门槛更低、连着接的轮数更多（更容易认出别人插进来是在跟她说）"""
    if not control.full_on():
        return _cfg
    f = control.full_cfg()
    c = dict(_cfg)
    c["window_sec"] = max(float(_cfg["window_sec"]), float(f["followup_window_sec"]))
    c["yes_score"] = min(float(_cfg["yes_score"]), float(f["yes_score"]))
    c["no_score"] = min(float(_cfg["no_score"]), float(f["no_score"]))
    c["max_turns"] = max(int(_cfg["max_turns"]), int(f["max_turns"]))
    c["owner_max_turns"] = max(int(_cfg["owner_max_turns"]), int(f["max_turns"]))
    c["ask_model"] = True
    return c


def _st(gid):
    return _g.setdefault(str(gid), {"t0": 0, "sent": 0, "text": "", "answered": set(), "turns": {}})


def note_speaking(gid, t0, text, answered, now=None):
    """她开始在群里发这一轮了（发的时候别人回她 也拿这一轮的话来比）
    t0 = 她看完消息开始想的时候；answered = 这一轮在回的人"""
    _st(gid).update(t0=t0, sent=now or time.time(), text=text or "", answered={str(u) for u in answered or ()})


def note_sent(gid, now=None):
    """她这一轮发完了"""
    _st(gid)["sent"] = now or time.time()


def reset(gid, uid=None):
    """有人 @ 她 / 叫她名字 / 引用她：接话轮数重新算"""
    t = _st(gid)["turns"]
    if uid is None:
        t.clear()
    else:
        t.pop(str(uid), None)


def _bigrams(s):
    s = re.sub(r"\s+", "", (s or "").lower())
    return {s[i:i + 2] for i in range(len(s) - 1) if _CJK.match(s[i]) and _CJK.match(s[i + 1])} - _STOP


def _short(name):
    n = re.sub(r"[\d_\-\s]+$", "", name or "").strip()
    return n if len(n) >= 2 else (name or "")


def check(gid, uid, name, text, now, history, only_pic=False, is_owner=False, alias=None):
    """这条没点名的群消息 是不是在接她的话。返回 (结论, 原因, 分数)；结论 yes / no / ask"""
    if not _cfg.get("enabled", True):
        return "no", "", 0
    cfg = _eff()
    st = _st(gid)
    hist = [h for h in (history or []) if h.get("ts")]
    mine = [i for i, h in enumerate(hist) if h.get("role") == "assistant"]
    me_ts = hist[mine[-1]]["ts"] if mine else 0
    if st["text"] and st["t0"] >= me_ts - 1:          # 她这一轮（可能还在发）
        my_text, t0, t_me, answered = st["text"], st["t0"], st["sent"], st["answered"]
        after = [h for h in hist if h.get("role") == "user" and h["ts"] > t0]
    elif mine:                                         # 重启过：从聊天记录里找她最后说的
        i_me = mine[-1]
        my_text, t0 = hist[i_me].get("text") or "", me_ts
        t_me = max(me_ts, st["sent"])
        prev_start = mine[-2] + 1 if len(mine) >= 2 else max(0, i_me - 6)
        answered = {str(h.get("uid")) for h in hist[prev_start:i_me] if h.get("role") == "user"}
        after = [h for h in hist[i_me + 1:] if h.get("role") == "user"]
    else:
        return "no", "", 0
    dt = now - t_me
    if dt > float(cfg["window_sec"]) or dt < -5:
        return "no", "", 0
    turns = st["turns"].get(str(uid))
    cap = int(cfg["owner_max_turns"] if is_owner else cfg["max_turns"])
    if turns and now - turns[1] > float(cfg["turn_reset_min"]) * 60:
        st["turns"].pop(str(uid), None)
        turns = None
    if turns and turns[0] >= cap:
        return "no", f"不 @ 她已经连着接了 {turns[0]} 轮（要 @ 她才接）", 0

    others = [h for h in after if str(h.get("uid")) != str(uid)]
    first = not any(str(h.get("uid")) == str(uid) for h in after)

    s, why = 0.0, []
    if str(uid) in answered:
        s += 2
        why.append("她刚回的就是他")
    nicks = {_short(name)} | ({alias} if alias else set())
    if any(n and len(n) >= 2 and n in my_text for n in nicks):
        s += 2
        why.append("她刚点了他的名")
    if "你们" in text:
        s += 0.5
    elif "你" in text:
        s += 1.5
        why.append("说的是「你」")
    if _Q.search(text):
        s += 1
        why.append("在问")
    common = _bigrams(text) & _bigrams(my_text)
    if common:
        s += 2.5 if len(common) >= 2 else 1
        why.append("接了她的词「" + "、".join(sorted(common)[:3]) + "」")
    if dt <= 30:
        s += 1
        why.append(f"紧跟着她 {int(max(dt, 0))} 秒")
    elif dt <= 90:
        s += 0.5
    if first:
        s += 0.5
    if len(others) >= 2:
        s -= 1.5
        why.append(f"中间有别人插了 {len(others)} 句")
    if any(f"@{n}" in (h.get("text") or "") for h in others for n in {name, _short(name)} if n):
        s -= 2.5
        why.append("别人刚 @ 了他 多半在回那个人")
    if only_pic:
        s -= 1.5
        why.append("只发了图")
    reason = "、".join(why) or "没什么信号"
    if s >= float(cfg["yes_score"]):
        if only_pic and random.random() >= float(cfg["pic_prob"]):
            return "no", reason + "（图 这次不接）", s
        return "yes", reason, s
    if s <= float(cfg["no_score"]) or only_pic or not cfg.get("ask_model", True):
        return "no", reason, s
    return "ask", reason, s


def took(gid, uid, now=None):
    """决定接这条了：记一轮"""
    t = _st(gid)["turns"]
    n = (t.get(str(uid)) or [0, 0])[0] + 1
    t[str(uid)] = [n, now or time.time()]
    return n


JUDGE_SYSTEM = ("你在旁观一个 QQ 群。群里有个成员叫「{bot}」（是个 AI）。判断最后一条消息是不是在跟{bot}说话："
                "接她刚才的话、回答她、反问她、跟她抬杠、叫她做事 都算「是」；"
                "在跟别人说话、跟大家说、自言自语、只是发个反应 算「否」。只回答一个字：是 或 否")


def _ago(sec):
    sec = max(0, int(sec))
    return f"{sec}秒前" if sec < 60 else f"{sec // 60}分钟前"


async def judge(llm, history, name, text, now, bot="大肥鱼", log=print):
    """拿不准的时候 让模型看最近几条判断。返回 True/False（出错当 False）"""
    lines = []
    for h in [h for h in (history or []) if h.get("ts")][-8:]:
        who = bot if h.get("role") == "assistant" else (h.get("name") or "某人")
        if h.get("re"):
            who += f"（回复 {h['re']}）"
        lines.append(f"[{_ago(now - h['ts'])}] {who}: {(h.get('text') or '')[:120]}")
    lines.append(f"[刚刚] {name}: {text[:160]}   ← 判断这一条")
    try:
        out = await llm(JUDGE_SYSTEM.format(bot=bot), [{"role": "user", "content": "\n".join(lines)}],
                        temperature=0, max_tokens=4)
    except Exception as e:
        log("[接话] 判断失败:", repr(e)[:100])
        return False
    out = (out or "").strip()
    return out.startswith("是") or (out[:3].find("是") >= 0 and "否" not in out[:3])
