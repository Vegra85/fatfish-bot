#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导入表情包（第二版）
  一、「大肥鱼」「大肥鱼和GPT娘」动图（B站 可以叫我云大人 / 雾雪 绘制，kiriyuki.com）
      标注（什么时候用、情绪、标签、梗）来自 dsh-nekowhale-stickers 项目（CC BY-NC 4.0）
      → 从作者网站下载到 bot\\stickers\\nw\\（校验文件 已经下好的跳过）
  二、Claude 从「DeepSeek 鲸鱼娘表情包合集」里挑的那批（apply_stickers.py）
  三、旧的 46 张拼音名字改成中文名字（文件不动 旧名字还认）
  全部写进 stickers\\index.json。可以重复运行：只补没下好的、更新标注
  非商业分享 保留作者署名（见 index.json 里的 credits）
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIR = ROOT / "stickers"
OUT = DIR / "nw"
INDEX = DIR / "index.json"
META = DIR / "nekowhale.json"
REPORT = ROOT.parent / "sticker_import.txt"
SITE = "https://kiriyuki.com"
UA = "nekowhale-stickers (+https://kiriyuki.com)"
PACKS = ("大肥鱼", "大肥鱼和GPT娘")

CREDITS = {
    "nekowhale": "「大肥鱼」「大肥鱼和GPT娘」动图：B站 可以叫我云大人 / 雾雪（space.bilibili.com/103739），kiriyuki.com。"
                 "大肥鱼形象设计 ZipZipPipe（space.bilibili.com/4168597）、上善无形（space.bilibili.com/4456176）；"
                 "GPT娘形象设计 ZipZipPipe。标注数据：dsh-nekowhale-stickers"
                 "（github.com/Loping151/dsh-nekowhale-stickers，CC BY-NC 4.0）。仅非商业使用",
    "base": "大肥鱼图片包（溟月 © 上善无形 / 女仆版 ZipZipPipe，CC BY-NC-SA 4.0）",
    "dp": "DeepSeek 鲸鱼娘表情包合集（网友整理）",
}

# 不导入的（明着的性暗示）
EXCLUDE = {"大肥鱼/EX61_猫儿这个XX", "大肥鱼和GPT娘/TT11_交个朋友"}

# 名字改一下更好认的
RENAME = {
    "大肥鱼/16N04_宣判": "判无罪", "大肥鱼/16N04_宣判-2": "判有罪", "大肥鱼/16N04_宣判-3": "判死刑",
    "大肥鱼/特殊01": "认真起来", "大肥鱼/特殊02": "放大招", "大肥鱼/7N09_终极侮辱1": "终极侮辱",
    "大肥鱼/EX14_一键三连1": "求三连", "大肥鱼/EX15_一键三连2": "一键三连",
    "大肥鱼/EX09_催眠2": "被迷住", "大肥鱼/4N01_催眠": "被催眠", "大肥鱼/11N02_WATCHING-YOU": "盯着你",
    "大肥鱼/4N11_giveyouup": "爬上来", "大肥鱼/4N12_letyoudown": "掉下去", "大肥鱼/ADN05_好吃": "搜到好货",
    "大肥鱼/EX27_嗦冰棍": "嗦冰棍2", "大肥鱼/EX29_分冰棍": "分冰棍2", "大肥鱼/EX49_True_music": "神曲泪目",
    "大肥鱼和GPT娘/3TT12_躲猫猫": "和GPT娘躲猫猫", "大肥鱼和GPT娘/TT09_摸下巴": "摸GPT娘下巴",
    "大肥鱼和GPT娘/2TT12_monkey_game": "Pocky游戏", "大肥鱼和GPT娘/3TT04_smoker": "舌头拖走",
    "大肥鱼和GPT娘/TT02_比心": "和GPT娘比心", "大肥鱼和GPT娘/2TT06_合影": "和GPT娘合影",
}

