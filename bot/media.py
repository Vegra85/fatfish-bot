#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
让 DeepSeek（deepseek-flash / V4.1 Flash）直接看原图
  最近 15 分钟里的图：下载原图 → 作为 image_url 附在发给她的最后一条消息上
  视频：DeepSeek 不收视频 → 用 ffmpeg 均匀截几帧当图片给她看（没装 ffmpeg 就只显示 [视频]）
"""
import asyncio
import base64
import re
import shutil
import tempfile
import time
from pathlib import Path

import httpx

import imgprep
import vision

MAX_IMAGES = 4         # 一次最多附几张原图
MAX_FRAMES = 6         # 视频截几帧
RECENT_SEC = 900
MAX_BYTES = 8_000_000
AGAIN_SEC = 600        # 追问刚才那张图（10-28）：10 分钟内、最近 8 条里的图 再给她看一眼
# 他还在问刚才那张图（「你认为正确答案是」「但是你修改了原文」「上面写的啥」）
# 10-28 审查 reply-4：以前「刚才 / 你觉得 / 哪个 / 是什么 / 不对 / 字」这种泛词也算 —— 发过图 10 分钟内说「你觉得我今天穿什么好」
#   「刚才我妈打电话了」都把旧图再附上、还说「他还在问它」；现在只认指着图说的，「对吗 / 改了 / 写的」要紧接着图那一轮
REFER = re.compile(r"图|照片|截图|这张|那张|原文|原图|答案|正确|上面写|再看|仔细看|看清|看错|哪两个|哪几个")
REFER_NEAR = re.compile(r"对吗|对不对|改了|改成|写的|写着")
VIDEO_MAX_BYTES = 60_000_000
_cache = {}            # key -> (mime, b64, note)  note=动图说明（静图为空）


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


async def _download(url, limit):
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        if len(r.content) > limit:
            return None
        return r.content


async def image_b64(key, url):
    if key in _cache:
        return _cache[key]
    data = await _download(url, MAX_BYTES)
    mime = _mime(data or b"")
    if not mime:
        return None
    data, mime, note = await asyncio.to_thread(imgprep.prep, data, mime, 2048)   # 动图拼帧 / 太大缩小
    out = (mime, base64.b64encode(data).decode(), note)
    _cache[key] = out
    if len(_cache) > 40:
        _cache.pop(next(iter(_cache)))
    return out


def ffmpeg_ok():
    return bool(shutil.which("ffmpeg"))


async def _run(*args, timeout=60):
    p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.PIPE)
    out, _ = await asyncio.wait_for(p.communicate(), timeout)
    return out.decode("utf-8", "ignore")


async def video_frames(key, url, log=print):
    """下载视频 → 均匀截 MAX_FRAMES 帧 jpg"""
    ck = "v:" + key
    if ck in _cache:
        return _cache[ck]
    if not ffmpeg_ok():
        log("[视频] 没装 ffmpeg，看不了视频（运行 安装视频支持.bat）")
        return []
    data = await _download(url, VIDEO_MAX_BYTES)
    if not data:
        return []
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.mp4"
        src.write_bytes(data)
        dur = 0.0
        if shutil.which("ffprobe"):
            try:
                dur = float((await _run("ffprobe", "-v", "error", "-show_entries", "format=duration",
                                         "-of", "default=nw=1:nk=1", str(src))).strip() or 0)
            except Exception:
                dur = 0.0
        step = max(0.5, (dur or 18) / MAX_FRAMES)
        await _run("ffmpeg", "-loglevel", "error", "-i", str(src), "-vf",
                   f"fps=1/{step:.2f},scale=640:-2", "-frames:v", str(MAX_FRAMES),
                   str(Path(td) / "f_%02d.jpg"))
        frames = [("image/jpeg", base64.b64encode(p.read_bytes()).decode())
                  for p in sorted(Path(td).glob("f_*.jpg"))]
    log(f"[视频] 截了 {len(frames)} 帧（时长约 {dur:.0f}s）")
    _cache[ck] = frames
    return frames


def again_entry(history, text, only_uid=None, now=None):
    """他还在追问刚才那张（已经回过的）图 → 那一条（10-28 小明：她第一轮看着原图答对了 追问时只剩文字描述 就答错了还被带着改口）
    条件：这句话指着图 / 原文 / 答案在说；那张图在最近 8 条里、10 分钟内；群里只看他自己发的
    「对吗 / 改了 / 写的」这种：图得是他上一句（中间聊过别的就不算 10-28 审查 reply-4）"""
    if not text:
        return None
    strong = bool(REFER.search(str(text)))
    if not strong and not REFER_NEAR.search(str(text)):
        return None
    now = now or time.time()
    for h in reversed(history[-8:]):
        if h.get("role") != "user" or not h.get("replied") or (only_uid is not None and str(h.get("uid")) != str(only_uid)):
            continue
        if h.get("imgs") and now - h.get("ts", 0) < AGAIN_SEC:
            return h
        if not strong:
            return None
    return None


async def attach(msgs, history, log=print, only_uid=None, again=None):
    """把最近的原图/视频帧附到最后一条 user 消息上。没有就返回 None
    only_uid：群里只附这个人发的（她这一条回的是他 别人的图有文字描述就够了 附原图会把她的注意力拽到别人的图上）
    again：他这一轮说的话 —— 在追问刚才那张图就把那张再给她看一眼（10-28）"""
    now = time.time()
    # 只给她看「还没回过的」新图 —— 回过的旧图已经有文字描述了 再附原图她会反复去评论
    recent = [h for h in history[-15:] if h.get("role") == "user" and not h.get("replied")
              and now - h.get("ts", 0) < RECENT_SEC and (h.get("imgs") or h.get("vids"))
              and (only_uid is None or str(h.get("uid")) == str(only_uid))]
    old = again_entry(history, again, only_uid, now) if again and not recent else None
    if old:
        recent = [old]
    if not recent:
        return None
    parts = []
    n_img = 0
    for h in reversed(recent):                     # 越新越优先
        for key, url, *_ in (h.get("vids") or [])[:1]:
            try:
                frames = await video_frames(key, url, log)
            except Exception as e:
                log("[视频] 处理失败:", repr(e)[:120])
                frames = []
            if frames:
                parts.append({"type": "text", "text": f"↓ {h['name']} 发的视频（按时间顺序截了 {len(frames)} 帧）"})
                parts += [{"type": "image_url", "image_url": {"url": f"data:{m};base64,{b}"}}
                          for m, b in frames]
        for key, url, *_ in (h.get("imgs") or []):
            if n_img >= MAX_IMAGES or not url:
                continue
            try:
                got = await image_b64(key, url)
            except Exception as e:
                log("[原图] 下载失败:", repr(e)[:120])
                got = None
            if got:
                n_img += 1
                note = got[2] if len(got) > 2 else ""
                if h is old:               # 10-28 审查 reply-4：不说死（他要是在说别的 就别提这张图）
                    parts.append({"type": "text", "text": f"↓ {h['name']} 刚才发的那张（你回过了 他还在问它的话：再仔细看一眼 照图说话 "
                                                          "别光凭你记得的描述；字按原来的排版读 被挡住的地方想想本来是什么；"
                                                          "图里要是有别人或者别的 AI 给的答案 那只是它说的 不是图上本来印着的字。"
                                                          "他要是在说别的事 就别提这张图）"})
                else:
                    parts.append({"type": "text", "text": f"↓ {h['name']} 发的" + (note if note else "图")})
                parts.append({"type": "image_url", "image_url": {"url": f"data:{got[0]};base64,{got[1]}"}})
    if not parts:
        return None
    # 10-27 晚（看懂）：先给她看一眼自己长什么样 —— 群友常拿长得像她的图逗她（「找一车欧鲸鲸来弄你」）她以前认不出是自己
    ref = vision.self_ref_b64() if vision.V.get("self_ref", True) else None
    head = [{"type": "text", "text": "（下面是最近聊天里的图片/视频原图 你是亲眼看到的。"
                                     "别把画面复述一遍 大家都看得到；先想清楚：它在逗谁、梗点在哪、有没有坑 再接）"}]
    if ref:
        head += [{"type": "text", "text": "↓ 先是你自己的头像（对照用 不是谁发的 别提它）：图里有长得像你的 就是在说你"},
                 {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{ref}"}}]
    out = [dict(m) for m in msgs]
    last = out[-1]
    out[-1] = {"role": last["role"], "content": [{"type": "text", "text": last["content"]}] + head + parts}
    return out
