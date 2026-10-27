#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片交给模型之前先整理一下（直接看图和识图描述都用这一份）
  动图（多帧 GIF / WEBP）：均匀抽最多 6 帧 拼成一张 3×2 的图 角上标顺序
      —— 不然模型只看得到第一帧 动图表情包的「动作」全丢了（借鉴 qq-bridge 的做法）
  静态 GIF / WEBP：转成 PNG（有的模型不收）
  特别大的图：长边缩到 max_edge（截图太大 本地识图会报错 也慢）；长截图窄的那边至少留 TALL_MIN（10-28 审查 media-12）
  没装 Pillow 或者处理失败：原样返回 不影响原来的流程
"""
import io

GRID_MAX = 6
TILE = 300
# 长截图（一边比另一边长两倍多）：缩完窄的那边至少留这么宽（10-28 审查 media-12：以前只按长边缩 1080×6000 → 230×1280 字全糊了）
TALL_MIN = 720
TALL_PIXELS = 3_200_000      # 但总像素不超过这么多（大约 720×4400）
TALL_EDGE = 5000             # 长边也别超过这么长（有的识图模型收不了太长的图）


def _fit(w, h, max_edge):
    """缩到多大：平常按长边缩到 max_edge；长截图保证窄的那边够宽（字看得清）—— 总像素、长边各有上限"""
    s = max_edge / max(w, h)
    short = min(w, h)
    if short * s < TALL_MIN and max(w, h) > 2 * short:
        s = max(s, min(TALL_MIN / short, (TALL_PIXELS / (w * h)) ** 0.5, TALL_EDGE / max(w, h)))
    s = min(s, 1.0)
    return max(1, round(w * s)), max(1, round(h * s))


def _frames(im):
    from PIL import ImageSequence
    out, durs = [], []
    for fr in ImageSequence.Iterator(im):
        out.append(fr.convert("RGBA"))
        durs.append(int(fr.info.get("duration", im.info.get("duration", 100)) or 100))
    return out, durs


def _flat(fr, size):
    from PIL import Image
    fr = fr.copy()
    fr.thumbnail((size, size))
    bg = Image.new("RGB", fr.size, "white")
    bg.paste(fr, mask=fr.split()[-1])
    return bg


def _grid(frames, durs):
    from PIL import Image, ImageDraw
    n = len(frames)
    k = min(GRID_MAX, n)
    idx = [round(i * (n - 1) / (k - 1)) for i in range(k)] if k > 1 else [0]
    tiles = [_flat(frames[i], TILE) for i in idx]
    cols = k if k <= 3 else (2 if k == 4 else 3)
    rows = (k + cols - 1) // cols
    tw = max(t.width for t in tiles)
    th = max(t.height for t in tiles)
    gap = 6
    sheet = Image.new("RGB", (cols * tw + (cols - 1) * gap, rows * th + (rows - 1) * gap), (225, 225, 225))
    d = ImageDraw.Draw(sheet)
    for j, t in enumerate(tiles):
        x = (j % cols) * (tw + gap)
        y = (j // cols) * (th + gap)
        sheet.paste(t, (x + (tw - t.width) // 2, y + (th - t.height) // 2))
        d.rectangle([x, y, x + 16, y + 16], fill=(40, 40, 40))
        d.text((x + 5, y + 3), str(j + 1), fill="white")
    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=85)
    secs = sum(durs) / 1000
    note = (f"动图（共 {n} 帧 约 {secs:.1f} 秒）按时间顺序抽了 {k} 帧拼成一张 "
            f"从左到右、从上到下看 角上数字是顺序")
    return out.getvalue(), "image/jpeg", note


def prep(data, mime, max_edge=1600):
    """返回 (bytes, mime, note)；note 只有动图才有"""
    if not data or mime not in ("image/gif", "image/webp", "image/png", "image/jpeg"):
        return data, mime, ""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        n = getattr(im, "n_frames", 1) or 1
        if n > 1:
            frames, durs = _frames(im)
            if len(frames) > 1:
                return _grid(frames, durs)
        if mime in ("image/gif", "image/webp"):
            im.seek(0)
            fr = im.convert("RGBA")
            if max(fr.size) > max_edge:
                fr.thumbnail(_fit(*fr.size, max_edge))
            out = io.BytesIO()
            fr.save(out, "PNG")
            return out.getvalue(), "image/png", ""
        if max(im.size) > max_edge:
            im = im.convert("RGB")
            im.thumbnail(_fit(*im.size, max_edge))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=90)
            return out.getvalue(), "image/jpeg", ""
    except Exception:
        pass
    return data, mime, ""