# 发之前要掂量一下的（会显示在提示词里）
NOTES = {
    "大肥鱼/6N06_友好手势": "竖中指 只对熟人开玩笑",
    "大肥鱼/16N04_宣判-2": "带竖中指 只对熟人开玩笑",
    "大肥鱼/16N04_宣判-3": "举着枪 只对熟人开玩笑",
    "大肥鱼/11N04_上膛": "掏枪 只对熟人开玩笑",
    "大肥鱼/13N04_你是猪": "只对熟人",
    "大肥鱼/3N08_扎小人": "开玩笑地记仇",
    "大肥鱼/14N11_百万群友": "玩梗 不是真要钱",
    "大肥鱼/EX76_我也要死么": "玩梗 别对真难过的人发",
    "大肥鱼/13N12_舔": "只对很熟的人",
    "大肥鱼/15N03_抓项圈": "只对很熟的人开玩笑",
    "大肥鱼/15N04_被项圈": "只对很熟的人开玩笑",
    "大肥鱼和GPT娘/TT05_舌吻": "只对很熟的人开玩笑",
    "大肥鱼和GPT娘/TT10_舔猫": "只对很熟的人",
    "大肥鱼和GPT娘/TT08_盖帽": "绿帽梗 只对熟人",
}

# 旧表情包：拼音名字 → 中文名字
BASE_RENAME = {
    "shengqi": "气鼓鼓冒烟", "xianqi": "嫌弃摆手", "huachi": "爱心眼花痴", "jiazai": "脑子加载中",
    "wenhao": "举手大问号", "jiaban": "黑眼圈加班", "ganfan": "星星眼干饭", "buyao": "交叉手不要",
    "yaoqian": "捧着小钱包", "deyi": "闭眼握拳好耶", "ok": "眨眼OK", "piaole": "被夸飘了", "bixin": "双手比心",
    "maozhua": "猫爪嘿嘿", "zaoshanghao": "挥手早上好", "qiurao": "跪地求饶", "yun": "蚊香眼",
    "zhenjing": "吓得跳起来", "xia": "闭眼大叫", "haixiu": "双手捂脸", "toukan": "尾巴后偷看", "wuyu": "死鱼眼",
    "shuijiao": "流口水睡觉", "tangping": "摊平不想动", "hello": "张开手你好", "morning": "伸懒腰",
    "goodnight": "睡帽晚安", "bye": "挥手拜拜", "peek": "画面边探头", "wow": "举手惊讶", "shy": "捂脸冒爱心",
    "pout": "抱臂不服", "cry": "揪围裙大哭", "smiletears": "含泪笑", "promise": "握拳约定",
    "phonehug": "抱着手机", "wait": "抱膝等", "snow": "贴窗看雪", "eraser": "拿橡皮擦", "fade": "像素消散",
    "yandere": "病娇笑", "cos_cat": "猫耳喵", "cos_idol": "偶像wink", "cos_school": "水手服",
    "cos_astro": "宇航服起飞", "cos_bride": "婚纱捧花",
}

_lines = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    _lines.append(s)


def q(s):
    return urllib.parse.quote(s, safe="")


def get(url, tries=4, timeout=30):
    """GET：返回 (状态码, content-type, 内容)。限流/服务器错误会等一会重试"""
    wait = 1.5
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.headers.get("content-type") or "", r.read()
        except urllib.error.HTTPError as e:
            if (e.code not in (429, 570) and e.code < 500) or i == tries - 1:
                return e.code, "", b""
        except Exception:
            if i == tries - 1:
                raise
        time.sleep(wait)
        wait *= 2
    return 0, "", b""


def write_json(path, data, indent=1):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, path)


