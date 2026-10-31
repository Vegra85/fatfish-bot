#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
表情包词典（10-30 v1.16.0，小明：「所有表情包都有一个 id 也许我们可以多花点功夫真的识别对这个表情包 然后多加点解释 以后就不用识别错了」）

  以前：识图按图片 id 缓存 —— 第一眼看错了，以后每次都是那句错的；没人核对；也不知道群里拿它表达什么。
  现在：每张图（QQ 文件名 = 内容的哈希；商城表情用它的 id + 官方名字）记一条：
    见过几次、在哪些群、最近几次别人发它前后在说什么（只留 5 条、每句 40 字、带 QQ 号好删）、第一眼的描述、缩略图、长相指纹（dHash）
  深度识别（deep）：同一张见到第 deep_after 次（默认 3）、或有人发给她的表情包 → 后台认真认一次（每天最多 deep_daily 张）：
    ① 看图模型带专门的提示（动图拼帧；逐字抄字；画面；是什么梗 / 出处，不确定就写不确定）
    ② 抄到了字 / 认出了梗名 → 上网查一次出处
    ③ 文字模型把 看图结果 + 查到的 + 群里真人发它时的几次上下文 合成一条词条：名字 / 字 / 画面 / 出处 / 意思 / 一般用来 / 把握
  以后再见到这张（或长得几乎一样的：dHash 差 ≤ near_bits）→ 直接用词条，不再重新看图
  小明 在控制台「表情包词典」页能看、能改（改过的锁住 不被自动覆盖）、能点「重新认」
  存 data/sticker_dict.json，缩略图 data/sticker_thumbs/<id>.jpg；删数据：forget(uid) 删他发的那几次上下文
  config → stickerdict（enabled / deep_after / deep_daily / near_bits / max_entries）
