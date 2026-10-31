#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
功能坏了时她怎么说（C21，10-30）—— behavior.outage_persona 开着才用（默认关）
  上游只给状态：哪个功能坏了、坏了多久、大概多久好；不许往外说幕后的词（系统 / key / 余额 / 报错）。
  怎么跟人讲由她自己定（已知错误 E1：这里和提示里都不写示范句）。
  一次故障一个说法：她第一次说出口的那句记下来；到恢复前再有人要，就把那句给她当「你之前这么说过」保持一致。恢复了就清掉。
  现在接了两样：语音（MiniMax 停着）、上网查（所有搜索源都不通）。状态记在 data/outage.json（重启后也一致）。
"""
import json
import time
from pathlib import Path

_dir = Path(__file__).resolve().parent / "data"


def configure(data_dir=None):
    global _dir
    if data_dir:
        _dir = Path(data_dir)


def _file():
    return _dir / "outage.json"


def _load():
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    try:
        _dir.mkdir(parents=True, exist_ok=True)
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_file())
    except OSError:
        pass


def _probe(feature, now):
    """→ (坏着没有, 大概到几点好 / 0 = 不知道, 从几点开始坏的 / 0 = 不知道)"""
    try:
        if feature == "voice":
            import voice
            until = float(voice._pause.get("until") or 0)
            return now < until, until, float(voice._pause.get("since") or 0)
        if feature == "search":
            import websearch
            o = getattr(websearch, "outage", None) or {}
            return bool(o.get("since")), 0.0, float(o.get("since") or 0)
    except Exception:
        pass
    return False, 0.0, 0.0


def current(feature, now=None):
    """这个功能现在坏着吗 → 记录（since / until / said）或 None；好了就把记录清掉"""
    now = now or time.time()
    bad, until, since = _probe(feature, now)
    d = _load()
    rec = d.get(feature)
    if bad:
        if not rec:
            rec = d[feature] = {"since": since if 0 < since <= now else now}
        rec["until"] = until
        _save(d)
        return rec
    if rec:
        d.pop(feature, None)
        _save(d)
    return None


def block(feature, what, now=None):
    """坏着 → 给她看的一段状态（不写示范句）；没坏 → 空"""
    now = now or time.time()
    r = current(feature, now)
    if not r:
        return ""
    mins = max(1, int((now - float(r["since"])) / 60))
    left = int((float(r.get("until") or 0) - now) / 60) if r.get("until") else 0
    s = (f"\n# {what}这会儿用不了\n你这边「{what}」出了毛病：已经 {mins} 分钟了，"
         + (f"大概还要 {left} 分钟。" if left > 0 else "什么时候好不确定。")
         + "这次做不到 —— 用你自己的方式跟对方说一句，别假装做了；别提系统、接口、key、余额、报错这些幕后的词。")
    if r.get("said"):
        s += f"这次毛病你之前跟人是这么说的：「{r['said']}」—— 跟这个说法保持一致，别换一个理由。"
    return s + "\n"


def note_said(feature, text):
    """她第一次就这次故障说出口的话（记下来 以后保持一致）"""
    t = " ".join(str(text or "").split())[:40]
    if not t:
        return
    d = _load()
    r = d.get(feature)
    if r and not r.get("said"):
        r["said"], r["said_at"] = t, time.time()
        _save(d)