def load_meta():
    meta = None
    try:
        meta = json.loads(META.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        code, ctype, body = get(SITE + "/meme/kit/nekowhale.json", tries=2, timeout=20)
        if code == 200 and "html" not in ctype:
            remote = json.loads(body.decode("utf-8"))
            if (remote.get("schema") == "sticker-kit/1" and isinstance(remote.get("stickers"), list)
                    and len(remote["stickers"]) >= 100
                    and (not meta or str(remote.get("version", "")) >= str(meta.get("version", "")))):
                meta = remote
                write_json(META, meta, indent=None)
                say(f"标注：用了作者网站上的最新版（{meta.get('version')}）")
        elif meta:
            say(f"标注：作者网站上的拿不到（HTTP {code}）用自带的那份（{meta.get('version')}）")
    except Exception as e:
        if meta:
            say(f"标注：作者网站没连上 用自带的那份（{meta.get('version')}）：{str(e)[:80]}")
    return meta


def load_manifest():
    for path in ("/api/kit/nekowhale", "/api/manifest"):
        try:
            code, _, body = get(SITE + path, tries=3)
        except Exception as e:
            say(f"清单 {path} 没连上：{str(e)[:100]}")
            continue
        if code == 200:
            try:
                m = json.loads(body.decode("utf-8"))
                if isinstance(m.get("items"), list):
                    return m
            except Exception:
                pass
        say(f"清单 {path}：HTTP {code}")
    return None


def sha12(b):
    return hashlib.sha1(b).hexdigest()[:12]


def local_name(stem, code, h, used):
    """动图存成英文文件名（QQ 发图时路径里没中文最稳）"""
    var = re.search(r"-(\d+)$", stem)
    c = code if re.fullmatch(r"[A-Za-z0-9]+", code or "") else ""
    if not c:
        digits = re.sub(r"\D", "", stem)
        c = ("SP" + digits) if digits else ("X" + (h[:8] or str(len(used))))
    f = f"{c}{'-' + var.group(1) if var else ''}.gif"
    if f in used:
        f = f"{f[:-4]}_{h[:6] or len(used)}.gif"
    used.add(f)
    return f


def fetch(job):
    sid, url, path, h = job
    try:
        if path.exists():
            if not h or sha12(path.read_bytes()) == h:
                return sid, "have", ""
        code, _, body = get(url)
        if code != 200 or not body:
            return sid, "fail", f"HTTP {code}"
        if not body.startswith(b"GIF8"):
            return sid, "fail", "下回来的不是动图"
        tmp = path.with_suffix(".part")
        tmp.write_bytes(body)
        os.replace(tmp, path)
        return sid, "new", ("" if not h or sha12(body) == h else "和清单里的校验值对不上（可能作者刚更新过）")
    except Exception as e:
        return sid, "fail", str(e)[:120]


def migrate_base(st):
    """旧的拼音名字 → 中文名字（文件不动 旧名字放进 alias 还认）"""
    n = 0
    for old, new in BASE_RENAME.items():
        if old not in st or new in st:
            continue
        info = st.pop(old)
        if not info.get("file"):
            for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                if (DIR / f"{old}{ext}").is_file():
                    info["file"] = f"{old}{ext}"
                    break
        if not info.get("file"):
            st[old] = info                         # 找不到文件 先不改
            continue
        info["alias"] = sorted(set(info.get("alias") or []) | {old})
        info.setdefault("pack", "大肥鱼图片包")
        st[new] = info
        n += 1
    return n


def import_nekowhale(st):
    meta = load_meta()
    if not meta:
        say("没有标注文件 stickers\\nekowhale.json 跳过动图")
        return 0, 0, []
    man = load_manifest()
    items = {it.get("id"): it for it in (man or {}).get("items", []) if it.get("pack") in PACKS}
    gif_base = (man or {}).get("gif_base") or "/media/gif"
    if not man:
        say("作者网站的清单没拿到 按默认地址试着下载")
    OUT.mkdir(parents=True, exist_ok=True)
    prev = {v.get("nw_id"): k for k, v in st.items() if v.get("pack") == "nekowhale"}
    for k in list(prev.values()):
        del st[k]                                  # 重新生成（标注可能更新了）
    jobs, todo, used = [], [], set()
    for s in meta.get("stickers", []):
        sid = s.get("id") or ""
        pack, _, stem = sid.partition("/")
        if pack not in PACKS or not stem or sid in EXCLUDE:
            continue
        it = items.get(sid) or {}
        code = it.get("code") or stem.split("_")[0]
        h = it.get("hash") or s.get("hash") or ""
        file = it.get("file") or f"{stem}.gif"
        url = f"{SITE}{gif_base}/{q(pack)}/{q(file)}" + (f"?h={h}" if h else "")
        fname = local_name(stem, code, h, used)
        jobs.append((sid, url, OUT / fname, h))
        todo.append((sid, s, stem, code, fname, h))
    say(f"动图：一共 {len(jobs)} 张（{len(EXCLUDE)} 张不导入），开始下载 已经下好的会跳过…")
    res, n_new, n_have, fails, warns = {}, 0, 0, [], []
    with ThreadPoolExecutor(4) as ex:
        for i, (sid, status, msg) in enumerate(ex.map(fetch, jobs), 1):
            res[sid] = status
            if status == "new":
                n_new += 1
                if msg:
                    warns.append(f"{sid}：{msg}")
            elif status == "have":
                n_have += 1
            else:
                fails.append(f"{sid}：{msg}")
            if i % 20 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}  新下载 {n_new}  已有 {n_have}  失败 {len(fails)}", flush=True)
    added = 0
    for sid, s, stem, code, fname, h in todo:
        if res.get(sid) == "fail" or not (OUT / fname).is_file():
            continue
        name = prev.get(sid) if prev.get(sid) and prev.get(sid) not in st else \
            (RENAME.get(sid) or stem[len(code) + 1:] or stem)
        base, i = name, 2
        while name in st:
            name, i = f"{base}{i}", i + 1
        e = {"emo": s.get("emotion") or "", "scene": s.get("use") or "", "kw": " ".join(s.get("tags") or []),
             "cap": s.get("desc") or "", "file": f"nw/{fname}", "pack": "nekowhale", "nw_id": sid, "hash": h,
             "from": f"B站 可以叫我云大人 · kiriyuki.com · {sid}"}
        if s.get("text"):
            e["text"] = s["text"].replace("\n", " ")
        if s.get("meme"):
            e["meme"] = s["meme"]
        if s.get("dir"):
            e["dir"] = s["dir"]
        if NOTES.get(sid):
            e["note"] = NOTES[sid]
        st[name] = e
        added += 1
    say(f"动图：新下载 {n_new} 张，之前下好的 {n_have} 张，写进表情包库 {added} 张")
    for w in warns[:10]:
        say("  注意 " + w)
    return added, n_new, fails


