#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第二步：把挑中的表情包导入大肥鱼的表情包库
  读 C:\\QQBot\\sticker_review\\selection.json（Claude 挑好的清单）
  → 从原文件夹复制、缩小（静图最长边 360，动图最长边 300，保留动画）
  → 放进 bot\\stickers\\dp\\（文件名用英文编号 中文名字只写在索引里），并写进 stickers\\index.json
原文件夹不动；重复导入会跳过已经导过的
"""
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageSequence

ROOT = Path(__file__).resolve().parent
DST = ROOT / "stickers"
OUT = DST / "dp"
INDEX = DST / "index.json"
REVIEW = ROOT.parent / "sticker_review"
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    Path(r"C:\path\to\你的表情包文件夹")
FIELDS = ("emo", "cap", "kw", "scene", "text", "note")


def save_static(im, base):
    im = im.convert("RGBA")
    im.thumbnail((360, 360))
    if im.getextrema()[3][0] < 255:            # 有透明
        p = base.with_suffix(".png")
        im.save(p, optimize=True)
    else:
        p = base.with_suffix(".jpg")
        im.convert("RGB").save(p, quality=90)
    return p


def save_anim(im, base, side=300):
    frames, durs = [], []
    for fr in ImageSequence.Iterator(im):
        f = fr.convert("RGBA")
        f.thumbnail((side, side))
        frames.append(f)
        durs.append(fr.info.get("duration", im.info.get("duration", 80)) or 80)
    p = base.with_suffix(".gif")
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=durs, loop=0, disposal=2)
    if p.stat().st_size > 1_500_000 and side > 200:
        return save_anim(im, base, side - 60)
    return p


def write_index(idx):
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, INDEX)


def main():
    sel = json.loads((REVIEW / "selection.json").read_text(encoding="utf-8"))
    cat = {}
    for line in (REVIEW / "catalog.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            cat[r["id"]] = r
        except Exception:
            pass
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    st = idx.setdefault("stickers", {})
    done = {v.get("src_id") for v in st.values() if v.get("src_id")}
    OUT.mkdir(parents=True, exist_ok=True)
    added, skipped, failed = 0, 0, []
    for s in sel.get("add", []):
        sid, name = s["id"], s["name"].strip()
        r = cat.get(sid)
        if not r:
            failed.append(f"{sid} {name}：清单里没有这张")
            continue
        if sid in done:
            skipped += 1
            continue
        base_name, i = name, 2
        while name in st:                       # 重名：后面加个数字
            name, i = f"{base_name}{i}", i + 1
        try:
            im = Image.open(SRC / r["path"].replace("\\", "/"))
            p = save_anim(im, OUT / sid) if getattr(im, "n_frames", 1) > 1 else save_static(im, OUT / sid)
        except Exception as e:
            failed.append(f"{sid} {name}：{e}")
            continue
        st[name] = {k: s[k] for k in FIELDS if s.get(k)}
        st[name].update({"file": f"dp/{p.name}", "pack": "鲸鱼娘表情包合集", "src_id": sid,
                         "from": "DeepSeek 鲸鱼娘表情包合集 " + r["path"]})
        done.add(sid)
        added += 1
        print(f"导入 {name:14s} ← {r['path']}  ({p.stat().st_size // 1024}KB)")
    write_index(idx)
    print(f"\n鲸鱼娘合集：新增 {added} 张，之前导过的 {skipped} 张。表情包库现在共 {len(st)} 张")
    if failed:
        print(f"有 {len(failed)} 张没导进去：")
        for f in failed:
            print("  " + f)
    return added, skipped, failed


if __name__ == "__main__":
    main()
