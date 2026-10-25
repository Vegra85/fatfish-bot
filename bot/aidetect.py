#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
认出群里别人家的 AI（10-26 小明：她跟群里的 AI 畅聊）
  看着像的（报错串、bot 框架的状态输出、名字像 bot、成段的完整句子、句号结尾、两句之间双空格、几秒连发、自称只能打字/本鲸）
    → 攒够几条（而且不全是同一句）→ 让模型看这个人最近十几条 判断是不是 AI（一个人一天最多问一次）→ 是而且有把握 → 标
  10-26 下午改：不再「一眼就标」—— 群友会一起复读机器人的报错（「LLM 请求失败…」）、模仿状态输出（「Human 状态」）；
    有的真人号上挂着机器人框架（偶尔吐一条「SnowLuma 状态」）平时还是本人在聊 —— 这些都交给模型看完再说
  标了 = 跟控制台里「别的机器人」一样：只有 @她/叫她/引用她才回 10 分钟最多 3 次 不算接话
  控制台里取消标记的人 记成「真人」以后不再自动标；老板和她自己不标
  记录 data/aidetect.json；日志 [认AI]
"""
import json
import re
import time
from collections import defaultdict, deque
from pathlib import Path

import control

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "aidetect.json"
STRONG = re.compile(r"LLM ?(响应|请求)(错误|失败)|All chat models fail|令牌额度已用尽|额度已用尽|insufficient_quota|rate_limit_exceeded|"
                    r"Insufficient Balance|agent 处理出错|未找到任何可用的对话模型|未配置 AI 接口|系统服务重启中|"
                    r"Error code: ?\d{3}|context_length_exceeded|SnowLuma 状态|运行时长: ?\d|版本: ?\d+\.\d+\.\d+ ?平台", re.I)
BOTNAME = re.compile(r"肥鱼|鲸|鱼丸|bot|ai|助手|机器人|gpt|豆包|kimi|元宝|千问|deepseek|小冰|claude", re.I)
SELFISH = re.compile(r"本鲸|本鱼|只能打字|发不了语音|看不了图|没法出声|我(是|只是)(一个)?(ai|人工智能|机器人|大模型)|作为(一个)?ai", re.I)
DBL = re.compile(r"[。！？!?，,]\s{2,}\S")
JUDGE = ("你在帮一个 QQ 群机器人分辨：群里某个账号是不是「别人部署的 AI 聊天机器人」（自动回复的大模型 bot），还是真人。\n"
         "AI 的典型样子：每条都是完整通顺的句子、标点规范、常用句号结尾、两句之间有双空格；几秒内就回、经常连发两三条；"
         "固定人设口吻（本鲸/本鱼/句尾都带「喵」）；会说自己发不了图/出不了声/只能打字；会报错（LLM 响应错误、额度用尽）；"
         "会输出 bot 框架的状态；对每个 @ 都回、跟别的 bot 能一直聊下去。\n"
         "真人的样子：短、口语、错别字、表情、断句随意、发命令（#sl 之类）、回得慢、话题跳、骂人挑衅。\n"
         "注意：群友经常一起复读、模仿机器人的报错和状态输出（一堆人刷「LLM 请求失败…」、发「Human 状态 版本: male」）—— 那是真人在玩梗；"
         "有的真人号上挂着机器人框架 偶尔吐一条状态 平时还是本人在聊 —— 也算真人。"
         "只有这个号自己正常说的话也像 AI 写的 才算 AI；拿不准就给低把握。\n"
         "只回一行 JSON：{\"bot\": true 或 false, \"confidence\": 0-100, \"why\": \"十个字以内\"}")

_msgs = defaultdict(lambda: deque(maxlen=30))      # (群, QQ) -> [(时间, 文字)]
_busy = set()
_asked = {}                                         # QQ -> 上次让模型看的时间（还没出结果的也算 免得连着问好几次）


def _load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE)
    except Exception:
        pass


def skip(uid, owner="", me=""):
    uid = str(uid)
    return uid in (str(owner), str(me)) or control.is_bot(uid) or control.is_human(uid)


def observe(gid, uid, name, text, now=None):
    """群里一条消息 → None / "check"（像 AI 该让模型看看了）。不会一眼就标 —— 都让模型看过再说"""
    now = now or time.time()
    text = str(text or "")
    q = _msgs[(str(gid), str(uid))]
    q.append((now, text))
    recent = [(t, x) for t, x in q if now - t < 2 * 3600 and x and not x.startswith("[表情包") and not x.startswith("[图片")]
    strong = sum(1 for _, x in recent if STRONG.search(x))
    recent = [(t, x) for t, x in recent if not STRONG.search(x)]      # 报错/状态输出不算「说的话」（可能是复读 / 挂着框架）
    if len(recent) < 8 or len({x for _, x in recent}) < 5:     # 正常说的话不够 / 一直复读同一句：先不看
        return None
    st = _load().get(str(uid)) or {}
    last = max(float(st.get("t") or 0), _asked.get(str(uid), 0))
    if now - last < 24 * 3600:                      # 一天最多问一次模型
        return None
    lens = [len(x) for _, x in recent]
    avg = sum(lens) / len(lens)
    period = sum(1 for _, x in recent if x.rstrip().endswith("。")) / len(recent)
    dbl = sum(1 for _, x in recent if DBL.search(x))
    burst = sum(1 for (a, _), (b, _) in zip(recent, recent[1:]) if b - a < 4) / max(1, len(recent) - 1)
    selfish = sum(1 for _, x in recent if SELFISH.search(x))
    hint = (strong >= 1 or bool(BOTNAME.search(name or "")) or dbl >= 2 or selfish >= 1
            or (avg >= 22 and period >= 0.3) or (burst >= 0.3 and avg >= 15))
    if not hint:
        return None
    _asked[str(uid)] = now
    return "check"


async def judge(llm, gid, uid, name, log=print):
    """让模型看这个人最近的话 → (是不是 AI, 把握, 理由)"""
    q = list(_msgs[(str(gid), str(uid))])[-16:]
    if not q:
        return False, 0, ""
    lines, prev = [], q[0][0]
    for t, x in q:
        m = STRONG.search(x)
        if m:                                           # 报错 / 框架状态：只写个记号（真人复读、挂着框架的号也会有）
            x = f"[机器人报错/状态输出：{m.group(0)[:16]}]"
        if lines and lines[-1][1] == x:                 # 连着发同一句：合成一行
            lines[-1][2] += 1
        else:
            lines.append([int(t - prev), x, 1])
        prev = t
    lines = [f"（隔 {g} 秒）{x[:160]}" + (f"（同一句连发 {n} 次）" if n > 1 else "") for g, x, n in lines]
    try:
        out = await llm(JUDGE, [{"role": "user", "content": f"账号名「{name}」最近的消息：\n" + "\n".join(lines)}],
                        temperature=0, max_tokens=80)
        m = re.search(r"\{.*\}", out or "", re.S)
        j = json.loads(m.group(0)) if m else {}
    except Exception as e:
        log(f"[认AI] 判断 {name} 失败：{repr(e)[:80]}")
        return False, 0, ""
    return bool(j.get("bot")), int(j.get("confidence") or 0), str(j.get("why") or "")[:30]


def record(uid, name, verdict, conf, why):
    d = _load()
    d[str(uid)] = {"t": time.time(), "name": name, "bot": verdict, "conf": conf, "why": why}
    _save(d)


async def check(llm, gid, uid, name, log=print, threshold=75):
    """看着像：问一次模型；是 AI 而且有把握 → 标进「别的机器人」。返回标了没有"""
    k = (str(gid), str(uid))
    if k in _busy:
        return False
    _busy.add(k)
    try:
        is_bot, conf, why = await judge(llm, gid, uid, name, log)
        record(uid, name, is_bot, conf, why)
        if is_bot and conf >= threshold:
            mark(uid, name, gid, f"模型看了最近的话：{why}（把握 {conf}）", log)
            return True
        log(f"[认AI] 群{gid} {name}({uid}) 看着有点像 AI 模型看了不是（{why} 把握 {conf}）")
        return False
    finally:
        _busy.discard(k)


def mark(uid, name, gid, why, log=print):
    note = f"自动认出的 AI：{name}（群{gid}）{why}"[:80]
    control.add_bot(uid, note)
    log(f"[认AI] 群{gid} {name}({uid}) 是别人家的 AI → 标成「别的机器人」（{why}）以后只有 @她 才回")
