#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
动画：她能做 5 秒的小动画发到 QQ（火山方舟 Seedance 视频生成）

谁能让她做（config → anim）：
  老板随时（每天最多 owner_daily 条 防失控）；熟人 = 好感 ≥ friend_favor 的人 每人每天 friend_daily 条；
  除老板以外所有人每天合计最多 others_daily 条；别人发来的图只有老板能让她「动起来」（user_image_owner_only）；
  她自己每天最多主动做 proactive 条（发到她正聊着的群）
她怎么做：回复里写 [[动画:画面描述]]
  [[动画:自己|动作]]      拿她的头像当第一帧（大肥鱼本人动起来）
  [[动画:表情包名|动作]]  拿那张表情包当第一帧
  [[动画:这张图|动作]]    拿对方刚发的图当第一帧（只有老板）
流程：提交任务 → 每 poll_sec 秒看一次 → 做好了下载 mp4 到 data/anim/ → 发 QQ 视频；做不出来就说一声
花费：按返回的 token 数 × price_cny_per_m 记账，花到 budget_cny 就不再做（data/anim_state.json）
key 只放在 config.json 的 anim.api_key，日志里不出现
"""
import asyncio
import base64
import io
import re
import time
from pathlib import Path

import httpx

import clock
import safeio

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "anim_state.json"
OUT = ROOT / "data" / "anim"
AVATAR = ROOT / "avatar.png"

DEFAULTS = {
    "enabled": True,
    "api_key": "",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "model": "doubao-seedance-2-0-mini-260615",
    "resolution": "480p",
    "duration": 5,
    "ratio": "1:1",              # 纯文字生成时的画面比例；拿图当第一帧时跟着图走
    "watermark": True,           # 右下角「AI生成」水印（AI 生成的视频按规定要标出来）
    "generate_audio": True,      # Seedance 2.0 mini 默认就带声音（不多算 token）
    "self_mode": "reference",    # 主角是她自己/表情包时：reference = 拿图当参考（长得像 场景随便换）；first_frame = 从这张图开始动
    "self_desc": "蓝色长卷发、戴白色女仆头饰、穿深蓝女仆裙和白围裙、身后有一条鲸鱼尾巴的Q版二次元女孩",
    "price_cny_per_m": 23,       # 每百万 token 多少元（Seedance 2.0 mini 不含视频输入）
    "budget_cny": 190,           # 一共最多花这么多 花到就停
    "owner_daily": 20,
    "friend_favor": 40,
    "friend_daily": 1,
    "others_daily": 5,
    "user_image_owner_only": True,
    "proactive": 1,              # 她每天最多主动做几条（0 = 只在有人要时做）
    "poll_sec": 8,
    "timeout_min": 12,
    "keep": 60,                  # data/anim 里最多留几个 mp4
}
A = dict(DEFAULTS)

# 有人想让她做动画/视频（只是聊到别人的视频不算）
ASK = re.compile(r"(做|生成|制作|创作|整|弄|搞)(?![了过的得])(个|一个|段|一段|条|一条|点)?[^。！？!?\n]{0,8}?(动画|视频|短片)"
                 r"|(来|画|拍|剪)(个|一个|段|一段|条|一条)[^。！？!?\n]{0,8}?(动画|视频|短片)"
                 r"|动起来|动一动")
MARK = re.compile(r"\[\[动画[:：]\s*([^\[\]\n]{2,300}?)\s*\]\]")
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

SELF_WORDS = {"自己", "我", "我自己", "本鱼", "大肥鱼", "头像"} | _cfg_names()
NAME_WORDS = re.compile("|".join(["(蓝色)?大肥鱼", "本鱼", "肥鱼"] + [re.escape(x) for x in sorted(_cfg_names(), key=len, reverse=True)]))
# ↑ 做视频的模型不认识她的名字 → 换成「她」
USER_WORDS = {"这张图", "这张", "这图", "图", "那张图", "他的图", "对方的图", "刚才的图"}
_TOKENS = {"480p": 864 * 480, "720p": 1280 * 720, "1080p": 1920 * 1080}   # 每帧像素（估花费用）

_st = None
_cli = None


def configure(cfg):
    A.clear()
    A.update(DEFAULTS)
    A.update({k: v for k, v in (cfg or {}).items() if not str(k).startswith("_")})


def _day():
    return clock.strf("%Y-%m-%d")             # 北京时间的一天（10-27）


def _fill(s):
    for k, v in (("spent", 0.0), ("count", 0), ("days", {}), ("tasks", {}), ("history", []),
                 ("pause_until", 0), ("pause_why", ""), ("last_error", "")):
        s.setdefault(k, v)
    return s


def _load():
    global _st
    if _st is None:
        try:
            st = safeio.read_json(STATE, None, print, busy_raise=True)   # 读坏了先另存 .bad
        except safeio.Busy:        # 一直打不开：花了多少、今天做了几条都不知道 → 这次先不做、不缓存、不存
            tmp = _fill(safeio.Unsaved())          # （10-28 Gemini 复查：以前当成全新的 花费和名额从 0 算、没等完的任务清空）
            tmp["pause_until"], tmp["pause_why"] = time.time() + 600, "记账文件一时打不开"
            return tmp
        _st = _fill(st if isinstance(st, dict) else {})
    return _st


def _save():
    if _st is None:                        # 还没读到过盘上那份：不写（10-28 Gemini 复查）
        return
    s = _st
    keep = sorted(s["days"])[-40:]
    s["days"] = {k: s["days"][k] for k in keep}
    s["history"] = s["history"][-40:]
    safeio.write_json(STATE, s)            # 原子写 + fsync（以前断电会变成空文件 → 花费从 0 算）


def _today():
    d = _load()["days"].setdefault(_day(), {})
    for k, v in (("owner", 0), ("others", 0), ("proactive", 0), ("by", {}), ("cny", 0.0), ("made", 0), ("failed", 0)):
        d.setdefault(k, v)
    return d


def est_cny():
    px = _TOKENS.get(str(A["resolution"]).lower(), _TOKENS["480p"])
    tokens = px * 24 * float(A["duration"]) / 1024
    return round(tokens * float(A["price_cny_per_m"]) / 1e6, 3)


def _reserved():
    return sum(float(t.get("est", 0)) for t in _load()["tasks"].values())


def _pause(sec, why):
    s = _load()
    s["pause_until"], s["pause_why"] = time.time() + sec, why
    _save()


def ready():
    """能不能做（不看是谁要）：返回 (行不行, 不行的原因)"""
    if not A.get("enabled"):
        return False, "做动画的功能关着"
    if not A.get("api_key"):
        return False, "做动画的工具还没装好"
    s = _load()
    if time.time() < s.get("pause_until", 0):
        return False, s.get("pause_why") or "做动画的工具出了点问题"
    if s["spent"] + _reserved() + est_cny() > float(A["budget_cny"]):
        return False, "做动画的钱花完了"
    return True, ""


def busy(conv):
    return any(t.get("conv") == conv for t in _load()["tasks"].values())


def asked(texts):
    return any(ASK.search(t or "") for t in texts or [])


def plan(conv, uid, is_owner, favor, texts):
    """这一轮：off（没人要）/ ok（可以做）/ deny（有人要 但做不了）。返回 (mode, 原因)"""
    if not asked(texts):
        return "off", ""
    ok, why = ready()
    if not ok:
        return "deny", why
    if busy(conv):
        return "deny", "上一个动画还在做"
    d = _today()
    if is_owner:
        if d["owner"] >= int(A["owner_daily"]):
            return "deny", "今天已经做了太多了 明天再做"
        return "ok", ""
    if (favor or 0) < float(A["friend_favor"]):
        return "deny", "只给熟人做 跟他还不够熟"
    if d["by"].get(str(uid), 0) >= int(A["friend_daily"]):
        return "deny", "今天已经给他做过了 明天再来"
    if d["others"] >= int(A["others_daily"]):
        return "deny", "今天做的动画已经够多了 明天再做"
    return "ok", ""


def proactive_ok():
    ok, _ = ready()
    return ok and int(A.get("proactive") or 0) > 0 and _today()["proactive"] < int(A["proactive"])


def block(mode, why="", can_img=False, proactive=False):
    """给她看的「做动画」说明（只在有人要、或她自己想做的时候出现）"""
    sound = "带声音" if A.get("generate_audio") else "没有声音"
    rules = ("画面描述一两句（80 字内）写清楚：主角、在哪、做什么动作、镜头怎么动"
             "（比如：她扑到一个超大的电脑键盘上来回打滚 键帽被压得一闪一闪 镜头从上往下慢慢拉近）。"
             "描述里用「她」指你自己（做视频的模型不认识「大肥鱼」这个名字）。"
             "别画真人和名人、别的作品里的角色，别要画面里出现字。\n")
    if proactive:
        return ("\n# 做动画\n你可以做一个 5 秒的小动画（" + sound + "）发到群里 系统会真的去做 一两分钟后发出来："
                "写一句配的话 再加一个 [[动画:画面描述]]（主角是你自己就写 [[动画:自己|画面]]）。" + rules
                + "没什么想做的就只写 [[不说]]。\n")
    if mode == "deny":
        return ("\n# 做动画\n对方要是想让你做动画/视频：这一轮做不了（" + (why or "现在做不了") + "）。"
                "直说做不了和原因，别答应、别说待会儿做，也别写动画标记。他不是这个意思就当没看见这段。\n")
    if mode != "ok":
        return ""
    s = ("\n# 做动画（这一轮可以做）\n对方要是想让你做个动画/视频：在回复里加一个 [[动画:画面描述]]，"
         f"系统会真的去做（5 秒 {sound}），一两分钟后自动发出来 —— 所以这次可以说「在做了 等我一两分钟」；"
         "不写这个标记就别答应。只做一个。\n" + rules
         + "主角是你自己：写 [[动画:自己|画面]]（会拿你的头像当参考 保证长得像你 场景随便换）；"
         "想让某张表情包里的角色动起来：[[动画:表情包名字|画面]]。\n")
    if can_img:
        s += "对方刚发了图 想让那张图动起来：写 [[动画:这张图|动作]]。\n"
    s += "他要是不是想让你做动画（比如只是在聊别人的视频）就别写这个标记。\n"
    return s


def extract(text):
    """把回复里的动画标记拿出来：返回 (去掉标记的文字, 第一个标记的内容或 None)"""
    specs = [m.group(1).strip() for m in MARK.finditer(text or "")]
    return re.sub(r"[ \t]{2,}", " ", MARK.sub("", text or "")).strip(), (specs[0] if specs else None)


def strip(text):
    return MARK.sub("", text or "").strip()


def parse(spec):
    """'自己|动作' / '表情包名|动作' / '这张图|动作' / '描述' → (第一帧来源, 名字, 画面描述)"""
    spec = (spec or "").strip()
    parts = re.split(r"\s*[|｜]\s*", spec, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        a, b = parts[0].strip("「」\"' "), parts[1]
        if a in SELF_WORDS:
            return "self", "", b[:300]
        if a in USER_WORDS:
            return "user", "", b[:300]
        return "sticker", a, b[:300]
    if NAME_WORDS.search(spec):
        return "self", "", spec[:300]
    return "text", "", spec[:300]


def video_prompt(kind, desc, mode):
    """给视频模型的描述：主角是她时 写清楚长什么样、拿参考图/第一帧怎么用"""
    p = NAME_WORDS.sub("她", desc or "").strip()
    if mode and kind in ("self", "sticker"):
        who = A.get("self_desc") if kind == "self" else "图片1里的角色"
        if mode == "reference":
            return f"参考图片1里的角色（{who}），保持她的长相、发型和衣服一致，生成这个画面：{p}"
        return f"第一帧里的这个角色（{who}）就是主角：{p}"
    return p


# ───────────── 第一帧 ─────────────
def frame_from_bytes(data):
    """任意图（含透明 PNG、动图第一帧）→ 能交给 Seedance 的 data URI：短边 ≥ 480、长边 ≤ 1920、宽高比 0.42~2.4"""
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    try:
        im.seek(0)
    except Exception:
        pass
    if im.mode in ("RGBA", "LA", "P", "PA"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    w, h = im.size
    if w / h > 2.4 or h / w > 2.4:              # 太扁 / 太长：补白边
        W, H = (w, int(w / 2.4) + 1) if w > h else (int(h / 2.4) + 1, h)
        bg = Image.new("RGB", (W, H), (255, 255, 255))
        bg.paste(im, ((W - w) // 2, (H - h) // 2))
        im, (w, h) = bg, (W, H)
    scale = 1.0
    if min(w, h) < 480:
        scale = 480 / min(w, h)
    if max(w, h) * scale > 1920:
        scale = 1920 / max(w, h)
    if abs(scale - 1) > 0.01:
        im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def frame_from_file(path):
    p = Path(path)
    return frame_from_bytes(p.read_bytes()) if p.is_file() else None


# ───────────── 火山方舟 ─────────────
def _client():
    global _cli
    if _cli is None:
        _cli = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=True)
    return _cli


def _hdr():
    return {"Authorization": "Bearer " + str(A["api_key"]), "Content-Type": "application/json"}


def _base():
    return str(A.get("base_url") or DEFAULTS["base_url"]).rstrip("/")


def _clean(msg):
    msg = str(msg or "")
    if A.get("api_key"):
        msg = msg.replace(str(A["api_key"]), "<key>")
    return re.sub(r"\s+", " ", msg)[:160]


def _err(r):
    """火山返回的错误 → (code, message)"""
    try:
        e = r.json().get("error") or {}
        return str(e.get("code") or r.status_code), _clean(e.get("message") or r.text)
    except Exception:
        return str(r.status_code), _clean(r.text)


def _explain(code, msg, status=0):
    """错误 → 给人看的原因；钥匙/模型/余额的问题顺便暂停一阵（别一直白试）"""
    c = (code or "").lower()
    if status == 401 or "authentication" in c or "invalidapikey" in c:
        _pause(6 * 3600, "做动画的 key 不对")
        return "火山方舟的 key 不对或者失效了（得让老板看看）"
    if "modelnotopen" in c or "notfound" in c or "invalidendpoint" in c or "accessdenied" in c:
        _pause(6 * 3600, "做动画的模型还没开通")
        return f"火山方舟里 {A['model']} 还没开通（老板要去控制台「开通管理」点开通）"
    if "overdue" in c or "balance" in c or "quota" in c or status == 402:
        _pause(12 * 3600, "火山账户没钱了")
        return "火山账户的钱不够了"
    if "setlimitexceeded" in c:
        _pause(6 * 3600, "到了火山的用量上限")
        return "到了火山那边设的用量上限（控制台里的安心体验/限额）"
    if "sensitive" in c or "risk" in c:
        return "这个画面火山那边的内容审核没过"
    if status == 429 or "ratelimit" in c or "overload" in c:
        return "火山那边太忙了"
    return f"火山那边说：{msg[:60]}"


def _book(job, tid, est):
    s = _load()
    d = _today()
    if job.get("proactive"):
        d["proactive"] += 1
    elif job.get("is_owner"):
        d["owner"] += 1
    else:
        d["others"] += 1
        d["by"][str(job.get("uid"))] = d["by"].get(str(job.get("uid")), 0) + 1
    s["tasks"][tid] = {**{k: job.get(k) for k in ("scene", "target", "conv", "uid", "name", "is_owner",
                                                    "proactive", "caption", "prompt", "kind")},
                       "t": time.time(), "est": est, "day": _day()}
    _save()


def _unbook(t):
    """没做出来：把今天的次数还回去"""
    d = _load()["days"].get(t.get("day") or _day())
    if not d:
        return
    if t.get("proactive"):
        d["proactive"] = max(0, d.get("proactive", 0) - 1)
    elif t.get("is_owner"):
        d["owner"] = max(0, d.get("owner", 0) - 1)
    else:
        d["others"] = max(0, d.get("others", 0) - 1)
        u = str(t.get("uid"))
        if (d.get("by") or {}).get(u):
            d["by"][u] -= 1


async def submit(job, desc, frame=None, log=print, kind="text"):
    """提交任务（先占上次数和预算）：返回 (任务 id, 失败原因)
    kind = self/sticker（主角是她/表情包里的角色 默认拿图当参考）、user（对方的图 从这张图开始动）、text"""
    ok, why = ready()
    if not ok:
        return None, why
    mode = None
    if frame:
        mode = "reference" if kind in ("self", "sticker") and str(A.get("self_mode")) == "reference" else "first_frame"
    url = _base() + "/contents/generations/tasks"
    flags_tried = False
    try:
        while True:
            prompt = video_prompt(kind, desc, mode)
            content = [{"type": "text", "text": prompt}]
            if frame:
                content.append({"type": "image_url", "image_url": {"url": frame},
                                "role": "reference_image" if mode == "reference" else "first_frame"})
            params = {"resolution": str(A["resolution"]), "duration": int(A["duration"]),
                      "ratio": "adaptive" if mode == "first_frame" else str(A["ratio"]),
                      "watermark": bool(A["watermark"]), "generate_audio": bool(A.get("generate_audio"))}
            if flags_tried:
                flags = (f" --resolution {params['resolution']} --duration {params['duration']}"
                         f" --watermark {str(params['watermark']).lower()}"
                         + ("" if mode == "first_frame" else f" --ratio {params['ratio']}"))
                body = {"model": A["model"], "content": [dict(content[0], text=prompt + flags)] + content[1:]}
            else:
                body = {"model": A["model"], "content": content, **params}
            r = await _client().post(url, headers=_hdr(), json=body)
            if r.status_code == 400:
                code, msg = _err(r)
                if mode == "reference" and re.search(r"reference|role|frame|mix|content", msg, re.I):
                    log(f"[动画] 参考图的做法被拒（{code} {msg[:80]}）改成从这张图开始动")
                    mode = "first_frame"
                    continue
                if not flags_tried and ("parameter" in code.lower() or "param" in msg.lower()):
                    log(f"[动画] 参数被拒（{code} {msg[:80]}）换成写在描述里的老写法再试一次")
                    flags_tried = True
                    continue
            break
        if r.status_code >= 300:
            code, msg = _err(r)
            why = _explain(code, msg, r.status_code)
            _load()["last_error"] = f"{clock.strf('%m-%d %H:%M')} 提交失败 {code}：{msg[:80]}"
            _save()
            log(f"[动画] 提交失败 {r.status_code} {code}：{msg[:120]}")
            return None, why
        tid = str(r.json().get("id") or "")
        if not tid:
            return None, "火山那边没给任务号"
    except Exception as e:
        log("[动画] 提交出错:", _clean(repr(e))[:120])
        return None, "连不上火山方舟"
    job["mode"] = mode or "text"
    _book({**job, "prompt": desc}, tid, est_cny())
    return tid, ""


async def wait(tid, log=print):
    """等任务做完：返回 (结果, 失败原因)
    10-28 审查 media-7：deadline 从开始等的时候算（重启后接着等的 以前按提交时间算 一进来就判超时、一次都不查）；
      等太久了交给 _give_up：再查最后一次 还在排队就取消 取消不了的记上 charge（done 按估价记账、次数不还）"""
    deadline = time.time() + float(A["timeout_min"]) * 60
    url = _base() + "/contents/generations/tasks/" + tid
    errs = 0
    while True:
        if time.time() > deadline:
            return await _give_up(tid, url, log)
        await asyncio.sleep(float(A["poll_sec"]))
        try:
            r = await _client().get(url, headers=_hdr())
            if r.status_code >= 300:
                code, msg = _err(r)
                if r.status_code in (401, 403, 404):
                    return None, _explain(code, msg, r.status_code)
                errs += 1
                if errs >= 8:
                    return None, f"查进度一直失败（{code}）"
                continue
            d = r.json()
        except Exception as e:
            errs += 1
            if errs >= 8:
                return None, "查进度一直连不上"
            log("[动画] 查进度出错:", _clean(repr(e))[:100])
            continue
        st = str(d.get("status") or "").lower()
        if st in ("succeeded", "success"):
            return d, ""
        if st in ("failed", "cancelled", "canceled", "expired"):
            e = d.get("error") or {}
            return None, _explain(str(e.get("code") or st), _clean(e.get("message") or st))


async def _give_up(tid, url, log=print):
    """等太久了（10-28 审查 media-7）：再查最后一次 —— 做好了照样要；还在排队就调接口取消
    （火山方舟 DELETE 这个任务：排队中的能取消 已经在做的不行）；取消不了 / 查不到状态的 在任务上记 charge：
    那边多半还会做完扣钱 done() 就按估价记账、今天的次数不还（以前一律当没做 预算 budget_cny 算不进去）"""
    st = ""
    try:
        r = await _client().get(url, headers=_hdr())
        if r.status_code < 300:
            d = r.json()
            st = str(d.get("status") or "").lower()
            if st in ("succeeded", "success"):
                return d, ""
            if st in ("failed", "cancelled", "canceled", "expired"):
                e = d.get("error") or {}
                return None, _explain(str(e.get("code") or st), _clean(e.get("message") or st))
    except Exception as e:
        log("[动画] 最后查一次进度出错:", _clean(repr(e))[:100])
    cancelled = False
    if st in ("queued", "pending"):
        try:
            r = await _client().delete(url, headers=_hdr())
            cancelled = r.status_code < 300
        except Exception as e:
            log("[动画] 取消任务出错:", _clean(repr(e))[:100])
    if cancelled:
        log(f"[动画] …{tid[-6:]} 等太久了 还在排队 已经取消")
    else:
        t = _load()["tasks"].get(tid)
        if t is not None:
            t["charge"] = True
            _save()
        log(f"[动画] …{tid[-6:]} 等太久了 火山那边{'还在做' if st == 'running' else '状态不明（' + (st or '没查到') + '）'}"
            " 取消不了 按估价记账")
    return None, "等太久了 没做完"


async def fetch(res, tid):
    """下载做好的视频 → 本地 mp4 路径"""
    url = ((res.get("content") or {}).get("video_url") or res.get("video_url") or "")
    if not url:
        raise RuntimeError("结果里没有视频地址")
    r = await _client().get(url, timeout=120)
    r.raise_for_status()
    if len(r.content) > 60 * 1024 * 1024:
        raise RuntimeError("视频太大")
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{clock.strf('%m%d_%H%M%S')}_{tid[-6:]}.mp4"
    p.write_bytes(r.content)
    vids = sorted(OUT.glob("*.mp4"), key=lambda x: x.stat().st_mtime)
    for old in vids[:-int(A["keep"])]:
        try:
            old.unlink()
        except Exception:
            pass
    return p


def done(tid, res=None, ok=True, why=""):
    """记账：做出来了按实际 token 算钱；没做出来把次数还回去"""
    s = _load()
    t = s["tasks"].pop(tid, None) or {}
    d = _today()
    usage = (res or {}).get("usage") or {}
    tokens = int(usage.get("completion_tokens") or usage.get("total_tokens") or 0)
    cny = round(tokens * float(A["price_cny_per_m"]) / 1e6, 4) if tokens else (float(t.get("est") or est_cny()) if ok else 0.0)
    if ok:
        s["spent"] = round(s["spent"] + cny, 4)
        s["count"] += 1
        d["cny"] = round(d["cny"] + cny, 4)
        d["made"] += 1
        s["last_error"] = ""
    else:
        d["failed"] += 1
        if t.get("charge"):            # 等超时了 火山那边还在做（取消不了）：按估价记账、次数不还（10-28 审查 media-7）
            cny = float(t.get("est") or est_cny())
            s["spent"] = round(s["spent"] + cny, 4)
            d["cny"] = round(d["cny"] + cny, 4)
        else:
            _unbook(t)
        s["last_error"] = f"{clock.strf('%m-%d %H:%M')} {why}"[:120]
    wiped = bool(t.get("wiped"))           # 做的时候他删了数据：记录里不留他（10-28 Gemini 第三轮复查）
    s["history"].append({"t": int(time.time()), "ok": ok, "conv": "" if wiped else t.get("conv"),
                         "uid": "" if wiped else t.get("uid"), "prompt": "" if wiped else (t.get("prompt") or "")[:80],
                         "tokens": tokens, "cny": cny if ok or t.get("charge") else 0, "why": why[:60]})
    _save()
    return cny, tokens


def running():
    return set(_load()["tasks"])


def forget_user(uid):
    """删数据：记账里去掉这个人（每天给他做了几条、做过的记录）"""
    import wipe
    n = wipe.anim_state(_load(), uid)
    if n:
        _save()
    return n


def pending():
    """重启前没等完的任务（重启后接着等 12 小时内的）"""
    s = _load()
    out = []
    for tid, t in list(s["tasks"].items()):
        if time.time() - float(t.get("t") or 0) > 12 * 3600:
            s["tasks"].pop(tid, None)
            continue
        out.append((tid, t))
    _save()
    return out


def fail_note(why):
    return f"动画没做出来…{why}" if why else "动画没做出来…"


def status():
    s = _load()
    d = (s["days"].get(_day()) or {})
    ok, why = ready()
    return {"enabled": bool(A.get("enabled")), "has_key": bool(A.get("api_key")), "ready": ok, "why": why,
            "model": A["model"], "resolution": A["resolution"], "duration": A["duration"],
            "spent": round(s["spent"], 3), "budget": float(A["budget_cny"]), "count": s["count"],
            "today": {k: d.get(k, 0) for k in ("owner", "others", "proactive", "made", "failed", "cny")},
            "running": len(s["tasks"]), "est": est_cny(), "last_error": s.get("last_error", ""),
            "pause_why": s.get("pause_why", "") if time.time() < s.get("pause_until", 0) else ""}


async def selftest(log=print):
    """启动时看一眼 key 能不能用（列任务 不花钱）"""
    if not A.get("enabled"):
        log("[动画] 没开（config → anim.enabled）")
        return
    if not A.get("api_key"):
        log("[动画] 还没填火山方舟的 key（config → anim.api_key）")
        return
    try:
        r = await _client().get(_base() + "/contents/generations/tasks", headers=_hdr(),
                                params={"page_num": 1, "page_size": 1})
        s = status()
        if r.status_code == 401:
            _pause(6 * 3600, "做动画的 key 不对")
            log("[动画] 火山方舟的 key 不对或失效了（401）做动画先停 6 小时")
        elif r.status_code >= 300:
            code, msg = _err(r)
            log(f"[动画] 自检：火山方舟回 {r.status_code} {code} {msg[:80]}（key 应该是对的 真做的时候再看）")
        else:
            log(f"[动画] 火山方舟 key 可用 · {A['model']} {A['resolution']} {A['duration']} 秒 约 ¥{s['est']}/条 · "
                f"已花 ¥{s['spent']} / 预算 ¥{s['budget']:g}")
    except Exception as e:
        log("[动画] 自检连不上火山方舟:", _clean(repr(e))[:120])
