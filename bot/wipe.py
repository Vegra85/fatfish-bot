#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
删掉一个人的数据（真删：不进回收站、不留备份）
  谁能删：他自己私聊她发「删除我的数据」再回「确认删除」；或者老板在控制台点「删数据」
  删什么：
    - 私聊：记忆文件（聊天、记下的事、心里挂着的事、外号）、聊天存档
    - 群里：他说的每一句（记忆里的、等提炼的、存档里的）、群记忆里关于他的事、她给他起的外号、
            提到他的前情提要（整段删 以后重新写）、别人引用他的话（换成「某人的一句话」）、
            她自己和别人话里他的名字（换成「某人」）
    - 别人私聊里她记下的关于他的事
    - 好感度（连外号）、动画次数和记录、梗里记下的他的原话、他发的图的描述缓存、心情记录里的名字
    - 共享印象：分不清哪条来自他 全清掉 过一会儿从剩下的聊天重新提炼
    - 控制台留的备份里有他的 也删
    - 每天的 data 包（daily/data_*.zip）、记忆卫生迁移前的整份备份（memhyg_*）、迁移记录和报告、读坏另存的 .bad-*：
      按上面一样的规矩改一遍（10-28 审查 memory-3：以前这些里他的东西原样都在 拿出来放回 data 就全回来了）
  记一笔什么时候删的（data/wiped.json 只有 QQ 号和时间）：重启补看漏掉的消息时 不会把删之前的又捡回来
  不动：控制台的规则（屏蔽 / 标成机器人 是老板定的）、bot.log（自己会滚掉）、QQ 自己的聊天记录（不归我们管）
