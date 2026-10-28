# -*- coding: utf-8 -*-
"""QQ 空间（10-27 v1.3.0）：刷好友动态 → 看 / 点赞 / 评论；当场看某人的说说；发 / 删她自己的说说；回她说说下的评论；看访客

- 登录态：每次找 NapCat 现要（OneBot 的 get_cookies，domain=user.qzone.qq.com）—— 不存密码、不用扫码；
  30 分钟换一次，接口说没登录就马上重拿一次
- 用的是 QQ 空间网页版自己用的那些接口（非官方 随时可能变）：读动态 feeds3_html_more、某人的说说 emotion_cgi_msglist_v6、
  一条详情 emotion_cgi_msgdetail_v6、点赞 internal_dolike_app、评论 / 回评论 emotion_cgi_re_feeds、访客 cgi_get_visitor_more；
  发 / 删说说用 NapCat 自带的 send_qzone_msg / delete_qzone_msg
- 点赞的 unikey 照动态里点赞按钮上写的原样用（http:// 开头 —— 用 https:// 接口照样回 succ 但其实没赞上）；
  下一轮刷的时候看按钮上的 data-islike 核对有没有真赞上
- 限速：每种动作之间隔一会儿（带随机）+ 每天上限；退避：登录态拿不到 / 失效重拿也不行 → 歇 1 小时；
  「操作太频繁」→ 歇 24 小时；连着出错 3 次 → 歇 6 小时
- 存的东西：data/qzone/state.json（看过 / 赞过 / 评过、她的说说、回过的评论、每天的次数、退避、自检）
            data/qzone/feed.json（7 天内看过的说说 + 她的感想 + 在哪提过）
            data/qzone_reports/日期.md（跟 B站 的观看报告一样）
这个文件只管「接口 + 存取 + 规矩」；她怎么想、怎么说（带人设的提示词）在 bot.py 里接
参考：ltbjx/astrbot_plugin_qzone（点赞要用 http:// 的 unikey、靠 data-islike 核对）、internetsb/Maizone（回评论的 @ 写法）
"""
import asyncio
import html
import json
import random
import re
import time
from pathlib import Path

import httpx

import clock
import safeio

ROOT = Path(__file__).resolve().parent
DIR = ROOT / "data" / "qzone"
STATE = DIR / "state.json"
FEED = DIR / "feed.json"
REPORTS = ROOT / "data" / "qzone_reports"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/138.0.0.0 Safari/537.36")
URLS = {
    "feeds": "https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more",
    "list": "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6",
    "detail": "https://h5.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msgdetail_v6",
    "like": "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app",
    "comment": "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds",
    "visitors": "https://h5.qzone.qq.com/proxy/domain/g.qzone.qq.com/cgi-bin/friendshow/cgi_get_visitor_more",
}

DEFAULTS = {
    "enabled": False,            # config 里没有 qzone 这一节 = 不开（公开版升级上来的人不会突然开始点赞评论）
    "read": True, "read_every_min": 120, "read_max_posts": 8, "read_days": 3,
    "like": True, "like_daily": 20, "like_min_score": 2, "like_per_read": 6,
    "comment": True, "comment_daily": 8, "comment_min_favor": 20, "comment_per_read": 2,
    "mention_private": True, "mention_group": True, "view_on_ask": True,
    "post": True, "post_daily": 1, "post_prob": 0.7, "post_hours": [13, 24],
    "post_sources": ["diary", "bili", "mood", "group"], "post_right": 1,
    "reply": True, "reply_daily": 10, "reply_rounds": 2,
    "visitors": True,
    "dry_run": {"like": False, "comment": False, "post": False, "reply": False},
    "timeout": 15, "cookie_ttl_min": 30,
}
Q = json.loads(json.dumps(DEFAULTS))


def configure(cfg=None):
    Q.clear()
    Q.update(json.loads(json.dumps(DEFAULTS)))
    for k, v in (cfg or {}).items():
        if k == "dry_run" and isinstance(v, dict):
            Q["dry_run"].update(v)
        elif not str(k).startswith("_"):
            Q[k] = v


def on(what=None):
    """空间整个开着 + 这一项开着"""
    return bool(Q.get("enabled", True)) and (what is None or bool(Q.get(what, True)))


def dry(what):
    return bool((Q.get("dry_run") or {}).get(what))


# ───────────────────────── 不标准的 JSON ─────────────────────────
_CB = re.compile(r"^\s*[\w$.]+\s*\(\s*(.*)\)\s*;?\s*$", re.S)
_FCB = re.compile(r"callback\(\s*(\{.*\})\s*\)", re.S)


def _js2json(s):
    """JS 对象字面量 → 标准 JSON：单引号、\\x22 这种转义、key 没加引号、undefined、多余的逗号"""
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c in "\"'":
            q, i, buf = c, i + 1, ['"']
            while i < n:
                ch = s[i]
                if ch == "\\" and i + 1 < n:
                    nx = s[i + 1]
                    if nx == "x" and re.match(r"[0-9a-fA-F]{2}", s[i + 2:i + 4] or ""):
                        buf.append("\\u00" + s[i + 2:i + 4])
                        i += 4
                        continue
                    if nx == "'":
                        buf.append("'")
                    elif nx in '"\\/bfnrtu':
                        buf.append("\\" + nx)
                    else:
                        buf.append(nx)
                    i += 2
                    continue
                if ch == q:
                    i += 1
                    break
                if ch == '"':
                    buf.append('\\"')
                elif ch in "\n\r\t":
                    buf.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
                elif ord(ch) < 0x20:
                    buf.append("\\u%04x" % ord(ch))
                else:
                    buf.append(ch)
                i += 1
            buf.append('"')
            out.append("".join(buf))
            continue
        if c.isdigit() or (c == "-" and i + 1 < n and s[i + 1].isdigit()):
            j = i + 1
            while j < n and s[j] in "0123456789.eE+-":
                j += 1
            out.append(s[i:j])
            i = j
            continue
        if c.isalpha() or c in "_$":
            j = i
            while j < n and (s[j].isalnum() or s[j] in "_$"):
                j += 1
            word = s[i:j]
            k = j
            while k < n and s[k] in " \t\r\n":
                k += 1
            if k < n and s[k] == ":":
                out.append(json.dumps(word))
            elif word in ("true", "false", "null"):
                out.append(word)
            elif word in ("undefined", "NaN", "Infinity"):
                out.append("null")
            else:
                out.append(json.dumps(word))
            i = j
            continue
        if c == ",":
            k = i + 1
            while k < n and s[k] in " \t\r\n":
                k += 1
            if k < n and s[k] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def loads(text):
    """QQ 空间回的 JSON 常常不标准：包在 _Callback(...) 里 / 评论接口回的是一段 HTML 里的 frameElement.callback({...})"""
    t = (text or "").strip()
    if not t:
        return None
    m = _CB.match(t)
    if m and not t.startswith("{"):
        t = m.group(1).strip()
    elif not t.startswith("{"):
        m = _FCB.search(t)
        if m:
            t = m.group(1)
    for cand in (t,):
        try:
            v = json.loads(cand)
            return v if isinstance(v, dict) else None
        except ValueError:
            pass
    s, e = t.find("{"), t.rfind("}")
    if s < 0 or e < s:
        return None
    t = t[s:e + 1]
    for f in (lambda x: x, _js2json):
        try:
            v = json.loads(f(t))
            return v if isinstance(v, dict) else None
        except ValueError:
            continue
    return None