def main():
    t0 = time.time()
    idx = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    st = idx.setdefault("stickers", {})
    idx.setdefault("photos", {})
    idx["_说明"] = ("表情包索引（第二版）：名字 → {emo 情绪, scene 什么时候用, kw 关键词, text 图上的字, file 文件, "
                  "note 提醒}。stickers.py 每次按聊天内容挑最贴的几张给她看。改了不用重启")
    idx["credits"] = CREDITS
    n_base = migrate_base(st)
    if n_base:
        say(f"旧表情包：{n_base} 张的名字改成了中文（旧名字还认）")
    nw_added, nw_new, nw_fails = import_nekowhale(st)
    write_json(INDEX, idx)
    dp_added, dp_skip, dp_fails = 0, 0, []
    try:
        import apply_stickers
        print("\n—— 鲸鱼娘合集 ——", flush=True)
        dp_added, dp_skip, dp_fails = apply_stickers.main()
        idx = json.loads(INDEX.read_text(encoding="utf-8"))
        st = idx.get("stickers", {})
        _lines.append(f"鲸鱼娘合集：新增 {dp_added} 张，之前导过的 {dp_skip} 张")
    except Exception as e:
        say(f"鲸鱼娘合集没导成：{e}")
    total = len(st)
    say(f"\n完成（用时 {int(time.time() - t0)} 秒）：表情包库现在共 {total} 张")
    if nw_fails or dp_fails:
        say(f"有 {len(nw_fails) + len(dp_fails)} 张没导进去（可以过会儿再双击一次 只会补这些）：")
        for f in (nw_fails + dp_fails)[:40]:
            say("  " + f)
    say("最后重启一下机器人（停止.bat → 启动.bat）新的挑表情包方式才生效")
    try:
        REPORT.write_text(time.strftime("%Y-%m-%d %H:%M:%S\n") + "\n".join(_lines) + "\n", encoding="utf-8-sig")
    except Exception:
        pass


if __name__ == "__main__":
    main()
