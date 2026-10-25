#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第一步：把一大堆表情包「过一遍」
  - 遍历整个表情包文件夹（含子文件夹），去重
  - 每张做一个小缩略图，交给本地 Ollama 识图：是不是鲸鱼娘、什么情绪、图上有什么字、适合什么场合、好不好用
  - 结果写进 C:\\QQBot\\sticker_review\\catalog.jsonl（可以中断，下次接着跑）
  - 每 48 张拼一张预览图 sheets\\sheet_XXX.jpg，方便人（和 Claude）快速翻看
不会改动原文件夹里的任何东西
"""
import base64
import hashlib
import io
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageSequence

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    Path(r"C:\path\to\你的表情包文件夹")
OUT = ROOT.parent / "sticker_review"
THUMBS = OUT / "thumbs"
SHEETS = OUT / "sheets"
CATALOG = OUT / "catalog.jsonl"
OLLAMA = ((CFG.get("local") or {}).get("base_url") or "http://localhost:11434").rstrip("/")
MODEL = (CFG.get("vision") or {}).get("model") or (CFG.get("local") or {}).get("model") or "qwen3-vl:8b-instruct"
EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
PER_SHEET = 48

EMOS = ("开心 得意 害羞 心动 喜欢 卖萌 撒娇 生气 嫌弃 无语 疑惑 思考 震惊 惊吓 难过 委屈 崩溃 "
        "困 摆烂 累 期待 拒绝 等待 感动 加油 腹黑 打招呼 晚安 早安 吃饭 干活 其他")

PROMPT = f"""这是一张图片。判断它能不能当 QQ 聊天表情包用，只输出 JSON：
{{"whale_girl": 图里主角是不是「蓝色长发、鲸鱼尾巴/鲸鱼鳍、常穿女仆装的鲸鱼娘」（true/false）,
 "kind": "表情包" 或 "壁纸/插画" 或 "漫画/多格" 或 "其他",
 "emotion": 从这些里选一个最贴的：{EMOS},
 "caption": "画面在干什么（20字内）",
 "text": "图上的文字（最多抄 30 个字 字多就只抄最醒目的一句；没有就空字符串）",
 "keywords": "聊天里出现哪些词时适合发它 空格分隔 5个左右",
 "scene": "什么场合发（15字内）",
 "quality": 1到5 作为聊天表情包的好用程度（表情清楚、一眼看懂情绪、构图干净=高；大场景壁纸、看不清、多人杂乱=低）,
 "nsfw": 是否有暴露/擦边内容（true/false）}}"""


def md5(b):
    return hashlib.md5(b).hexdigest()


def rid_of(h):
    return "s" + h[:8]          # 编号由图片内容决定：同时开两个也不会撞号


def load_done():
    """读已识别的结果；旧版的顺序编号改成按内容的编号，缩略图按原图重新生成一次"""
    done = {}
    if CATALOG.exists():
        for line in CATALOG.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            new = rid_of(r["md5"])
            if r.get("id") != new:
                r["id"] = new
                r["_fix_thumb"] = True
            if not r.get("ai", {}).get("error"):
                done[r["md5"]] = r
    fixed = [r for r in done.values() if r.pop("_fix_thumb", False)]
    if fixed:
        for r in fixed:
            try:
                thumb(Image.open(SRC / r["path"]), 190).save(THUMBS / f"{r['id']}.jpg", quality=85)
            except Exception:
                pass
        CATALOG.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                   for r in done.values()), encoding="utf-8")
        print(f"整理了 {len(fixed)} 条旧记录的编号")
    return done


LOCK = OUT / "running.lock"


def other_running():
    """另一个窗口也在跑？（它每处理一张图就会刷新这个文件）"""
    try:
        return time.time() - LOCK.stat().st_mtime < 120
    except FileNotFoundError:
        return False


def thumb(im, size):
    im = im.convert("RGBA")
    im.thumbnail((size, size))
    bg = Image.new("RGB", im.size, "white")
    bg.paste(im, mask=im.split()[-1])
    return bg


def ask(client, b64):
    body = {"model": MODEL, "stream": False, "think": False, "format": "json", "keep_alive": "30m",
            "messages": [{"role": "user", "content": PROMPT, "images": [b64]}],
            "options": {"temperature": 0.1, "num_predict": 600}}
    r = client.post(OLLAMA + "/api/chat", json=body, timeout=120)
    if r.status_code == 400 and "think" in r.text:
        body.pop("think")
        r = client.post(OLLAMA + "/api/chat", json=body, timeout=120)
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def font(size):
    for f in ("msyh.ttc", "simhei.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    return ImageFont.load_default()


def make_sheets(rows):
    SHEETS.mkdir(parents=True, exist_ok=True)
    cell, cols = 200, 8
    f = font(15)
    for i in range(0, len(rows), PER_SHEET):
        chunk = rows[i:i + PER_SHEET]
        n_rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cell, n_rows * (cell + 22)), "white")
        d = ImageDraw.Draw(sheet)
        for j, r in enumerate(chunk):
            x, y = (j % cols) * cell, (j // cols) * (cell + 22)
            try:
                t = Image.open(THUMBS / f"{r['id']}.jpg")
                sheet.paste(t, (x + (cell - t.width) // 2, y + (cell - t.height) // 2))
            except Exception:
                pass
            a = r.get("ai") or {}
            tag = f"{r['id']} {a.get('emotion', '?')}{'*' if r.get('animated') else ''} q{a.get('quality', '?')}"
            d.text((x + 3, y + cell + 2), tag, fill="black", font=f)
        sheet.save(SHEETS / f"sheet_{i // PER_SHEET + 1:03d}.jpg", quality=80)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    THUMBS.mkdir(parents=True, exist_ok=True)
    if other_running():
        print("已经有一个窗口在跑了，这个窗口不用开。关掉就行。")
        return
    files = sorted(p for p in SRC.rglob("*") if p.suffix.lower() in EXTS)
    print(f"找到 {len(files)} 张图，用 {MODEL} 识别（可以随时关窗口，下次接着跑）")
    done = load_done()
    seen = set(done)
    rows = list(done.values())
    t0 = time.time()
    new_n = 0
    with httpx.Client() as client, CATALOG.open("a", encoding="utf-8") as out:
        for k, p in enumerate(files, 1):
            try:
                raw = p.read_bytes()
            except Exception:
                continue
            h = md5(raw)
            if h in seen:
                continue
            seen.add(h)
            try:
                im = Image.open(io.BytesIO(raw))
                frames = getattr(im, "n_frames", 1)
                im.seek(0)
                w, hgt = im.size
                th = thumb(im, 190)
            except Exception as e:
                print("  打不开:", p.name, e)
                continue
            rid = rid_of(h)
            LOCK.write_text(str(time.time()), encoding="utf-8")
            th.save(THUMBS / f"{rid}.jpg", quality=85)
            buf = io.BytesIO()
            thumb(im, 512).save(buf, "PNG")
            try:
                ai = ask(client, base64.b64encode(buf.getvalue()).decode())
            except Exception as e:
                ai = {"error": repr(e)[:120]}
            r = {"id": rid, "path": str(p.relative_to(SRC)), "md5": h, "w": w, "h": hgt,
                 "bytes": len(raw), "animated": frames > 1, "frames": frames, "ai": ai}
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
            out.flush()
            rows.append(r)
            new_n += 1
            if new_n % 5 == 0 or ai.get("error"):
                el = time.time() - t0
                eta = (len(files) - k) * el / new_n / 60
                tag = f"出错（会在下次运行时重试）" if ai.get("error") else \
                    f"{ai.get('emotion', '')} q{ai.get('quality', '')} {ai.get('caption', '')[:20]}"
                print(f"  [{k}/{len(files)}] {rid} {tag}  （这次已看 {new_n} 张 用了 {el / 60:.0f} 分钟 "
                      f"大约还要 {eta:.0f} 分钟）", flush=True)
    try:
        LOCK.unlink()
    except Exception:
        pass
    uniq = {}
    for r in rows:
        uniq[r["md5"]] = r
    rows = sorted(uniq.values(), key=lambda r: r["path"])
    make_sheets(rows)
    good = [r for r in rows if (r.get("ai") or {}).get("quality", 0) >= 4
            and (r.get("ai") or {}).get("kind") == "表情包" and not (r.get("ai") or {}).get("nsfw")]
    print(f"\n完成：共 {len(rows)} 张（去重后）| 识别为好用表情包 {len(good)} 张")
    print(f"结果在 {OUT}")


if __name__ == "__main__":
    main()