# ───────────────────────── 文字清理 ─────────────────────────
_EM = re.compile(r"\[em\]e?\d+\[/em\]")
_AT = re.compile(r"@\{uin:(\d+),nick:([^,}]*)(?:,[^}]*)?\}")
_TAG = re.compile(r"<[^>]+>")


def clean(t):
    """[em]e100[/em] 表情去掉、@{uin:…,nick:小明,auto:1} → @小明、HTML 转义还原、多余空白合并"""
    t = html.unescape(str(t or ""))
    t = _AT.sub(lambda m: "@" + (m.group(2) or m.group(1)), t)
    t = _EM.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def strip_html(h):
    h = re.sub(r"<br\s*/?>", " ", str(h or ""), flags=re.I)
    return clean(_TAG.sub("", h))


def _vis(m):
    """看得到的范围：limited = 部分好友可见 / 仅自己可见 / 部分好友不可见；friends = QQ 好友可见（这两种都不在群里提：
    群里有不是他好友的人 10-28 Gemini 复查）"""
    for k in ("ugc_right", "ugcright", "right"):
        try:
            v = int(m.get(k))
        except (TypeError, ValueError):
            continue
        if v in (16, 64, 128):
            return "limited"
        if v == 4:
            return "friends"
    try:
        if int(m.get("secret") or 0):
            return "limited"
    except (TypeError, ValueError):
        pass
    return "public"


def norm_post(m, owner=""):
    """说说列表 / 详情里的一条 → {uin, tid, name, text, ts, imgs, video, rt, comments, cmtnum, vis}"""
    if not isinstance(m, dict):
        return None
    tid = str(m.get("tid") or m.get("key") or "").strip()
    uin = str(m.get("uin") or owner or "").strip()
    if not tid or not uin:
        return None
    imgs = []
    for p in m.get("pic") or []:
        if isinstance(p, dict):
            u = p.get("url2") or p.get("url3") or p.get("url1") or p.get("smallurl")
            if u and u not in imgs:
                imgs.append(str(u))
    video = False
    for v in m.get("video") or []:
        if isinstance(v, dict):
            video = True
            u = v.get("url1") or v.get("pic_url")
            if u and u not in imgs:
                imgs.append(str(u))
    rt = m.get("rt_con")
    rt = clean(rt.get("content")) if isinstance(rt, dict) else ""
    comments = []
    for c in m.get("commentlist") or []:
        if not isinstance(c, dict):
            continue
        ctid = str(c.get("tid") or "")
        comments.append({"uin": str(c.get("uin") or ""), "name": clean(c.get("name")), "text": clean(c.get("content")),
                         "tid": ctid, "ts": int(c.get("create_time") or c.get("createTime3") or 0), "parent": ""})
        for s in c.get("list_3") or []:
            if isinstance(s, dict):
                comments.append({"uin": str(s.get("uin") or ""), "name": clean(s.get("name")),
                                 "text": clean(s.get("content")), "tid": str(s.get("tid") or ""),
                                 "ts": int(s.get("create_time") or 0), "parent": ctid})
    try:
        ts = int(m.get("created_time") or m.get("createTime3") or m.get("abstime") or 0)
    except (TypeError, ValueError):
        ts = 0
    try:
        cmtnum = int(m.get("cmtnum") or len(comments))
    except (TypeError, ValueError):
        cmtnum = len(comments)
    return {"uin": uin, "tid": tid, "name": clean(m.get("name") or m.get("nickname")), "text": clean(m.get("content")),
            "ts": ts, "imgs": imgs[:6], "video": video, "rt": rt, "comments": comments, "cmtnum": cmtnum, "vis": _vis(m)}


def parse_feeds(raw, diag=None):
    """好友动态（feeds3_html_more）→ [{uin, tid, name, ts, unikey, islike, likecnt, text, imgs}]（只要说说 appid 311）
    diag（dict）：顺手记下这一页的原始情况 —— 只记字段名和条数 不记内容（好友动态 0 条时分清是真没有还是没认出来）"""
    d = loads(raw) if isinstance(raw, str) else raw
    if diag is not None:
        diag.clear()
    if not isinstance(d, dict):
        return None
    data = d.get("data")
    items = data.get("data") if isinstance(data, dict) else None
    if diag is not None:
        diag["top"] = sorted(str(k) for k in d)[:12]
        diag["data"] = sorted(str(k) for k in data)[:12] if isinstance(data, dict) else []
    if not isinstance(items, list):
        return []
    if diag is not None:
        diag.update({"n": 0, "apps": {}, "bad": 0, "keys": []})
    out, seen = [], set()
    for f in items:
        if not isinstance(f, dict):
            continue
        app = str(f.get("appid", ""))
        if diag is not None:
            diag["n"] += 1
            diag["apps"][app] = diag["apps"].get(app, 0) + 1
        if app != "311":
            continue
        uin, tid = str(f.get("uin") or "").strip(), str(f.get("key") or "").strip()
        if not uin or not tid:
            if diag is not None:
                diag["bad"] += 1
                diag["keys"] = diag["keys"] or sorted(str(k) for k in f)[:16]
            continue
        if tid in seen:
            continue
        seen.add(tid)
        h = str(f.get("html") or "").replace('\\"', '"').replace("\\/", "/")
        uk = None
        for m in re.finditer(r'data-unikey="([^"]+)"', h):
            if f"/{uin}/mood/{tid}" in m.group(1):
                uk = m.group(1)
                break
        at = h.find(uk) if uk else -1
        btn = h[max(0, at - 400):at + 400] if at >= 0 else h
        il = re.search(r'data-islike="(\d)"', btn)
        lc = re.search(r'data-likecnt="(\d+)"', btn)
        tm = re.search(r'<div[^>]*class="[^"]*\bf-info\b[^"]*"[^>]*>(.*?)</div>', h, re.S)
        imgs = []
        box = re.search(r'class="[^"]*\bimg-box\b[^"]*"(.*?)</div>', h, re.S)
        for src in re.findall(r'<img[^>]+src="([^"]+)"', box.group(1) if box else ""):
            if "qzonestyle.gtimg.cn" not in src and src not in imgs:
                imgs.append(src)
        try:
            ts = int(f.get("abstime") or 0)
        except (TypeError, ValueError):
            ts = 0
        out.append({"uin": uin, "tid": tid, "name": clean(f.get("nickname")), "ts": ts,
                    "unikey": uk or f"http://user.qzone.qq.com/{uin}/mood/{tid}",
                    "islike": int(il.group(1)) if il else None, "likecnt": int(lc.group(1)) if lc else None,
                    "text": strip_html(tm.group(1)) if tm else "", "imgs": imgs[:4]})
    return out


def parse_visitors(raw):
    d = loads(raw) if isinstance(raw, str) else raw
    if not isinstance(d, dict):
        return None
    data = d.get("data") if isinstance(d.get("data"), dict) else {}
    out = []
    for v in data.get("items") or []:
        if not isinstance(v, dict):
            continue
        try:
            t = int(v.get("time") or 0)
        except (TypeError, ValueError):
            t = 0
        uin = str(v.get("uin") or "")
        if uin:
            out.append({"uin": uin, "name": clean(v.get("name")), "ts": t, "src": v.get("src"),
                        "hide": bool(v.get("is_hide_visit"))})
    return out


