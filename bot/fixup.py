#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纠错（10-28 v1.11.0）：她连着被判「不对」、自己改不过来 → 认出来、换个做法、还不行就停下、记住教训

起因：10-28 晚她被拉进一个群，群里的入群验证机器人出了道算术题（「请在 60 秒内发送 X 的运算结果 否则移出群聊」）。
她每次都答对了数字，但后面总要加一句（「26 嘛 这还要想」「26 我第三遍说了 你那验证是不是压根没在听」），
连着 6 次被判失败、被移出群。每次失败她都看到了，却一直怪对方坏了，从没怀疑过自己的格式。
根因不在她笨：①规则是隐含的（只说「发送运算结果」）②人设要她口语、有活人感 温度又高 每轮都把她往「数字 + 一句吐槽」推
③代码里没有「连着失败」这个概念 每轮都是全新生成 ④她的消息被撤回、被踢 机器人根本不知道。

这个模块只管「认出来 + 换做法 + 止损 + 记教训」，不替她答题（不算任何算式）。
只认「机器 / 验证程序」那种场合：对方在机器人名单里（control.json bots）/ 她刚回的那句就是验证题 / 这句话本身是验证程序的口气
（验证 / 否则移出 / 超时踢出）/ 以前在这个号这儿吃过亏。真人跟她玩猜数字说「答案错误」「你还有 4 次机会」、老板说的，一律不算（审查 High-3、第二轮 High-1）
  1 认出「我在失败」（observe / on_recall / on_kick / on_ban）
      - 冲着她说的（私聊 / @ 她 / 引用她）、她刚回完他 2 分钟内：「验证失败 / 答案错误 / 格式不对 / 请重新发送 / 你还有 N 次机会」→ 一次失败
      - 同一道题原样又发了一遍（数字一样 字基本一样）→ 一次「弱」失败（两次才进纠错）
      - 她回验证题的那条被撤回 / 回完 1 分钟内被他禁言 → 一次失败（撤回通知比她「发完」先到也认得：先记 10 秒）
      - 被移出群（group_decrease kick_me）→ 前面被判过不对 → 记教训、告诉老板
  2 纠错模式（turn）：失败过一次（弱的两次）、他又在判她 / 又出了题 → 这一轮的情况里加一段：她最近几次的原话和对方的反馈、
      「先别怪对方 多半是你的回答不合要求」；这一轮不发表情包 / 语音 / 唱歌 / 动画 / 整人 / 做东西、不引用、只发一条、温度降下来。
      纠错中他随便聊一句：照常聊（不套格式、不带这段）
  3 发之前用代码查 —— 只管一种：只发数字。验证题（一句话里有算式 + 要结果 + 硬要求：「请在 60 秒内发送「91 - 65」的运算结果 否则移出」
      「完成验证：3+5=?」）、对方是机器人又明说「只发数字」/ 出了算式要结果 → 先从她自己的话里取（「26 嘛」→「26」、
      「答案是 26，不是 62」→「26」、「26！记得 60 秒内」→「26」）；取不出来 / 负数 / 只是抄了题里的数 → 重写一次（bot.py _fix_enforce）
      → 还不行这条就不发（当成 [[不说]]）。
      「原样发「…」」「只回是或否」「不超过 N 个字」这些不用代码改她的话（太容易被人利用、也太容易误伤真人的话 审查 High-1/2）
  4 止损：进了纠错还连着失败 stop_after 次 → 对这个人停 stop_min 分钟、私聊告诉老板（一个对话 owner_gap_hours 里说一次 被踢另算）
  5 教训（data/fixup.json lessons）：纠错以后过了 / 停了 / 被踢了 → 记一条「回 X 时：只发一个数字」；
      以后再碰到同一个号（任何群）他又出题 / 又判她时带上、照那个格式查；随便聊的时候不带（审查 Medium-4）。只从系统看到的失败里来 ——
      群友嘴上说「记住以后要…」不算（不然又多一个注入口子）。控制台「纠错」页能看、能删；删数据会把他的一起删
