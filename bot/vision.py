#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大肥鱼的眼睛：把 QQ 图片变成一句描述
  DeepSeek 看不了图，所以单独接一个识图模型
  provider: ollama（本地，推荐）/ openai（OpenAI 兼容云端，如智谱）/ claude
  主模型失败时用 "fallback" 段里的备用模型；都没配时图片还是显示成 [图片]
"""
import base64
import json
import re
import time
from pathlib import Path

import httpx

import imgprep
import localai

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
V = CFG.get("vision") or {}
CACHE = ROOT / "data" / "vision_cache.json"
MAX_BYTES = 4_500_000
PER_HOUR = int(V.get("max_per_hour", 40))

MAX_CHARS = int(V.get("max_chars", 150))
PROMPT = ("这是 QQ 聊天里别人发的一张图。用中文把它说清楚，让一个看不到图的人也能接话：\n"
          "1. 画面里有什么、谁在干什么（具体：颜色 动作 表情 背景里的东西）\n"
          "2. 图里的文字原样抄出来（没有就不写）\n"
          "3. 如果是表情包/梗图：它想表达什么情绪或梗\n"
          f"总共不超过 {MAX_CHARS} 字 不要说「这是一张图片」 直接说内容 写成一段话 不分行。")
CACHE_VER = "v3:"     # v3：动图改成看拼帧 旧的「只看第一帧」的描述作废

_recent = []


def _spec_ok(spec):
    if not spec:
        return False
    prov = spec.get("provider", "openai")
    if prov == "ollama":
        return True
    if prov == "claude":
        return bool(spec.get("api_key") or (CFG["llm"].get("claude") or {}).get("api_key"))
    return bool(spec.get("api_key"))


def enabled():
    return _spec_ok(V) or _spec_ok(V.get("fallback"))


def _load_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(c):
    if len(c) > 500:
        c = dict(list(c.items())[-400:])
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")


def _mime(b):
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"GIF8":
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_segments(segs):
    """从 OneBot 消息段里拿出图片 (key, url, summary)"""
    out = []
    for s in segs:
        if s.get("type") not in ("image", "mface"):
            continue
        d = s.get("data") or {}
        url = d.get("url") or ""
        if not url.startswith("http"):
            f = d.get("file") or ""
            url = f if f.startswith("http") else ""
        key = d.get("file_unique") or d.get("file") or url
        out.append((str(key)[:120], url, d.get("summary") or ""))
    return out


def _to_png(b64):
    """Ollama 不吃 GIF/WEBP：转成第一帧 PNG（没装 Pillow 就算了 交给备用模型）"""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(base64.b64decode(b64)))
        im.seek(0)
        out = io.BytesIO()
        im.convert("RGBA").save(out, "PNG")
        return base64.b64encode(out.getvalue()).decode()
    except Exception:
        return b64


def _prompt(note):
    if not note:
        return PROMPT
    return PROMPT + (f"\n注意：这是一张{note}。把它当成一个动作过程来说（一开始…然后…最后…）"
                     "说清楚动起来是在干什么、表达什么情绪 不要一格一格分开讲 不要提「拼图」「第几帧」。")


async def _ask_one(spec, b64, mime, note=""):
    prov = spec.get("provider", "openai")
    prompt = _prompt(note)
    if prov == "ollama" and mime in ("image/gif", "image/webp"):
        b64 = _to_png(b64)
    if prov == "ollama":
        return await localai.chat([{"role": "user", "content": prompt}], images=[b64],
                                  model=spec.get("model"), temperature=0.2, max_tokens=400)
    if prov == "claude":
        spec = {**(CFG["llm"].get("claude") or {}), **{k: v for k, v in spec.items() if v}}
        body = {"model": spec.get("model"), "max_tokens": 400,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text", "text": prompt}]}]}
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.post(spec["base_url"].rstrip("/") + "/v1/messages", json=body,
                             headers={"x-api-key": spec["api_key"],
                                      "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json()["content"]).strip()
    body = {"model": spec["model"], "max_tokens": 400, "temperature": 0.2,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": prompt}]}]}
    hdr = {"Authorization": f"Bearer {spec['api_key']}"} if spec.get("api_key") else {}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(spec["base_url"].rstrip("/") + "/chat/completions", json=body,
                         headers=hdr)
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()


async def _ask(b64, mime, log=print, primary_only=False, note=""):
    """先用主模型 失败了换备用"""
    specs = [x for x in ((V,) if primary_only else (V, V.get("fallback"))) if _spec_ok(x)]
    last = None
    for i, spec in enumerate(specs):
        try:
            out = await _ask_one(spec, b64, mime, note)
            if out:
                if i:
                    log(f"[识图] 主模型不可用，已用备用 {spec.get('model')}")
                return out
        except Exception as e:
            last = e
            log(f"[识图] {spec.get('provider')}/{spec.get('model')} 失败:", repr(e)[:120])
    if last:
        raise last
    return ""


def _trim(cap):
    """按句子收尾 不在半句话中间砍断"""
    cap = re.sub(r"\s+", " ", cap or "").strip()
    cap = re.sub(r"^(这是|图中是|图片中?是?|画面中?是?)一(张|幅)[^，,。]*?[，,。]", "", cap).strip()
    if len(cap) <= MAX_CHARS:
        return cap.rstrip("。")
    cut = cap[:MAX_CHARS]
    i = max(cut.rfind(x) for x in "。！？；，,")
    return (cut[:i] if i > MAX_CHARS * 0.5 else cut).rstrip("。，,") + "…"


def free_primary():
    """主识图是本地模型 = 不花钱 可以每张群图都看"""
    return V.get("provider") == "ollama"


async def describe(key, url, log=print, primary_only=False):
    if not enabled() or not url:
        return None
    key = CACHE_VER + key
    cache = _load_cache()
    if key in cache:
        return cache[key]
    now = time.time()
    _recent[:] = [t for t in _recent if now - t < 3600]
    if len(_recent) >= PER_HOUR:
        return None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            data = r.content
        if len(data) > MAX_BYTES:
            return None
        mime = _mime(data)
        if not mime:
            return None
        _recent.append(now)
        import asyncio
        data, mime, note = await asyncio.to_thread(imgprep.prep, data, mime, 1280)  # 动图拼帧 / 大图缩小
        cap = await _ask(base64.b64encode(data).decode(), mime, log, primary_only, note)
        cap = _trim(cap)
        if cap and note and not cap.startswith("动图"):
            cap = "动图：" + cap
        if cap:
            cache[key] = cap
            _save_cache(cache)
            log(f"[识图] {cap}")
        return cap or None
    except Exception as e:
        log("[识图] 失败:", repr(e)[:150])
        return None


async def caption_text(text, segs, log=print, primary_only=False):
    """把文本里的 [图片]/[表情包] 依次换成带描述的版本"""
    imgs = image_segments(segs)
    if not imgs:
        return text
    for key, url, summary in imgs[:3]:
        cap = await describe(key, url, log, primary_only)
        if not cap:
            continue
        for tag in ("[图片]", "[表情包]"):
            if tag in text:
                label = "表情包" if (tag == "[表情包]" or summary) else "图片"
                text = text.replace(tag, f"[{label}：{cap}]", 1)
                break
    return text


async def caption_entry(h, log=print, primary_only=False):
    """给历史里的一条消息补看图（它存了 imgs 但还没看过）"""
    if h.get("seen") or not h.get("imgs"):
        return False
    segs = [{"type": "image", "data": {"file": k, "url": u, "summary": sm}}
            for k, u, sm in h["imgs"]]
    new = await caption_text(h["text"], segs, log, primary_only)
    if new != h["text"]:
        h["text"] = new
        h["seen"] = True
        return True
    return False
