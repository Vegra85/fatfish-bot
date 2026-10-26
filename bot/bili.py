#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大肥鱼的 B 站冲浪模块
  抓取热门 / 热搜 / 高赞评论  →  让她自己"看"  →  提炼成她的话
"""
import json
import random
import time
from pathlib import Path

import httpx

import clock

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}

ROOT = Path(__file__).resolve().parent
TRENDS = ROOT / "data" / "trends.json"


class Bili:
    def __init__(self):
        self.c = httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True)
        self.ready = False

    async def ensure(self):
        if self.ready:
            return
        try:
            r = await self.c.get("https://api.bilibili.com/x/frontend/finger/spi")
            self.c.cookies.set("buvid3", r.json()["data"]["b_3"], domain=".bilibili.com")
            self.ready = True
        except Exception:
            pass

    async def popular(self, n=12):
        await self.ensure()
        r = await self.c.get("https://api.bilibili.com/x/web-interface/popular",
                             params={"ps": n, "pn": 1})
        out = []
        for v in (r.json().get("data") or {}).get("list", [])[:n]:
            out.append({
                "aid": v.get("aid"),
                "bvid": v.get("bvid"),
                "title": v.get("title", ""),
                "up": (v.get("owner") or {}).get("name", ""),
                "view": (v.get("stat") or {}).get("view", 0),
                "danmaku": (v.get("stat") or {}).get("danmaku", 0),
                "tname": v.get("tname", ""),
                "pic": (v.get("pic") or "").replace("http://", "https://"),
            })
        return out

    async def hot_search(self, n=20):
        await self.ensure()
        r = await self.c.get("https://api.bilibili.com/x/web-interface/search/square",
                             params={"limit": n})
        d = (r.json().get("data") or {}).get("trending") or {}
        return [t.get("keyword", "") for t in (d.get("list") or [])[:n] if t.get("keyword")]

    async def comments(self, aid, n=6):
        await self.ensure()
        try:
            r = await self.c.get("https://api.bilibili.com/x/v2/reply",
                                 params={"type": 1, "oid": aid, "sort": 2, "ps": n, "pn": 1})
            reps = (r.json().get("data") or {}).get("replies") or []
        except Exception:
            return []
        out = []
        for rep in reps[:n]:
            txt = (rep.get("content") or {}).get("message", "").replace("\n", " ").strip()
            sub = ""
            sr = rep.get("replies") or []
            if sr:
                sub = (sr[0].get("content") or {}).get("message", "").replace("\n", " ").strip()
            if txt:
                out.append({"like": rep.get("like", 0), "text": txt[:120], "sub": sub[:80]})
        return out

    async def close(self):
        await self.c.aclose()


# ───────────────────────── 存储 ─────────────────────────

def load_trends():
    if TRENDS.exists():
        try:
            return json.loads(TRENDS.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"items": [], "keywords": [], "sessions": {}, "updated": 0}


def save_trends(t):
    TRENDS.parent.mkdir(parents=True, exist_ok=True)
    TRENDS.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")


def latest_pic():
    """最近一轮刷到的东西的封面图"""
    t = load_trends()
    for i in reversed(t.get("items", [])):
        if i.get("pic"):
            return i["pic"]
    return None


def prune(t, hours=72):
    cut = time.time() - hours * 3600
    t["items"] = [i for i in t.get("items", []) if i.get("ts", 0) >= cut][-60:]
    return t


def trends_block(limit=4):
    """给系统提示词用的一段"""
    t = load_trends()
    items = [i for i in t.get("items", []) if i.get("take")][-limit:]
    if not items:
        return ""
    lines = ["\n# 你最近在 B 站刷到的（可以随口提，但别硬聊）"]
    import clock
    for i in items:
        line = f"- {clock.day_ago(i['ts'])}刷到：{i['take']}"     # 按天说（10-26 前缀缓存：每小时变一次的字会让提示词缓存失效）
        if i.get("bvid") and i.get("share_score", 0) >= 2:
            line += f"（视频《{i.get('top', '')}》 想分享就带上 https://b23.tv/{i['bvid']}）"
        lines.append(line)
    kw = [k for k in t.get("keywords", []) if k][:6]
    if kw:
        lines.append("- 现在热搜上挂着：" + " ／ ".join(kw))
    return "\n".join(lines) + "\n"


# ───────────────────────── 刷一轮 ─────────────────────────

SCROLL_PROMPT = """你在替一个叫「大肥鱼」的 20 岁鲸鱼娘（住在电脑里的 AI）模拟"刷B站"。

她刚刚刷到下面这些东西，请用**她本人的口吻**写 2~3 条短句，记下她看到了什么、怎么想的。

要求：
- 每条不超过 30 字
- 不要用逗号和句号，用空格断句
- 不要列点、不要复述标题、不要说"我刷到了某某视频"
- 挑最有意思的说，无聊的直接不提
- 语气必须是她：软乎乎有点萌 偶尔小傲娇 短
- 只用 ||| 分隔多条，不要输出别的

【视频】
{videos}

【评论区神回复】
{comments}

【当前热搜】
{keywords}
"""


async def scroll_session(llm_fn, log=print):
    """刷一轮：抓内容 → 让她写点感想 → 存起来"""
    b = Bili()
    try:
        vids = await b.popular(12)
        if not vids:
            return None
        picked = random.sample(vids, min(5, len(vids)))

        # 只对 2 个视频抓评论，省时间也省请求
        cmt_targets = random.sample(picked, min(2, len(picked)))
        cms = []
        for v in cmt_targets:
            cs = await b.comments(v["aid"], 5)
            for c in cs:
                cms.append(f"[{v['title'][:20]}] {c['text']}")

        kws = await b.hot_search(20)

        vtext = "\n".join(
            f"- {v['title'][:40]} | UP:{v['up']} | {v['tname']} | 播放{v['view']}"
            for v in picked)
        ctext = "\n".join(f"- {c}" for c in cms[:10]) or "（没看到有意思的）"
        ktext = " ／ ".join(kws[:12])

        prompt = (SCROLL_PROMPT.replace("{videos}", vtext)
                  .replace("{comments}", ctext)
                  .replace("{keywords}", ktext))
        take = await llm_fn("你是在帮一个角色记录她刷B站的见闻。只输出她要说的那几句话。",
                            [{"role": "user", "content": prompt}],
                            temperature=1.0, max_tokens=220)
        take = (take or "").strip()
        if not take:
            return None

        t = load_trends()
        t.setdefault("items", []).append({
            "ts": time.time(),
            "videos": [v["title"][:50] for v in picked],
            "top": picked[0]["title"][:50] if picked else "",
            "pic": (picked[0].get("pic") or "") if picked else "",
            "comments": cms[:6],
            "take": take.replace("|||", " / ").replace("\n", " / ")[:200],
        })
        t["keywords"] = kws[:20]
        day = clock.strf("%Y-%m-%d")
        t.setdefault("sessions", {})
        t["sessions"][day] = t["sessions"].get(day, 0) + 1
        t["updated"] = time.time()
        save_trends(prune(t))
        log(f"[B站] 刷完一轮 → {take.replace(chr(124) * 3, ' / ')[:80]}")
        return take
    except Exception as e:
        log("[B站] 出错:", repr(e)[:200])
        return None
    finally:
        await b.close()
