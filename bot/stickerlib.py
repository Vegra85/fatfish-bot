#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
她自己的表情包库（C31，10-31 v1.15.14 影子版：只记不发）

  小明 10-30：「群里的表情包偷过来用 + 怎么挑」。表情包词典（stickerdict）已经在认群里大家发的图了：
  认清楚、群里不止一个人发过的那些 → 把原图存进她自己的库（data/sticker_lib/）；
  她每回一句话 → 让判断模型从库里挑一张「这时候接一张合不合适 / 哪张」→ 只记下来（data/sticker_shadow.jsonl），不发。
  小明 在控制台「表情包词典」页最下面看「她会挑哪张」，点「挑得好 / 不好」；挑得够准了再改成真发（shadow: false 以后再接）。

  收录条件（config → stickerlib）：
    深度认过且把握 >= min_conf（默认 60），或者老板在词典里核对过；群里 >= min_people（默认 2）个不同的人发过；
    认图时判断模型说它「不适合拿来自己发」（截图 / 聊天记录 / 真人照片 / 擦边 / 隐私）的不收；词条里写着这些的也不收
  挑图：库里按热度（被发的次数、在几个群）取前 cands 张给判断模型看（名字 / 意思 / 一般用来），
    它看最近几句 + 她刚说的 → 选一张或者不选；同一个对话 gap_min 分钟最多挑一次、一天最多 daily 次
  删数据：图本身是群里公开发的表情包；影子记录里的聊天只留最近 4 句、每句 60 字，forget(uid) 删那个人说的