"""
import io
import json
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULTS = {"enabled": True, "deep_after": 3, "deep_daily": 30, "near_bits": 6, "max_entries": 3000}
C = dict(DEFAULTS)
_lock = threading.Lock()
_D = {"d": None}
_inflight = set()
_pending = {}        # 对话 → (id, 时间)：发完这张以后别人接的那句 记进这次的上下文


def configure(cfg=None, data_dir=None):
    global DATA
    for k, v in (cfg or {}).items():
        if not str(k).startswith("_") and k in DEFAULTS:
            C[k] = v
    if data_dir:
        DATA = Path(data_dir)
    _D["d"] = None


def on():
    return bool(C.get("enabled", True))


def _file():
    return DATA / "sticker_dict.json"


def thumb_path(sid):
    return DATA / "sticker_thumbs" / (re.sub(r"[^0-9A-Za-z_.-]", "_", str(sid))[:100] + ".jpg")


def load():
    if _D["d"] is None:
        try:
            d = json.loads(_file().read_text(encoding="utf-8"))
            _D["d"] = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            _D["d"] = {}
        _D["d"].setdefault("items", {})
        _D["d"].setdefault("alias", {})
        _D["d"].setdefault("days", {})
    return _D["d"]


def reload():
    _D["d"] = None


_S = {"t": 0.0, "dirty": False}


def save(force=False):
    """存盘：每张图都存一次太勤 —— 20 秒内攒着（flush() / 下一次 save 再写）；force 立刻写"""
    d = load()
    if not force and time.time() - _S["t"] < 20:
        _S["dirty"] = True
        return
    try:
        items = d["items"]
        if len(items) > int(C["max_entries"]):        # 太多：只见过一次、最久没见的先扔（深度认过的 / 改过的留着）
            drop = sorted((k for k, v in items.items() if v.get("n", 0) <= 1 and not v.get("deep") and not v.get("fix")),
                          key=lambda k: items[k].get("last", 0))[:len(items) - int(C["max_entries"])]
            for k in drop:
                items.pop(k, None)
        DATA.mkdir(parents=True, exist_ok=True)
        with _lock:
            tmp = _file().with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(_file())
        _S["t"], _S["dirty"] = time.time(), False
    except OSError:
        pass


def flush():
    if _S["dirty"]:
        save(force=True)


# ───────────── 控制台改词条：控制台是另一个进程 → 写进 sticker_edits.jsonl 排队，机器人这边 apply_edits() 再改（不然两边各存各的会互相盖掉）
def _edits_file():
    return DATA / "sticker_edits.jsonl"


def request_edit(sid, fix=None, redo=None):
    """控制台那边调：排一条改动"""
    DATA.mkdir(parents=True, exist_ok=True)
    with open(_edits_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": str(sid), "fix": fix, "redo": redo, "t": round(time.time())}, ensure_ascii=False) + "\n")
    return True


def pending_edits():
    out = {}
    try:
        for ln in _edits_file().read_text(encoding="utf-8").splitlines():
            try:
                x = json.loads(ln)
                out.setdefault(str(x.get("id")), {}).update({k: v for k, v in x.items() if k in ("fix", "redo") and v is not None})
            except ValueError:
                pass
    except OSError:
        pass
    return out


def apply_edits():
    """机器人这边调：把排着的改动改进词典；返回要重新认的 id"""
    pe = pending_edits()
    if not pe:
        return []
    try:
        _edits_file().write_text("", encoding="utf-8")
    except OSError:
        return []
    redo = []
    for sid, x in pe.items():
        if edit(sid, fix=x.get("fix"), redo=x.get("redo")) and x.get("redo"):
            redo.append(_sid(sid))
    save(force=True)
    return redo


def _sid(sid):
    d = load()
    return d["alias"].get(str(sid), str(sid))


def get(sid):
    return load()["items"].get(_sid(sid))


def text_for(e):
    """给她看的那句（词条 → 一段描述）；没认过 → None"""
    if not e:
        return None
    if e.get("fix"):
        return "表情包（老板核对过的）：" + str(e["fix"])[:220]
    dp = e.get("deep") or {}
    if not dp or float(dp.get("conf") or 0) < 40:
        return None
    parts = []
    if dp.get("name"):
        parts.append(f"表情包「{dp['name']}」")
    else:
        parts.append("表情包")
    if dp.get("meaning"):
        parts.append("意思：" + dp["meaning"])
    if dp.get("usage"):
        parts.append("一般用来：" + dp["usage"])
    if dp.get("scene"):
        parts.append("画面：" + dp["scene"])
    if dp.get("text"):
        parts.append("字：「" + dp["text"] + "」")
    if dp.get("origin"):
        parts.append("出处：" + dp["origin"])
    return "；".join(parts)[:260]


def lookup(sid):
    """认过的（深度认过 / 老板改过）→ 给她看的那句；没认过 → None"""
    if not on():
        return None
    return text_for(get(sid))


# ───────────── 长相指纹（同一张表情被压缩 / 改尺寸后哈希会变）─────────────
def dhash(data):
    """第一帧 9×8 灰度 → 64 位差值哈希（十六进制）；没装 Pillow / 打不开 → \"\""""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.seek(0)
        g = im.convert("L").resize((9, 8))
        px = list(g.getdata())
        bits = 0
        for y in range(8):
            for x in range(8):
                bits = (bits << 1) | (1 if px[y * 9 + x] > px[y * 9 + x + 1] else 0)
        return f"{bits:016x}"
    except Exception:
        return ""


def _ham(a, b):
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


def near(dh, exclude=""):
    """长得几乎一样、已经认过的那条的 id；没有 → \"\""""
    if not dh:
        return ""
    best, bd = "", 99
    for k, v in load()["items"].items():
        if k == exclude or not v.get("dh") or not (v.get("deep") or v.get("fix")):
            continue
        h = _ham(dh, v["dh"])
        if h < bd:
            best, bd = k, h
    return best if bd <= int(C["near_bits"]) else ""


