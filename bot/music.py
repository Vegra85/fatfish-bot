#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现找现唱（10-26 小明：点了她不会的歌 去网易云/B站找完全对得上的原曲 截 10 来秒高潮唱一下）
  网易云：搜歌 → 歌名要完全对上（点了歌手的 歌手也要对上）、不要伴奏/翻唱/DJ 版
          → 官方外链能播就下（VIP 歌、只给大陆播的歌放不了 —— 不绕过 直接去 B站）
          → 歌词时间轴里重复最多的那几句 = 副歌 从那开始截
  B站：搜「歌名 歌手」→ 标题对得上、不是翻唱/伴奏/教学/合集的视频里挑播放最多的 → 音轨 → 按响度找最响的一段
  截 sec 秒（默认 12）→ 24k 单声道 wav（头尾淡入淡出）；整首下下来的音频剪完就删 只留这一小段
  找过的记在 data/song_online.json：下次点同一首直接用（不用再下）
"""
import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
CLIP_DIR = ROOT / "data" / "voice"
TMP_DIR = ROOT / "data" / "song_tmp"
CACHE = ROOT / "data" / "song_online.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
NE_HEADERS = {"User-Agent": UA, "Referer": "https://music.163.com/", "Cookie": "appver=2.9.11; os=pc"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_BYTES = 40 * 1024 * 1024
# 不是原曲的版本（点歌的人自己写了这些词的除外）
NOT_ORIG = re.compile(r"伴奏|纯音乐|\binst\b|instrumental|翻唱|\bcover\b|翻自|\bdj\b|remix|改编|教学|吉他|钢琴|尤克里里|口琴|小提琴|"
                      r"架子鼓|指弹|弹唱|合集|串烧|混剪|鬼畜|reaction|片段|\bcut\b|舞蹈|翻跳|练习|试唱|ai翻唱|ai版|ai cover|模仿|"
                      r"女声版|男声版|降调|升调|加速|倍速|慢速|铃声|教程|歌词版|动态歌词|循环|1小时|一小时|10小时", re.I)
PREFER = re.compile(r"原唱|官方|mv|高音质|无损|hi-?res|完整版|官方版|official|音乐", re.I)
# 10-26 晚：电脑不在中国大陆时 网易云对海外 IP 的搜索只回一串加密的字（{"abroad": true, "result": "35b1…"}）
# 以前把这串字当歌单读 → AttributeError 整个唱歌出错、她只说了句「咳咳」。不去破解它：认出来就直接走 B站 6 小时后再试
ABROAD_HOLD = 6 * 3600
BILI_TRY = 3                  # B站 第一个视频放不了（地区限制/拿不到音轨）就换下一个 最多试这么多个
_NE = {"abroad_until": 0.0, "told": 0.0}


def ne_abroad():
    """网易云这会儿是不是把这台电脑当海外 IP（只给加密的结果 用不了）"""
    return time.time() < _NE["abroad_until"]


def _norm(t):
    t = str(t or "").lower()
    t = re.sub(r"[（(【\[].*?[)）】\]]", "", t)                     # 括号里的（Live / 电影插曲 / 伴奏…）
    return re.sub(r"[\s《》<>「」『』\"'“”‘’·・,，.。!！?？~～\-_/|:：&+、]", "", t)


def parse(req):
    """「雨爱 - 杨丞琳」「杨丞琳的雨爱」「《雨爱》杨丞琳」→ (歌名, 歌手)"""
    r = str(req or "").strip()
    m = re.match(r"^\s*《([^《》]+)》\s*(.*)$", r)
    if m:
        return m.group(1).strip(), re.sub(r"^[-—|｜\s的]+", "", m.group(2)).strip()
    for sep in (" - ", " — ", "－", "—", " -", "- ", "|", "｜", "/"):
        if sep in r:
            a, b = r.split(sep, 1)
            return a.strip(), b.strip()
    m = re.match(r"^(.{1,8}?)的(.{1,30})$", r)
    if m and not re.search(r"(这|那|一|哪)", m.group(1)):
        return m.group(2).strip(), m.group(1).strip()
    return r, ""


def _ok_name(name, want, raw_req):
    """歌名完全对得上（去掉括号里的 Live/伴奏 之类再比）、而且不是伴奏翻唱版"""
    if _norm(name) != _norm(want):
        return False
    extra = str(name or "")
    return not (NOT_ORIG.search(extra) and not NOT_ORIG.search(raw_req or ""))


def _artist_ok(names, artist):
    if not artist:
        return True
    a = _norm(artist)
    return any(a and (a in _norm(n) or _norm(n) in a) for n in names if _norm(n))


# ───────────── 网易云 ─────────────
def ne_search(title, artist, client, log=print):
    if ne_abroad():
        return []
    q = f"{title} {artist}".strip()
    try:
        r = client.get("https://music.163.com/api/search/get/web",
                       params={"s": q, "type": 1, "offset": 0, "limit": 15}, headers=NE_HEADERS)
        j = r.json()
    except Exception as e:
        log(f"[唱歌] 网易云搜不了（{repr(e)[:80]}）")
        return []
    if not isinstance(j, dict):
        log(f"[唱歌] 网易云回的不是正常的搜索结果（{type(j).__name__}）")
        return []
    res = j.get("result")
    if j.get("abroad") or isinstance(res, str):
        now = time.time()
        _NE["abroad_until"] = now + ABROAD_HOLD
        if now - _NE["told"] > ABROAD_HOLD:
            _NE["told"] = now
            log("[唱歌] 网易云把这台电脑当成海外 IP：搜索结果是加密的（abroad）用不了 → 现找先走 B站（6 小时后再试网易云）")
        return []
    songs = (res.get("songs") if isinstance(res, dict) else None) or []
    out = []
    for s in songs:
        if not isinstance(s, dict):
            continue
        names = [a.get("name") or "" for a in s.get("artists") or s.get("ar") or [] if isinstance(a, dict)]
        if not _ok_name(s.get("name"), title, f"{title} {artist}"):
            continue
        if not _artist_ok(names, artist):
            continue
        out.append({"id": s.get("id"), "name": s.get("name"), "artists": names,
                    "dur": float(s.get("duration") or s.get("dt") or 0) / 1000.0, "fee": s.get("fee")})
    out.sort(key=lambda x: (bool(re.search(r"live|现场|演唱会", str(x["name"]), re.I)),   # 录音室原版排前面
                            x["fee"] not in (0, 8, None)))                                  # 再是免费能播的（其余照网易云的顺序）
    return out


def ne_audio(sid, client):
    """官方外链：免费歌给 mp3；VIP / 只给大陆播的会跳到 404 → None"""
    try:
        r = client.get(f"https://music.163.com/song/media/outer/url?id={int(sid)}.mp3", headers=NE_HEADERS,
                       follow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200 or "404" in str(r.url) or len(r.content) < 50_000:
        return None
    if "audio" not in r.headers.get("content-type", "") and not r.content[:3] in (b"ID3",) \
            and r.content[:2] not in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return None
    return r.content[:MAX_BYTES]


def ne_lyric(sid, client):
    try:
        j = client.get("https://music.163.com/api/song/lyric",
                       params={"id": int(sid), "lv": 1, "kv": 1, "tv": -1}, headers=NE_HEADERS).json()
        return ((j or {}).get("lrc") or {}).get("lyric") or ""
    except Exception:
        return ""


_TS = re.compile(r"\[(\d{1,2}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
CREDIT = re.compile(r"(作词|作曲|编曲|制作|词|曲|演唱|原唱|混音|录音|和声|吉他|贝斯|鼓|弦乐|监制|出品|OP|SP)\s*[:：]", re.I)


def chorus_from_lrc(lrc, dur=0):
    """歌词时间轴 → 副歌开始的秒数（重复最多的那几句第一次出现在前奏和第一段主歌之后的地方）；找不到 None"""
    lines = []
    for raw in (lrc or "").splitlines():
        stamps = _TS.findall(raw)
        text = _TS.sub("", raw).strip()
        if not stamps or not text or CREDIT.search(text[:12]):
            continue
        n = _norm(text)
        if len(n) < 3:
            continue
        for mm, ss in stamps:
            lines.append((int(mm) * 60 + float(ss.replace(":", ".")), n))
    if len(lines) < 6:
        return None
    lines.sort()
    count = {}
    for _, n in lines:
        count[n] = count.get(n, 0) + 1
    rep = {n for n, c in count.items() if c >= 2}
    if not rep:
        return None
    top = max(count[n] for n in rep)
    best = [n for n in rep if count[n] == top]
    total = dur or (lines[-1][0] + 20)
    for i, (t, n) in enumerate(lines):
        if n in best and t >= 0.18 * total:
            j = i                               # 往前找：同一段副歌里前面几句也是重复的 从第一句开始
            while j > 0 and lines[j - 1][1] in rep and t - lines[j - 1][0] < 25 and i - j < 4:
                j -= 1
            return max(0.0, lines[j][0] - 0.3)
    return max(0.0, next(t for t, n in lines if n in best) - 0.3)


# ───────────── B站 ─────────────
def _strip_html(t):
    return re.sub(r"<[^>]+>", "", str(t or ""))


def _dur_secs(d):
    if isinstance(d, (int, float)):
        return float(d)
    p = [int(x) for x in re.findall(r"\d+", str(d or ""))]
    return float(p[-3] * 3600 + p[-2] * 60 + p[-1]) if len(p) >= 3 else float(p[-2] * 60 + p[-1]) if len(p) == 2 else 0.0


async def bili_audio(c, params, page=1):
    """B站视频 → (音轨 bytes, 视频信息)。失败抛 RuntimeError（中文原因）"""
    import bili2
    v, why = None, ""
    for url, signed in (("https://api.bilibili.com/x/web-interface/wbi/view", True),
                        ("https://api.bilibili.com/x/web-interface/view", False)):
        try:
            r = await c.c.get(url + "?" + c.signed(params)) if signed else await c.c.get(url, params=params)
            j = bili2._j(r, "视频信息")
            if j.get("code") == 0 and j.get("data"):
                v = j["data"]
                break
            why = j.get("message") or j.get("code")
        except Exception as e:
            why = e
    if not v:
        raise RuntimeError(f"读不到这个视频（{why}）")
    pages = v.get("pages") or []
    cid = (pages[page - 1].get("cid") if 0 < page <= len(pages) else None) or v.get("cid")
    bvid = v.get("bvid")
    pj = None
    for url, signed in (("https://api.bilibili.com/x/player/wbi/playurl", True),
                        ("https://api.bilibili.com/x/player/playurl", False)):
        q = {"bvid": bvid, "cid": cid, "fnval": 16, "fnver": 0, "fourk": 1}
        try:
            r = await c.c.get(url + "?" + c.signed(q)) if signed else await c.c.get(url, params=q)
            j = bili2._j(r, "播放地址")
            if j.get("code") == 0 and ((j.get("data") or {}).get("dash") or {}).get("audio"):
                pj = j
                break
            why = j.get("message") or j.get("code")
        except Exception as e:
            why = e
    if not pj:
        raise RuntimeError(f"拿不到音轨（{why}）")
    best = sorted(pj["data"]["dash"]["audio"], key=lambda x: -int(x.get("bandwidth") or 0))[0]
    urls = [best.get("baseUrl") or best.get("base_url")] + list(best.get("backupUrl") or best.get("backup_url") or [])
    for u in [x for x in urls if x]:
        try:
            buf = bytearray()
            async with c.c.stream("GET", u, headers={"Referer": f"https://www.bilibili.com/video/{bvid}/",
                                                     "Accept": "*/*", "Range": "bytes=0-"}) as rr:
                if rr.status_code not in (200, 206):
                    why = f"HTTP {rr.status_code}"
                    continue
                async for ch in rr.aiter_bytes():
                    buf += ch
                    if len(buf) > MAX_BYTES * 2:
                        raise RuntimeError("音轨太大了（超过 80MB）")
            if len(buf) > 10000:
                return bytes(buf), v
            why = "下下来是空的"
        except RuntimeError:
            raise
        except Exception as e:
            why = repr(e)[:80]
    raise RuntimeError(f"音轨下不下来（{why}）")


async def bili_search(c, kw):
    await asyncio.sleep(0.3)
    import bili2
    import urllib.parse
    hdr = {"Referer": "https://search.bilibili.com/all?keyword=" + urllib.parse.quote(kw),
           "Origin": "https://search.bilibili.com"}
    j = bili2._j(await c.c.get("https://api.bilibili.com/x/web-interface/wbi/search/type?"
                               + c.signed({"search_type": "video", "keyword": kw, "page": 1, "page_size": 20,
                                           "platform": "pc", "web_location": 1430654}), headers=hdr), "搜索")
    return [{"bvid": x.get("bvid"), "title": _strip_html(x.get("title")), "up": x.get("author") or "",
             "play": int(x.get("play") or 0), "dur": _dur_secs(x.get("duration"))}
            for x in ((j.get("data") or {}).get("result") or [])]


def bili_pick(results, title, artist, raw_req=""):
    """搜到的视频里挑「就是这首歌的原唱」那个（最像的一个；没有 None）"""
    ranked = bili_rank(results, title, artist, raw_req)
    return ranked[0] if ranked else None


def bili_rank(results, title, artist, raw_req=""):
    """搜到的视频按「像原唱」排好：标题里有歌名；点了歌手的 标题或 UP 主里有歌手；不要翻唱伴奏教学合集
    → 第一个放不了（地区限制、拿不到音轨）就试下一个（10-26 晚）"""
    want = _norm(title)
    out = []
    for i, x in enumerate(results or []):
        if not isinstance(x, dict):
            continue
        t = x["title"]
        if want not in _norm(t):
            continue
        if NOT_ORIG.search(t) and not NOT_ORIG.search(raw_req):
            continue
        if x["dur"] and (x["dur"] < 60 or x["dur"] > 600):
            continue
        sc = len(str(x["play"]))                                     # 播放量的位数（数量级）
        if artist:
            if _artist_ok([t, x["up"]], artist):
                sc += 5
            else:
                continue
        if PREFER.search(t):
            sc += 2
        out.append((sc, i, x))
    out.sort(key=lambda z: (-z[0], z[1]))          # 分高的在前；一样高的按 B站 给的顺序（跟以前只挑一个时选的同一个）
    return [x for _, _, x in out]


# ───────────── 找高潮 / 剪 ─────────────
def _ff():
    return shutil.which("ffmpeg")


def loudest(path, sec, dur=0):
    """按响度找最响的一段（避开前奏和结尾）→ 开始秒数"""
    ff = _ff()
    if not ff:
        return 0.0
    try:
        import numpy as np
        r = subprocess.run([ff, "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                           capture_output=True, timeout=120, creationflags=NO_WINDOW)
        a = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32)
    except Exception:
        return 0.0
    if not len(a):
        return 0.0
    hop = 2000                                                       # 0.25 秒一帧
    n = len(a) // hop
    if n < 8:
        return 0.0
    e = np.sqrt((a[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    w = max(1, int(sec * 4))
    if n <= w:
        return 0.0
    cs = np.concatenate([[0.0], np.cumsum(e)])
    win = cs[w:] - cs[:-w]
    total = n / 4.0
    lo, hi = int(0.2 * total * 4), int(max(0.2, 0.85 * total - sec) * 4)
    if hi <= lo or hi > len(win):
        lo, hi = 0, len(win)
    i = int(np.argmax(win[lo:hi])) + lo
    return i / 4.0


def cut(src, start, sec, out):
    ff = _ff()
    if not ff:
        return None
    fo = min(1.5, sec / 4)
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, start):.2f}", "-t", f"{sec:.2f}",
           "-i", str(src), "-vn", "-ac", "1", "-ar", "24000", "-sample_fmt", "s16",
           "-af", f"afade=t=in:st=0:d=0.3,afade=t=out:st={sec - fo:.2f}:d={fo:.2f}", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=NO_WINDOW)
    except Exception:
        return None
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 2000:
        return None
    return out


# ───────────── 记过的 ─────────────
def _cache():
    try:
        d = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def _save_cache(d):
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(CACHE)
    except Exception:
        pass


def _key(title, artist):
    return _norm(title) + "|" + _norm(artist)


# ───────────── 入口 ─────────────
def fetch(req, log=print, sec=12, sessdata=""):
    """点的歌 → {"path", "title", "artist", "source", "start"}；找不到 / 放不了 → {"fail": 原因}"""
    title, artist = parse(req)
    if not _norm(title):
        return {"fail": "没说是哪首"}
    if not _ff():
        return {"fail": "没装 ffmpeg 剪不了"}
    key = _key(title, artist)
    cache = _cache()
    hit = cache.get(key) or (cache.get(_key(title, "")) if not artist else None)
    if hit and (CLIP_DIR / hit.get("clip", "")).is_file():
        log(f"[唱歌] 「{req}」以前找过：{hit.get('source')}《{hit.get('title')}》{hit.get('artist')}")
        return dict(hit, path=CLIP_DIR / hit["clip"])
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    tag = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    out = CLIP_DIR / f"song_net_{tag}.wav"
    why = []
    # 1) 网易云（在海外用不了时 ne_search 直接返回空 不去要）
    try:
        got = _ne_fetch(title, artist, req, sec, out, cache, key, why, log)
    except Exception as e:                      # 网易云这边出什么错都别让唱歌整个挂掉 接着去 B站
        why.append(f"网易云出错：{repr(e)[:60]}")
        got = None
    if got:
        return got
    # 2) B站
    try:
        meta = asyncio.run(_bili_fetch(title, artist, req, sec, out, sessdata, log))
    except RuntimeError as e:
        why.append(f"B站：{e}")
        meta = None
    except Exception as e:
        why.append(f"B站出错：{repr(e)[:80]}")
        meta = None
    if meta:
        cache[key] = meta
        _save_cache(cache)
        return dict(meta, path=out)
    log(f"[唱歌] 网上找「{req}」没找到能唱的：" + "；".join(why[-4:]))
    return {"fail": "；".join(why[-3:]) or "没找到"}


def _ne_fetch(title, artist, req, sec, out, cache, key, why, log):
    """网易云：歌名对上的免费原唱 → 剪好 → {…, "path"}；不行返回 None（原因追加进 why）"""
    with httpx.Client(timeout=15, headers={"User-Agent": UA}) as client:
        cands = ne_search(title, artist, client, log)
        if not cands:
            why.append("网易云在海外用不了（只给加密的结果）" if ne_abroad() else "网易云没有歌名对得上的")
        for s in cands[:4]:
            audio = ne_audio(s["id"], client)
            if not audio:
                why.append(f"网易云《{s['name']}》放不了（VIP 或者只给大陆播）")
                continue
            tmp = TMP_DIR / f"ne_{s['id']}.mp3"
            tmp.write_bytes(audio)
            try:
                st = chorus_from_lrc(ne_lyric(s["id"], client), s["dur"])
                how = "歌词里的副歌"
                if st is None:
                    st, how = loudest(tmp, sec, s["dur"]), "最响的一段"
                if cut(tmp, st, sec, out):
                    meta = {"title": s["name"], "artist": "、".join(s["artists"]), "source": "网易云",
                            "id": s["id"], "start": round(st, 1), "how": how, "clip": out.name, "t": time.time()}
                    cache[key] = meta
                    _save_cache(cache)
                    log(f"[唱歌] 网上找「{req}」→ 网易云《{meta['title']}》{meta['artist']}（{how} 从 {st:.0f} 秒开始 {sec} 秒）")
                    return dict(meta, path=out)
                why.append("剪不出来")
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return None


async def _bili_fetch(title, artist, req, sec, out, sessdata, log):
    import bili2
    c = bili2.Client(sessdata)
    try:
        try:
            await c.ready()
        except Exception as e:
            raise RuntimeError(f"连不上 / 被拦了（{e}）")
        res = await bili_search(c, f"{title} {artist}".strip())
        cands = bili_rank(res, title, artist, req)
        if not cands:
            raise RuntimeError("没有标题对得上的原唱视频")
        data, whys = None, []
        for x in cands[:BILI_TRY]:                  # 第一个放不了（地区限制 / 拿不到音轨）换下一个
            try:
                data, v = await bili_audio(c, {"bvid": x["bvid"]})
                break
            except RuntimeError as e:
                whys.append(f"{x['bvid']} {e}")
                data = None
        if data is None:
            raise RuntimeError("；".join(whys[-2:]) or "音轨下不下来")
        if whys:
            log(f"[唱歌] B站前面 {len(whys)} 个视频放不了（{whys[0][:60]}）→ 用的是第 {len(whys) + 1} 个")
    finally:
        try:
            await c.close()
        except Exception:
            pass
    tmp = TMP_DIR / f"bili_{x['bvid']}.m4a"
    tmp.write_bytes(data)
    try:
        st = loudest(tmp, sec)
        if not cut(tmp, st, sec, out):
            raise RuntimeError("剪不出来")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    # 歌手只写点歌的人说了的（UP 主不一定是唱的人：雨爱那个视频是 UP「JLRS-LeoFM」传的杨丞琳原唱）
    meta = {"title": title, "artist": artist or "", "source": "B站", "id": x["bvid"],
            "video": x["title"][:60], "up": x["up"], "start": round(st, 1), "how": "最响的一段",
            "clip": out.name, "t": time.time()}
    log(f"[唱歌] 网上找「{req}」→ B站《{x['title'][:30]}》UP {x['up']}（最响的一段 从 {st:.0f} 秒开始 {sec} 秒）")
    return meta