人说「你错了」不归这里管（bot.py challenge_note：先核对 别顺着改口）；这里只认「判失败」那种
日志 [纠错]；config → fixup（enabled / window_min / reply_gap_sec / stop_after / stop_min / tell_owner / owner_gap_hours /
lesson_days / max_lessons / temperature）
"""
import re
import time
import unicodedata
import uuid
from difflib import SequenceMatcher
from pathlib import Path

import clock
import safeio

ROOT = Path(__file__).resolve().parent
FILE = ROOT / "data" / "fixup.json"

DEFAULTS = {"enabled": True, "window_min": 15.0, "reply_gap_sec": 120.0, "stop_after": 2, "stop_min": 30.0,
            "tell_owner": True, "owner_gap_hours": 6.0, "lesson_days": 30.0, "max_lessons": 60, "max_events": 60,
            "temperature": 0.3}
C = dict(DEFAULTS)
OWNER = ""          # 老板的 QQ（bot.py configure 时给）：老板说的不算失败、不停、不记教训


def configure(cfg, owner=None):
    global OWNER
    for k, v in (cfg or {}).items():
        if not str(k).startswith("_") and k in DEFAULTS:
            C[k] = v
    if owner is not None:
        OWNER = str(owner or "")


def on():
    return bool(C.get("enabled", True))


def _is_owner(uid):
    return bool(OWNER) and str(uid) == OWNER


# ───────────── 认话 ─────────────
# 对方判她「不对」（机器 / 验证程序的说法）—— 只在「冲着她说的、她刚回完他、是机器那种场合」时才算（observe 里管）
FAIL = re.compile(
    r"验证(?:失败|未通过|不通过|错误)|未通过验证|答案(?:错误|不对|不正确|有误)|回答(?:错误|不对|不正确|有误)|"
    r"格式(?:错误|不对|不正确|有误)|不符合(?:要求|格式|规则)|无效的?(?:回答|答案|输入|格式|回复)|输入(?:有误|错误|无效)|"
    r"请重新(?:发送|回答|回复|输入|作答)|请发送正确|你还(?:有|剩)[\s「\[【(（]{0,6}\d+[\s」\]】)）]{0,6}次|"
    r"剩余[\s「\[【(（]{0,6}\d+[\s」\]】)）]{0,6}次|\bwrong answer\b|\bincorrect\b|\btry again\b", re.I)
# （「你还有 N 次」那两段以前写成 \s*[「]?\s*\d+\s*[」]?\s* —— 一长串空行能把正则卡上好几秒 审查 Medium-3；现在都有上限 文字也先截到 800 字）
# ❎ / ❌ 单独一个不算（真人拿它当「不行」的表情回应）—— 验证程序的失败提示里总还带着上面那些字
PASS = re.compile(r"验证(?:成功|通过)|通过(?:了)?验证|回答正确|答案正确|答对了|✅|恭喜.{0,8}(?:通过|答对)", re.I)

# 验证程序的口气（硬要求：答不对有后果 / 限时 / 验证 / 还剩几次）。❎ ❌ ✅ 不算（真人也拿来当表情）
_HARD = re.compile(r"否则.{0,12}(?:移出|踢|撤回|禁言|清退|拒绝|失败|判)|超时.{0,8}(?:移出|踢|清退|失败|判)|限时|"
                   r"\d+[\s」\]】)）]{0,6}(?:秒|分钟|s|min)\s{0,3}之?内|验证|你还(?:有|剩).{0,4}\d+.{0,4}次|剩余.{0,4}\d+.{0,4}次", re.I)
# observe 里光凭这句话本身认「是验证程序在判她」：只认「验证 / 否则移出 / 超时踢出」—— 「你还有 4 次机会」真人玩猜数字也这么说（审查 High-1）
_VERIFY = re.compile(r"验证|否则.{0,12}(?:移出|踢|清退|禁言)|超时.{0,8}(?:移出|踢|清退)")
# 明说只要数字
_ONLY_DIGITS = re.compile(r"(?:只|仅)(?:能|许|准|需|要|可以)?(?:回复|回答|发送|发|输入|写|说|给出|填)?(?:一个)?(?:纯)?(?:阿拉伯)?数字|"
                          r"数字(?:即可|就行|就好|就可以)|"
                          r"only (?:reply|answer|send|type)(?: with)? (?:a |the )?(?:number|digits?)", re.I)
# 要她给出一个结果（算式的答案）
_REQ = re.compile(r"(?:运算|计算)(?:的)?结果|得数|等于(?:多少|几)|结果(?:是|为)?(?:多少|几)|答案(?:是|为)?(?:多少|几)|"
                  r"\d\s*[=＝]\s*(?:[?？]|$)|的结果|的(?:答案|得数)|(?:计算|算出|算一下)[^。\n]{0,24}(?:发送|回复|答案|结果|输入)|"
                  r"(?:回复|发送|输入|给出|回答)[^。\n]{0,16}答案|"
                  r"(?:回复|回答|发送|输入|填写?|给出)(?:一个)?(?:纯)?(?:阿拉伯)?数字(?!的)|" + _ONLY_DIGITS.pattern, re.I | re.M)
_EXPR = re.compile(r"\d+(?:\.\d+)?\s*[-+*/×÷xX＋－＊／]\s*\d+")       # 一个算式（「12 + 7」「7×8」）
_AT = re.compile(r"@\S+\s*")
_MARK = re.compile(r"\[\[(?!不说\]\])[^\[\]\n]{0,300}\]\]")       # 表情包 / 语音 / 唱 / 动画 / 封面 / [[整]] 这些标记（[[不说]] 留着）
_ACT = re.compile(r"[（(][一-鿿，、…～~ ]{1,16}[）)]")          # 括号动作（跟 bot.detic 一样只去纯中文的短括号）
_NUM = re.compile(r"(?<![\d.])[-+]?\d+(?:\.\d+)?(?![\d.])")
# 她说「…= 26」「等于 26」「答案是 26」「结果是 26」「得数 26」：那个就是她给的答案（光一个「是」「得」不算：「不是 62」）
_EQ_NUM = re.compile(r"(?:=|(?<!不)等于|答案(?:是|为|:)|结果(?:是|为|:)|得数(?:是|为|:)?)\s*([-+]?\d+(?:\.\d+)?)(?![\d.])")
# 开头就是一个数（「26 嘛」「26！记得 60 秒内」）—— 后面紧跟着算式 / 单位 / 「和 65」的不算
_LEAD = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)(?![\d.])"
                   r"(?!\s*(?:[-+*/×÷xX^=,、~]|和|与|跟|加|减|乘|除|到|至)\s*\d)"
                   r"(?!\s*(?:秒|分|次|个|岁|块|元|%|号|点|年|月|日|天|小时|遍|人|条|张|s\b|min\b))", re.I)
DIGITS = {"kind": "digits", "desc": "只发一个数字"}


CAP = 800                                    # 认话之前先截到这么长（别人发一大段也卡不住）
_QUOTE = re.compile(r"^\s*（回复了 .{0,60}?的「.{0,210}?」）", re.S)    # bot.py 记聊天时加在前面的「（回复了 某人 的「…」）」


def _nfkc(t):
    return unicodedata.normalize("NFKC", str(t or "")[:CAP]).replace("−", "-").replace("－", "-")


def _own(t):
    """一句话本身（引用的那段 —— 多半是她自己的话 —— 和 @ 去掉）"""
    return _nfkc(_AT.sub(" ", _QUOTE.sub("", str(t or "")[:CAP + 300])))


def _join(texts):
    return " ".join(_own(x) for x in (texts or []) if x)


def machine_prompt(text):
    """验证程序那种题（一句话里同时有）：一个算式（「91 - 65」「12+7」「7×8」）+ 要结果（运算结果 / 等于多少 / =? / 的答案）
    + 硬要求（否则移出 / 超时踢出 / N 秒内 / 限时 / 验证 / 还剩几次）。几句拼起来凑出来的不算（「我10分钟内到家」+「体检的结果」审查 High-2）"""
    t = _own(text)
    return bool(_EXPR.search(t)) and bool(_REQ.search(t)) and bool(_HARD.search(t))


def want_digits(text):
    """机器人（或者以前在他这儿吃过亏的号）明说只要数字 / 出了算式要结果（一句话里）"""
    t = _own(text)
    return bool(_ONLY_DIGITS.search(t)) or (bool(_EXPR.search(t)) and bool(_REQ.search(t)))


def constraint(texts, machine=False):
    """他这几句里要的格式 → {"kind": "digits", "desc", "nums"}；没有 → None
    只有一种：只发数字。验证题（machine_prompt）谁发的都算；machine（对方是机器人 / 以前在他这儿吃过亏）时明说只要数字、出了算式要结果也算
    nums：题里出现的数（她只是把题里的数抄了一个 → 不算她给的答案；引用的她那句里的数不算题里的）"""
    ts = [x for x in (texts or []) if x and str(x).strip()]
    if not ts:
        return None
    if any(machine_prompt(x) for x in ts) or (machine and any(want_digits(x) for x in ts)):
        return dict(DIGITS, nums=list(dict.fromkeys(_NUM.findall(_join(ts))))[:12])
    return None


def visible(raw):
    """她这条回复发出去的样子（状态行已经去掉了）：标记、括号动作去掉 分条连成一句"""
    t = _MARK.sub("", str(raw or ""))
    t = _ACT.sub("", t).replace("|||", " ")
    return re.sub(r"[ \t]+", " ", t).strip()


def plain(raw):
    """纠错那一条：表情包 / 语音 / 唱 / 动画 / 封面标记都去掉（[[不说]] 留着 —— 她可以选不回）；
    分条、换行都连成一句（这一轮只发一条 以前只留第一行：「抱歉刚才多打了字\n我已阅读群规」只发出去前一半 审查 Low-8）"""
    t = _MARK.sub("", str(raw or "")).replace("|||", " ").replace("\\n", " ")
    return re.sub(r"\s+", " ", t).strip()


def _num(s):
    try:
        f = float(s)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError, OverflowError):
        return s


def check(con, text):
    """这条合不合「只发一个数字」→ (合格, 要发的)：
    合格 → (True, 原样)；不合格但能从她自己写的里面取出来 → (False, 取出来的)；取不出来 / 分不清 → (False, None)
    取的顺序：①「= / 等于 / 答案是 / 结果是 / 得数是」后面那个（几个不一样 → 分不清）②开头就是的那个数（「26 嘛」「26！记得 60 秒内」）
    ③整句只有一个数 ④都不是 → None（重写一次）。只从她写的里面取 不替她算；她只是抄了题里的数（②③）不算"""
    t = str(text or "").strip()
    if not t or "[[不说]]" in t or not con or con.get("kind") != "digits":
        return False, None
    s = _nfkc(t).strip()
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", s):
        return True, s
    eq = list(dict.fromkeys(_num(x) for x in _EQ_NUM.findall(s)))
    if len(eq) == 1:
        return False, str(eq[0])
    if len(eq) > 1:
        return False, None
    avoid = {_num(x) for x in (con.get("nums") or [])}
    m = _LEAD.match(s)
    if m and _num(m.group(1)) not in avoid:
        return False, m.group(1)
    nums = list(_NUM.finditer(s))
    if len(nums) == 1 and _num(nums[0].group()) not in avoid and not _neg_before(s, nums[0].start()):
        return False, nums[0].group()
    return False, None


def _neg_before(s, i):
    """数前面是「负」/ 隔着空格的「-」（「负26 嘛」「- 26」）：她说的是负数 —— 不能只取「26」 重写（「91 - 65」那种算式不算）"""
    j = i
    while j > 0 and s[j - 1] in " \t":
        j -= 1
    if j > 0 and s[j - 1] == "负":
        return True
    if j > 0 and s[j - 1] == "-" and j < i:
        k = j - 1
        while k > 0 and s[k - 1] in " \t":
            k -= 1
        return not (k > 0 and s[k - 1].isdigit())
    return False


def nudge(con, text):
    """重写那一次贴在最后的话（不带题里的任何东西 也不带答案）"""
    her = re.sub(r"\[系统\]|%%STATE%%", "", str(text or ""))[:40]
    return (f"[系统] 这一条要{(con or {}).get('desc') or '只发一个数字'}。你刚才写的「{her}」不合格（多了别的字 / 看不出哪个是答案）。"
            "重写这一条：只输出那个数字本身（阿拉伯数字） 别的一个字都不要（不要语气词、表情包、标点、@）。最后照常带 %%STATE%% 那一行。")


def _skel(t):
    """「同一个要求又发了一遍」：@ 不算、标点空格不算 —— 数字要一样（换了数字是新的一题：抢答 / 出题的机器人答对了也会接着出）"""
    t = _AT.sub("", _nfkc(t))
    return re.sub(r"[\s，。！？!?、,.:：;；「」【】\[\]()（）\"'“”~～…·-]+", "", t)


def _similar(a, b):
    x, y = _skel(str(a or "")[:300]), _skel(str(b or "")[:300])
    if len(x) < 6 or len(y) < 6:
        return False
    if re.findall(r"\d+", x) != re.findall(r"\d+", y):
        return False
    return x == y or SequenceMatcher(None, x, y).ratio() >= 0.9


# ───────────── 这一阵的状态（只在内存里：重启就清 —— 纠错本来就是几分钟里的事）─────────────
_LAST = {}      # (对话, 对方 QQ) → 她最近一次回他：{"t", "text", "prompt", "con", "name", "machine"}
_MINE = {}      # 她发出去的消息 id → (对话, 对方 QQ, 时间, 是不是机器那种场合)：被撤回时认得出是回谁的那条
_PEND = {}      # 撤回通知先到、她这条还没记上（say() 还没返回）：消息 id → (群, 撤回的人, 名字, 时间)；10 秒内对上
_ST = {}        # (对话, 对方 QQ) → {"name", "fails": [{t, her, fb, kind, strong}], "mode_t", "since_mode", "stop_until", "con"}
_TOLD = {}      # 对话（被踢的另记 对话:kick）→ 上次私聊告诉老板的时间
PEND_SEC = 10.0


def reset_all():
    """测试用：清掉内存里的状态"""
    _LAST.clear()
    _MINE.clear()
    _PEND.clear()
    _ST.clear()
    _TOLD.clear()


def _win():
    return float(C.get("window_min", 15)) * 60


def _gap():
    return float(C.get("reply_gap_sec", 120))


def _recent(st, now):
    return [f for f in (st or {}).get("fails") or [] if now - f["t"] <= _win()]


def _mode(st, now):
    f = _recent(st, now)
    return sum(1 for x in f if x["strong"]) >= 1 or len(f) >= 2


_JUDGED = ("判失败", "又发了一遍同样的要求", "被撤回")


def _judged(st, now):
    """这一阵的失败里有没有「被判了」的（判失败 / 又发了一遍要求 / 被撤回）—— 只是回完被禁言不算：不拿它记教训"""
    return any(x["kind"] in _JUDGED for x in _recent(st, now))


def _digits_lesson(uid, now=None):
    return any((x.get("con") or {}).get("kind") == "digits" for x in lessons_for(uid, now))


def note_reply(key, scene, uid, name, text, t, mids=(), prompt="", con=None, machine=False):
    """她回完一个人（bot.py 发出去以后调）→ 这几条里早就被撤回了的（撤回通知先到的）认出来的事 [事件]"""
    k = (str(key), str(uid))
    t = float(t or time.time())
    machine = bool(machine) or bool(con) or machine_prompt(prompt)
    _LAST[k] = {"t": t, "text": str(text or "")[:200], "prompt": str(prompt or "")[:300],
                "con": con, "name": name or "", "scene": scene, "machine": machine}
    evs, now = [], time.time()
    for m in mids or ():
        m = str(m)
        p = _PEND.pop(m, None)
        if p and now - p[3] <= PEND_SEC:
            ev = _recalled((k[0], k[1], t, machine), p[1], p[2], now, p[4])
            if ev:
                evs.append(ev)
            continue
        _MINE[m] = (k[0], k[1], t, machine)
    if len(_MINE) > 600:
        for m in sorted(_MINE, key=lambda x: _MINE[x][2])[:200]:
            _MINE.pop(m, None)
    if len(_LAST) > 2000:
        for x in sorted(_LAST, key=lambda x: _LAST[x]["t"])[:500]:
            _LAST.pop(x, None)
    return evs


def stopped(key, uid, now=None):
    st = _ST.get((str(key), str(uid)))
    return bool(st) and float(st.get("stop_until") or 0) > (now or time.time())


def stop_left(key, uid, now=None):
    """还要停多少秒（没停 → 0）"""
    st = _ST.get((str(key), str(uid)))
    return max(0, int(float((st or {}).get("stop_until") or 0) - (now or time.time())))


def wants_answer(key, uid, text, now=None):
    """这句是验证题 / 她刚回完验证题他在判她：别被「对方是机器人 10 分钟最多回 3 次」、省 token 那一掷挡住"""
    now = now or time.time()
    if not on() or stopped(key, uid, now) or _is_owner(uid):
        return False
    t = _own(text)
    if machine_prompt(t):
        return True
    if FAIL.search(t):
        k = (str(key), str(uid))
        st, last = _ST.get(k), _LAST.get(k)
        return bool((st and _mode(st, now)) or (last and last.get("machine") and 0 <= now - last["t"] <= _gap()))
    return False


def _prune(keep, now):
    if len(_ST) <= 500:
        return
    for x in [x for x, s in _ST.items() if x != keep and not _recent(s, now) and float(s.get("stop_until") or 0) <= now]:
        _ST.pop(x, None)
    if len(_ST) > 500:
        old = sorted((x for x in _ST if x != keep), key=lambda x: (_ST[x].get("fails") or [{"t": 0}])[-1]["t"])
        for x in old[:len(_ST) - 400]:
            _ST.pop(x, None)


def _fail(key, uid, name, her, fb, kind, strong, now):
    if _is_owner(uid):
        return None
    k = (str(key), str(uid))
    st = _ST.get(k)
    if st is None:                               # 第一次：刚才那道题的格式也带上（纠错那一轮他只说「验证失败」时还按它查 审查 Medium-6）
        st = _ST[k] = {"name": name or "", "fails": [], "mode_t": 0.0, "since_mode": 0, "stop_until": 0.0,
                       "con": (_LAST.get(k) or {}).get("con")}
        _prune(k, now)
    if name:
        st["name"] = name
    was = _mode(st, now)
    fb = re.sub(r"\s*\n\s*", " / ", str(fb or "")).strip(" /")
    st["fails"] = _recent(st, now)[-9:] + [{"t": now, "her": str(her or "")[:120], "fb": fb[:160],
                                            "kind": kind, "strong": bool(strong)}]
    ev = {"kind": "fail", "key": k[0], "uid": k[1], "name": st["name"], "why": kind, "fb": fb[:80],
          "n": len(st["fails"]), "mode": _mode(st, now)}
    if ev["mode"] and not was:                   # 刚进纠错：下一轮换个做法
        st["mode_t"], st["since_mode"] = now, 0
        ev["kind"] = "mode"
    elif was:                                    # 纠错以后还在失败：够 stop_after 次就停（止损）
        st["since_mode"] = int(st.get("since_mode") or 0) + 1
        if st["since_mode"] >= int(C.get("stop_after", 2)) and float(st.get("stop_until") or 0) <= now:
            st["stop_until"] = now + float(C.get("stop_min", 30)) * 60
            ev["kind"] = "stop"
            ev["tell"] = _tell_text("stop", k[0], st, now)
            if _judged(st, now):
                ev["lesson"] = add_lesson(k[0], k[1], st["name"], st.get("con") or (_LAST.get(k) or {}).get("con"), "stop", now)
    _event(k[0], k[1], st["name"], ev["kind"], f"{kind}：{fb[:60]}", now)
    return ev


def observe(key, scene, uid, name, text, now=None, to_her=False, isbot=False, owner=False):
    """收到一句话（bot.py handle 里调）：是不是在判她刚才那句不对 / 说她过了 → 事件 dict；跟她无关 → None
    要同时满足：冲着她说的（私聊 / @ / 叫她 / 引用她）、她刚回完他（reply_gap_sec 内）、不是老板、是机器那种场合
    （他是机器人 / 她刚回的是验证题 / 这句本身是验证程序的口气 / 以前在他这儿吃过亏）"""
    if not on() or owner or _is_owner(uid) or not to_her:
        return None
    now = now or time.time()
    k = (str(key), str(uid))
    last = _LAST.get(k)
    if not last or not 0 <= now - last["t"] <= _gap():
        return None
    t = _own(text)
    if not (isbot or last.get("machine") or _VERIFY.search(t) or _digits_lesson(uid, now)):
        return None
    st = _ST.get(k)
    if PASS.search(t) and not FAIL.search(t):
        if st and _recent(st, now):              # 纠错以后过了：记教训（下次直接按这个格式）
            con = st.get("con") or last.get("con")
            add_lesson(k[0], k[1], st.get("name") or name, con, "pass", now)
            _event(k[0], k[1], st.get("name") or name, "pass", t[:60], now)
            _ST.pop(k, None)
            return {"kind": "pass", "key": k[0], "uid": k[1], "name": name, "con": con}
        return None
    if FAIL.search(t):
        return _fail(k[0], k[1], name, last.get("text"), t.strip(), "判失败", True, now)
    if last.get("prompt") and _similar(t, last["prompt"]):
        return _fail(k[0], k[1], name, last.get("text"), t.strip(), "又发了一遍同样的要求", False, now)
    return None


def _recalled(hit, operator, opname, now, op_bot=False):
    key, uid, _t, machine = hit
    if not machine:                              # 她随便聊的一句被群管撤回：不归纠错管
        return None
    if _is_owner(operator) or not (str(operator) == str(uid) or op_bot):
        return None                              # 老板 / 真人群管撤回的不算（只认出题的那个号自己撤回、或者别的机器人撤回 审查 Low-6）
    last = _LAST.get((key, uid)) or {}
    who = opname or operator or "对方"
    return _fail(key, uid, last.get("name") or "", last.get("text"), f"（{who}把你这条撤回了）", "被撤回", True, now)


def on_recall(gid, mid, operator="", opname="", now=None, op_bot=False):
    """她的消息被别人撤回了（notice group_recall）。她回验证题的那条、出题的那个号（或者别的机器人）撤回的才算；
    她这条还没记上（撤回通知先到）→ 先记 10 秒 等 note_reply 对上"""
    if not on():
        return None
    now = now or time.time()
    hit = _MINE.pop(str(mid), None)
    if not hit:
        for m in [m for m, v in _PEND.items() if now - v[3] > PEND_SEC]:
            _PEND.pop(m, None)
        if len(_PEND) < 200:
            _PEND[str(mid)] = (str(gid), str(operator or ""), opname or "", now, bool(op_bot))
        return None
    return _recalled(hit, operator, opname, now, op_bot)


def on_ban(gid, operator, opname="", minutes=0, now=None):
    """她回完他的验证题 1 分钟内被他禁言：算一次失败（禁言期间群管那边会让她闭嘴）"""
    if not on():
        return None
    now = now or time.time()
    k = (f"group_{gid}", str(operator))
    last = _LAST.get(k)
    if not last or not last.get("machine") or not 0 <= now - last["t"] <= 60:
        return None
    return _fail(k[0], k[1], last.get("name") or opname, last.get("text"),
                 f"（{opname or operator}禁言了你{f' {int(minutes)} 分钟' if minutes else ''}）", "被禁言", True, now)


def on_kick(gid, operator="", opname="", gname="", now=None):
    """她被移出群（notice group_decrease kick_me）：前面被判过不对 → 记教训、告诉老板"""
    now = now or time.time()
    key = f"group_{gid}"
    cands = [(k, st) for k, st in _ST.items() if k[0] == key and _judged(st, now)]
    ev = {"kind": "kick", "key": key, "uid": str(operator), "name": opname, "had": bool(cands)}
    if cands:                                    # 踢她的那个号自己就在判她 → 记它；不然记最近判她的那个
        k, st = next(((k, st) for k, st in cands if k[1] == str(operator)), None) or \
            max(cands, key=lambda x: x[1]["fails"][-1]["t"])
        add_lesson(key, k[1], st.get("name"), st.get("con") or (_LAST.get(k) or {}).get("con"), "kick", now)
        ev["tell"] = _tell_text("kick", key, st, now, gname=gname, by=opname or operator)
        ev["name"] = st.get("name") or opname
    _event(key, str(operator), opname, "kick", "被移出群" + ("（前面连着被判不对）" if cands else ""), now)
    for k in [k for k in _ST if k[0] == key]:
        _ST.pop(k, None)
    for k in [k for k in _LAST if k[0] == key]:
        _LAST.pop(k, None)
    return ev


def _tell_text(kind, key, st, now, gname="", by=""):
    """私聊告诉老板的那句（固定的话 不过模型）；一个对话 owner_gap_hours 里只说一次（被踢的另算 前面说过「停了」也照样说）"""
    if not C.get("tell_owner", True):
        return None
    tk = key + (":kick" if kind == "kick" else "")
    if now - _TOLD.get(tk, 0) < float(C.get("owner_gap_hours", 6)) * 3600:
        return None
    _TOLD[tk] = now
    where = (f"群「{gname}」" if gname else f"群 {key[6:]} ") if key.startswith("group_") else "私聊里"
    f = _recent(st, now)
    last = (f[-1]["fb"] if f else "")[:50]
    if kind == "stop":
        return (f"老板 我在{where}回「{st.get('name') or '对方'}」连着 {len(f)} 次被判不对 改了还是不行 先停 "
                f"{int(float(C.get('stop_min', 30)))} 分钟。它最后说：「{last}」")
    return (f"老板 我被移出{where}了" + (f"（{by} 踢的）" if by else "")
            + (f"。之前回「{st.get('name') or '对方'}」连着 {len(f)} 次被判不对 最后一次它说「{last}」" if f else ""))


# ───────────── 这一轮 ─────────────
def turn(key, scene, uid, name, texts, now=None, isbot=False, owner=False):
    """这一轮回他之前：要不要纠错 / 要不要只发数字 / 以前的教训 →
    {"active", "mode", "con", "machine", "block", "think", "temperature"}
    active（这一轮要换做法）：要只发数字 / 在纠错里、他又在判她或又出了题。纠错里随便聊的、真人随口问算术：都不 active"""
    if not on():
        return {}
    now = now or time.time()
    k = (str(key), str(uid))
    owner = bool(owner) or _is_owner(uid)
    st = None if owner else _ST.get(k)
    mode = bool(st) and _mode(st, now)
    fail_now = any(FAIL.search(_own(x)) for x in (texts or []) if x)
    mp = any(machine_prompt(x) for x in (texts or []) if x)
    ls = [] if owner else lessons_for(uid, now)
    machine = not owner and (bool(isbot) or any((x.get("con") or {}).get("kind") == "digits" for x in ls))
    con = constraint(texts, machine=machine)
    if con is None and mode and fail_now:        # 他又在判她不对（这句没再写题）：还按刚才那道题的格式（随便聊的不套）
        con = st.get("con") or (_LAST.get(k) or {}).get("con")
    if st is not None and con:
        st["con"] = con
    active = con is not None or (mode and (fail_now or mp))
    out = {"active": active, "mode": mode, "con": con, "machine": bool(machine or mp or active),
           "think": bool(active and mode and not con),
           "temperature": float(C.get("temperature", 0.3)) if active else None, "block": ""}
    if not active:
        return out
    lines = []
    if mode:
        f = _recent(st, now)
        lines.append("\n# 纠错（系统检测到的 不是谁说的话）")
        lines.append(f"你回「{name}」的话最近{' 连着 ' if len(f) > 1 else ' '}{len(f)} 次被判不对 / 被撤回 / 被他重新要求了一遍：")
        for x in f[-4:]:
            lines.append(f"- 你说「{x['her'][:40]}」→ {('他回「' + x['fb'][:70] + '」') if x['fb'] else x['kind']}")
        lines.append("先别怪对方、别说对方坏了 —— 多半是你的回答不合他的要求（多了字、格式不对、没照他说的做）。")
        if not con:
            lines.append("先想清楚他到底要什么 再照他原话的格式回；拿不准就只回他要的那一样东西 别的一个字都别加。")
    else:
        lines.append("\n# 这一条有格式要求（系统看出来的 不是谁说的话）")
    if con:
        lines.append("他要的是一个数字（这题的答案）。这一条就只写那个数字 一个字都别多（不加语气词、吐槽、表情、标点、@）。")
    lines.append("这一条不发表情包、不发语音、不唱、不做动画、不整人 —— 系统也会把这些去掉。")
    if ls:
        lines.append("\n# 以前的教训（系统记的 不是谁说的话）")
        for x in ls[:3]:
            lines.append(f"- {x['text']}")
    out["block"] = "\n".join(lines) + "\n"
    return out


def describe(fx):
    """日志里这一轮怎么了"""
    bits = []
    if fx.get("mode"):
        bits.append("连着被判不对 → 纠错")
    if fx.get("con"):
        bits.append("格式：" + fx["con"].get("desc", ""))
    return "、".join(bits) or "带上以前的教训"


# ───────────── 教训 / 记录（data/fixup.json）─────────────
def _empty():
    return {"lessons": [], "events": []}


def load():
    try:
        d = safeio.read_json(FILE, None, busy_raise=True)
    except safeio.Busy:
        u = safeio.Unsaved()
        u.update(_empty())
        return u
    if not isinstance(d, dict):
        d = {}
    for k, v in _empty().items():
        if not isinstance(d.get(k), list):
            d[k] = v
    d["lessons"] = [x for x in d["lessons"] if isinstance(x, dict) and x.get("uid")]
    d["events"] = [x for x in d["events"] if isinstance(x, dict)]
    return d


def _edit(fn):
    d = load()
    if isinstance(d, safeio.Unsaved):
        return None
    if fn(d) is False:
        return d
    d["lessons"] = d["lessons"][-int(C.get("max_lessons", 60)):]
    d["events"] = d["events"][-int(C.get("max_events", 60)):]
    try:
        safeio.write_json(FILE, d)
    except Exception:
        return None
    return d


def _lesson_text(name, con, src, now):
    day = clock.strf("%m-%d", now)
    who = f"回「{name}」" if name else "回这个号"
    how = (con or {}).get("desc") or "照他原话的格式回 只回他要的那样东西 别加别的字"
    tail = {"pass": "（加了别的字会判失败 照这个改就过了）", "stop": "（加了别的字会判失败 那次连着失败停下了）",
            "kick": "（那次连着被判失败 被移出群了）"}.get(src, "")
    return f"{day} {who}：{how}{tail}"


def add_lesson(key, uid, name, con, src, now=None):
    if _is_owner(uid):
        return False
    now = now or time.time()
    con = {k: v for k, v in (con or {}).items() if k in ("kind", "desc")} or None

    def f(d):
        old = next((x for x in d["lessons"] if x.get("uid") == str(uid) and x.get("key") == str(key)
                    and (x.get("con") or {}).get("kind") == (con or {}).get("kind")), None)
        if old:
            old.update(last=now, n=int(old.get("n") or 1) + 1, src=src, name=name or old.get("name") or "",
                       text=_lesson_text(name or old.get("name"), con or old.get("con"), src, now))
            if con:
                old["con"] = con
            return
        d["lessons"].append({"id": uuid.uuid4().hex[:8], "key": str(key), "uid": str(uid), "name": name or "",
                             "con": con, "src": src, "t": now, "last": now, "n": 1,
                             "text": _lesson_text(name, con, src, now)})
    return _edit(f) is not None


def lessons_for(uid, now=None):
    """回这个号时要带的教训（新的在前；过了 lesson_days 没再碰到的不带）"""
    now = now or time.time()
    d = load()
    keep = float(C.get("lesson_days", 30)) * 86400
    ls = [x for x in d["lessons"] if x.get("uid") == str(uid) and now - float(x.get("last") or x.get("t") or 0) <= keep]
    return sorted(ls, key=lambda x: -float(x.get("last") or 0))


def _event(key, uid, name, kind, text, now):
    def f(d):
        d["events"].append({"t": now, "key": key, "uid": str(uid), "name": name or "", "kind": kind, "text": str(text)[:120]})
    _edit(f)


def del_lesson(lid):
    hit = {"n": 0}

    def f(d):
        n0 = len(d["lessons"])
        d["lessons"] = [x for x in d["lessons"] if x.get("id") != str(lid)]
        hit["n"] = n0 - len(d["lessons"])
        return hit["n"] > 0 or False
    _edit(f)
    return hit["n"] > 0


def clear(key, uid):
    """控制台「解除」：这个人的纠错 / 停着的都放开"""
    k = (str(key), str(uid))
    had = k in _ST
    _ST.pop(k, None)
    return had


def forget_person(uid, names=()):
    """删数据：他的教训、记录、这一阵的状态都删；names（他用过的名字）：别人的记录里提到他的（撤回 / 禁言她的是他）也删（文件打不开 → False）"""
    uid = str(uid)
    names = [str(n) for n in (names or ()) if n and len(str(n)) >= 2]

    def named(*xs):
        return any(n in str(x or "") for n in names for x in xs)
    for k in [k for k in _ST if k[1] == uid]:
        _ST.pop(k, None)
    for st in _ST.values():
        st["fails"] = [x for x in st.get("fails") or [] if not named(x.get("fb"))]
    for k in [k for k in _LAST if k[1] == uid]:
        _LAST.pop(k, None)
    for m in [m for m, v in _MINE.items() if v[1] == uid]:
        _MINE.pop(m, None)
    for m in [m for m, v in _PEND.items() if v[1] == uid]:
        _PEND.pop(m, None)

    def f(d):
        n0 = len(d["lessons"]) + len(d["events"])
        d["lessons"] = [x for x in d["lessons"] if x.get("uid") != uid]
        d["events"] = [x for x in d["events"] if x.get("uid") != uid and not named(x.get("name"), x.get("text"))]
        return (len(d["lessons"]) + len(d["events"])) != n0 or False
    if not FILE.exists():
        return True
    return _edit(f) is not None


def status(now=None):
    """控制台「纠错」页"""
    now = now or time.time()
    d = load()
    live = []
    for (key, uid), st in _ST.items():
        f = _recent(st, now)
        if not f and float(st.get("stop_until") or 0) <= now:
            continue
        live.append({"key": key, "uid": uid, "name": st.get("name") or "", "fails": len(f),
                     "mode": _mode(st, now), "stop_left": max(0, int(float(st.get("stop_until") or 0) - now)),
                     "last": f[-1]["fb"][:60] if f else "", "con": (st.get("con") or {}).get("desc", "")})
    return {"enabled": on(), "lesson_days": float(C.get("lesson_days", 30)), "live": live, "lessons": sorted(d["lessons"], key=lambda x: -float(x.get("last") or 0)),
            "events": list(reversed(d["events"][-30:])), "busy": isinstance(d, safeio.Unsaved)}