def link(sid, target):
    """这张跟 target 是同一张（长得一样）→ 以后按 target 那条用"""
    d = load()
    if str(sid) != str(target) and target in d["items"]:
        d["alias"][str(sid)] = target
        save()


# ───────────── 见到一次 ─────────────
def seen(sid, conv, uid, summary="", before=(), said="", to_her=False, sticker=False, now=None, mface=""):
    """群里 / 私聊里有人发了这张 → 记一次；返回 \"deep\"（该深度认了）/ \"\""""
    if not on() or not sid:
        return ""
    now = float(now or time.time())
    d = load()
    k = _sid(sid)
    e = d["items"].setdefault(k, {"n": 0, "first": round(now), "convs": [], "ctx": []})
    e["n"] = int(e.get("n", 0)) + 1
    e["last"] = round(now)
    if summary and not e.get("summary"):
        e["summary"] = str(summary)[:30]
    if mface:
        e["mface"] = str(mface)[:40]
    if sticker or summary or mface:
        e["sticker"] = True
    if conv not in e["convs"]:
        e["convs"] = (e["convs"] + [conv])[-10:]
    e["ctx"] = (e.get("ctx") or []) + [{"t": round(now), "uid": str(uid), "conv": conv,
                                        "before": [str(x)[:40] for x in list(before)[-2:]], "said": str(said or "")[:40], "after": ""}]
    del e["ctx"][:-5]
    _pending[conv] = (k, now)
    save()
    if not e.get("redo") and (e.get("fix") or e.get("deep")):
        return ""
    if k in _inflight:
        return ""
    if e.get("redo") or e["n"] >= int(C["deep_after"]) or (to_her and e.get("sticker")):
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if int(d["days"].get(day, 0)) >= int(C["deep_daily"]):
            return ""
        return "deep"
    return ""


def after(conv, uid, text, now=None):
    """发完表情包以后（60 秒内）别人接的那一句 → 记进那次的上下文（看群里拿它表达什么）"""
    p = _pending.get(conv)
    now = float(now or time.time())
    if not p or now - p[1] > 60 or not str(text or "").strip():
        return
    _pending.pop(conv, None)
    e = load()["items"].get(p[0])
    if e and e.get("ctx") and not e["ctx"][-1].get("after"):
        e["ctx"][-1]["after"] = str(text)[:40]
        e["ctx"][-1]["after_uid"] = str(uid)
        save()


def note_auto(sid, cap, dh="", url=""):
    """第一眼看图的描述（还没深度认）记一下（控制台对照用）"""
    if not on():
        return
    d = load()
    e = d["items"].setdefault(_sid(sid), {"n": 0, "first": round(time.time()), "convs": [], "ctx": []})
    if cap and not e.get("auto"):
        e["auto"] = str(cap)[:300]
    if dh and not e.get("dh"):
        e["dh"] = dh
    if url:
        e["url"] = str(url)[:600]
    save()


# ───────────── 深度认 ─────────────
DEEP_LOOK = """这是 QQ 群里大家常发的一张表情包（{frames}）。请认真认清楚它，后面会拿你的结果当词典用，错了就会一直错：
1. 字：图里所有的字，逐字照抄（看不清的用▢）；没有字写「无」
2. 画面：谁 / 什么东西、在做什么、什么表情和动作（动图按「一开始…然后…最后…」说）
3. 梗：如果你认得这是哪个梗 / 哪部作品 / 哪个名场面 / 哪个固定的表情包系列，写出来；不认得就写「不确定」（别瞎编）
4. 情绪：它传达的是什么情绪 / 态度
按这四行输出，每行以「字：」「画面：」「梗：」「情绪：」开头。"""