# ───────────────────────── 登录态 ─────────────────────────
class QzErr(Exception):
    """kind：login 登录态不行 / perm 没权限看 / busy 操作太频繁 / net 网络或服务出错 / bad 回的东西看不懂 / off 没开"""

    def __init__(self, kind, msg=""):
        super().__init__(msg or kind)
        self.kind = kind
        self.msg = msg or kind


def gtk(key):
    h = 5381
    for ch in key or "":
        h += (h << 5) + ord(ch)
    return str(h & 0x7FFFFFFF)


def parse_cookie(s):
    jar = {}
    for part in re.split(r";\s*", str(s or "")):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                jar[k.strip()] = v.strip()
    return jar


class Auth:
    """找 NapCat 要空间的登录态（get_cookies）。call = 机器人的 OneBot 调用（async (action, **params) -> 回包）"""

    def __init__(self):
        self.v = None
        self.t = 0.0
        self.call = None
        self._lock = None

    def bind(self, call):
        self.call = call

    def invalidate(self):
        self.v, self.t = None, 0.0

    async def get(self, force=False):
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.v and not force and time.time() - self.t < float(Q.get("cookie_ttl_min", 30)) * 60:
                return self.v
            if not self.call:
                raise QzErr("login", "还没连上 QQ")
            r = await self.call("get_cookies", domain="user.qzone.qq.com")
            d = (r or {}).get("data") if isinstance(r, dict) else None
            if not isinstance(d, dict) or not d.get("cookies"):
                raise QzErr("login", "NapCat 没给空间的登录态" + (f"（{str((r or {}).get('message') or (r or {}).get('wording') or '')[:40]}）"
                                                         if isinstance(r, dict) else "（QQ 没连上）"))
            jar = parse_cookie(d["cookies"])
            uin = re.sub(r"^[oO]0*", "", jar.get("uin") or jar.get("p_uin") or "")
            key = jar.get("p_skey") or jar.get("skey") or ""
            if not uin.isdigit() or not key:
                raise QzErr("login", "登录态里缺 uin / p_skey")
            self.v = {"uin": uin, "gtk": gtk(key), "cookie": "; ".join(f"{k}={v}" for k, v in jar.items())}
            self.t = time.time()
            return self.v


AUTH = Auth()
LAST_FEEDS = {}       # 最近一次刷好友动态的原始情况（feeds_diag 用 只有条数和字段名）
_LAST = {}            # 动作 → 上次做的时间（限速）
GAPS = {"read": (2, 4), "detail": (1.5, 3), "like": (8, 20), "comment": (25, 70), "reply": (15, 45), "visitors": (1, 2)}


async def _pace(kind):
    lo, hi = GAPS.get(kind, (1, 2))
    wait = _LAST.get(kind, 0) + random.uniform(lo, hi) - time.time()
    if wait > 0:
        await asyncio.sleep(wait)
    _LAST[kind] = time.time()


def _classify(d, status=200):
    """回包 → None（成功）或者 QzErr"""
    if not isinstance(d, dict):
        return QzErr("bad", f"看不懂空间回的东西（HTTP {status}）")
    code = d.get("code", d.get("ret"))
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = 0 if code is None and status == 200 else -1
    if code == 0:
        return None
    msg = str(d.get("message") or d.get("msg") or d.get("subcode") or code)[:80]
    if code == -3000 or re.search(r"登录|登陆|login|skey|失效", msg, re.I):
        return QzErr("login", f"登录态失效（{msg}）")
    if re.search(r"频繁|太快|稍后|过多|frequen", msg, re.I):
        return QzErr("busy", f"操作太频繁（{msg}）")
    if re.search(r"权限|保密|不可见|无法访问|没有权限|好友|主人设置", msg) or code in (-10031, 403, -403):
        return QzErr("perm", msg)
    return QzErr("net", f"code {code}：{msg}")


async def _http(method, url, params=None, data=None, ref=None, retried=False):
    a = await AUTH.get()
    p = dict(params or {})
    p.setdefault("g_tk", a["gtk"])
    headers = {"User-Agent": UA, "Referer": ref or f"https://user.qzone.qq.com/{a['uin']}",
               "Origin": "https://user.qzone.qq.com", "Cookie": a["cookie"]}
    try:
        async with httpx.AsyncClient(timeout=float(Q.get("timeout", 15)), follow_redirects=True) as c:
            r = await c.request(method, url, params=p, data=data, headers=headers)
    except httpx.HTTPError as e:
        raise QzErr("net", f"连不上空间（{type(e).__name__}）") from e
    text = r.text
    if r.status_code == 401 or (r.status_code == 403 and "login" in text.lower()):
        if not retried:
            AUTH.invalidate()
            return await _http(method, url, params, data, ref, True)
        raise QzErr("login", f"登录态失效（HTTP {r.status_code}）")
    if r.status_code >= 500:
        raise QzErr("net", f"空间出错了（HTTP {r.status_code}）")
    d = loads(text)
    err = _classify(d, r.status_code) if d is not None else (QzErr("perm", "没权限（HTTP 403）") if r.status_code == 403
                                                              else QzErr("bad", f"看不懂空间回的东西（HTTP {r.status_code}）"))
    if err and err.kind == "login" and not retried:
        AUTH.invalidate()
        return await _http(method, url, params, data, ref, True)
    if err:
        raise err
    return d, text


# ───────────────────────── 接口 ─────────────────────────
async def feeds(page=1):
    """好友动态第一页（新的在前）。看不懂 → QzErr"""
    await _pace("read")
    a = await AUTH.get()
    d, text = await _http("GET", URLS["feeds"], {
        "uin": a["uin"], "scope": 0, "view": 1, "filter": "all", "flag": 1, "applist": "all", "pagenum": page,
        "aisortEndTime": 0, "aisortOffset": 0, "aisortBeginTime": 0, "begintime": 0, "format": "json",
        "useutf8": 1, "outputhtmlfeed": 1})
    diag = {}
    out = parse_feeds(d, diag)
    if out is None:
        raise QzErr("bad", "好友动态看不懂")
    diag["mine"] = sum(1 for x in out if x["uin"] == a["uin"])
    LAST_FEEDS.clear()
    LAST_FEEDS.update(diag)
    return [x for x in out if x["uin"] != a["uin"]]


def feeds_diag():
    """好友动态 0 条时补一句原因（只有条数和字段名）：真没人发 / 都是她自己的 / 都是别的类型 / 说说没认出来"""
    d = LAST_FEEDS
    if not d:
        return ""
    if "n" not in d:
        return f"（回包里没找到动态列表：{','.join(d.get('top') or []) or '空'} / data：{','.join(d.get('data') or []) or '空'}）"
    n, apps = d["n"], d.get("apps") or {}
    if not n:
        return "（这一页原始也是 0 条：好友最近没发东西 或者只给她看到这些）"
    s311 = apps.get("311", 0)
    parts = [f"这一页原始 {n} 条"]
    if s311:
        parts.append(f"说说 {s311} 条" + (f"（{d['mine']} 条是她自己的）" if d.get("mine") else ""))
    if n - s311:
        parts.append(f"别的类型 {n - s311} 条（相册 / 转载 / 日志之类 不看）")
    if d.get("bad"):
        parts.append(f"{d['bad']} 条说说没认出来（字段：{','.join(d.get('keys') or [])}）")
    return "（" + " · ".join(parts) + "）"


