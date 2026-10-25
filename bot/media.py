#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
让 DeepSeek（deepseek-flash / V4.1 Flash）直接看原图
  最近 15 分钟里的图：下载原图 → 作为 image_url 附在发给她的最后一条消息上
  视频：DeepSeek 不收视频 → 用 ffmpeg 均匀截几帧当图片给她看（没装 ffmpeg 就只显示 [视频]）
"""
import asyncio
import base64
import shutil
import tempfile
import time
from pathlib import Path

import httpx

import imgprep

MAX_IMAGES = 4         # 一次最多附几张原图
MAX_FRAMES = 6         # 视频截几帧
RECENT_SEC = 900
MAX_BYTES = 8_000_000
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


async def attach(msgs, history, log=print, only_uid=None):
    """把最近的原图/视频帧附到最后一条 user 消息上。没有就返回 None
    only_uid：群里只附这个人发的（她这一条回的是他 别人的图有文字描述就够了 附原图会把她的注意力拽到别人的图上）"""
    now = time.time()
    # 只给她看「还没回过的」新图 —— 回过的旧图已经有文字描述了 再附原图她会反复去评论
    recent = [h for h in history[-15:] if h.get("role") == "user" and not h.get("replied")
              and now - h.get("ts", 0) < RECENT_SEC and (h.get("imgs") or h.get("vids"))
              and (only_uid is None or str(h.get("uid")) == str(only_uid))]
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
                parts.append({"type": "text", "text": f"↓ {h['name']} 发的" + (note if note else "图")})
                parts.append({"type": "image_url", "image_url": {"url": f"data:{got[0]};base64,{got[1]}"}})
    if not parts:
        return None
    out = [dict(m) for m in msgs]
    last = out[-1]
    out[-1] = {"role": last["role"], "content": [
        {"type": "text", "text": last["content"]},
        {"type": "text", "text": "（下面是最近聊天里的图片/视频原图 你是亲眼看到的）"}] + parts}
    return out
