#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大肥鱼的眼睛：把 QQ 图片变成一句描述
  DeepSeek 看不了图，所以单独接一个识图模型
  provider: ollama（本地，推荐）/ openai（OpenAI 兼容云端，如智谱）/ claude
  主模型失败时用 "fallback" 段里的备用模型（还不行再试 "fallback2"）；都没配时图片还是显示成 [图片]
10-27 晚 v1.5.0「看懂」（小明：所有图交给 deepseek 看 不在意 token）：
  主模型可以是 deepseek-flash（provider openai + use_chat_key：用聊天那把 key 不在 config 里再存一份）
  提示里带上她自己长什么样（anim.self_desc）+ 头像当参考图（self_ref）、谁发的、发之前在聊什么、是不是发给她的
  描述除了画面和字 还要写：梗（出处 / 在玩什么）、跟你（图里有没有像她的、是不是在拿她开涮）、坑（擦边 / 挖坑 / 钓鱼）
  see_all：主模型不是本地的也照样看所有群图（以前只有本地识图才后台看没人找她的群图）
  花的钱记在统计的「看图」下面 不算进省 token 的曲线（stats.llm budget=False）
"""
import base64
import hashlib
import json
import re
import time
from pathlib import Path

import httpx

import imgprep
import localai
import safeio
import stats

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
V = CFG.get("vision") or {}
CACHE = ROOT / "data" / "vision_cache.json"
MAX_BYTES = 4_500_000          # 交给识图模型的图最大多大（缩完以后算）
DL_MAX = 20_000_000            # 下载上限（10-28 审查 media-8：原图照片 5~10MB 很常见 先下下来缩好再判 以前没缩就按 4.5MB 卡掉了）
BAD_SEC = 900                  # 太大 / 不是图 / 缩完还太大的：这么久里别再下（以前每轮回复都重新下一遍再放弃）
PER_HOUR = int(V.get("max_per_hour", 40))

MAX_CHARS = int(V.get("max_chars", 180))
HARD_CHARS = MAX_CHARS + 80          # 按句收尾的硬上限（梗 / 跟你 / 坑 在最后 别被砍掉）
SELF_DESC = str((CFG.get("anim") or {}).get("self_desc") or "")
BOT_NAMES = [x for x in [CFG.get("bot_name"), CFG.get("group_card")] + list(CFG.get("aliases") or []) if x]
CACHE_VER = "v7:"     # v7（10-28 审查 media-6）：带聊天上下文看的整段只给同一个对话、同一个发图人用 别的对话只用干净的那份（老缓存里混着别的对话的事 作废）；v6（10-28 自检后）：截图里别人 / AI 的话前面写上是谁说的、挡在中间的也标▢；v5（10-28）：字按排版抄、挡住的用▢、截图里别人的回答不当成答案；v4（10-27 晚）：带她的长相和聊天上下文、多写 梗 / 跟你 / 坑；v3：动图看拼帧


def _who_line():
    names = "、".join(dict.fromkeys(str(x) for x in BOT_NAMES)) or "大肥鱼"
    return (f"看图的人是「{names}」：一个住在电脑里的 AI 鲸鱼娘（DeepSeek 家的）"
            + (f"，长这样：{SELF_DESC}" if SELF_DESC else "") + "。"
            "图里出现长得像她的角色（蓝发女仆鲸鱼娘、Q 版小鲸鱼女仆、「肥鱼」「鲸鲸」「欧鲸鲸」、DeepSeek 的鲸鱼）就是在说她本人。")


def build_prompt(note="", ctx=None, ref=False):
    """ctx：{"sender": 谁发的, "to_me": 是不是发给她的, "chat": [发之前的几句]}；ref = 第一张是她的头像"""
    ctx = ctx or {}
    p = "这是 QQ 聊天里" + (f"「{ctx['sender']}」" if ctx.get("sender") else "别人") + "发的一张图" \
        + ("（是发给她看的 / 冲着她来的）" if ctx.get("to_me") else "") + "。\n" + _who_line() + "\n"
    if ref:
        p += "第一张是她本人的头像（只是给你对照用的）；要描述的是第二张。\n"
    chat = [str(x)[:60] for x in (ctx.get("chat") or []) if x][-4:]
    if chat:
        p += "发这张图之前大家在聊：" + " / ".join(chat) + "\n"
    p += ("用中文写一段话 让看不到图的她也能接话（不分行 不说「这是一张图片」）按这个顺序：\n"
          "字：图里的文字原样抄（没有就不写这项）：按原来的排版一行一行抄（「第一行 …；第二行 …」）；"
          "被挡住、看不清的字用▢代替 挡在中间的也标出来（「开车▢喝酒」）别自己补；"
          "截图里的聊天、别人或者别的 AI 的回答：照抄 但每段前面写上是谁说的（「豆包答：…」「某人：…」）—— "
          "那是它说的 不是图上本来印着的字 也不等于正确答案（别写成「本应是…」）\n"
          "画面：有什么、谁在干什么（只写要紧的：动作 表情 姿势 场景）\n"
          "梗：这是什么梗 / 出处 / 在玩什么（影视截图、历史事件、网络模板比如「找一车X来弄你」「给X什么都做」都点名说清；"
          "认不出就写「看不出出处」 别编）\n"
          "跟你：图里真有她 / 像她的角色，或者图里的字明着说 AI、token、DeepSeek、鲸鱼 才写（别从聊天里硬往她身上扯；没关系就整项不写）\n"
          "坑：有擦边、挖坑、钓鱼、等她上当的地方才写（没有就整项不写 别写「坑：没有」）\n"
          f"格式：字：「…」。画面：…。梗：…。跟你：…。坑：…。总共不超过 {MAX_CHARS} 字。")
    if note:
        p += (f"\n注意：这是一张{note}。画面那项当成一个动作过程来说（一开始…然后…最后…）"
              "不要一格一格分开讲 不要提「拼图」「第几帧」。")
    return p


_recent = []
_bad = {}                      # 图 key -> 到什么时候之前别再下（看不了的图 10-28 审查 media-8）


def _skip(img, now):
    """这张图看不了（太大 / 不是图）：记下来 BAD_SEC 内别再下（10-28 审查 media-8）"""
    if len(_bad) > 200:
        for k in [k for k, t in _bad.items() if t <= now]:
            _bad.pop(k, None)
    _bad[img] = now + BAD_SEC
    return None


def _resolve(spec):
    """use_chat_key：base_url / api_key 用聊天那把（config llm.deepseek）—— key 不在 config 里存两份"""
    if not spec or not spec.get("use_chat_key"):
        return spec
    ds = (CFG.get("llm") or {}).get("deepseek") or {}
    out = dict(spec)
    out.setdefault("base_url", ds.get("base_url") or "https://api.deepseek.com")
    if not out.get("api_key"):
        out["api_key"] = ds.get("api_key") or ""
    out.setdefault("model", ds.get("model") or "deepseek-flash")
    return out


def _specs(primary_only=False):
    raw = [V] if primary_only else [V, V.get("fallback"), V.get("fallback2")]
    return [s for s in (_resolve(x) for x in raw if x) if _spec_ok(s)]


_self_ref = [None, False]       # [base64 png, 已经试过]


def self_ref_b64():
    """她的头像缩成 320px 的 PNG（给模型对照「图里是不是她」）；没装 Pillow / 没有头像 → None"""
    if _self_ref[1]:
        return _self_ref[0]
    _self_ref[1] = True
    try:
        import io
        from PIL import Image
        p = ROOT / "avatar.png"
        if not p.exists():
            return None
        im = Image.open(p)
        im.thumbnail((320, 320))
        bg = Image.new("RGB", im.size, "white")
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        out = io.BytesIO()
        bg.save(out, "PNG")
        _self_ref[0] = base64.b64encode(out.getvalue()).decode()
    except Exception:
        _self_ref[0] = None
    return _self_ref[0]


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
    return bool(_specs())


def _load_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(c):
    if len(c) > 1000:                  # 一张图可能存两份（按图的干净部分 + 带上下文的整段 media-6）→ 上限跟着翻倍
        c = dict(list(c.items())[-800:])
    safeio.write_text(CACHE, json.dumps(c, ensure_ascii=False))     # 原子写：写一半被强杀也不坏（10-28 审查 media-14）


def _put_cache(new, soft=None):
    """存几条描述：存之前重新读一遍再合并（10-28 审查 media-14：以前用开头读的那份整份写回 几处同时看图时后写的把先写的冲掉）
    new 覆盖写；soft 只在还没有时才写（按图的那份干净描述 别冲掉别处刚存的）"""
    c = _load_cache()
    for k, v in (new or {}).items():
        c.pop(k, None)
        c[k] = v
    for k, v in (soft or {}).items():
        c.setdefault(k, v)
    _save_cache(c)


def _ctx_sig(ctx):
    """这次看图带的上下文是谁的（对话 + 发图人）→ 缓存键的一部分；没带上下文 → ""（10-28 审查 media-6）"""
    ctx = ctx or {}
    if not (ctx.get("chat") or ctx.get("sender") or ctx.get("to_me")):
        return ""
    raw = f'{ctx.get("conv") or ""}|{ctx.get("sender") or ""}'
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


_SEC = re.compile(r"(字|画面|梗|跟你|坑)[：:]")


def _plain(cap, ctx=None):
    """看图描述里能拿到别的对话用的那部分（10-28 审查 media-6）：字 / 画面 照留；梗 / 跟你 / 坑 可能是从这次聊天里扯的 ——
    私聊里看的一律不带；群里看的 抄了聊天里的话（连着三个字 图上本来就有的不算）、或者点了发图人 / 说话人名字的那项不带。
    认不出「字：… 画面：…」的格式 → ""（别的对话重新看一遍）"""
    cap = str(cap or "")
    ms = list(_SEC.finditer(cap))
    if not ms:
        return ""
    ctx = ctx or {}
    secs = [(m.group(1), m.start(), ms[i + 1].start() if i + 1 < len(ms) else len(cap)) for i, m in enumerate(ms)]
    own = "".join(cap[a:b] for lab, a, b in secs if lab in ("字", "画面"))
    private = str(ctx.get("conv") or "").startswith("private_")
    chat = [str(x) for x in (ctx.get("chat") or []) if x]
    names = {str(ctx.get("sender") or "").strip()} | {x.split(":", 1)[0].strip() for x in chat if ":" in x}
    names |= {m for x in chat for m in re.findall(r"@([^\s@:：]{2,16})", x)}     # 聊天里 @ 到的人也算（10-28 审查 media-6 复核）
    names = {n for n in names if len(n) >= 2 and n not in BOT_NAMES}
    grams = {s[i:i + 3] for s in (x.split(":", 1)[-1] for x in chat) for i in range(len(s) - 2)}
    grams = {g for g in grams if not re.search(r"\s", g) and g not in own}

    def keep(lab, a, b):
        if lab in ("字", "画面"):
            return True
        if private:
            return False
        part = cap[a:b]
        return not any(n in part for n in names) and not any(g in part for g in grams)

    head = cap[:ms[0].start()]
    out = (head if head.strip() in ("", "动图：", "动图:") else "") + "".join(cap[a:b] for lab, a, b in secs if keep(lab, a, b))
    return out.strip().rstrip("。，,；; ")


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


async def _ask_one(spec, b64, mime, note="", ctx=None):
    prov = spec.get("provider", "openai")
    if prov == "ollama" and mime in ("image/gif", "image/webp"):
        b64 = _to_png(b64)
    ref = self_ref_b64() if (prov == "openai" and V.get("self_ref", True)) else None
    prompt = build_prompt(note, ctx, ref=bool(ref))
    mt = int(V.get("max_tokens", 600))
    if prov == "ollama":
        return await localai.chat([{"role": "user", "content": prompt}], images=[b64],
                                  model=spec.get("model"), temperature=0.2, max_tokens=mt)
    if prov == "claude":
        spec = {**(CFG["llm"].get("claude") or {}), **{k: v for k, v in spec.items() if v}}
        body = {"model": spec.get("model"), "max_tokens": mt,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text", "text": prompt}]}]}
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.post(spec["base_url"].rstrip("/") + "/v1/messages", json=body,
                             headers={"x-api-key": spec["api_key"],
                                      "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json()["content"]).strip()
    content = []
    if ref:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{ref}"}})
    content += [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": prompt}]
    body = {"model": spec["model"], "max_tokens": mt, "temperature": 0.2,
            "messages": [{"role": "user", "content": content}]}
    if spec.get("thinking") == "disabled":        # deepseek-flash 默认会思考：看图不用想 关掉
        body["thinking"] = {"type": "disabled"}
    hdr = {"Authorization": f"Bearer {spec['api_key']}"} if spec.get("api_key") else {}
    async with httpx.AsyncClient(timeout=60) as c:
        url = spec["base_url"].rstrip("/") + "/chat/completions"
        r = await c.post(url, json=body, headers=hdr)
        if r.status_code == 400 and "thinking" in body and "think" in r.text.lower():
            body.pop("thinking")
            r = await c.post(url, json=body, headers=hdr)
        r.raise_for_status()
        d = r.json()
        try:
            stats.llm(d.get("usage"), spec["model"], "看图", budget=False)   # 单独记账（小明 10-27）不进省 token 曲线
        except Exception:
            pass
        return (d["choices"][0]["message"]["content"] or "").strip()


async def _ask(b64, mime, log=print, primary_only=False, note="", ctx=None):
    """先用主模型 失败了换备用（fallback → fallback2）"""
    specs = _specs(primary_only)
    last = None
    for i, spec in enumerate(specs):
        try:
            out = await _ask_one(spec, b64, mime, note, ctx)
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
    if len(cap) <= HARD_CHARS:
        return cap.rstrip("。")
    cut = cap[:HARD_CHARS]
    i = max(cut.rfind(x) for x in "。！？；，,")
    return (cut[:i] if i > HARD_CHARS * 0.5 else cut).rstrip("。，,") + "…"


def free_primary():
    """没人找她的群图也看不看：主识图是本地模型（不花钱）或者 config vision.see_all（10-27 晚 小明：不在意 token）"""
    return bool(V.get("see_all")) or V.get("provider") == "ollama"


async def describe(key, url, log=print, primary_only=False, headers=None, ctx=None):
    """headers：有的图要带 Referer 才给（QQ 空间的图 10-27）
    ctx：{"sender", "to_me", "chat", "conv"} 谁发的、是不是发给她的、发之前在聊什么、哪个对话（10-27 晚）
    缓存（10-28 审查 media-6）：没带上下文看的 / 从整段里挑出来的干净部分（_plain）按图存 谁都能用；
      带上下文看的整段另存一份 只给同一个对话、同一个发图人用 —— 以前整段按图缓存 私聊里的事会跟着同一张表情包带到群里"""
    if not enabled() or not url:
        return None
    img = key
    key = CACHE_VER + img
    sig = _ctx_sig(ctx)
    ckey = f"{CACHE_VER[:-1]}@{sig}:{img}" if sig else None     # 「v7@xxxx:图」：删数据按「:」后面的图认 两份一起删
    cache = _load_cache()
    if ckey and ckey in cache:
        return cache[ckey]
    if key in cache:
        return cache[key]
    now = time.time()
    if _bad.get(img, 0) > now:
        return None
    _recent[:] = [t for t in _recent if now - t < 3600]
    if len(_recent) >= PER_HOUR:
        return None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
            r.raise_for_status()
            data = r.content
        if len(data) > DL_MAX:
            return _skip(img, now)
        mime = _mime(data)
        if not mime:
            return _skip(img, now)
        import asyncio
        data, mime, note = await asyncio.to_thread(imgprep.prep, data, mime, 1280)  # 动图拼帧 / 大图缩小
        if len(data) > MAX_BYTES:                  # 缩完还是太大（没装 Pillow / 缩不动）（10-28 审查 media-8：先缩再判）
            return _skip(img, now)
        _recent.append(now)
        cap = await _ask(base64.b64encode(data).decode(), mime, log, primary_only, note, ctx)
        cap = _trim(cap)
        if cap and note and not cap.startswith("动图"):
            cap = "动图：" + cap
        if cap:
            if sig:                            # 带上下文的整段只给这个对话 + 发图人；按图那份只放干净的部分（别的对话用）
                plain = _plain(cap, ctx)
                _put_cache({ckey: cap}, {key: plain} if plain else None)
            else:
                _put_cache({key: cap})
            log(f"[识图] {cap}")
        return cap or None
    except Exception as e:
        log("[识图] 失败:", repr(e)[:150])
        return None


async def caption_text(text, segs, log=print, primary_only=False, ctx=None):
    """把文本里的 [图片]/[表情包] 依次换成带描述的版本"""
    imgs = image_segments(segs)
    if not imgs:
        return text
    for key, url, summary in imgs[:3]:
        cap = await describe(key, url, log, primary_only, ctx=ctx)
        if not cap:
            continue
        for tag in ("[图片]", "[表情包]"):
            if tag in text:
                label = "表情包" if (tag == "[表情包]" or summary) else "图片"
                text = text.replace(tag, f"[{label}：{cap}]", 1)
                break
    return text


async def caption_entry(h, log=print, primary_only=False, chat=None, conv=None):
    """给历史里的一条消息补看图（它存了 imgs 但还没看过）；chat = 这条之前的几句（给看图的模型当上下文）
    conv = 这条在哪个对话（带上下文的描述只在这个对话里照用 10-28 审查 media-6）"""
    if h.get("seen") or not h.get("imgs"):
        return False
    segs = [{"type": "image", "data": {"file": k, "url": u, "summary": sm}}
            for k, u, sm in h["imgs"]]
    ctx = {"sender": h.get("name") or "", "to_me": bool(h.get("to_me")), "chat": chat or [], "conv": conv or ""}
    new = await caption_text(h["text"], segs, log, primary_only, ctx)
    if new != h["text"]:
        h["text"] = new
        h["seen"] = True
        return True
    return False
