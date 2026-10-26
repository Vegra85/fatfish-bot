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
  10-26 晚改：有个真人号被误标 —— 号上挂着自己部署的机器人 那台机器人 14 秒里漏了 10 条思考过程（「用户在@我…我作为
    大肥鱼妹妹 需要自然回应」）。现在：漏出来的思考过程（THINK）也算框架输出；思考过程后面几秒内连着发的也算；
    这些都不算「这个号自己说的话」、给模型看时写成记号；模型多回一个 mixed（混用号：平时本人在聊 偶尔冒机器人输出）→ 不标
  10-26 晚 2：好几个照着「真人群友」调的鱼（短句、口语、不打标点）模型光看句子判成了真人 跟她互道晚安几十轮 →
    模型现在还看行为：她说完几秒他就回、两三条连发、@她/引用她、跟她的来回（把她的话也放进去）、自称本鱼/住在电脑里、
    括号动作、吐过的框架状态 / 指令报错（按 QQ 号记 跨群）；判成真人以后证据又多了 2 小时后可以再看一次；
    对聊刹车（bot.py）发现停不下来时也会叫它马上再看一眼
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
                    r"Error code: ?\d{3}|context_length_exceeded|SnowLuma 状态|NapCat 信息|运行时[长间]: ?\d|"
                    r"版本: ?\d+\.\d+\.\d+ ?平台|权限不足以使用此指令|/sid 获取 ID", re.I)
BOTNAME = re.compile(r"肥鱼|鲸|鱼丸|bot|ai|助手|机器人|gpt|豆包|kimi|元宝|千问|deepseek|小冰|claude", re.I)
SELFISH = re.compile(r"本鲸|本鱼|只能打字|发不了语音|看不了图|没法出声|我(是|只是|就是)(一个|一条|一只)?.{0,4}(ai|人工智能|机器人|大模型|胖鱼|鲸鱼娘)|"
                     r"作为(一个)?ai|住在(电脑|这框|屏幕|服务器|机房)里|被主人@|主人@了", re.I)
BRACKET = re.compile(r"[（(](?!回复了)[^（）()]{4,30}[）)]")      # 括号动作「（尾巴一摆，缩回屏幕后面去了）」
# bot 框架的回复格式：「@某人」后面直接换行再说话（10-26 两天的群聊：3 个 bot 几百句这样 真人一句都没有）
ATNL = re.compile(r"^(（回复了[^）]{0,200}）)?@[^\s@]{1,30}[ \u3000]*\n")
DBL = re.compile(r"[。！？!?，,]\s{2,}\S")
# 漏出来的机器人思考过程（大模型把「我该怎么回」也发出来了）
THINK = re.compile(r"^\s*[\"“]?(用户|对方)(在|又|刚|正在)?(@我|艾特我|问我|说|发了|让我|想让我)|我作为.{0,16}(需要|应该|得)|"
                   r"需要(自然|简短|礼貌|简单)?地?回(应|复)|^\s*(简短回复|直接回|不用查资料|不用(调用)?工具|这是(在)?(闲聊|聊)|"
                   r"话题是|接个梗就行|我(先)?(想想|分析一下)|思考[:：]|回复思路)|(根据|按照?)(人设|设定|系统提示|system ?prompt)|</?think>",
                   re.I | re.M)