async def posts(uin, num=5, pos=0):
    """某人最近的说说（新的在前）→ [post]；他设了权限 / 不是好友 → QzErr('perm')"""
    await _pace("read")
    d, _ = await _http("GET", URLS["list"], {
        "uin": str(uin), "ftype": 0, "sort": 0, "pos": int(pos), "num": int(num), "replynum": 100,
        "callback": "_preloadCallback", "code_version": 1, "format": "jsonp", "need_comment": 1,
        "need_private_comment": 1}, ref=f"https://user.qzone.qq.com/{uin}")
    return [p for p in (norm_post(m, str(uin)) for m in d.get("msglist") or []) if p]


async def detail(uin, tid):
    await _pace("detail")
    d, _ = await _http("GET", URLS["detail"], {"uin": str(uin), "tid": str(tid), "format": "jsonp"},
                       ref=f"https://user.qzone.qq.com/{uin}")
    p = norm_post(d, str(uin))
    if not p:
        raise QzErr("bad", "说说详情看不懂")
    return p


async def like(uin, tid, unikey=""):
    await _pace("like")
    a = await AUTH.get()
    key = unikey or f"http://user.qzone.qq.com/{uin}/mood/{tid}"
    await _http("POST", URLS["like"], data={
        "qzreferrer": f"https://user.qzone.qq.com/{a['uin']}", "opuin": a["uin"], "unikey": key, "curkey": key,
        "appid": 311, "from": 1, "typeid": 0, "abstime": int(time.time()), "fid": str(tid), "active": 0,
        "format": "json", "fupdate": 1})
    return True


async def comment(uin, tid, content):
    await _pace("comment")
    a = await AUTH.get()
    await _http("POST", URLS["comment"], data={
        "topicId": f"{uin}_{tid}__1", "uin": a["uin"], "hostUin": str(uin), "feedsType": 100, "inCharset": "utf-8",
        "outCharset": "utf-8", "plat": "qzone", "source": "ic", "platformid": 52, "format": "fs", "ref": "feeds",
        "content": content})
    return True


async def reply(tid, c_uin, c_tid, c_nick, content):
    """回她自己说说下面的一条评论（@ 写法照 Maizone：@{uin:…,nick:…,auto:1}）"""
    await _pace("reply")
    a = await AUTH.get()
    me = a["uin"]
    nick = re.sub(r"[,{}:\s]", "", str(c_nick or ""))[:20] or str(c_uin)
    await _http("POST", URLS["comment"], data={
        "topicId": f"{me}_{tid}__1", "uin": me, "hostUin": me, "feedsType": 100, "inCharset": "utf-8",
        "outCharset": "utf-8", "content": f"@{{uin:{c_uin},nick:{nick},auto:1}}{content}", "format": "fs",
        "plat": "qzone", "source": "ic", "platformid": 52, "ref": "feeds", "commentId": str(c_tid),
        "commentUin": str(c_uin), "richtype": "", "richval": "", "paramstr": "1"})
    return True


async def visitors():
    await _pace("visitors")
    a = await AUTH.get()
    d, _ = await _http("GET", URLS["visitors"], {"uin": a["uin"], "mask": 7, "page": 1, "fupdate": 1, "clear": 1})
    out = parse_visitors(d)
    if out is None:
        raise QzErr("bad", "访客看不懂")
    return out


async def publish(call, content, right=1):
    """发说说：NapCat 的 send_qzone_msg → tid"""
    r = await call("send_qzone_msg", content=content, ugc_right=int(right or 1))
    if not isinstance(r, dict):
        raise QzErr("net", "QQ 没连上 发不了")
    if r.get("retcode") != 0:
        msg = str(r.get("message") or r.get("wording") or r.get("retcode"))[:80]
        raise QzErr("busy" if re.search(r"频繁|太快", msg) else "net", f"发说说失败（{msg}）")
    tid = str(((r.get("data") or {}) if isinstance(r.get("data"), dict) else {}).get("tid") or "")
    if not tid:
        raise QzErr("bad", "发说说没拿到 tid")
    return tid


async def delete(call, tid):
    r = await call("delete_qzone_msg", tid=str(tid))
    if not isinstance(r, dict):
        raise QzErr("net", "QQ 没连上 删不了")
    if r.get("retcode") != 0:
        raise QzErr("net", f"删说说失败（{str(r.get('message') or r.get('wording') or r.get('retcode'))[:80]}）")
    return True


# ───────────────────────── 存档 ─────────────────────────
def _empty_state():
    return {"seen": {}, "liked": {}, "commented": {}, "mine": [], "replied": {}, "days": {}, "pause_until": 0,
            "pause_why": "", "fails": 0, "last_read": 0, "last_try": 0, "visitors": [], "visit_mention": {},
            "selftest": {}, "post_plan": {}, "unverified": [], "events": []}


_S = {"d": None, "m": None}          # 机器人这边只有一份（同一个进程里大家改的是同一个 dict 不会互相冲掉）
_F = {"d": None, "m": None}


def _mtime(p):
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def load():
    m = _mtime(STATE)
    if _S["d"] is not None and m == _S["m"]:
        return _S["d"]
    try:
        s = safeio.read_json(STATE, None, print, busy_raise=True)
    except safeio.Busy:                    # 一直打不开：这一轮当成歇着（不刷、不赞、不评、不存）（10-28 Gemini 复查：
        tmp = safeio.Unsaved(_empty_state())   # 以前当成空的 一存就把赞过 / 评过 / 回过、「太频繁」歇到几点全冲掉 → 重复评论、提前恢复）
        tmp["pause_until"], tmp["pause_why"] = time.time() + 600, "状态文件一时打不开"
        return tmp
    if not isinstance(s, dict):
        s = {}
    base = _empty_state()
    base.update(s)
    _S.update(d=base, m=m)
    return base


def save(s):
    if isinstance(s, safeio.Unsaved):      # 读不出来时临时给的：不能存（10-28 Gemini 复查）
        return
    now = time.time()
    for k in ("seen", "liked", "commented", "replied", "take_fail"):             # 只留 30 天
        d = s.get(k)
        if isinstance(d, dict):
            for t in [t for t, v in d.items() if now - float((v.get("ts") if isinstance(v, dict) else v) or 0) > 30 * 86400]:
                d.pop(t, None)
    s["mine"] = [m for m in s.get("mine") or [] if now - float(m.get("ts") or 0) < 60 * 86400][-60:]
    cut = clock.strf("%Y-%m-%d", now - 14 * 86400)
    s["days"] = {k: v for k, v in (s.get("days") or {}).items() if k >= cut}
    s["events"] = (s.get("events") or [])[-80:]
    s["visitors"] = (s.get("visitors") or [])[-60:]
    DIR.mkdir(parents=True, exist_ok=True)
    safeio.write_json(STATE, s)
    _S.update(d=s, m=_mtime(STATE))


def today(s, kind, add=0):
    """今天做了几次（她的一天按北京时间 0 点算）"""
    day = clock.strf("%Y-%m-%d")
    d = s.setdefault("days", {}).setdefault(day, {})
    if add:
        d[kind] = int(d.get(kind, 0)) + add
    return int(d.get(kind, 0))


