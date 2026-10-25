#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
刷 B 站 2.0（借鉴 dsh-bili-subagent）
  不只看热门榜：按她的兴趣、群里最近的话题、当前热搜去搜视频
  过门槛（播放量、发布时间）→ 挑两个没看过的 → 看简介、热评、弹幕（填了 SESSDATA 还能读字幕）
  → 写一份「观看报告」：讲了什么、她怎么看、值不值得分享、分享时说什么
  报告存 data/bili_reports/日期.md；感想进 trends.json 给聊天和冒泡用
"""
import asyncio
import hashlib
import hmac
import json
import random
import re
import time
import urllib.parse
import uuid
from pathlib import Path

import httpx

import bili

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "data" / "bili_reports"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
         33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
         61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62,
         11, 36, 20, 34, 44, 52]
DEFAULT_INTERESTS = ["米饭 美食", "番剧 推荐", "搞笑 日常", "鲸鱼 海洋", "DeepSeek", "大肥鱼",
                     "猫 哈基米", "游戏 整活", "AI 新闻"]


class Blocked(Exception):
    """B站风控（412 / 返回网页不是数据）"""


def _j(r, where=""):
    if r.status_code == 412 or "json" not in r.headers.get("content-type", ""):
        raise Blocked(f"{where} HTTP {r.status_code}".strip())
    try:
        j = r.json()
    except ValueError:
        raise Blocked(f"{where} HTTP {r.status_code} 非JSON".strip())
    if isinstance(j, dict) and j.get("code") in (-412, -352):       # 200 但内容是「请求被拦截」
        raise Blocked(f"{where} code {j.get('code')}".strip())
    return j


COOKIE_FILE = ROOT / "data" / "bili_cookies.json"
KEEP = ("buvid3", "buvid4", "b_nut", "_uuid", "bili_ticket", "bili_ticket_expires")


def _uuid_cookie():
    h = uuid.uuid4().hex.upper()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}{int(time.time() * 1000) % 100000:05d}infoc"


def _lsid():
    return f"{uuid.uuid4().hex[:8].upper()}_{int(time.time() * 1000):X}"


def _strip(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h or "")).strip()


def _clean_sessdata(v):
    """从浏览器复制时常带上 SESSDATA= 前缀、分号、引号；逗号要保持编码后的样子"""
    v = (v or "").strip().strip('"').strip("'").strip()
    if v.upper().startswith("SESSDATA="):
        v = v[9:]
    return v.split(";")[0].strip().replace(",", "%2C")


async def login_status(sessdata):
    """填了 cookie 的话 看看登没登上（只读一下账号信息 不做别的）"""
    if not _clean_sessdata(sessdata):
        return "没填 sessdata（不登录：读不了字幕 也更容易被限流）"
    c = Client(sessdata)
    try:
        d = (_j(await c.c.get("https://api.bilibili.com/x/web-interface/nav")).get("data")) or {}
        if d.get("isLogin"):
            return f"已登录 {d.get('uname', '?')}（能读字幕了）"
        return "cookie 无效或者过期了（重新从浏览器复制 SESSDATA）"
    except Blocked as e:
        return f"暂时连不上（B站限流 {e}）先照常跑 过会儿自动再试"
    except Exception as e:
        return f"检查失败 {repr(e)[:60]}"
    finally:
        await c.close()


class Client:
    """像一个真浏览器那样带全 cookie（buvid3/buvid4/b_nut/_uuid/bili_ticket）再调接口
    只带 UA 直接调搜索接口 很容易 412；cookie 存起来下次接着用（像同一台电脑回来刷）"""

    def __init__(self, sessdata=""):
        self.c = httpx.AsyncClient(headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/",
                                            "Origin": "https://www.bilibili.com",
                                            "Accept": "application/json, text/plain, */*",
                                            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"},
                                   timeout=20, follow_redirects=True)
        for k, v in self._saved().items():
            self.c.cookies.set(k, v, domain=".bilibili.com")
        sessdata = _clean_sessdata(sessdata)
        if sessdata:
            self.c.cookies.set("SESSDATA", sessdata, domain=".bilibili.com")
        self.mixin = None

    @staticmethod
    def _saved():
        try:
            d = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if time.time() > float(d.get("bili_ticket_expires") or 0):
            d.pop("bili_ticket", None)
            d.pop("bili_ticket_expires", None)
        return {k: str(v) for k, v in d.items() if k in KEEP and v}

    def _ck(self, name):
        """读一个 cookie（同名的有好几个也不报错 取第一个）"""
        jar = getattr(self.c.cookies, "jar", None)
        if jar is not None:
            for c in jar:
                if c.name == name:
                    return c.value
            return None
        return self.c.cookies.get(name)

    def _save(self):
        try:
            d = {k: self._ck(k) for k in KEEP if self._ck(k)}
            COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
            COOKIE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def forget():
        """被拦了：下次换一套新身份"""
        try:
            COOKIE_FILE.unlink()
        except Exception:
            pass

    async def _ticket(self):
        """B站网页自己也会拿的 bili_ticket（HMAC 签名）"""
        ts = int(time.time())
        sign = hmac.new(b"XgwSnGZ1p", f"ts{ts}".encode(), hashlib.sha256).hexdigest()
        r = await self.c.post("https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket",
                              params={"key_id": "ec02", "hexsign": sign, "context[ts]": ts, "csrf": ""})
        d = (r.json().get("data") or {}) if r.status_code == 200 else {}
        if d.get("ticket"):
            self.c.cookies.set("bili_ticket", d["ticket"], domain=".bilibili.com")
            self.c.cookies.set("bili_ticket_expires", str(ts + int(d.get("ttl") or 259200) - 600),
                               domain=".bilibili.com")

    async def ready(self):
        if not (self._ck("buvid3") and self._ck("b_nut")):
            try:                                   # 先打开首页 拿 buvid3 / b_nut
                await self.c.get("https://www.bilibili.com/",
                                 headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
            except Exception:
                pass
        if not self._ck("buvid4"):
            try:
                spi = (await self.c.get("https://api.bilibili.com/x/frontend/finger/spi")).json().get("data") or {}
                if spi.get("b_3") and not self._ck("buvid3"):
                    self.c.cookies.set("buvid3", spi["b_3"], domain=".bilibili.com")
                if spi.get("b_4"):
                    self.c.cookies.set("buvid4", spi["b_4"], domain=".bilibili.com")
            except Exception:
                pass
        if not self._ck("_uuid"):
            self.c.cookies.set("_uuid", _uuid_cookie(), domain=".bilibili.com")
        self.c.cookies.set("b_lsid", _lsid(), domain=".bilibili.com")
        if not self._ck("bili_ticket"):
            try:
                await self._ticket()
            except Exception:
                pass
        await asyncio.sleep(random.uniform(0.5, 1.5))
        nav = _j(await self.c.get("https://api.bilibili.com/x/web-interface/nav"), "nav")
        w = (nav.get("data") or {}).get("wbi_img") or {}
        raw = w["img_url"].rsplit("/", 1)[-1].split(".")[0] + w["sub_url"].rsplit("/", 1)[-1].split(".")[0]
        self.mixin = "".join(raw[i] for i in MIXIN)[:32]
        self._save()
        return bool((nav.get("data") or {}).get("isLogin"))

    def signed(self, params):
        p = dict(sorted({**params, "wts": int(time.time())}.items()))
        q = urllib.parse.urlencode({k: re.sub(r"[!'()*]", "", str(v)) for k, v in p.items()})
        return q + "&w_rid=" + hashlib.md5((q + self.mixin).encode()).hexdigest()

    async def search(self, kw):
        await asyncio.sleep(random.uniform(0.8, 2.0))
        hdr = {"Referer": "https://search.bilibili.com/all?keyword=" + urllib.parse.quote(kw),
               "Origin": "https://search.bilibili.com"}
        j = _j(await self.c.get("https://api.bilibili.com/x/web-interface/wbi/search/type?"
                              + self.signed({"search_type": "video", "keyword": kw, "page": 1,
                                             "page_size": 20, "platform": "pc", "web_location": 1430654}),
                              headers=hdr), "搜索")
        out = []
        for v in ((j.get("data") or {}).get("result") or []):
            out.append({"bvid": v.get("bvid"), "aid": v.get("aid"), "title": _strip(v.get("title")),
                        "up": v.get("author"), "play": int(v.get("play") or 0),
                        "pubdate": int(v.get("pubdate") or 0), "desc": _strip(v.get("description"))[:200],
                        "pic": "https:" + v["pic"] if str(v.get("pic", "")).startswith("//") else v.get("pic", "")})
        return out

    async def popular(self):
        await asyncio.sleep(random.uniform(0.5, 1.5))
        j = _j(await self.c.get("https://api.bilibili.com/x/web-interface/popular", params={"ps": 20}), "热门")
        return [{"bvid": v.get("bvid"), "aid": v.get("aid"), "title": v.get("title", ""),
                 "up": (v.get("owner") or {}).get("name"), "play": (v.get("stat") or {}).get("view", 0),
                 "pubdate": v.get("pubdate", 0), "desc": (v.get("desc") or "")[:200],
                 "pic": (v.get("pic") or "").replace("http://", "https://")}
                for v in ((j.get("data") or {}).get("list") or [])]

    async def detail(self, v):
        await asyncio.sleep(random.uniform(0.6, 1.6))
        d = _j(await self.c.get("https://api.bilibili.com/x/web-interface/view",
                              params={"bvid": v["bvid"]}), "视频详情").get("data") or {}
        v["cid"] = d.get("cid")
        v["desc"] = (d.get("desc") or v.get("desc") or "")[:300]
        v["tname"] = d.get("tname", "")
        v["duration"] = d.get("duration", 0)
        # 热评
        try:
            r = _j(await self.c.get("https://api.bilibili.com/x/v2/reply/main",
                                  params={"type": 1, "oid": v["aid"], "mode": 3, "ps": 10}))
            reps = ((r.get("data") or {}).get("top_replies") or []) + ((r.get("data") or {}).get("replies") or [])
            v["comments"] = [f"[{c.get('like', 0)}赞] {(c.get('content') or {}).get('message', '')[:120]}"
                             for c in reps[:8]]
        except Exception:
            v["comments"] = []
        # 弹幕（不用登录）
        try:
            xml = (await self.c.get("https://api.bilibili.com/x/v1/dm/list.so", params={"oid": v["cid"]})).text
            dms = re.findall(r"<d [^>]*>([^<]*)</d>", xml)
            freq = {}
            for x in dms:
                if 3 <= len(x) <= 30:
                    freq[x] = freq.get(x, 0) + 1
            v["danmaku"] = [k for k, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:25]]
        except Exception:
            v["danmaku"] = []
        # 字幕（要登录）
        v["subtitle"] = ""
        try:
            p = _j(await self.c.get("https://api.bilibili.com/x/player/wbi/v2?"
                                  + self.signed({"bvid": v["bvid"], "cid": v["cid"]})))
            subs = ((p.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
            if subs:
                u = subs[0].get("subtitle_url", "")
                u = "https:" + u if u.startswith("//") else u
                body = _j(await self.c.get(u)).get("body") or []
                v["subtitle"] = " ".join(x.get("content", "") for x in body)[:2500]
        except Exception:
            pass
        return v

    async def close(self):
        await self.c.aclose()


REPORT_PROMPT = """你在替「大肥鱼」（住在电脑里的鲸鱼娘 嘴硬心软 爱吃白饭 爱玩梗）刷 B 站。她这轮因为「{why}」点开了下面这些视频。
根据给的材料写观看报告（没有的东西别编 没字幕就只根据简介/评论/弹幕说）。