DEEP_MERGE = """你在整理一本「QQ 群表情包词典」。下面是一张表情包的资料：看图模型认真看了一遍的结果、网上查到的（可能没有 / 可能不相关）、
群里真人几次发它时前后在说什么。请合成一条词条，只输出 JSON：
{"name": "大家会怎么叫它（短，没有就按画面起个短名）", "text": "图里的字（没有就空）", "scene": "画面一句话", "origin": "出处 / 梗（不确定就空，别编）",
 "meaning": "它在群聊里表达什么（结合真人用它的上下文）", "usage": "一般什么时候发（一句）", "conf": 0~100 的把握,
 "ok": true / false（适不适合当一张普通表情包自己拿来发：截图、聊天记录、真人照片、擦边、带别人隐私的 → false）}
看图结果和真人用法冲突时以真人用法为准；网上的资料对不上这张图就别用。"""


async def deep(sid, url, look, llm, search=None, fetch=None, log=print, now=None):
    """深度认一张：look(b64, mime, prompt) 看图；llm(system, messages) 文字模型；search(q) → [{title, snippet}]；
    fetch(url) → bytes。返回词条 dict 或 None"""
    k = _sid(sid)
    if k in _inflight:
        return None
    _inflight.add(k)
    now = float(now or time.time())
    d = load()
    e = d["items"].setdefault(k, {"n": 0, "first": round(now), "convs": [], "ctx": []})
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    d["days"] = {x: v for x, v in d["days"].items() if x >= time.strftime("%Y-%m-%d", time.localtime(now - 7 * 86400))}
    d["days"][day] = int(d["days"].get(day, 0)) + 1
    save()
    try:
        url = url or e.get("url") or ""
        if not url or fetch is None:
            return None
        data = await fetch(url)
        if not data:
            return None
        import base64

        import imgprep
        mime = _mime(data)
        if not mime:
            return None
        dh = dhash(data)
        if dh:
            e["dh"] = dh
        _thumb(k, data)
        pdata, pmime, note = imgprep.prep(data, mime, 1280)
        seen_raw = await look(base64.b64encode(pdata).decode(), pmime, DEEP_LOOK.format(frames=note or "静态图"))
        seen_raw = str(seen_raw or "").strip()
        if not seen_raw:
            return None
        found = []
        m_txt = re.search(r"字[:：]\s*(.+)", seen_raw)
        m_meme = re.search(r"梗[:：]\s*(.+)", seen_raw)
        q = ""
        if m_meme and "不确定" not in m_meme.group(1):
            q = m_meme.group(1).strip()[:20] + " 表情包"
        elif m_txt and m_txt.group(1).strip() not in ("无", "") and len(m_txt.group(1).strip()) >= 2:
            q = m_txt.group(1).strip().replace("▢", "")[:20] + " 表情包 梗"
        if q and search is not None:
            try:
                found = (await search(q) or [])[:5]
            except Exception as ex:
                log("[表情包词典] 上网查失败:", repr(ex)[:80])
        ctx_lines = []
        for c in (e.get("ctx") or [])[-5:]:
            ctx_lines.append("之前：" + " / ".join(c.get("before") or []) + (f"；发图的人说：{c['said']}" if c.get("said") else "")
                             + (f"；之后有人接：{c['after']}" if c.get("after") else ""))
        user = ("【看图结果】\n" + seen_raw[:800] + "\n【网上查到的】\n"
                + ("\n".join(f"- {x.get('title', '')}：{str(x.get('snippet', ''))[:120]}" for x in found) or "（没查）")
                + "\n【群里真人发它时】\n" + ("\n".join(ctx_lines) or "（没记到）")
                + (f"\n【QQ 给的名字】{e.get('mface') or e.get('summary')}" if (e.get("mface") or e.get("summary")) else ""))
        raw = await llm(DEEP_MERGE, [{"role": "user", "content": user}])
        m = re.search(r"\{.*\}", str(raw or ""), re.S)
        js = json.loads(m.group(0)) if m else None
        if not isinstance(js, dict):
            return None
        entry = {k2: str(js.get(k2) or "")[:120] for k2 in ("name", "text", "scene", "origin", "meaning", "usage")}
        try:
            entry["conf"] = max(0, min(100, int(float(js.get("conf") or 0))))
        except (TypeError, ValueError):
            entry["conf"] = 0
        if isinstance(js.get("ok"), bool):           # 表情包库（C31 10-31）：适不适合她自己拿来发
            entry["ok"] = js["ok"]
        entry["t"] = round(time.time())
        entry["look"] = seen_raw[:600]
        entry["searched"] = q
        d = load()
        e = d["items"].setdefault(k, e)
        e["deep"] = entry
        e.pop("redo", None)
        save(force=True)
        log(f"[表情包词典] 认好了 {k[:16]}：「{entry.get('name')}」{entry.get('meaning', '')[:30]}（把握 {entry['conf']}）")
        return entry
    except Exception as ex:
        log("[表情包词典] 深度认出错:", repr(ex)[:120])
        return None
    finally:
        _inflight.discard(k)