MEDIA = re.compile(r"\[(表情|图片|表情包|动画表情|视频)[^\]]*\]")
BURST_GAP = 6          # 思考过程后面隔不到 6 秒连着发的 也算框架发的（那一串里的其他思考、最后那句回复）
QUICK_SEC = 10         # 她说完这么多秒内就接上 = 秒回她
RECHECK_SEC = 2 * 3600  # 判过以后 证据又多了 最快隔多久再看
JUDGE = ("你在帮一个 QQ 群机器人（下面说的「她」）分辨：群里某个账号是不是「别人部署的 AI 聊天机器人」（自动回复的大模型 bot），还是真人。\n"
         "这些群里很多 bot 是照着「真人群友」调教的：短句、口语、不打标点、错落、会玩梗、会撒娇 —— 光看句子像真人不算数。"
         "更可靠的是行为（下面都替你算好了）：她一说完几秒就接上、一回两三条连发、句句 @她/引用她、跟她一来一回停不下来（互道晚安几十轮）、"
         "自称「本鱼/本鲸」或者说自己住在电脑里、写括号动作（「（尾巴一摆，缩回去了）」）、在群里吐过 bot 框架的状态"
         "（SnowLuma 状态、NapCat 信息）或者指令报错（「权限不足以使用此指令」）。\n"
         "AI 的样子还有：句子完整通顺、标点规范、句号结尾、两句之间有双空格；会说自己发不了图/出不了声/只能打字；会报错（LLM 响应错误、额度用尽）。\n"
         "真人的样子：回得慢、话题跳、错别字、发命令（#sl 之类）、使唤她干活、骂人挑衅、跟大家聊而不是只围着她转。"
         "真人也会跟她聊得很勤、回得快、@她 引用她 —— 光凭这个不算；要看是不是跟机器人的样子一起出现"
         "（每次几秒内两三条连发、自称本鱼、括号动作、框架状态），还有他说的话本身像不像人。\n"
         "注意：群友经常一起复读、模仿机器人的报错和状态输出（一堆人刷「LLM 请求失败…」、发「Human 状态 版本: male」）—— 那是真人在玩梗。\n"
         "有的真人号上挂着机器人框架：平时是本人在聊（口语、@别人问事、聊自己的电脑和 token），偶尔冒出一段机器人的输出"
         "（状态、报错、漏出来的「用户在@我…我需要自然回应」这种思考过程、一段很工整的回复）—— 这叫「混用号」 算真人：bot=false、mixed=true。"
         "混用号的本人不会跟她秒回地一来一回。\n"
         "写成「[机器人框架的输出…]」的 是系统已经认出来的框架输出：只当线索 别当成这个号自己说的话。拿不准就给低把握。\n"
         "只回一行 JSON：{\"bot\": true 或 false, \"mixed\": true 或 false, \"confidence\": 0-100, \"why\": \"十个字以内\"}")

_msgs = defaultdict(lambda: deque(maxlen=40))      # (群, QQ) -> [(时间, 文字, 是不是 @她/引用她)]
_her = defaultdict(lambda: deque(maxlen=60))       # 群 -> [(时间, 她说的)]
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


def _flags(q):
    """每条是不是机器人框架的输出：报错/状态（STRONG）、漏出来的思考过程（THINK）、
    思考过程后面隔不到几秒接着发的（那一串里的其他思考和最后那句回复）"""
    think = [bool(THINK.search(x[1])) for x in q]
    out = [bool(t or STRONG.search(x[1])) for t, x in zip(think, q)]
    for i, f in enumerate(think):
        j = i + 1
        while f and j < len(q) and q[j][0] - q[j - 1][0] <= BURST_GAP:
            out[j] = True
            j += 1
    return out


def _words(x):
    """去掉表情/图片记号后 这条真正说的字"""
    return MEDIA.sub("", str(x or "")).strip()


def skip(uid, owner="", me=""):
    uid = str(uid)
    return uid in (str(owner), str(me)) or control.is_bot(uid) or control.is_human(uid)


def her(gid, t, text=""):
    """她在群里发了一条（bot.py 每次发出去都告诉这里）：拿来看别人是不是她一说完几秒就接"""
    _her[str(gid)].append((float(t), str(text or "")[:80]))


def evidence(gid, uid, now=None, win=2 * 3600):
    """这个号最近 win 秒（默认 2 小时）在这个群的「行为」：秒回她几次、@她/引用她几次、连发、自称、括号动作、框架输出（跨群记的）"""
    now = now or time.time()
    q = [x for x in _msgs.get((str(gid), str(uid))) or [] if now - x[0] < win]
    fw = _flags(q)
    plain = [x for x, f in zip(q, fw) if not f]
    ts = [x[0] for x in q]
    hs = [t for t, _ in _her.get(str(gid)) or [] if now - t < win]
    quick = sum(1 for h in hs if any(0 < t - h <= QUICK_SEC for t in ts))
    st = _load().get(str(uid)) or {}
    ev = {"quick": quick, "her": len(hs),
          "to_her": sum(1 for x in q if len(x) > 2 and x[2]),
          "burst": sum(1 for a, b in zip(ts, ts[1:]) if b - a <= 3.5),
          "selfref": sum(1 for x in plain if SELFISH.search(x[1])),
          "atnl": sum(1 for x in q if ATNL.search(x[1])),
          "bracket": sum(1 for x in plain if BRACKET.search(x[1])),
          "fw": int(st.get("fw") or 0), "fw_eg": str(st.get("fw_eg") or "")}
    # 真人也会跟她聊得勤、回得快、@她 —— 这两样只算一点；连发、自称、括号动作、框架输出算得重
    ev["score"] = (min(ev["quick"], 10) // 2 + min(ev["to_her"], 10) // 4 + min(ev["burst"], 10) // 2
                   + 2 * min(ev["selfref"], 5) + 2 * min(ev["bracket"], 5) + 3 * min(ev["fw"], 3) + 3 * min(ev["atnl"], 3))
    return ev