"""
import contextvars
import json
import os
import re
import threading
import time
from pathlib import Path

import safeio


def _cfg_names():
    """config 里她的名字（bot_name / group_card / profile.nickname）—— 改名了不用改代码"""
    try:
        import json as _j
        from pathlib import Path as _P
        c = _j.loads((_P(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
        out = [c.get("bot_name"), c.get("group_card"), (c.get("profile") or {}).get("nickname")]
        return {str(x).strip() for x in out if x and len(str(x).strip()) >= 2}
    except Exception:
        return set()

SELF_NAMES = {"大肥鱼", "肥鱼", "她的小名", "本鱼", "蓝色大肥鱼"} | _cfg_names()
GENERIC = {"某人", "群友", "老板", "管理员", "群主", "大家", "网友", "全体成员", "系统", "哈哈", "哈哈哈", "嗯嗯", "啊啊"}
# 名字是个常用词的人（10-28 v1.9.4 memory-11）：按名字只抹「明确指他」的写法（@他、回复了他、行首「他：」），不做全文替换 ——
# 不然删「可乐」会把别人的「我喝了瓶可乐」改成「我喝了瓶某人」。config → wipe.keep_words 可以加
COMMON_WORDS = {"可乐", "奶茶", "咖啡", "橘子", "橙子", "苹果", "西瓜", "草莓", "葡萄", "柠檬", "桃子", "香蕉", "芒果", "布丁",
                "饼干", "面包", "米饭", "包子", "饺子", "汤圆", "年糕", "豆腐", "土豆", "番茄", "辣条", "薯片", "雪糕", "冰淇淋",
                "星星", "月亮", "太阳", "云朵", "白云", "彩虹", "雪花", "小雨", "下雨", "晴天", "春天", "夏天", "秋天", "冬天",
                "小猫", "小狗", "猫咪", "狗狗", "兔子", "老虎", "狮子", "熊猫", "企鹅", "海豚", "鲸鱼", "金鱼", "小鱼", "小鸟",
                "宝宝", "宝贝", "老师", "同学", "朋友", "学生", "哥哥", "姐姐", "弟弟", "妹妹", "爸爸", "妈妈", "大佬", "萌新",
                "睡觉", "吃饭", "学习", "上班", "游戏", "音乐", "电影", "手机", "电脑", "快乐", "开心", "无聊", "晚安", "早安",
                "你好", "谢谢", "加油", "可爱", "好的", "没事", "随便", "路人", "游客", "匿名", "未知", "测试"}


def _keep_words():
    try:
        c = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8-sig"))
        return {str(x).strip() for x in ((c.get("wipe") or {}).get("keep_words") or []) if str(x).strip()}
    except Exception:
        return set()


KEEP_WORDS = COMMON_WORDS | _keep_words()


# ───────────── 文件 ─────────────
def _rj(p, default=None):
    for i in range(6):
        try:
            return json.loads(Path(p).read_bytes().decode("utf-8-sig"))   # 记事本存过的带 BOM 也认（10-28 Gemini 复查）
        except FileNotFoundError:
            return default
        except PermissionError:            # 一时被占着：等一下再读（10-28 Gemini 复查：以前一次不成就当成没有）
            time.sleep(0.05 * (i + 1))
        except Exception:
            return default
    _busy(p)                               # 一直打不开：记下来（删数据那边会在日志里说这几个没删到）
    return default


def _busy(p):
    s = _BUSY_CV.get()
    (BUSY_READ if s is None else s).add(Path(p).name)


def _read_lines(p):
    """一行一条的文本（聊天存档 .jsonl、报告 .md）→ [行]；没有这个文件 → []；一直打不开 → None（记进「没删到」）
    写到一半断电留下的半个字：不再整份读不出来（10-28 Gemini 第三轮复查：以前整份悄悄跳过 他的原话留在存档里、翻聊天记录还翻得到）
    —— 按 surrogateescape 读、写回去时同样写 那几个字节原样不动"""
    raw = None
    for i in range(6):
        try:
            raw = Path(p).read_bytes()
            break
        except FileNotFoundError:
            return []
        except PermissionError:
            time.sleep(0.05 * (i + 1))
        except OSError:
            break
    if raw is None:
        _busy(p)
        return None
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", "surrogateescape")
    out = text.split("\n")                # 只按换行切（话里的 U+2028 这种 splitlines 也会切 那一条就被拆坏了）
    if out and out[-1] == "":
        out.pop()
    return [x[:-1] if x.endswith("\r") else x for x in out]


def _write_lines(p, lines):
    """跟 _read_lines 配对：原子写回（surrogateescape 保留读不懂的字节；换行跟以前 write_text 一样用系统的 Windows 上是 \\r\\n）"""
    p = Path(p)
    tmp = p.with_name(p.name + ".wipe.tmp")
    tmp.write_bytes("".join(x + os.linesep for x in lines).encode("utf-8", "surrogateescape"))
    os.replace(tmp, p)


BUSY_READ = set()      # 不在哪一次删数据里读的（预览等）：记在这 没人看
_BUSY_CV = contextvars.ContextVar("wipe_busy", default=None)


def track_busy():
    """这一次删数据自己的「一直打不开的文件」→ 那个集合（同一个任务 / 它开的线程里读的都记进来）
    （10-28 Gemini 第二轮复查：以前整个进程共用一个 —— 「删除我的数据」预览时打不开的、别人那次删数据的 都算进这一次）"""
    s = set()
    _BUSY_CV.set(s)
    return s


def _wj(p, obj, indent=1):
    safeio.write_json(p, obj, indent)      # 原子写 + fsync、另一边正开着时等一下再换（10-28 Gemini 复查）


def _jl(p):
    out = []
    for ln in _read_lines(p) or []:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def _ok_name(n):
    """能不能拿这个名字去别人的话里、记忆里按字抹掉他
    两个字一样的（猫猫 / 明明 / hh）、两个字母以内的英文（QQ / up）、常用词（星星 / 橘子）不按字抹：太容易撞上别人的普通话
    （10-28 Gemini 复查 #13 试过放开叠词名 复查发现删「猫猫」会把别人「我家猫猫好可爱」一起抹掉、删「QQ」把「QQ空间」改坏）
    这几种算「弱名字」（_weak_name）：只抹明确指他的写法（v1.9.4 小明 10-28 定）"""
    n = (n or "").strip()
    return (len(n) >= 2 and n not in SELF_NAMES and n not in GENERIC
            and not re.fullmatch(r"[\d\W_]+", n) and len(set(n)) > 1
            and not re.fullmatch(r"[A-Za-z]{1,2}", n) and n not in KEEP_WORDS)


def _weak_name(n):
    """名字认得出是个名字、但太容易撞上普通话的（叠词 / 两个字母 / 常用词）→ 删数据时只抹「@他」「回复了他」「行首 他：」"""
    n = (n or "").strip()
    if len(n) < 2 or n in SELF_NAMES or n in GENERIC or re.fullmatch(r"[\d\W_]+", n):
        return False
    return not _ok_name(n)


def _explicit(n):
    """弱名字明确指他的写法：@他（后面是空白 / 标点 / 结尾）、（回复了 他 的「…」）、行首「他：」"""
    e = re.escape(n)
    return re.compile(r"@" + e + r"(?!\w)|（回复了 " + e + r" 的「|^" + e + r"[：:]|「" + e + r"」", re.M)   # \w 包括汉字：「@猫猫子」不算


def mentions(s, names, loose=False):
    """这段话里有没有提到他：正常名字按字找；弱名字只认明确指他的写法
    loose = 她自己的东西（前情提要、心情记录、梦、空间报告…）：弱名字也按字找（多删一点没关系 会重新写 复查：以前名字是常用词的人
    删完数据 前情提要里「X说他今天去医院了」还在）"""
    s = str(s or "")
    if not s:
        return False
    for n in names or ():
        if not n:
            continue
        if _weak_name(n) and not loose:
            if _explicit(n).search(s):
                return True
        elif n in s:
            return True
    return False


QUOTE = re.compile(r"^（回复了 .*?的「.*?」）", re.S)


# ───────────── 先看一遍：他是谁 ─────────────
def collect(data, uid):
    """删之前收集：他用过的名字、她给他起的外号、他说过的话、他发过的图（好在别处把他抹掉）"""
    data, uid = Path(data), str(uid)
    names, calls, texts, imgs, mids = set(), set(), set(), set(), set()      # mids：他发过的消息的 message_id（记忆的 src 里要抹掉 10-27）
    fav = (_rj(data / "favor.json", {}) or {}).get(uid) or {}
    if _ok_name(fav.get("name")) or _weak_name(fav.get("name")):
        names.add(fav["name"].strip())
    if fav.get("call"):
        calls.add(str(fav["call"]).strip())
    pm = _rj(data / "memory" / f"private_{uid}.json", {}) or {}
    for h in (pm.get("history") or []) + (pm.get("overflow") or []):
        if h.get("role") == "user" and (_ok_name(h.get("name")) or _weak_name(h.get("name"))):
            names.add(h["name"].strip())
        if h.get("role") == "user" and h.get("mid") is not None:
            mids.add(str(h["mid"]))
    calls |= {str(v).strip() for v in (pm.get("alias") or {}).values() if v}

    def take(h, mine):
        if _ok_name(h.get("name")) or _weak_name(h.get("name")):
            mine.add(h["name"].strip())
        t = QUOTE.sub("", (h.get("text") or "")).strip()
        if len(t) >= 8:
            texts.add(t)
        for x in h.get("imgs") or []:
            if x:
                imgs.add(str(x[0]))
        if h.get("mid") is not None:
            mids.add(str(h["mid"]))

    for p in (data / "memory").glob("group_*.json"):
        m = _rj(p, {}) or {}
        mine = set()
        for h in (m.get("history") or []) + (m.get("overflow") or []):
            if h.get("role") == "user" and str(h.get("uid")) == uid:
                take(h, mine)
        names |= mine
        for n in mine:
            c = (m.get("alias") or {}).get(n)
            if c:
                calls.add(str(c).strip())
    for p in (data / "archive").glob("*/group_*.jsonl"):
        for e in _jl(p):
            if e.get("role") == "user" and str(e.get("uid")) == uid:
                take(e, names)
    for p in (data / "archive").glob(f"*/private_{uid}.jsonl"):
        for e in _jl(p):
            if e.get("role") == "user" and e.get("mid") is not None:
                mids.add(str(e["mid"]))
    # names 里也有弱名字（叠词 / 常用词）—— 本文件里的函数都按 mentions / scrub 的规矩只抹明确指他的写法；
    # strong = 只有正常名字（给 commit.forget_person 这种按字找的用）（10-28 v1.9.4）
    return {"names": names, "strong": {n for n in names if not _weak_name(n)}, "calls": calls, "texts": texts,
            "imgs": imgs, "mids": mids}


# ───────────── 改一份记忆 ─────────────
def scrub(t, names, texts=(), loose=False):
    """别人话里的他：引用他的话 → 「某人的一句话」；他的原话 → …；他的名字 → 某人
    loose = 她自己的东西（心情记录、梦、空间记录）：弱名字也全文换（见 mentions）"""
    if not t:
        return t
    for n in sorted(names, key=len, reverse=True):
        t = re.sub(r"（回复了 " + re.escape(n) + r" 的「.*?」）", "（回复了某人的一句话）", t, flags=re.S)
    for x in sorted(texts, key=len, reverse=True):
        if len(x) >= 8 and x in t:
            t = t.replace(x, "…")
    for n in sorted(names, key=len, reverse=True):
        if _weak_name(n) and not loose:    # 叠词 / 常用词的名字：只换「@他」、行首「他：」、「「他」」（10-28 v1.9.4）
            e = re.escape(n)
            t = re.sub(r"@" + e + r"(?!\w)", "@某人", t)
            t = re.sub(r"^" + e + r"(?=[：:])", "某人", t, flags=re.M)
            t = t.replace("「" + n + "」", "「某人」")
        else:
            t = t.replace(n, "某人")
    return t


def _about(f, names, uid=""):
    if uid and str(f.get("uid") or "") == str(uid):          # 10-26 晚起记忆会记下是谁（QQ 号）的事
        return True
    if uid and str(f.get("speaker") or "") == str(uid):      # 10-27 记忆卫生：他说的话记下的（speaker = 说话人 QQ）
        return True
    s = str(f.get("text") or "") + " " + str(f.get("key") or "") + " " + str(f.get("who") or "")
    if str(f.get("who") or "").strip() in (names or ()) and not (uid and f.get("uid")):
        return True                            # 记的就是这个名字的人（没记号的老条目）：弱名字也认（整个名字一模一样）
    return mentions(s, {n for n in names or () if not _weak_name(n)})


def facts_about(m, names, uid="", mids=None):
    """删掉关于他的记忆：有效的、旧的（被取代的 / 归档的 / 归档的推断）、墓碑里的都删（墓碑本身就是一句关于他的话）。返回删了几条有效的
    10-27：他说的话记下的（speaker 是他）也删；别人的记忆依据里（src）有他的消息 → 把他那几条 message_id 抹掉"""
    if not names and not uid:
        return 0
    fs = [f for f in m.get("facts") or [] if isinstance(f, dict)]
    dead = [f for f in fs if _about(f, names, uid)]
    if dead:
        m["facts"] = [f for f in fs if not _about(f, names, uid)]
    if mids:
        for f in m.get("facts") or []:
            s = f.get("src") if isinstance(f, dict) else None
            if isinstance(s, list) and any(str(x) in mids for x in s):
                f["src"] = [x for x in s if str(x) not in mids]
                if not f["src"]:
                    f.pop("src")
    # 记忆强化的记录（10-28 v1.9.4）：别人的记忆里「他提起过 / 回他时用到过」的那几次 —— 他的 QQ 号和消息号都抹掉（那次记录整条删）
    for f in m.get("facts") or []:
        lg = f.get("reinforce_log") if isinstance(f, dict) else None
        if isinstance(lg, list):
            keep = [e for e in lg if not (isinstance(e, dict) and ((uid and str(e.get("uid") or "") == str(uid))
                                                                   or (mids and str(e.get("mid") or "") in mids)))]
            if len(keep) != len(lg):
                f["reinforce_log"] = keep
    tt = m.get("tomb_text") or []
    if tt and (names or uid):                  # 10-28 审查 memory-2：墓碑也记着是谁（who / uid）了 按 QQ 号也删
        m["tomb_text"] = [t for t in tt if not _about(t, names, uid)]
    return len([f for f in dead if f.get("status") == "active"])


def conv_mem(m, uid, names, texts, mids=None):
    """就地改一个群（或者别人私聊）的记忆：删他的话、抹掉别人话里的他、删关于他的记忆和外号
    群：提到他的前情提要整段删（他在场 里面都是他说的事）；别人的私聊：前情提要里只把名字换成「某人」"""
    uid = str(uid)
    before = json.dumps(m, sort_keys=True, ensure_ascii=False, default=str)
    group = m.get("type") == "group" or str(m.get("key", "")).startswith("group_")
    rep = {"msgs": 0, "scrubbed": 0, "facts": 0, "summary": 0, "alias": 0, "make": 0}
    own = {str(h.get("mid")) for h in (m.get("history") or []) + (m.get("overflow") or [])
           if h.get("role") == "user" and str(h.get("uid")) == uid and h.get("mid") is not None}
    for fld in ("history", "overflow"):
        if not m.get(fld):
            continue
        keep = []
        for h in m[fld]:
            if h.get("role") == "user" and str(h.get("uid")) == uid:
                rep["msgs"] += 1
                continue
            if group:
                t = h.get("text") or ""
                t2 = scrub(t, names, texts)
                if t2 != t:
                    h["text"] = t2
                    rep["scrubbed"] += 1
            keep.append(h)
        m[fld] = keep
    rep["facts"] = facts_about(m, names, uid, set(mids or ()) | own)
    if m.get("summary") and mentions(m["summary"], names, loose=True):
        # 群：整段删（会重写）；别人的私聊：正常名字换「某人」、只有叠词 / 常用词名字对上的 整段删（会重写）——
        # 别按字换（「小红每天喝橘子汁」会变成「喝某人汁」 复查）
        strong = {n for n in names if not _weak_name(n)}
        if group or not mentions(m["summary"], strong):
            m["summary"] = ""
        else:
            m["summary"] = scrub(m["summary"], strong)
            if mentions(m["summary"], names, loose=True):
                m["summary"] = ""
        rep["summary"] = 1
    al = m.get("alias") or {}
    for n in list(al):
        if n in names:
            al.pop(n)
            rep["alias"] += 1
    mab = m.get("mem_added_by")                # 群里按人算的「今天记了几条」（10-26 晚）：名字也是他的痕迹
    if isinstance(mab, dict):
        for n in list(mab):
            if n in names:
                mab.pop(n)
    r = m.get("rel") or {}
    if r.get("loops"):
        r["loops"] = [x for x in r["loops"] if not mentions(x.get("text"), names, loose=True)]
    if r.get("topic") and mentions(r["topic"], names, loose=True):
        r["topic"] = ""
    lmb = m.get("last_make_by")                # 群里按人记的「他上一个做的东西」（里面有他说的话）（10-28 Gemini 复查 #4）
    if isinstance(lmb, dict) and lmb.pop(uid, None) is not None:
        rep["make"] += 1
    if isinstance(m.get("last_make"), dict) and str(m["last_make"].get("uid") or "") == uid:
        m.pop("last_make", None)
        rep["make"] += 1
    # 改没改：前后整份比（10-28 Gemini 复查 #13：以前只看这几个计数 淡忘归档 / 被取代 / 墓碑里的他删了却不算 → 没写盘 文件里原样）
    rep["changed"] = json.dumps(m, sort_keys=True, ensure_ascii=False, default=str) != before
    return rep


# ───────────── 文件级的 ─────────────
def private_files(data, uid):
    """私聊：记忆文件和存档整个删掉"""
    data, uid = Path(data), str(uid)
    rep = {"private_msgs": 0, "private_files": 0}
    p = data / "memory" / f"private_{uid}.json"
    if p.exists():
        rep["private_msgs"] = len((_rj(p, {}) or {}).get("history") or [])
        p.unlink()
        rep["private_files"] += 1
    for a in (data / "archive").glob(f"*/private_{uid}.jsonl"):
        rep["private_msgs"] = max(rep["private_msgs"], len(_jl(a)))
        a.unlink()
        rep["private_files"] += 1
    for b in (data / "memory").glob(f"private_{uid}.json.bad-*"):     # 读坏时另存的那几份（10-28 审查 memory-3）
        try:
            b.unlink()
            rep["private_files"] += 1
        except OSError:
            pass
    return rep


def archives(data, uid, names, texts):
    """群聊存档：删他的每一句 别人的话里抹掉他"""
    data, uid = Path(data), str(uid)
    rep = {"archive_lines": 0, "archive_files": 0}
    mine = re.compile(r'"uid":\s*"?' + re.escape(uid) + r'(?!\d)')
    for p in (data / "archive").glob("*/group_*.jsonl"):
        lines = _read_lines(p)
        if lines is None:                  # 一直打不开：记进这一次的「没删到」（10-28 Gemini 第三轮复查：以前悄悄跳过）
            continue
        out, changed = [], False
        for ln in lines:
            try:
                e = json.loads(ln)
            except Exception:              # 写坏的半行：是他的就删（认 "uid": 他的号） 别人的原样留着
                if mine.search(ln):
                    rep["archive_lines"] += 1
                    changed = True
                else:
                    out.append(ln)
                continue
            if not isinstance(e, dict):
                out.append(ln)
                continue
            if e.get("role") == "user" and str(e.get("uid")) == uid:
                rep["archive_lines"] += 1
                changed = True
                continue
            t = e.get("text") or ""
            t2 = scrub(t, names, texts)
            if t2 != t:
                e["text"] = t2
                ln = json.dumps(e, ensure_ascii=False)
                changed = True
            out.append(ln)
        if changed:
            _write_lines(p, out)
            rep["archive_files"] += 1
    return rep


def anim_state(s, uid):
    """动画记账里去掉他（s 是动画的状态 就地改）"""
    uid, n = str(uid), 0
    for d in (s.get("days") or {}).values():
        if uid in (d.get("by") or {}):
            d["by"].pop(uid)
            n += 1
    h = s.get("history") or []
    s["history"] = [x for x in h if not (x.get("conv") == f"private_{uid}" or str(x.get("uid")) == uid)]
    for t in (s.get("tasks") or {}).values():        # 还在做的那件：照样等、交货、记账（钱花了） 但画的是什么 / 他的名字不留
        if isinstance(t, dict) and (str(t.get("uid")) == uid or t.get("conv") == f"private_{uid}") and not t.get("wiped"):
            t.update(prompt="", name="", caption="", wiped=time.time())     # （10-28 Gemini 第三轮复查：以前做完照样把他记进记录）
            n += 1
    return n + len(h) - len(s["history"])


def _drop_keys(o, keys):
    if isinstance(o, dict):
        n = 0
        for k in list(o):
            if k in keys:
                o.pop(k)
                n += 1
            else:
                n += _drop_keys(o[k], keys)
        return n
    if isinstance(o, list):
        return sum(_drop_keys(x, keys) for x in o)
    return 0


def misc(data, uid, names, texts, imgs, backup_dirs=(), with_anim=True):
    """好感度、梗、图片描述、心情、共享印象、控制台缓存和备份"""
    data, uid = Path(data), str(uid)
    rep = {}
    fav = _rj(data / "favor.json", {}) or {}
    if uid in fav:
        fav.pop(uid)
        _wj(data / "favor.json", fav)
        rep["favor"] = 1
    # 梗里记的原话
    s = _rj(data / "slang.json")
    if s:
        pre = {t[:40] for t in texts} | {t.strip()[:40] for t in texts}
        n = 0
        for e in (s.get("terms") or {}).values():
            seen = e.get("seen") or []
            keep = [x for x in seen if x not in pre and not mentions(x, names)]
            n += len(seen) - len(keep)
            if len(keep) != len(seen):
                e["seen"] = keep
        if n:
            _wj(data / "slang.json", s)
            rep["slang_lines"] = n
    # 他发的图 识图的描述缓存
    vc = _rj(data / "vision_cache.json")
    if vc and imgs:
        n = 0
        for k in list(vc):
            if k.split(":", 1)[-1] in imgs:
                vc.pop(k)
                n += 1
        if n:
            (data / "vision_cache.json").write_text(json.dumps(vc, ensure_ascii=False), encoding="utf-8")
            rep["img_captions"] = n
    # 心情记录里的名字
    md = _rj(data / "mood2.json")
    if md and names:
        n = 0
        for e in md.get("timeline") or []:
            for fld in ("why", "ev", "names"):              # ev / names：10-27 记忆卫生 心情时间线里记的「谁做了什么」
                w = e.get(fld) or ""
                if isinstance(w, list):                     # 名字列表：一个个比（整个就是他的名字 → 去掉）
                    w2 = [x for x in w if str(x).strip() not in names]
                else:
                    w2 = scrub(str(w), names, loose=True)
                if w2 != w:
                    e[fld] = w2
                    n += 1
        if md.get("pending") and scrub(md["pending"], names, loose=True) != md["pending"]:
            md["pending"] = scrub(md["pending"], names, loose=True)
            n += 1
        if n:
            _wj(data / "mood2.json", md)
    # 梦（10-27 遗忘曲线：梦是拿日记和群里快忘的事编的 照理不带名字 —— 万一带了 换成「某人」）
    dr = _rj(data / "dreams.json")
    if dr and names:
        n = 0
        for e in dr.get("entries") or []:
            t = e.get("text") or ""
            t2 = scrub(t, names, loose=True)
            if t2 != t:
                e["text"] = t2
                n += 1
        if n:
            _wj(data / "dreams.json", dr)
            rep["dreams"] = n
    # QQ 空间（10-27 v1.3.0）：她看过的他的说说、她赞 / 评 / 回过他的记录、访客里的他、报告和「最近」里提到他的
    #（她留在他空间里的评论在 QQ 空间上 不归这里管 他自己能删）
    qz = data / "qzone"
    fd = _rj(qz / "feed.json")
    if isinstance(fd, dict) and fd.get("items"):
        keep = [x for x in fd["items"] if str(x.get("uin")) != uid]
        if len(keep) != len(fd["items"]):
            rep["qzone_posts"] = len(fd["items"]) - len(keep)
            fd["items"] = keep
            _wj(qz / "feed.json", fd)
    st = _rj(qz / "state.json")
    if isinstance(st, dict):
        n = 0
        for k in ("liked", "commented", "replied"):
            d = st.get(k)
            if isinstance(d, dict):
                for t in [t for t, v in d.items() if isinstance(v, dict) and str(v.get("uin")) == uid]:
                    d.pop(t, None)
                    n += 1
        vs = st.get("visitors") or []
        keep = [v for v in vs if not (isinstance(v, dict) and str(v.get("uin")) == uid)]
        n += len(vs) - len(keep)
        st["visitors"] = keep
        if (st.get("visit_mention") or {}).pop(uid, None) is not None:
            n += 1
        for m in st.get("mine") or []:
            if isinstance(m, dict) and (m.get("rounds") or {}).pop(uid, None) is not None:
                n += 1
        evs = st.get("events") or []               # 「最近」里记了是谁的（qzone.event 的 uin）整条删 不光换名字（10-28 审查 qzone-11 复查）
        keep = [e for e in evs if not (isinstance(e, dict) and str(e.get("uin") or "") == uid)]
        if len(keep) != len(evs):
            n += len(evs) - len(keep)
            st["events"] = keep
        for e in st.get("events") or []:
            if isinstance(e, dict) and names and scrub(e.get("text") or "", names) != (e.get("text") or ""):   # 里面有别人的评论原话：按别人的话的规矩（复查）
                e["text"] = scrub(e.get("text") or "", names)
                n += 1
        if n:
            _wj(qz / "state.json", st)
            rep["qzone_records"] = n
    if names:
        n = 0
        for f in (data / "qzone_reports").glob("*.md"):
            lines = _read_lines(f)
            if lines is None:
                continue
            keep, drop = [], False
            for x in lines:                    # 「- 某人的说说」下面那行「  - 她：…」跟着一起删
                if drop and x.startswith("  "):
                    continue
                drop = mentions(x, names, loose=True) or uid in x
                if not drop:
                    keep.append(x)
            if len(keep) != len(lines):
                n += len(lines) - len(keep)
                _write_lines(f, keep)
        if n:
            rep["qzone_report_lines"] = n
    # 记忆卫生一次性迁移的记录和报告（10-28 审查 memory-3）：逐条写着「私聊 他的QQ：原文」、名字 → QQ 号对应表
    n = _scrub_memhyg(data, uid, names)
    if n:
        rep["memhyg_lines"] = n
    # 共享印象：分不清哪条是从他那来的 全清掉重新提炼
    sh = _rj(data / "shared.json")
    if isinstance(sh, dict):               # 有这个文件就记一笔「刚删过」：正在提炼的那一批（可能有他）回来以后扔掉（10-28 Gemini 复查）
        if sh.get("notes"):
            rep["shared_notes"] = len(sh["notes"])
        sh["notes"], sh["last_refresh"], sh["wiped"] = [], 0, time.time()
        _wj(data / "shared.json", sh)
    # 按对话记的小状态（语音、表情包、冒泡）；认 AI 的判断记录（10-26：里面有他的名字和模型的判断）
    for f in ("voice_state.json", "sticker_state.json", "speakup.json", "panel_cache.json", "aidetect.json",
              "places.json"):                            # places：他说过自己在哪（10-28 worldclock.py）
        d = _rj(data / f)
        if d is not None and _drop_keys(d, {uid, f"private_{uid}"}):
            _wj(data / f, d)
    if with_anim:
        st = _rj(data / "anim_state.json")
        if st and anim_state(st, uid):
            _wj(data / "anim_state.json", st)
    for f in [data / "voice" / "in_last.wav"] + list((data / "voice").glob("in_*.wav")):   # 正在听 / 没删掉的语音（可能是他的）
        try:
            f.unlink()
        except OSError:
            pass
    # 控制台改东西前留的备份
    n = 0
    for d in backup_dirs:
        for f in Path(d).glob("*"):
            try:
                if f.is_file() and (uid in f.name or re.search(r"(?<!\d)" + uid + r"(?!\d)",
                                                                f.read_text(encoding="utf-8", errors="ignore"))):
                    f.unlink()
                    n += 1
            except OSError:
                pass
    if n:
        rep["backups"] = n
    return rep


def _scrub_memhyg(data, uid, names):
    """memhyg.json（dates / archived / kept）和 memhyg_report.md 里：他的私聊、带他名字或 QQ 号的那几条删掉 → 删了几条"""
    data, uid = Path(data), str(uid)
    num = re.compile(r"(?<!\d)" + re.escape(uid) + r"(?!\d)")

    def hit(s):
        s = str(s or "")
        return bool(num.search(s)) or mentions(s, names, loose=True)
    n = 0
    st = _rj(data / "memhyg.json")
    if isinstance(st, dict):
        for k in ("dates", "archived", "kept"):
            lst = st.get(k)
            if not isinstance(lst, list):
                continue
            keep = [x for x in lst if not (isinstance(x, dict) and (x.get("conv") == f"private_{uid}" or hit(x.get("old"))
                                                                     or hit(x.get("new")) or hit(x.get("text"))))]
            if len(keep) != len(lst):
                n += len(lst) - len(keep)
                st[k] = keep
        if n:
            _wj(data / "memhyg.json", st)
    rp = data / "memhyg_report.md"
    lines = _read_lines(rp) or []
    keep = [x for x in lines if not hit(x)]
    if len(keep) != len(lines):
        n += len(lines) - len(keep)
        _write_lines(rp, keep)
    return n


# ───────────── 备份里的他（10-28 审查 memory-3）─────────────
def _snapshot(d):
    import hashlib
    out = {}
    for f in Path(d).rglob("*"):
        if f.is_file():
            try:
                out[str(f.relative_to(d))] = hashlib.md5(f.read_bytes()).hexdigest()
            except OSError:
                pass
    return out


def _wipe_copy(d, uid, names, texts=(), imgs=(), mids=()):
    """一份 data 的拷贝（每天的包解出来的 / 记忆卫生迁移前的整份备份）按删数据一样的规矩改一遍 → 改了没有"""
    d, uid = Path(d), str(uid)
    c = collect(d, uid)                          # 备份那会儿他用的名字、说过的话也算上
    names, texts = set(names or ()) | c["names"], set(texts or ()) | c["texts"]
    imgs, mids = set(imgs or ()) | c["imgs"], set(mids or ()) | c["mids"]
    before = _snapshot(d)
    for p in (d / "memory").glob("*.json"):
        if p.stem == f"private_{uid}":
            continue                             # 下面 private_files 整个删
        m = _rj(p)
        if not isinstance(m, dict):
            continue
        m.setdefault("key", p.stem)
        if conv_mem(m, uid, names, texts, mids)["changed"]:
            _wj(p, m)
    private_files(d, uid)
    archives(d, uid, names, texts)
    misc(d, uid, names, texts, imgs)
    return _snapshot(d) != before


def _wipe_zip(z, uid, names, texts=(), imgs=(), mids=()):
    """每天的 data 包：解到临时文件夹 → 按规矩改 → 有改动才重新打包（先写临时文件再换过去 跟他无关的包一个字节都不动）"""
    import tempfile
    import zipfile
    z = Path(z)
    with tempfile.TemporaryDirectory(prefix="wipe-") as td:
        td = Path(td)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(td)
        if not (td / "data").is_dir() or not _wipe_copy(td / "data", uid, names, texts, imgs, mids):
            return False
        tmp = z.with_name(z.name + ".wipe.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for f in sorted(td.rglob("*")):
                if f.is_file() and not f.name.endswith(".tmp"):
                    out.write(f, f.relative_to(td).as_posix())
        os.replace(tmp, z)
    return True


def backups(root, uid, names=(), texts=(), imgs=(), mids=()):
    """备份里的他（10-28 审查 memory-3）：以前只删控制台的备份 —— 每天的 data 包留 14 天、记忆卫生迁移前的整份备份一直留着
    里面他的私聊、好感、心情原样都在（她跟他说的是「删了就找不回来了」）
    root = backup_20261024：daily/data_*.zip 和 memhyg_*（迁移前整份 data/memory 的拷贝）都按删数据一样的规矩改一遍"""
    root, uid = Path(root), str(uid)
    rep = {"backup_zips": 0, "backup_copies": 0, "backup_fail": 0}
    for d in sorted(root.glob("memhyg_*")):
        try:
            if d.is_dir() and _wipe_copy(d, uid, names, texts, imgs, mids):
                rep["backup_copies"] += 1
        except Exception:
            rep["backup_fail"] += 1
    for z in sorted((root / "daily").glob("data_*.zip")):
        try:
            if _wipe_zip(z, uid, names, texts, imgs, mids):
                rep["backup_zips"] += 1
        except Exception:
            rep["backup_fail"] += 1
            try:
                z.with_name(z.name + ".wipe.tmp").unlink()
            except OSError:
                pass
    return {k: v for k, v in rep.items() if v}


# ───────────── 记一笔：什么时候删的 ─────────────
_PENDING = {}          # {wiped.json 路径: {QQ号: 时间}} 一时打不开时 这次删的人先记在这（读的时候并进去、能写了补写）（10-28 Gemini 复查）
_SEEN = {}             # {wiped.json 路径: 上次读到的那份} 一时打不开时用它（别当成谁都没删过 也别每回复一轮都等着重试）
_MARK_LOCK = threading.RLock()     # 删完那一下（事件循环里）和心跳补写（线程里）别同时改、同时写（10-28 Gemini 第三轮复查）
# 坏了的 wiped.json 里还认得出来的「QQ号: 时间」：只认完整的（后面跟着 , 或 }）—— 截在时间中间的半截数不要（第五轮复查）
_WIPED_RE = re.compile(r'"(\d{5,12})"\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*(?=[,}])')


def _ts(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def mark(data, uid, ts=None):
    """记下「他什么时候删的数据」→ True 记上了 / False 文件一时打不开（先记在内存里 心跳那边隔一会儿补写 flush_pending）
    读来改：以前读不出来就当成空的写回 以前删过的人的时间全没了 补回时可能又把他删之前的话记回来（10-28 Gemini 复查）"""
    p = Path(data) / "wiped.json"
    with _MARK_LOCK:
        _PENDING.setdefault(str(p), {})[str(uid)] = float(ts or time.time())
        return flush_pending(data)


def flush_pending(data):
    """还没写进 wiped.json 的那几个补写进去 → True 没有要补的 / 补上了；False 还是打不开
    文件读坏了（update_json 另存 .bad 从空的开始）：上次读到的那份也并回去（10-28 Gemini 第三轮复查：以前只剩这一次的人）"""
    p = Path(data) / "wiped.json"
    key = str(p)
    with _MARK_LOCK:
        pend = dict(_PENDING.get(key) or {})
        if not pend:
            return True
        src = {**(_SEEN.get(key) or {}), **pend}
        got = safeio.update_json(p, lambda d: d.update({k: v for k, v in src.items() if _ts(d.get(k)) < _ts(v)}), {})
        if got is None:
            return False
        cur = _PENDING.get(key) or {}
        for k, v in pend.items():          # 写的这会儿又有新的进来：留着下次补
            if cur.get(k) == v:
                cur.pop(k, None)
        if not cur:
            _PENDING.pop(key, None)
        _SEEN[key] = dict(got)
        return True


def pending(data):
    """还没写进去的 {QQ号: 时间}"""
    return dict(_PENDING.get(str(Path(data) / "wiped.json")) or {})


def all_wiped(data, strict=False):
    """{QQ号: 什么时候删的}（还没写进去的那几个也算上）
    每回复一轮都要问：这个进程读到过一次以后 只读一次、不等，被占着 / 坏了就用上次读到的那份
    （10-28 Gemini 第二轮复查：以前每次等着重试 卡住整只机器人）；还一次都没读到过（开机补回那次）→ 等一下再读
    （第三轮复查：只读一次的话 开机那一下被占着就当成谁都没删过 补回把他删之前的私聊又捡回来）
    strict=True：还是读不到 / 坏了、这个进程又没读到过好的 → 抛 safeio.Busy（补回那边用 别当成谁都没删过）
    坏了：认得出来的捡回来（跟上次读到的合并、取晚的）、记一个 _salvaged（什么时候发现坏的）、心跳那边写一份好的回去；
      名单可能不全 —— 补回没记忆的私聊时 只拉 _salvaged 以后的（salvaged_at）"""
    p = Path(data) / "wiped.json"
    key = str(p)
    seen = key in _SEEN
    raw, tries = None, (6 if strict and not seen else 1)     # 只有补回那次（strict）才等（第四轮复查：一直读不了时每轮回复都卡 0.75 秒）
    for i in range(tries):
        try:
            raw = p.read_bytes()
            break
        except FileNotFoundError:
            raw = b"{}"
            break
        except PermissionError:
            if i < tries - 1:
                time.sleep(0.05 * (i + 1))
        except OSError:
            break
    d, salvaged = None, False
    if raw is not None:
        try:
            d = json.loads(raw.decode("utf-8-sig") or "{}")
            d = d if isinstance(d, dict) else {}
        except ValueError:                 # 写到一半 / 手改坏了（第四轮复查：以前坏了就每次开机都当成读不到 补回一直跳过没记忆的私聊）
            salvaged = True
            d = dict(_SEEN.get(key) or {})
            for k, v in _WIPED_RE.findall(raw.decode("utf-8", "ignore")):
                if float(v) > _ts(d.get(k)):
                    d[k] = float(v)
            d["_salvaged"] = max(_ts(d.get("_salvaged")), time.time())
            with _MARK_LOCK:               # 记进待补写：心跳那边写一份好的回去（坏的那份 update_json 会另存 .bad）
                pend = _PENDING.setdefault(key, {})
                for k, v in d.items():
                    if _ts(v) > _ts(pend.get(k)):
                        pend[k] = v
        _SEEN[key] = d
    if d is None or (salvaged and strict and not seen):
        if strict and not seen:            # 坏了的名单可能不全（截掉的正好是最近删过的人）：补回那边按读不到算（第五轮复查）
            raise safeio.Busy("wiped.json 打不开" if d is None else "wiped.json 坏了")
        d = _SEEN.get(key) or {}
    pend = _PENDING.get(key) or {}
    d = {**d, **{k: v for k, v in pend.items() if _ts(d.get(k)) < _ts(v)}}
    out = {}
    for k, v in d.items():
        if not str(k).isdigit():           # _salvaged 这种记号不是 QQ 号
            continue
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def salvaged_at(data):
    """wiped.json 上次坏了、捡回来的是什么时候（0 = 没坏过）：那之前删过数据的人可能不在名单里"""
    key = str(Path(data) / "wiped.json")
    return _ts((_SEEN.get(key) or {}).get("_salvaged"))


def wiped_at(data, uid):
    return all_wiped(data).get(str(uid), 0.0)


# ───────────── 删之前给他看 / 删完告诉他 ─────────────
def preview(data, uid):
    """只看不改：删的话会删掉多少"""
    data, uid = Path(data), str(uid)
    c = collect(data, uid)
    pm = _rj(data / "memory" / f"private_{uid}.json", {}) or {}
    rep = {"private_msgs": len(pm.get("history") or []),
           "private_facts": len([f for f in pm.get("facts") or [] if isinstance(f, dict) and f.get("status") == "active"]),
           "msgs": 0, "facts": 0,
           "favor": 1 if uid in (_rj(data / "favor.json", {}) or {}) else 0, "names": sorted(c["names"])}
    for p in (data / "memory").glob("*.json"):
        if p.stem == f"private_{uid}":
            continue
        m = _rj(p, {}) or {}
        rep["msgs"] += sum(1 for h in (m.get("history") or []) + (m.get("overflow") or [])
                           if h.get("role") == "user" and str(h.get("uid")) == uid)
        rep["facts"] += sum(1 for f in m.get("facts") or []
                            if isinstance(f, dict) and f.get("status") == "active" and _about(f, c["names"]))
    rep["archive_lines"] = sum(1 for p in (data / "archive").glob("*/group_*.jsonl") for e in _jl(p)
                               if e.get("role") == "user" and str(e.get("uid")) == uid)
    return rep


def describe(r):
    """「私聊记录 31 条、群里说的 12 句、记下的 4 件事、好感度」"""
    bits = []
    if r.get("private_msgs"):
        bits.append(f"私聊记录 {r['private_msgs']} 条")
    gm = max(int(r.get("msgs") or 0), int(r.get("archive_lines") or 0))
    if gm:
        bits.append(f"群里说过的 {gm} 句")
    nf = int(r.get("private_facts") or 0) + int(r.get("facts") or 0)
    if nf:
        bits.append(f"记下的关于你的 {nf} 件事")
    if r.get("favor"):
        bits.append("好感度和外号")
    return "、".join(bits) or "没多少东西（本来就没记什么）"


# ───────────── 机器人没开的时候（控制台直接删文件） ─────────────
def run_offline(data, uid, backup_dirs=()):
    data, uid = Path(data), str(uid)
    busy = track_busy()
    c = collect(data, uid)
    rep = {"msgs": 0, "facts": 0, "summary": 0, "scrubbed": 0, "groups": 0}
    for p in (data / "memory").glob("*.json"):
        if p.stem == f"private_{uid}":
            continue
        m = _rj(p)
        if not isinstance(m, dict):
            continue
        m.setdefault("key", p.stem)
        r = conv_mem(m, uid, c["names"], c["texts"], c.get("mids"))
        if r["changed"]:
            _wj(p, m)
            for k in ("msgs", "facts", "summary", "scrubbed"):
                rep[k] += r[k]
            rep["groups"] += 1 if r["msgs"] else 0
    pf = private_files(data, uid)
    rep.update(pf)
    rep.update(archives(data, uid, c["names"], c["texts"]))
    rep.update(misc(data, uid, c["names"], c["texts"], c["imgs"], backup_dirs))
    rep.update(backups(data.parent / "backup_20261024", uid, c["names"], c["texts"], c["imgs"], c.get("mids")))   # 10-28 审查 memory-3
    ok = mark(data, uid)
    for _ in range(3):                     # 控制台这边记在内存里没用（机器人那边不知道）：多等几下再写（10-28 Gemini 第二轮复查）
        if ok:
            break
        time.sleep(0.5)
        ok = flush_pending(data)
    if not ok:
        rep["mark_fail"] = 1
    if busy:
        rep["busy"] = sorted(busy)
    rep["names"] = sorted(c["names"])
    rep["strong_names"] = sorted(c["strong"])   # 给 commit.forget_person（按字找）用：不带叠词 / 常用词名字（10-28 v1.9.4）
    return rep