def event(s, what, text="", uin=""):
    """控制台「最近」那一栏（uin = 说的是谁：删数据时按号删 10-28 审查 qzone-11）"""
    e = {"t": time.time(), "what": what, "text": str(text)[:120]}
    if uin:
        e["uin"] = str(uin)
    s.setdefault("events", []).append(e)


def forget(uid, names=(), scrub=None, s=None, fd=None):
    """删数据（10-28 审查 qzone-11）：进程里正拿着的那份也删一遍 —— 刷空间 / 回评论那一轮手里拿的是同一个 dict，
    wipe.py 直接改文件 那一轮存盘时会把他的说说、赞评记录又写回去。
    s / fd 不给 = 改这个进程缓存的那份并存盘（删数据时调）；给了 = 只改不存（那一轮存盘前调 自己存）。
    scrub(text, names) → 「最近」里他的名字换成「某人」（wipe.scrub）。返回删了几处"""
    uid, own = str(uid), s is None and fd is None
    if own:
        s, fd = _S["d"], _F["d"]
    n, nf = 0, 0
    if isinstance(fd, dict) and isinstance(fd.get("items"), list):
        keep = [x for x in fd["items"] if str(x.get("uin")) != uid]
        nf = len(fd["items"]) - len(keep)
        if nf:
            fd["items"] = keep
    if isinstance(s, dict):
        for k in ("liked", "commented", "replied"):
            d = s.get(k)
            if isinstance(d, dict):
                for t in [t for t, v in d.items() if isinstance(v, dict) and str(v.get("uin")) == uid]:
                    d.pop(t, None)
                    n += 1
        vs = s.get("visitors") or []
        keep = [v for v in vs if not (isinstance(v, dict) and str(v.get("uin")) == uid)]
        if len(keep) != len(vs):
            n += len(vs) - len(keep)
            s["visitors"] = keep
        if (s.get("visit_mention") or {}).pop(uid, None) is not None:
            n += 1
        for m in s.get("mine") or []:
            if isinstance(m, dict) and (m.get("rounds") or {}).pop(uid, None) is not None:
                n += 1
        evs = s.get("events") or []
        keep = [e for e in evs if not (isinstance(e, dict) and str(e.get("uin") or "") == uid)]
        n += len(evs) - len(keep)
        for e in keep:
            if isinstance(e, dict) and names and scrub and e.get("text"):
                t2 = scrub(e["text"], names)
                if t2 != e["text"]:
                    e["text"] = t2
                    n += 1
        if len(keep) != len(evs):
            s["events"] = keep
    if own:
        if nf:
            feed_save(fd)
        if n:
            save(s)
    return n + nf


def paused(s, now=None):
    return float(s.get("pause_until") or 0) > (now or time.time())


def note_err(s, e, log=print):
    """出错 → 退避：登录 1 小时 / 太频繁 24 小时 / 连错 3 次 6 小时；没权限看某个人的不算出错"""
    if not isinstance(e, QzErr):
        e = QzErr("net", repr(e)[:80])
    if e.kind == "perm":
        return
    s["fails"] = int(s.get("fails") or 0) + 1
    h = 1 if e.kind == "login" else 24 if e.kind == "busy" else 6 if s["fails"] >= 3 else 0
    if h:
        s["pause_until"], s["pause_why"] = time.time() + h * 3600, e.msg
        s["fails"] = 0
        log(f"[空间] {e.msg} → 歇 {h} 小时再说（聊天不受影响）")
        event(s, "歇一会", f"{e.msg}（{h} 小时）")


def note_ok(s):
    s["fails"] = 0


def feed_load():
    m = _mtime(FEED)
    if _F["d"] is not None and m == _F["m"]:
        return _F["d"]
    try:
        d = safeio.read_json(FEED, None, print, busy_raise=True)
    except safeio.Busy:                    # 一直打不开：这次当没有、不存（10-28 Gemini 复查：以前当成空的 一存 7 天看过的说说全没了）
        return safeio.Unsaved(json.loads(json.dumps(_F["d"])) if _F["d"] is not None else {"items": []})
    d = d if isinstance(d, dict) and isinstance(d.get("items"), list) else {"items": []}
    _F.update(d=d, m=m)
    return d


def feed_save(d):
    if isinstance(d, safeio.Unsaved):
        return
    cut = time.time() - 7 * 86400
    d["items"] = [x for x in d.get("items") or [] if float(x.get("read") or 0) >= cut][-300:]
    DIR.mkdir(parents=True, exist_ok=True)
    safeio.write_json(FEED, d)
    _F.update(d=d, m=_mtime(FEED))


def feed_find(d, tid):
    return next((x for x in d.get("items") or [] if x.get("tid") == str(tid)), None)


def feed_put(d, item):
    old = feed_find(d, item.get("tid"))
    if old:
        old.update({k: v for k, v in item.items() if v not in (None, "") or k not in old})
        return old
    item.setdefault("mention", {"p": 0, "g": {}})
    d.setdefault("items", []).append(item)
    return item


def report(lines):
    if not lines:
        return
    REPORTS.mkdir(parents=True, exist_ok=True)
    with (REPORTS / f"{clock.strf('%Y-%m-%d')}.md").open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ───────────────────────── 聊天里提 ─────────────────────────
def _bigrams(t):
    t = re.sub(r"[^一-鿿A-Za-z0-9]", "", str(t or ""))
    return {t[i:i + 2] for i in range(len(t) - 1)}


STOP2 = {"今天", "什么", "怎么", "这个", "那个", "就是", "还是", "可以", "没有", "一个", "我们", "你们", "他们", "自己",
         "知道", "觉得", "真的", "哈哈", "时候", "现在", "然后", "不是", "感觉", "还有", "一下", "这么", "空间", "说说"}


def mentioned(item, said, alone=True):
    """她这句是不是提到了这条说说：跟正文 / 她的感想撞了两个以上的词；
    这一轮只给她看了这一条（alone）的话 撞一个词、或者明说了「空间 / 说说」也算"""
    s = str(said or "")
    if not s:
        return False
    hit = len(((_bigrams(item.get("text")) | _bigrams(item.get("take"))) - STOP2) & _bigrams(s))
    if hit >= 2:
        return True
    return bool(alone and (hit >= 1 or re.search(r"空间|说说|动态|朋友圈", s)))


def _bot(uin):
    """别人家的 AI（控制台 / 认 AI 标的）：后来才认出来的 之前刷进 feed 的说说也别拿出来提（10-28 审查 qzone-10）"""
    try:
        import control                  # 在函数里 import：这个文件顶上不多依赖一个模块
        return control.is_bot(uin)
    except Exception:
        return False


def for_private(uid, now=None, limit=2, days=None):
    """私聊里能提的：他的说说 她看过、还没在私聊里提过（新的在前）"""
    now = now or time.time()
    days = float(days or Q.get("read_days", 3)) + 2
    if _bot(uid):
        return []
    d = feed_load()
    out = [x for x in d["items"] if x.get("uin") == str(uid) and not (x.get("mention") or {}).get("p")
           and now - float(x.get("read") or 0) < days * 86400 and (x.get("text") or x.get("img_desc"))]
    out.sort(key=lambda x: -float(x.get("ts") or x.get("read") or 0))
    return out[:limit]