{videos}

只输出 JSON：
{"items": [{"bvid": "BV号",
  "about": "这个视频讲了什么（40字内 客观）",
  "take": "她看完的感想 用她的口吻 30字内 不用逗号句号 用空格断句",
  "share_score": 0到3（3=真好玩 很想马上丢到群里 / 0=无聊）,
  "share_line": "如果要丢到群里 她会配的一句话（20字内）"}]}"""


def _fmt(v):
    s = f"【{v['bvid']}】{v['title']} | UP:{v.get('up')} | {v.get('tname', '')} | 播放{v.get('play')}\n简介：{v.get('desc', '')}"
    if v.get("subtitle"):
        s += f"\n字幕（节选）：{v['subtitle'][:1500]}"
    if v.get("comments"):
        s += "\n热评：" + " / ".join(v["comments"][:6])
    if v.get("danmaku"):
        s += "\n高频弹幕：" + " / ".join(v["danmaku"][:15])
    return s


async def scroll_session(llm_fn, cfg, hot_terms=None, log=print):
    t = bili.load_trends()
    if time.time() < t.get("blocked_until", 0):
        return None
    seen = set(t.get("seen", []))
    c = Client(cfg.get("sessdata", ""))
    try:
        login = await c.ready()
        interests = cfg.get("interests") or DEFAULT_INTERESTS
        hot = [k for k in (t.get("keywords") or []) if k][:10]
        r = random.random()
        try:
            if r < 0.5 or not (hot or hot_terms):
                why = random.choice(interests)
                cands = await c.search(why)
            elif r < 0.8:
                why = random.choice((hot_terms or []) + hot)
                cands = await c.search(why)
                why = f"群里/热搜在聊「{why}」"
            else:
                why = "随手刷了刷热门"
                cands = await c.popular()
        except Blocked as e:
            if why == "随手刷了刷热门":
                raise
            log(f"[B站] 搜索被拦了（{e}）改刷热门")
            why = "随手刷了刷热门"
            cands = await c.popular()
        now = time.time()
        min_play = int(cfg.get("min_views", 30000))
        min_age = float(cfg.get("min_age_hours", 12)) * 3600
        max_age = float(cfg.get("max_age_days", 90)) * 86400
        ok = [v for v in cands if v.get("bvid") and v["bvid"] not in seen and v.get("play", 0) >= min_play
              and (not v.get("pubdate") or min_age <= now - v["pubdate"] <= max_age)]
        if not ok:
            log(f"[B站] 「{why}」没刷到够格的视频（候选 {len(cands)}）")
            return None
        pick = random.sample(ok[:10], min(2, len(ok[:10])))
        got = []
        for v in pick:
            try:
                got.append(await c.detail(v))
            except Blocked as e:
                log(f"[B站] 读视频详情被拦了（{e}）跳过这个")
        if not got:
            raise Blocked("视频详情全被拦")
        pick = got
    except Blocked as e:
        Client.forget()
        t = bili.load_trends()
        t["blocked_until"] = time.time() + 3600
        bili.save_trends(t)
        log(f"[B站] 被风控了（{e}）歇一小时再刷")
        return None
    finally:
        await c.close()
    prompt = REPORT_PROMPT.replace("{why}", why).replace("{videos}", "\n\n".join(_fmt(v) for v in pick))
    raw = await llm_fn("你在帮一个角色写她刷视频的观看报告。只输出 JSON。",
                       [{"role": "user", "content": prompt}], temperature=0.8, max_tokens=700)
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        items = (json.loads(m.group(0)) if m else {}).get("items") or []
    except ValueError:
        log("[B站] 观看报告格式坏了 这轮跳过")
        return None
    by = {v["bvid"]: v for v in pick}
    t = bili.load_trends()
    takes = []
    REPORTS.mkdir(parents=True, exist_ok=True)
    md = [f"\n## {time.strftime('%H:%M')} {why}{'（已登录 读了字幕）' if login else ''}"]
    for it in items:
        v = by.get(it.get("bvid"))
        if not v:
            continue
        take = str(it.get("take", ""))[:60]
        t.setdefault("items", []).append({
            "ts": time.time(), "bvid": v["bvid"], "videos": [v["title"][:50]], "top": v["title"][:50],
            "pic": v.get("pic", ""), "comments": v.get("comments", [])[:4], "take": take,
            "about": str(it.get("about", ""))[:80], "share_score": int(it.get("share_score") or 0),
            "share_line": str(it.get("share_line", ""))[:40]})
        takes.append(take)
        md.append(f"- [{v['title']}](https://www.bilibili.com/video/{v['bvid']}) · {v.get('up')} · 播放{v.get('play')}\n"
                  f"  - 讲了：{it.get('about', '')}\n  - 她：{take}\n  - 分享指数 {it.get('share_score', 0)}：{it.get('share_line', '')}")
        seen.add(v["bvid"])
    if not takes:
        return None
    t["seen"] = list(seen)[-300:]
    day = time.strftime("%Y-%m-%d")
    t.setdefault("sessions", {})[day] = t.get("sessions", {}).get(day, 0) + 1
    t["updated"] = time.time()
    bili.save_trends(bili.prune(t))
    with (REPORTS / f"{day}.md").open("a", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    log(f"[B站] 因为「{why}」看了 {len(takes)} 个 → {' / '.join(takes)[:80]}")
    return " / ".join(takes)