def _mime(b):
    if b[:3] == b"GIF":
        return "image/gif"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:2] == b"\xff\xd8":
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _thumb(k, data):
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.seek(0)
        im = im.convert("RGB")
        im.thumbnail((160, 160))
        p = thumb_path(k)
        p.parent.mkdir(parents=True, exist_ok=True)
        im.save(p, "JPEG", quality=80)
    except Exception:
        pass


# ───────────── 控制台 ─────────────
def listing(limit=200):
    out = []
    for k, v in load()["items"].items():
        if not v.get("sticker") and not v.get("deep") and int(v.get("n", 0)) < 2:
            continue
        out.append({"id": k, "n": v.get("n", 0), "last": v.get("last", 0), "convs": len(v.get("convs") or []),
                    "summary": v.get("summary") or v.get("mface") or "", "auto": v.get("auto") or "", "deep": v.get("deep") or None,
                    "fix": v.get("fix") or "", "redo": bool(v.get("redo")), "thumb": thumb_path(k).exists(),
                    "ctx": [{"before": c.get("before"), "said": c.get("said"), "after": c.get("after")} for c in (v.get("ctx") or [])[-3:]]})
    out.sort(key=lambda x: (-int(x["n"]), -int(x["last"] or 0)))
    return out[:limit]


def edit(sid, fix=None, redo=None):
    """控制台：改词条（fix：老板写的解释 空 = 撤掉）/ 标「重新认」"""
    d = load()
    k = _sid(sid)
    e = d["items"].get(k)
    if not e:
        return False
    if fix is not None:
        if str(fix).strip():
            e["fix"] = str(fix).strip()[:300]
            e["fix_t"] = round(time.time())
        else:
            e.pop("fix", None)
    if redo is not None:
        if redo:
            e["redo"] = True
        else:
            e.pop("redo", None)
    save()
    return True


def forget(uid, texts=(), names=()):
    """删数据：他发表情包时记下的上下文删掉；别人发的那几次里 他说的话（前文 / 接的那句）和提到他名字的也去掉；返回删了几处"""
    uid = str(uid)
    pre = {str(t).strip()[:40] for t in texts or () if str(t).strip()}
    nm = [str(x) for x in names or () if len(str(x)) >= 2]

    def bad(x):
        x = str(x or "")
        return bool(x) and (x.strip()[:40] in pre or any(n in x for n in nm))
    n = 0
    d = load()
    for v in d["items"].values():
        before = len(v.get("ctx") or [])
        v["ctx"] = [c for c in v.get("ctx") or [] if c.get("uid") != uid]
        n += before - len(v["ctx"])
        for c in v["ctx"]:
            if c.get("after_uid") == uid or bad(c.get("after")):
                c["after"], c["after_uid"] = "", ""
                n += 1
            b = [x for x in c.get("before") or [] if not bad(x)]
            if len(b) != len(c.get("before") or []):
                n += len(c.get("before") or []) - len(b)
                c["before"] = b
            if bad(c.get("said")):
                c["said"] = ""
                n += 1
    for conv in [c for c in _pending if c == f"private_{uid}"]:
        _pending.pop(conv, None)
    save(force=True)
    return n