def for_group(gid, uids, now=None, days=2):
    """群里能提的（最多一条）：发说说的人就在这个群、最近说过话；不敏感、谁都能看；这个群没提过"""
    now = now or time.time()
    uids = {str(u) for u in uids or () if not _bot(u)}
    if not uids:
        return None
    d = feed_load()
    c = [x for x in d["items"] if x.get("uin") in uids and not x.get("sensitive") and x.get("vis", "public") == "public"
         and str(gid) not in ((x.get("mention") or {}).get("g") or {}) and now - float(x.get("read") or 0) < days * 86400
         and int(x.get("score") or 0) >= 1 and x.get("text")]
    c.sort(key=lambda x: (-int(x.get("score") or 0), -float(x.get("read") or 0)))
    return c[0] if c else None


def mark(tid, where, gid=""):
    """提过了：where = p（私聊）/ g（群 gid）"""
    d = feed_load()
    x = feed_find(d, tid)
    if not x:
        return
    m = x.setdefault("mention", {"p": 0, "g": {}})
    if where == "p":
        m["p"] = time.time()
    else:
        m.setdefault("g", {})[str(gid)] = time.time()
    feed_save(d)


def visited(uid, now=None, days=3):
    """他最近来过她的空间（访客记录）"""
    now = now or time.time()
    s = load()
    v = next((x for x in reversed(s.get("visitors") or []) if x.get("uin") == str(uid) and not x.get("hide")), None)
    if not v or now - float(v.get("ts") or 0) > days * 86400:
        return None
    if now - float((s.get("visit_mention") or {}).get(str(uid)) or 0) < 7 * 86400:
        return None
    return v


def visit_mentioned(uid):
    s = load()
    s.setdefault("visit_mention", {})[str(uid)] = time.time()
    save(s)


# ───────────────────────── 当场看 / 退出 ─────────────────────────
_GO = r"(看看?|瞅瞅?|瞧瞧?|来|去|逛逛?|刷刷?).{0,3}我的?(?:QQ)?(空间|(?<=的)说说|说说(?![你他她])|动态)"   # 「我说说你」= 我讲讲你 不算；「看看我的说说你就知道了」算（10-28 审查 qzone-2）
ASK_GO = re.compile(_GO)              # 明说叫她去看（说过别看的人只认这种）
ASK_SELF = re.compile(_GO + r"|"
                      r"我(?:刚|才|今天|又|刚刚|昨天)?(?:才)?发了?(?:一?条|个|一个|篇)?(?:新的?)?(说说|空间|动态)|"
                      r"我(?:的)?(空间|说说)(?:里|上)?(?:发|更新)了|(?:看|去)(?:过|了)?我(?:的)?空间(?:没|了吗|吗)")
# 「看了 / 看到 / 瞅了眼 你的说说」是已经看过 不是叫她看（10-28 审查 qzone-6）
ASK_OTHER = re.compile(r"(?:帮我|你去|去)?(?:看看?|瞅瞅?|瞧瞧?)(?![了到过眼])\s*(@?[^\s的看]{1,16}?)\s*的?(?:QQ|qq)?(空间|说说|动态)")
_BIE = r"(?<![特分区告级性类识离辨差个])别(?![人的处家个忘])"     # 「别人」「特别」「别忘了」里的「别」不算否定
# 「别 / 不许 / 不想你」和「看 / 来 / 刷」中间隔着「老 / 偷 / 你 / 再」也算（别老看 / 不许你看 / 不要再来）；
# 中间只认这几种字（「别急来看我空间」「别忘了看我空间」不算）（10-28 审查 qzone-2）
# 「我不希望 / 不喜欢 / 不愿意 / 不太想让 你看我空间」也算（10-28 审查 qzone-2 复查）：希望 / 喜欢 / 愿意 后面要带「你」（「我不喜欢看我说说里的评论」不算）
OPT_OUT = re.compile(r"(" + _BIE + r"|不要|不许|不准|不能|不可以|不太?想让?你?|不让你?|不太?(?:希望|喜欢|愿意)让?你)\s?"
                     r"(?:你|再|又|老|总是?|老是|一直|偷偷?|随便|乱|天天|成天|整天|每天都?|动不动就?|没事|有事没事|给我|一天到晚)*"
                     r"(?:看|刷|逛|来|去|翻|踩|点赞|评论)[^\s，,。！!？?；;]{0,3}?我的?(?:QQ|qq)?(空间|说说)")
OPT_IN = re.compile(r"(可以|能|欢迎)(?:再)?(?:去)?(?:看|刷|逛)(?:我的?)?(?:QQ)?(空间|说说)(?:了|啦|哦|吧|呀)?")
NO_GROUP = re.compile(r"(别|不要|不许|不准|不能|不可以)你?在群里(?:提|说)(?:我的?)?(?:QQ)?(空间|说说)")
GROUP_OK = re.compile(r"(可以|能)(?:再)?在群里(?:提|说|聊)(?:我的?)?(?:QQ)?(空间|说说)")    # 撤「别在群里提」（10-28 审查 qzone-1）
# 否定词（10-28 审查 qzone-1）：「你不能看我空间」「谁说你可以看我空间了」「我没说可以看」都不算同意；「别人」「特别」里的「别」不算
NEG = re.compile(_BIE + r"|不要|不许|不准|不能|不可以|不太?想|不让|不用|不太?(?:希望|喜欢|愿意)|没说|谁说|谁让|谁准|谁允许")
NEG_Q = re.compile(_BIE + r"|不要|不许|不准|不想你|不让你|不太?(?:希望|喜欢|愿意)让?你|没说|谁说|谁让|谁准|谁允许")   # 问句里：「你就不能来看看吗」是反问 不算
NEG_WORD = re.compile(r"别|不要|不许|不准|不能|不可以|不想你?|不让你?|没说|谁说")     # 单独一个否定词（「不要 看我空间」中间有空格）
AB = re.compile(r"能不能|可不可以|可以不可以|要不要|想不想|让不让|许不许|准不准|行不行")    # 正反问：不是否定 是问句
_AB_YES = {"可不可以": "可以", "可以不可以": "可以"}
# 抱怨不是邀请（10-28 审查 qzone-2）：「你为什么老来我空间」「你怎么又来了」「你偷看我空间」；「你为什么不来看看」还是邀请
MOAN = re.compile(r"(?:为什么|为啥|干嘛|干啥|凭什么|怎么)[^不没]{0,4}$|(?:老|总|总是|老是|一直|偷偷?|又|天天|成天|整天|每天都?)$")
# 抱怨写在后面也算（10-28 审查 qzone-2 复查）：「你看我空间干嘛」「你来我空间干什么」；「看看我空间 干嘛不看」还是邀请
MOAN_AFTER = re.compile(r"^\s*(?:是要|想要?|要)?(?:干嘛|干啥|干什么|做什么|做啥|搞什么|搞啥)(?![不没])")
QUOTED = re.compile(r"（回复了 [^\n]{0,40}?的「[\s\S]*?」）")          # 引用别人的那句（bot.py quoted_text 拼的）
FORWARD = re.compile(r"\[转发的聊天记录[\s\S]*?\n\]|\[聊天记录\]")       # 转发的聊天记录（bot.py forward_text 拼的）


