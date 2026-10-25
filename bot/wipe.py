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
  记一笔什么时候删的（data/wiped.json 只有 QQ 号和时间）：重启补看漏掉的消息时 不会把删之前的又捡回来
  不动：控制台的规则（屏蔽 / 标成机器人 是老板定的）、bot.log（自己会滚掉）、QQ 自己的聊天记录（不归我们管）
"""
import json
import os
import re
import time
from pathlib import Path

SELF_NAMES = {"大肥鱼", "肥鱼", "她的小名", "本鱼", "蓝色大肥鱼"}
GENERIC = {"某人", "群友", "老板", "管理员", "群主", "大家", "网友", "全体成员", "系统", "哈哈", "哈哈哈", "嗯嗯", "啊啊"}


# ───────────── 文件 ─────────────
def _rj(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _wj(p, obj, indent=1):
    p = Path(p)
    tmp = p.with_suffix(p.suffix + ".wipe.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, p)


def _jl(p):
    out = []
    try:
        for ln in Path(p).read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    except Exception:
        pass
    return out


def _ok_name(n):
    n = (n or "").strip()
    return (len(n) >= 2 and n not in SELF_NAMES and n not in GENERIC
            and not re.fullmatch(r"[\d\W_]+", n) and len(set(n)) > 1)


QUOTE = re.compile(r"^（回复了 .*?的「.*?」）", re.S)


# ───────────── 先看一遍：他是谁 ─────────────
def collect(data, uid):
    """删之前收集：他用过的名字、她给他起的外号、他说过的话、他发过的图（好在别处把他抹掉）"""
    data, uid = Path(data), str(uid)
    names, calls, texts, imgs = set(), set(), set(), set()
    fav = (_rj(data / "favor.json", {}) or {}).get(uid) or {}
    if _ok_name(fav.get("name")):
        names.add(fav["name"].strip())
    if fav.get("call"):
        calls.add(str(fav["call"]).strip())
    pm = _rj(data / "memory" / f"private_{uid}.json", {}) or {}
    for h in pm.get("history") or []:
        if h.get("role") == "user" and _ok_name(h.get("name")):
            names.add(h["name"].strip())
    calls |= {str(v).strip() for v in (pm.get("alias") or {}).values() if v}

    def take(h, mine):
        if _ok_name(h.get("name")):
            mine.add(h["name"].strip())
        t = QUOTE.sub("", (h.get("text") or "")).strip()
        if len(t) >= 8:
            texts.add(t)
        for x in h.get("imgs") or []:
            if x:
                imgs.add(str(x[0]))

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
    return {"names": names, "calls": calls, "texts": texts, "imgs": imgs}


# ───────────── 改一份记忆 ─────────────
def scrub(t, names, texts=()):
    """别人话里的他：引用他的话 → 「某人的一句话」；他的原话 → …；他的名字 → 某人"""
    if not t:
        return t
    for n in sorted(names, key=len, reverse=True):
        t = re.sub(r"（回复了 " + re.escape(n) + r" 的「.*?」）", "（回复了某人的一句话）", t, flags=re.S)
    for x in sorted(texts, key=len, reverse=True):
        if len(x) >= 8 and x in t:
            t = t.replace(x, "…")
    for n in sorted(names, key=len, reverse=True):
        t = t.replace(n, "某人")
    return t


def _about(f, names):
    s = str(f.get("text") or "") + " " + str(f.get("key") or "")
    return any(n in s for n in names)


def facts_about(m, names):
    """删掉关于他的记忆：有效的、旧的、墓碑里的都删（墓碑本身就是一句关于他的话）。返回删了几条有效的"""
    if not names:
        return 0
    fs = [f for f in m.get("facts") or [] if isinstance(f, dict)]
    dead = [f for f in fs if _about(f, names)]
    if dead:
        m["facts"] = [f for f in fs if not _about(f, names)]
    tt = m.get("tomb_text") or []
    if tt:
        m["tomb_text"] = [t for t in tt if not _about(t, names)]
    return len([f for f in dead if f.get("status") == "active"])


def conv_mem(m, uid, names, texts):
    """就地改一个群（或者别人私聊）的记忆：删他的话、抹掉别人话里的他、删关于他的记忆和外号
    群：提到他的前情提要整段删（他在场 里面都是他说的事）；别人的私聊：前情提要里只把名字换成「某人」"""
    uid = str(uid)
    group = m.get("type") == "group" or str(m.get("key", "")).startswith("group_")
    rep = {"msgs": 0, "scrubbed": 0, "facts": 0, "summary": 0, "alias": 0}
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
    rep["facts"] = facts_about(m, names)
    if m.get("summary") and any(n in m["summary"] for n in names):
        m["summary"] = "" if group else scrub(m["summary"], names)
        rep["summary"] = 1
    al = m.get("alias") or {}
    for n in list(al):
        if n in names:
            al.pop(n)
            rep["alias"] += 1
    r = m.get("rel") or {}
    if r.get("loops"):
        r["loops"] = [x for x in r["loops"] if not any(n in str(x.get("text") or "") for n in names)]
    if r.get("topic") and any(n in r["topic"] for n in names):
        r["topic"] = ""
    rep["changed"] = any(rep[k] for k in ("msgs", "scrubbed", "facts", "summary", "alias"))
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
    return rep


def archives(data, uid, names, texts):
    """群聊存档：删他的每一句 别人的话里抹掉他"""
    data, uid = Path(data), str(uid)
    rep = {"archive_lines": 0, "archive_files": 0}
    for p in (data / "archive").glob("*/group_*.jsonl"):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        out, changed = [], False
        for ln in lines:
            try:
                e = json.loads(ln)
            except Exception:
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
            tmp = p.with_suffix(".wipe.tmp")
            tmp.write_text("".join(x + "\n" for x in out), encoding="utf-8")
            os.replace(tmp, p)
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
            keep = [x for x in seen if x not in pre and not any(nm in x for nm in names)]
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
            w = e.get("why") or ""
            w2 = scrub(w, names)
            if w2 != w:
                e["why"] = w2
                n += 1
        if md.get("pending") and scrub(md["pending"], names) != md["pending"]:
            md["pending"] = scrub(md["pending"], names)
            n += 1
        if n:
            _wj(data / "mood2.json", md)
    # 共享印象：分不清哪条是从他那来的 全清掉重新提炼
    sh = _rj(data / "shared.json")
    if sh and sh.get("notes"):
        rep["shared_notes"] = len(sh["notes"])
        sh["notes"], sh["last_refresh"] = [], 0
        _wj(data / "shared.json", sh)
    # 按对话记的小状态（语音、表情包、冒泡）
    for f in ("voice_state.json", "sticker_state.json", "speakup.json", "panel_cache.json"):
        d = _rj(data / f)
        if d is not None and _drop_keys(d, {uid, f"private_{uid}"}):
            _wj(data / f, d)
    if with_anim:
        st = _rj(data / "anim_state.json")
        if st and anim_state(st, uid):
            _wj(data / "anim_state.json", st)
    try:
        (data / "voice" / "in_last.wav").unlink()   # 最后听的一条语音（可能是他的）
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


# ───────────── 记一笔：什么时候删的 ─────────────
def mark(data, uid, ts=None):
    p = Path(data) / "wiped.json"
    d = _rj(p, {}) or {}
    d[str(uid)] = ts or time.time()
    _wj(p, d)


def all_wiped(data):
    """{QQ号: 什么时候删的}"""
    d = _rj(Path(data) / "wiped.json", {}) or {}
    out = {}
    for k, v in d.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            pass
    return out


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
    c = collect(data, uid)
    rep = {"msgs": 0, "facts": 0, "summary": 0, "scrubbed": 0, "groups": 0}
    for p in (data / "memory").glob("*.json"):
        if p.stem == f"private_{uid}":
            continue
        m = _rj(p)
        if not isinstance(m, dict):
            continue
        m.setdefault("key", p.stem)
        r = conv_mem(m, uid, c["names"], c["texts"])
        if r["changed"]:
            _wj(p, m)
            for k in ("msgs", "facts", "summary", "scrubbed"):
                rep[k] += r[k]
            rep["groups"] += 1 if r["msgs"] else 0
    pf = private_files(data, uid)
    rep.update(pf)
    rep.update(archives(data, uid, c["names"], c["texts"]))
    rep.update(misc(data, uid, c["names"], c["texts"], c["imgs"], backup_dirs))
    mark(data, uid)
    rep["names"] = sorted(c["names"])
    return rep
