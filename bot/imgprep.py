#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片交给模型之前先整理一下（直接看图和识图描述都用这一份）
  动图（多帧 GIF / WEBP）：均匀抽最多 6 帧 拼成一张 3×2 的图 角上标顺序
      —— 不然模型只看得到第一帧 动图表情包的「动作」全丢了（借鉴 qq-bridge 的做法）
      只留要拼的那几帧（10-28 Gemini 复查 #23）；帧太多 / 宽×高×帧数太大的只看第一帧（当静图）
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
# 动图帧太多 / 太大就只看第一帧（10-28 Gemini 复查 #23）：要拼的帧再少 也得从头一帧一帧解到最后 太多了光解码就要好几秒
ANIM_MAX_FRAMES = 2000
ANIM_MAX_PIXELS = 2_000_000_000     # 宽×高×帧数（1000×1000 的 2000 帧、每帧整张都在变：解一遍就要好几秒）


def _fit(w, h, max_edge):
    """缩到多大：平常按长边缩到 max_edge；长截图保证窄的那边够宽（字看得清）—— 总像素、长边各有上限"""
    s = max_edge / max(w, h)
    short = min(w, h)
    if short * s < TALL_MIN and max(w, h) > 2 * short:
        s = max(s, min(TALL_MIN / short, (TALL_PIXELS / (w * h)) ** 0.5, TALL_EDGE / max(w, h)))
    s = min(s, 1.0)
    return max(1, round(w * s)), max(1, round(h * s))


def _pick(n):
    """n 帧里按时间均匀挑最多 GRID_MAX 帧的下标（第一帧、最后一帧都在）"""
    k = min(GRID_MAX, n)
    return [round(i * (n - 1) / (k - 1)) for i in range(k)] if k > 1 else [0]


def _frames(im):
    """按顺序走一遍：只把要拼的那几帧转成 RGBA、马上缩到 TILE 留下；总帧数、总时长照旧每一帧都算
    （10-28 Gemini 复查 #23：以前每一帧都转成整张 RGBA 全留在 list 里、_grid 只用其中 6 帧 —— 峰值内存 ≈ 帧数×宽×高×4
     跟文件大小无关：800×800 每帧只动几个像素的 GIF 300 帧才 0.27MB 就要 769MB、1000 帧 MemoryError 被吞掉原样返回）
    返回 (挑好的帧, 总帧数, 总时长毫秒)
    每一帧都先 load()（10-28 复查 #23）：WebP 的每帧时长是 load 的时候才读出来的 —— 不 load 的帧拿到的是上一张 load 过的那帧的时长
    （每帧时长不一样的 WebP 动图「约 X 秒」就算错：12 帧 7.8 秒算成 7.1 秒）；GIF / APNG 本来下一次 seek 也要 load 上一帧 不多花工夫"""
    from PIL import ImageSequence
    want = set(_pick(getattr(im, "n_frames", 1) or 1))
    out, n, ms = [], 0, 0
    for fr in ImageSequence.Iterator(im):
        fr.load()
        if n in want:
            t = fr.convert("RGBA")
            t.thumbnail((TILE, TILE))
            out.append(t)
        ms += int(fr.info.get("duration", im.info.get("duration", 100)) or 100)
        n += 1
    return out, n, ms


def _flat(fr, size):
    from PIL import Image
    fr = fr.copy()
    fr.thumbnail((size, size))
    bg = Image.new("RGB", fr.size, "white")
    bg.paste(fr, mask=fr.split()[-1])
    return bg


def _grid(frames, n, ms):
    """frames：_frames 挑好、缩好的那几帧；n：一共几帧；ms：一共多少毫秒（10-28 Gemini 复查 #23：以前收的是全部帧）"""
    from PIL import Image, ImageDraw
    k = len(frames)
    tiles = [_flat(fr, TILE) for fr in frames]
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
    secs = ms / 1000
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
        # 帧太多 / 宽×高×帧数太大的动图不拼了 往下走只看第一帧（10-28 Gemini 复查 #23）
        # 手机拍的 JPEG 带第二张图（MPO：深度图 / 预览图）不是动图（10-28 Gemini 复查：以前说成「动图 共 2 帧 约 0.2 秒」）
        if 1 < n <= ANIM_MAX_FRAMES and im.width * im.height * n <= ANIM_MAX_PIXELS and im.format != "MPO":
            frames, total, ms = _frames(im)
            if len(frames) > 1:
                return _grid(frames, total, ms)
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