def _clauses(texts):
    """他这几句 → [(小句, 是不是问句)]（10-28 审查 qzone-1）：按标点 / 换行切开 一句里的否定管不到另一句；
    正反问「能不能看我空间」换成「能看我空间」（不算否定）、这一句记成问句；
    引用的、转发的是别人说的 先剔掉（10-28 审查 qzone-2）"""
    t = "\n".join(FORWARD.sub("\n", QUOTED.sub(" ", str(x or ""))) for x in texts)[-300:]
    out = []
    for m in re.finditer(r"([^\n，,。！!？?；;～~…]+)([？?]?)", t):
        c = m.group(1)
        if not c.strip():
            continue
        q = bool(m.group(2) or AB.search(c) or re.search(r"(吗|么|嘛|不|没)\s*$", c))
        out.append((AB.sub(lambda x: _AB_YES.get(x.group(0), x.group(0)[0]), c), q))
    return out


def _pre(c, i):
    """小句 c 里第 i 个字前面、跟它连着的那段；空格前面的只认单独一个否定词（「不要 看我空间」）"""
    parts = re.split(r"\s+", c[:i])
    pre = parts[-1]
    if len(parts) > 1 and NEG_WORD.fullmatch(parts[-2]):
        pre = parts[-2] + pre
    return pre


def _neg(c, i, j, q=False):
    """小句 c 里 c[i:j] 这段连同它前面的字 带不带否定（问句里的「不能 / 不可以」是反问 不算）"""
    return bool((NEG_Q if q else NEG).search(_pre(c, i) + c[i:j]))


def _out(c, q=False):
    """这一小句是不是「别看我空间」（问句里只认 别 / 不要 / 不许 / 不准：「你就不能来看看我空间吗」是反问）"""
    return any(not q or re.match(r"别|不要|不许|不准|不太?(?:希望|喜欢|愿意)", m.group(1)) for m in OPT_OUT.finditer(c))


def _go(c, q, rx):
    """这一小句里有没有真叫她看（前面带否定的、抱怨的不算）（10-28 审查 qzone-2）"""
    return any(not _neg(c, m.start(), m.end(), q) and not MOAN.search(_pre(c, m.start())) and not MOAN_AFTER.match(c[m.end():])
               for m in rx.finditer(c))


def ask(texts, own=()):
    """他这几句里有没有叫她看空间：('self', '') / ('other', 名字或QQ号) / None
    own = 她自己的名字（「看看 <她> 的空间」不算别人的）（10-28 审查 qzone-6）"""
    cs = _clauses(texts)
    if any(_out(c, q) or NO_GROUP.search(c) for c, q in cs):
        return None
    if any(_go(c, q, ASK_SELF) for c, q in cs):
        return ("self", "")
    own = {str(x) for x in own or () if x}
    for c, q in cs:
        for m in ASK_OTHER.finditer(c):
            if _neg(c, m.start(), m.end(), q):
                continue
            who = re.sub(r"^(?:一下|一眼|下|眼)", "", m.group(1).lstrip("@").strip()).lstrip("@").strip()
            if who and who not in ("我", "我的", "你", "你的", "自己", "她", "他") and "你" not in who and who not in own:
                return ("other", who)
    return None


def invited(texts):
    """他明说叫她去看自己的空间（「快来看看我空间」）：说过别看的人只认这种 「我刚发了条说说」不算（10-28 审查 qzone-2）"""
    cs = _clauses(texts)
    return not any(_out(c, q) for c, q in cs) and any(_go(c, q, ASK_GO) for c, q in cs)


def opt(texts):
    """'off'（别看我空间）/ 'on'（可以看了）/ 'nogroup'（别在群里提）/ 'groupok'（可以在群里提了）/ None
    （10-28 审查 qzone-1）带否定的「可以看」（谁说你可以看 / 我没说可以看）算 off；问句（能看我空间吗）不算表态 交给 ask() 当场看"""
    cs = _clauses(texts)
    if any(NO_GROUP.search(c) for c, _ in cs):
        return "nogroup"
    if any(_out(c, q) for c, q in cs):
        return "off"
    for c, q in cs:
        for m in OPT_IN.finditer(c):
            if _neg(c, m.start(), m.end()):
                return "off"
            if not q:
                return "on"
    for c, q in cs:
        m = GROUP_OK.search(c)
        if m and not q and not _neg(c, m.start(), m.end()):
            return "groupok"
    return None


def view_block(who, items, err="", self_view=True, group=False, descs=None, hidden=0):
    """当场看的结果 → 给她这一轮看的一段；hidden = 群里没给她的「部分好友可见」那几条（10-28 审查 qzone-4）"""
    name = who or "他"
    if err:
        return (f"\n# 你刚想去看「{name}」的 QQ 空间 但没看成\n原因：{err}。照实说没看成（可以说原因）"
                "别编他空间里有什么。\n")
    if not items and hidden:
        return (f"\n# 你刚去看了「{name}」的 QQ 空间\n最近 {hidden} 条都不是谁都能看的（QQ 好友可见 / 部分好友可见）：群里别提内容"
                "（可以说想聊的话私聊）。\n")
    if not items:
        return f"\n# 你刚去看了「{name}」的 QQ 空间\n里面一条说说都没有（或者都看不到）。照实说。\n"
    out = [f"\n# 你刚去看了「{name}」的 QQ 空间（这一轮现看的 下面就是你看到的全部 别补没写的）"]
    for p in items[:5]:
        line = f"- {clock.ago(p.get('ts')) if p.get('ts') else ''}：{(p.get('text') or '（没写字）')[:120]}"
        if p.get("rt"):
            line += f"（转发的：{p['rt'][:60]}）"
        ds = (descs or {}).get(p.get("tid")) or []
        if ds:
            line += "（配图：" + "；".join(ds[:2]) + "）"
        elif p.get("imgs"):
            line += f"（配了 {len(p['imgs'])} 张图 没看清）"
        if p.get("cmtnum"):
            line += f" · {p['cmtnum']} 条评论"
        if p.get("vis", "public") != "public" and not group:
            line += "（不是谁都能看的：" + ("部分好友可见）" if p.get("vis") == "limited" else "QQ 好友可见）")
        out.append(line)
    if not group and not self_view and any(p.get("vis", "public") != "public" for p in items[:5]):
        # 老板私聊看别人的空间：不是谁都能看的那几条也给（小明 10-28 定 v1.9.4）—— 只在这儿跟老板说 别带到群里 / 别人那
        out.append("标着「不是谁都能看的」那几条：只在这个私聊里跟老板说，别带到群里、也别跟别人提。")
    if group and not self_view:
        out.append("这是你替老板看的别人的空间：群里别说内容 只说一句「私聊跟你说」。")
    elif group:
        out.append("群里大家都看得到你说的：挑一两条随口聊 别一条条念；生病 / 分手 / 很丧这类别在群里说 改私聊问。")
    else:
        out.append("挑你最有感觉的一两条聊 别一条条念。")
    if hidden:
        out.append(f"（另外 {hidden} 条不是谁都能看的（QQ 好友可见 / 部分好友可见）没给你：群里别提。）")
    return "\n".join(out) + "\n"