def _note_fw(uid, gid, text):
    """这个号吐了一条框架状态 / 指令报错：按 QQ 号记下来（换个群也认得）"""
    m = STRONG.search(text)
    if not m:
        return
    d = _load()
    r = d.setdefault(str(uid), {})
    r["fw"] = int(r.get("fw") or 0) + 1
    r["fw_eg"], r["fw_g"], r["fw_t"] = m.group(0)[:20], str(gid), time.time()
    _save(d)


def observe(gid, uid, name, text, now=None, to_her=False):
    """群里一条消息 → None / "check"（像 AI 该让模型看看了）。不会一眼就标 —— 都让模型看过再说"""
    now = now or time.time()
    text = str(text or "")
    q = _msgs[(str(gid), str(uid))]
    q.append((now, text, bool(to_her)))
    if STRONG.search(text):
        _note_fw(uid, gid, text)
    items = [x for x in q if now - x[0] < 2 * 3600 and x[1]]
    fw = _flags(items)
    strong = sum(fw)
    # 报错/状态/思考过程（还有跟它们连着发的）不算「这个号自己说的话」（可能是复读 / 真人号挂着框架）；光发表情图片的也不算
    recent = [(x[0], _words(x[1])) for x, f in zip(items, fw) if not f]
    recent = [(t, x) for t, x in recent if len(x) >= 2]
    if len(recent) < 8 or len({x for _, x in recent}) < 5:     # 正常说的话不够 / 一直复读同一句：先不看
        return None
    st = _load().get(str(uid)) or {}
    ev = evidence(gid, uid, now)
    if now - _asked.get(str(uid), 0) < RECHECK_SEC:
        return None
    if "t" in st:                                    # 看过了：一天最多一次；证据比上次多了不少 2 小时后可以再看
        since = now - float(st.get("t") or 0)
        grown = ev["score"] >= 8 and ev["score"] >= int(st.get("score") or 0) + 5
        if since < RECHECK_SEC or (since < 24 * 3600 and not grown):
            return None
    lens = [len(x) for _, x in recent]
    avg = sum(lens) / len(lens)
    period = sum(1 for _, x in recent if x.rstrip().endswith("。")) / len(recent)
    dbl = sum(1 for _, x in recent if DBL.search(x))
    burst = sum(1 for (a, _), (b, _) in zip(recent, recent[1:]) if b - a < 4) / max(1, len(recent) - 1)
    hint = (strong >= 1 or bool(BOTNAME.search(name or "")) or dbl >= 2 or ev["selfref"] >= 1
            or (avg >= 22 and period >= 0.3) or (burst >= 0.3 and avg >= 15)
            or (ev["quick"] >= 3 and ev["burst"] >= 5) or ev["fw"] >= 1 or ev["bracket"] >= 2 or ev["atnl"] >= 2)
    if not hint:
        return None
    _asked[str(uid)] = now
    return "check"


def _ev_text(ev):
    out = [f"她最近 2 小时在这个群说了 {ev['her']} 句；这个号在她说完 {QUICK_SEC} 秒内就接上 {ev['quick']} 次",
           f"@她 / 引用她 {ev['to_her']} 次", f"3 秒内连发 {ev['burst']} 次",
           f"自称本鱼/本鲸/住在电脑里这类 {ev['selfref']} 句", f"括号动作 {ev['bracket']} 句"]
    if ev["fw"]:
        out.append(f"这个号（按 QQ 号算 不管哪个群）吐过 {ev['fw']} 次 bot 框架的状态 / 指令报错（例：{ev['fw_eg']}）")
    if ev["atnl"]:
        out.append(f"{ev['atnl']} 句是「@某人」后面直接换行再说话（bot 框架的回复格式 真人几乎不会这样）")
    return "；".join(out)