"""
import json
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULTS = {"enabled": True, "shadow": True, "min_conf": 60, "min_people": 2, "max_items": 300, "cands": 8,
            "gap_min": 5, "daily": 150,
            "every_n": 5, "send_gap_min": 10, "same_hours": 6}   # 转正以后（shadow: false 11-06 v1.17.19）：她回 5 轮最多跟 1 张、两张隔 10 分钟、同一张 6 小时内不重复
C = dict(DEFAULTS)
_lock = threading.Lock()
_D = {"d": None}
_last = {}          # 对话 → 上次影子挑图的时间
_inflight = set()
BAD = re.compile(r"截图|聊天记录|真人|照片|自拍|证件|二维码|色情|擦边|裸|政治|隐私")

PICK_SYS = """你在帮一个 QQ 群聊天机器人判断：她刚说完一句话，这时候顺手再发一张表情包合不合适、发哪张。
下面给你最近几句聊天、她刚说的话、她的表情包库里的几张（编号 / 名字 / 意思 / 一般用来）。
只在表情包的意思跟这时候的气氛和她这句话的态度真的对得上时才选；对不上、气氛严肃、别人在说正事、她这句已经够了 → 不选。
只输出 JSON：{"pick": "编号或空字符串", "why": "一句话理由"}"""


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


def lib_dir():
    return DATA / "sticker_lib"


def _file():
    return DATA / "sticker_lib.json"


def shadow_file():
    return DATA / "sticker_shadow.jsonl"


def labels_file():
    return DATA / "sticker_shadow_labels.json"


def load():
    with _lock:
        if _D["d"] is None:
            try:
                d = json.loads(_file().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                d = {}
            d.setdefault("items", {})
            d.setdefault("days", {})
            for k in ("sent", "n", "recent"):        # 转正以后的限频（11-06 v1.17.19）：每个对话上次发的 / 她回了几轮 / 每张上次发的时间
                if not isinstance(d.get(k), dict):
                    d[k] = {}
            _D["d"] = d
        return _D["d"]


def reload():
    _D["d"] = None
    return load()


def save():
    d = load()
    with _lock:
        try:
            _file().parent.mkdir(parents=True, exist_ok=True)
            tmp = _file().with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            tmp.replace(_file())
        except OSError:
            pass


def people(e):
    """词典里这张：最近几次是几个不同的人发的"""
    return len({str(c.get("uid")) for c in (e.get("ctx") or []) if c.get("uid")})


def eligible(e, in_lib=False):
    """词典条目 → 够不够格收进她的库（理由）
    in_lib（已经在库里的 挑图时用）：不看把握 —— 11-06 v1.17.22：重新认以后把握低了（DeepSeek 打分偏低）就不给挑了；
    把握只管收不收新的，收进来的只看适不适合自己发 / 截图照片擦边"""
    if not e:
        return False, "没这张"
    dp = e.get("deep") or {}
    if not e.get("fix"):
        if not dp:
            return False, "还没深度认"
        if not in_lib and float(dp.get("conf") or 0) < float(C["min_conf"]):
            return False, "把握不够"
        if dp.get("ok") is False:
            return False, "认图时说不适合自己发"
        if BAD.search(" ".join(str(dp.get(k) or "") for k in ("name", "scene", "origin", "meaning", "usage"))):
            return False, "像截图 / 照片 / 擦边"
    if people(e) < int(C["min_people"]):
        return False, "发过的人不够多"
    return True, ""


def has(sid):
    return str(sid) in load()["items"]


def want(sid, e):
    """有人刚发了这张：该不该现在把原图存下来"""
    if not on() or not sid or has(sid) or sid in _inflight:
        return False
    if len(load()["items"]) >= int(C["max_items"]):
        return False
    return eligible(e)[0]


def _ext(data):
    if data[:3] == b"GIF":
        return ".gif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def safe_name(sid):
    return re.sub(r"[^0-9A-Za-z_.-]", "_", str(sid))[:80]


async def steal(sid, url, fetch, e=None, log=print):
    """存原图（fetch(url) → bytes）→ True / False"""
    if not url or fetch is None or sid in _inflight:
        return False
    _inflight.add(sid)
    try:
        data = await fetch(url)
        if not data or len(data) < 200:
            return False
        if len(data) > 5 * 1024 * 1024:
            return False
        name = safe_name(sid).rsplit(".", 1)[0] + _ext(data)
        p = lib_dir() / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        dp = (e or {}).get("deep") or {}
        load()["items"][str(sid)] = {"t": round(time.time()), "file": name, "name": str(dp.get("name") or (e or {}).get("fix") or "")[:30]}
        save()
        log(f"[表情包库] 收了一张「{load()['items'][str(sid)]['name'] or sid[:12]}」（库里现在 {len(load()['items'])} 张）")
        return True
    except Exception as ex:
        log("[表情包库] 存图出错:", repr(ex)[:100])
        return False
    finally:
        _inflight.discard(sid)


def candidates(entries, k=None):
    """库里的 → [(sid, 词典条目)] 按热度（次数 + 群数）取前 k 张；词典条目不够格了的跳过"""
    out = []
    recent = _recent_sids()                     # 转正以后：6 小时内发过的不再给它挑（11-06 v1.17.19）
    for sid in load()["items"]:
        e = entries.get(sid)
        if not e or not eligible(e, in_lib=True)[0] or sid in recent:
            continue
        heat = int(e.get("n", 0)) + 3 * len(e.get("convs") or [])
        out.append((heat, sid, e))
    out.sort(key=lambda x: -x[0])
    return [(sid, e) for _, sid, e in out[:int(k or C["cands"])]]


def _desc(e):
    if e.get("fix"):
        return "（老板核对过的）" + str(e["fix"])[:120]
    dp = e.get("deep") or {}
    return f"{dp.get('name', '')}｜意思：{str(dp.get('meaning', ''))[:60]}｜一般用来：{str(dp.get('usage', ''))[:50]}"


def ready(conv, now=None):
    """这个对话现在能不能挑一次图（开关 / 库里有图 / 间隔 / 一天上限；转正以后还要限频允许 —— 发不了就不白问模型）"""
    if not on() or not load()["items"]:
        return False
    now = float(now or time.time())
    if live() and send_block(conv, now=now):
        return False
    if now - _last.get(conv, 0) < float(C["gap_min"]) * 60:
        return False
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    return int(load()["days"].get(day, 0)) < int(C["daily"])


async def shadow_pick(conv, chat, said, entries, llm, log=print, now=None):
    """chat：[(谁, 说的)] 最近几句；said：她刚说的；entries：词典 items；llm(system, messages) → 文字
    → 记一行影子（挑了 / 没挑都记 —— 控制台看「该挑没挑」也有用）；返回那一行"""
    now = float(now or time.time())
    if not ready(conv, now):
        return None
    cands = candidates(entries)
    if not cands:
        return None
    _last[conv] = now
    d = load()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    d["days"] = {x: v for x, v in d["days"].items() if x >= time.strftime("%Y-%m-%d", time.localtime(now - 7 * 86400))}
    d["days"][day] = int(d["days"].get(day, 0)) + 1
    save()
    lines = [(str(w)[:12], str(t)[:60]) for w, t in list(chat)[-4:] if str(t or "").strip()]
    user = ("【最近几句】\n" + ("\n".join(f"{w}：{t}" for w, t in lines) or "（没有）")
            + f"\n【她刚说的】\n{str(said)[:120]}\n【她的表情包库】\n"
            + "\n".join(f"{i}. {_desc(e)}" for i, (_, e) in enumerate(cands, 1)))
    try:
        raw = await llm(PICK_SYS, [{"role": "user", "content": user}])
        m = re.search(r"\{.*\}", str(raw or ""), re.S)
        js = json.loads(m.group(0)) if m else {}
    except Exception as ex:
        log("[表情包库] 影子挑图出错:", repr(ex)[:100])
        return None
    pick = str((js or {}).get("pick") or "").strip()
    sid = ""
    if pick.isdigit() and 1 <= int(pick) <= len(cands):
        sid = cands[int(pick) - 1][0]
    row = {"t": round(now), "conv": conv, "chat": [list(x) for x in lines], "said": str(said)[:120],
           "cands": [s for s, _ in cands], "pick": sid, "name": (load()["items"].get(sid) or {}).get("name", "") if sid else "",
           "why": str((js or {}).get("why") or "")[:80]}
    try:
        with open(shadow_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
    log(f"[表情包库·影子] {conv}：" + (f"会发「{row['name'] or sid[:12]}」（{row['why'][:30]}）" if sid else f"不发（{row['why'][:30]}）"))
    return row


# ───────────── 转正（11-06 v1.17.19 小明 选：shadow: false = 挑中了真发；限频是 小明 定的）─────────────
def live():
    return on() and not C.get("shadow", True)


def note_reply(conv, now=None):
    """她在这个对话回了一轮（转正以后才数：回够 every_n 轮才能再跟一张）"""
    d = load()
    d["n"][str(conv)] = int(d["n"].get(str(conv), 0)) + 1
    if len(d["n"]) > 500:
        for k in list(d["n"])[:100]:
            d["n"].pop(k, None)
    save()


def _recent_sids(now=None):
    if not live():
        return set()
    now = float(now or time.time())
    win = float(C["same_hours"]) * 3600
    return {s for s, t in (load()["recent"] or {}).items() if now - float(t or 0) < win}


def rename(sid, name):
    """词典重新认好了 → 库里这张的名字跟着改（11-06 v1.17.21：以前收进来时的名字一直不变 —— 认错的「芙宁娜扣盆」改不过来）"""
    it = load()["items"].get(str(sid))
    nm = str(name or "").strip()[:30]
    if not it or not nm or it.get("name") == nm:
        return False
    it["name"] = nm
    save()
    return True


def file_of(sid):
    it = load()["items"].get(str(sid)) or {}
    p = lib_dir() / str(it.get("file") or "")
    return p if it.get("file") and p.is_file() else None


def send_block(conv, sid=None, now=None):
    """能不能发（不能 → 原因；能 → 空）：这个对话上一张以后她回够了几轮、隔够了几分钟；这张 6 小时内没发过；图还在"""
    now = float(now or time.time())
    d = load()
    last = d["sent"].get(str(conv)) or {}
    if last:
        n = int(d["n"].get(str(conv), 0))
        if n < int(C["every_n"]):
            return f"上一张以后她才回了 {n} 轮"
        if now - float(last.get("t") or 0) < float(C["send_gap_min"]) * 60:
            return f"离上一张还不到 {int(C['send_gap_min'])} 分钟"
    if sid is not None:
        if str(sid) in _recent_sids(now):
            return f"这张 {int(C['same_hours'])} 小时内发过"
        if file_of(sid) is None:
            return "图找不到了"
    return ""


def mark_sent(conv, sid, now=None):
    now = float(now or time.time())
    d = load()
    d["sent"][str(conv)] = {"t": round(now), "sid": str(sid)}
    d["n"][str(conv)] = 0
    d["recent"][str(sid)] = round(now)
    keep = max(float(C["same_hours"]) * 3600, 86400)
    d["recent"] = {s: t for s, t in d["recent"].items() if now - float(t or 0) < keep}
    if len(d["sent"]) > 500:
        for k in list(d["sent"])[:100]:
            d["sent"].pop(k, None)
    save()


# ───────────── 控制台 ─────────────
def shadow_rows(limit=100):
    try:
        lines = shadow_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit * 3:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out[-limit:][::-1]


def labels():
    try:
        return json.loads(labels_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def label(key, good):
    """控制台点「挑得好 / 不好 / 撤掉」：key = 那一行的 时间|对话；good = True / False / None"""
    lb = labels()
    if good is None:
        lb.pop(str(key), None)
    else:
        lb[str(key)] = bool(good)
    try:
        labels_file().parent.mkdir(parents=True, exist_ok=True)
        labels_file().write_text(json.dumps(lb, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return False
    return True


def view(entries, limit=60):
    lb = labels()
    rows = shadow_rows(limit)
    for r in rows:
        r["key"] = f"{r.get('t')}|{r.get('conv')}"
        r["label"] = lb.get(r["key"])
    items = []
    for sid, it in load()["items"].items():
        e = entries.get(sid) or {}
        items.append({"id": sid, "name": it.get("name") or (e.get("deep") or {}).get("name") or "", "t": it.get("t"),
                      "n": e.get("n", 0), "people": people(e), "ok": eligible(e, in_lib=True)[0]})
    items.sort(key=lambda x: -int(x["n"] or 0))
    good = sum(1 for v in lb.values() if v)
    return {"items": items, "rows": rows, "good": good, "bad": sum(1 for v in lb.values() if v is False),
            "shadow": bool(C.get("shadow", True)), "on": on(), "conf": {k: C[k] for k in DEFAULTS}}


def forget(uid, texts=(), names=()):
    """删他的：影子记录里他说的那几句（按名字 / 原话认）→ 删了几行"""
    texts = {str(t)[:60] for t in texts if t}
    names = {str(n)[:12] for n in names if n}
    if not texts and not names:
        return 0
    try:
        lines = shadow_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    out, n = [], 0
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        keep = [c for c in r.get("chat") or [] if not (c[0] in names or c[1] in texts)]
        if len(keep) != len(r.get("chat") or []):
            r["chat"] = keep
            n += 1
        out.append(json.dumps(r, ensure_ascii=False))
    if n:
        try:
            shadow_file().write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
        except OSError:
            return 0
    return n