def private_block(items, visit=None):
    out = []
    if items:
        out.append("\n# 你在他 QQ 空间看到的（你自己刷到的 还没跟他提过）")
        for x in items:
            line = f"- {clock.ago(x.get('ts') or x.get('read'))}发的：{(x.get('text') or '')[:80]}"
            if x.get("img_desc"):
                line += f"（配图：{x['img_desc'][0][:40]}）"
            if x.get("take"):
                line += f" —— 你当时的感觉：{x['take']}"
            if x.get("commented"):
                line += f"（你在下面评论了：{x.get('comment', '')[:30]}）"
            elif x.get("liked"):
                line += "（你点了赞）"
            out.append(line)
        out.append("想起来可以顺口提一句（「看你空间说…」）别硬聊、别每条都说；他的事聊着不对劲就别提。")
    if visit:
        out.append(f"\n# 访客记录里有他：{clock.ago(visit.get('ts'))}来看过你的 QQ 空间。可以随口提一句 别追问。")
    return ("\n".join(out) + "\n") if out else ""


def group_block(item):
    if not item:
        return ""
    return (f"\n# 你刷到过「{item.get('name') or '他'}」的 QQ 空间（他就在这个群 你还没在群里提过）\n"
            f"- {clock.ago(item.get('ts') or item.get('read'))}发的：{(item.get('text') or '')[:60]}"
            + (f" —— 你当时的感觉：{item['take']}" if item.get("take") else "")
            + "\n正好聊到相关的才顺口带一句（「看你空间说…」）不相关就别提；别复述原文。\n")


# ───────────────────────── 刷动态时给模型看的 ─────────────────────────
READ_PROMPT = """你在替一个角色刷 QQ 空间（好友动态）。她叫「{name}」。她的人设（节选）：
{persona}

下面是她这会儿刷到的几条好友说说（编号、谁发的、她跟这个人熟不熟、发了多久、正文、配图是什么样）：
{posts}

用她的视角 给每条写一个 JSON 对象。只输出 JSON：
{{"items": [{{"n": 编号, "take": "她心里怎么想（20字内 口语 别复述原文）", "score": 0到3（3=很有意思 很想聊 / 0=没意思）,
  "sensitive": true或false（生病、受伤、分手、丧事、很丧、求助、考砸这类）, "like": true或false（想不想点赞）,
  "comment": "想在下面评论的大意（15字内；没想法就空着）",
  "life": "值得记住的近况（20字内 没有就空着；说说里的「明天 / 下周」按「发于」那天换成日期 比如「10-30 考驾照」）",
  "loop": {{"text": "过几天想问问他的事（20字内）", "after_hours": 从他发这条说说算起多少小时后}} 或者 null,
  "mood": -2到2（看完她心情变化）}}]}}

规矩：
- 只根据正文和配图判断 别编没写的事
- 广告、转发抽奖、营销号、看不懂的：score 0 不赞不评
- sensitive 的不开玩笑：想赞就赞；评论只写一句安慰的大意 或者不评
- 跟她不熟（陌生人 / 只是认识）的 comment 空着
- 别人吵架、骂人的：不赞不评"""


def posts_text(items, rel=None):
    """给 READ_PROMPT 的 {posts}"""
    lines = []
    for i, x in enumerate(items, 1):
        r = (rel or {}).get(x.get("uin")) or "不熟"
        ts = x.get("ts")
        when = (f"{clock.ago(ts)}（发于 {clock.strf('%m-%d', ts)} 周{clock.WEEK[clock.dt(ts).weekday()]} {clock.strf('%H:%M', ts)}）"
                if ts else "")             # 写上哪天发的：「明天 / 下周」好换成日期（10-28 审查 qzone-7）
        line = (f"{i}. 「{x.get('name') or x.get('uin')}」（{r}） {when}："
                f"{(x.get('text') or '（没写字）')[:200]}")
        if x.get("rt"):
            line += f"（转发：{x['rt'][:80]}）"
        for dsc in (x.get("img_desc") or [])[:2]:
            line += f"（配图：{dsc[:60]}）"
        if x.get("imgs") and not x.get("img_desc"):
            line += f"（配了 {len(x['imgs'])} 张图 没看清）"
        lines.append(line)
    return "\n".join(lines)


def _whole(raw):
    """被截断的 {"items": [{…}, {…}, {…  → 前面完整的那几个对象（10-28 审查 qzone-12：以前一条都不要）"""
    s = raw or ""
    k = s.find('"items"')
    i = s.find("[", k) if k >= 0 else -1
    if i < 0:
        return []
    dec, out, i = json.JSONDecoder(), [], i + 1
    while i < len(s):
        while i < len(s) and s[i] in " \t\r\n,":
            i += 1
        if i >= len(s) or s[i] != "{":
            break
        try:
            obj, i = dec.raw_decode(s, i)
        except ValueError:
            break
        out.append(obj)
    return out


def parse_takes(raw):
    m = re.search(r"\{.*\}", raw or "", re.S)
    items = None
    if m:
        try:
            items = json.loads(m.group(0)).get("items") or []
        except (ValueError, AttributeError):
            items = None
    if items is None:
        items = _whole(raw)                # 被截断了：前面完整的那几条救回来
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            n = int(it.get("n"))
            score = max(0, min(3, int(it.get("score") or 0)))
            mood = max(-2, min(2, int(it.get("mood") or 0)))
        except (TypeError, ValueError):
            continue
        loop = it.get("loop") if isinstance(it.get("loop"), dict) and str(it["loop"].get("text") or "").strip() else None
        out.append({"n": n, "take": str(it.get("take") or "")[:40], "score": score, "sensitive": bool(it.get("sensitive")),
                    "like": bool(it.get("like")), "comment": str(it.get("comment") or "")[:30],
                    "life": str(it.get("life") or "")[:40], "loop": loop, "mood": mood})
    return out


# ───────────────────────── 防漏（她发出去的东西 所有人都看得到） ─────────────────────────
def leak(text, names=(), terms=(), allow=()):
    """发出去之前查一遍：QQ 号、认识的人的名字、群名、私聊里才有的词 → 返回撞到了什么（没有就空）"""
    t = str(text or "")
    if re.search(r"(?<!\d)\d{6,11}(?!\d)", t):
        return "QQ 号"
    allow = {str(a) for a in allow if a}
    for n in sorted({str(x) for x in names if x}, key=len, reverse=True):
        if len(n) >= 2 and n not in allow and n in t:
            return f"名字「{n}」"
    for w in terms:
        w = str(w or "")
        if len(w) >= 3 and w in t and not any(w in a for a in allow):
            return f"「{w}」"
    return ""


def one_line(raw, limit=80):
    """模型写的一句：去掉引号、状态行、多余的换行"""
    t = str(raw or "").split("%%STATE%%")[0]
    t = re.sub(r"\[\[[^\]]*\]\]", "", t)
    t = " ".join(x.strip() for x in t.strip().splitlines() if x.strip())
    t = t.strip().strip("「」\"'“”")
    return t[:limit]


def status(s=None):
    """控制台 / 心跳用的一句话"""
    s = s or load()
    now = time.time()
    if not on():
        return "没开"
    if paused(s, now):
        return f"歇着（{s.get('pause_why', '')[:30]}，还剩 {int((float(s['pause_until']) - now) / 60)} 分钟）"
    day = (s.get("days") or {}).get(clock.strf("%Y-%m-%d")) or {}
    return (f"今天看了 {day.get('read', 0)} 条 · 赞 {day.get('like', 0)}/{Q.get('like_daily')} · "
            f"评 {day.get('comment', 0)}/{Q.get('comment_daily')} · 发 {day.get('post', 0)}/{Q.get('post_daily')} · "
            f"回评论 {day.get('reply', 0)}/{Q.get('reply_daily')}")