async def judge(llm, gid, uid, name, log=print):
    """让模型看这个人最近的话、跟她的来回、算好的行为 → (是不是 AI, 把握, 理由, 混用号)"""
    q = list(_msgs[(str(gid), str(uid))])[-24:]
    if not q:
        return False, 0, "", False
    fw = _flags(q)
    lines, prev = [], q[0][0]
    for x, f in zip(q, fw):
        t, s = x[0], x[1]
        if f:                                           # 框架输出：只写个记号（真人复读、挂着框架的号也会有）
            m = STRONG.search(s)
            s = f"[机器人框架的输出：报错/状态 {m.group(0)[:16]}]" if m else "[机器人框架的输出：漏出来的思考过程/自动回复]"
        if lines and lines[-1][1] == s:                 # 连着发同一句 / 一串框架输出：合成一行
            lines[-1][2] += 1
        else:
            lines.append([int(t - prev), s, 1])
        prev = t
    lines = [f"（隔 {g} 秒）{x[:160]}" + ((f"（连着 {n} 条）" if x.startswith("[机器人框架") else f"（同一句连发 {n} 次）")
                                         if n > 1 else "") for g, x, n in lines]
    body = f"账号名「{name}」最近的消息：\n" + "\n".join(lines)
    hs = [(t, s) for t, s in _her.get(str(gid)) or [] if t >= q[0][0] - 60]
    if hs:                                              # 她和这个号的来回（看得出是不是她一说完就接）
        conv = sorted([(t, "她", s) for t, s in hs] + [(x[0], "他", x[1]) for x in q], key=lambda z: z[0])[-16:]
        rows, last_her = [], None
        for t, who, s in conv:
            if who == "她":
                rows.append(f"[她] {s[:70]}")
                last_her = t
            else:
                gap = f" · 她说完 {int(t - last_her)} 秒" if last_her is not None and t - last_her < 120 else ""
                rows.append(f"[他{gap}] {s[:70]}")
        body += "\n\n她和这个号最近的来回（按时间）：\n" + "\n".join(rows)
    body += "\n\n算好的行为：" + _ev_text(evidence(gid, uid))
    try:
        out = await llm(JUDGE, [{"role": "user", "content": body}], temperature=0, max_tokens=80)
        m = re.search(r"\{.*\}", out or "", re.S)
        j = json.loads(m.group(0)) if m else {}
    except Exception as e:
        log(f"[认AI] 判断 {name} 失败：{repr(e)[:80]}")
        return False, 0, "", False
    try:
        conf = int(float(j.get("confidence") or 0))
    except (TypeError, ValueError):
        conf = 0
    return bool(j.get("bot")), conf, str(j.get("why") or "")[:30], bool(j.get("mixed"))


def verdict(uid):
    """上次模型怎么判的（没判过 → {}）"""
    return _load().get(str(uid)) or {}


def record(uid, name, verdict, conf, why, mixed=False, score=0):
    d = _load()
    r = d.setdefault(str(uid), {})                      # 框架输出的记录（fw）留着
    r.update(t=time.time(), name=name, bot=verdict, conf=conf, why=why, score=int(score))
    if mixed:
        r["mixed"] = True
    else:
        r.pop("mixed", None)
    _save(d)


async def check(llm, gid, uid, name, log=print, threshold=75, force=False):
    """看着像：问一次模型；是 AI 而且有把握 → 标进「别的机器人」。返回标了没有（正在看着 → None）
    force：对聊刹车叫的（停不下来了）—— 不管上次什么时候看的 现在就看"""
    k = (str(gid), str(uid))
    if k in _busy:
        return None
    _busy.add(k)
    if force:
        _asked[str(uid)] = time.time()
    try:
        is_bot, conf, why, mixed = await judge(llm, gid, uid, name, log)
        ev = evidence(gid, uid)
        fp = ev["atnl"] >= 3 and not mixed              # 框架的回复格式出现好几次：模型说像真人也标（真人几乎不会这样）
        record(uid, name, (is_bot or fp) and not mixed, conf, why, mixed, ev["score"])
        if mixed:                                       # 真人号挂着机器人：平时是本人在聊 不标
            log(f"[认AI] 群{gid} {name}({uid}) 像是真人号挂着机器人（{why} 把握 {conf}）不标")
            return False
        if is_bot and conf >= threshold:
            mark(uid, name, gid, f"模型看了最近的话：{why}（把握 {conf}）", log)
            return True
        if fp:
            mark(uid, name, gid, f"bot 框架的回复格式（@人后直接换行）{ev['atnl']} 次；模型说{'像' if not is_bot else '有点像'}"
                                 f"{'真人' if not is_bot else ' AI'}（把握 {conf}）", log)
            return True
        log(f"[认AI] 群{gid} {name}({uid}) 看着有点像 AI 模型看了不是（{why} 把握 {conf}）")
        return False
    finally:
        _busy.discard(k)


def mark(uid, name, gid, why, log=print):
    note = f"自动认出的 AI：{name}（群{gid}）{why}"[:80]
    control.add_bot(uid, note)
    log(f"[认AI] 群{gid} {name}({uid}) 是别人家的 AI → 标成「别的机器人」（{why}）以后只有 @她 才回")
