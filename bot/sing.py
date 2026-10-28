#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大肥鱼唱歌（10-26）
  她「唱」= 放一段录好的歌（bot\\songs 里的音频 剪一段）当 QQ 语音发出去 —— 不是语音合成 不花 MiniMax 的钱
  加歌：控制台「唱歌」页（粘贴 B站链接一键导入 / 上传音频）；或者直接把 mp3/m4a/wav/flac 放进 bot\\songs（文件名就是歌名）
  songs\\songs.json（控制台写 机器人只读）：每首歌的名字、别名、唱哪一段、出处（有人问就照实说是谁做的）、默认唱哪首
  有人让她唱（「唱首歌」「唱个试试」「来首歌」「点歌」）→ 这一轮提示里告诉她会唱哪些 → 她单独一行写 [[唱:歌名]]
    → 先把她别的话发完 → 剪好那段（ffmpeg 转 24k 单声道 wav 头尾淡入淡出）当语音发 → 聊天记录里记成 [[唱:歌名]]
  点的歌歌单里没有（10-26 小明）：去网易云/B站找完全对得上的原唱 截 online_sec（默认 12 秒）高潮唱（music.py）
    找不到就直说没找到 —— 绝不拿歌单里的歌顶替（以前「唱雨爱」她放了 Let Me Go）
  只说「唱首歌」没点名：先问对方想听什么（不唱）；问她会唱什么：报歌单里那几首
  限制：同一个对话 gap_sec（默认 120 秒）内只唱一次、每天最多 daily_max（默认 30）次；
        有人说「别唱了」→ 这个对话 6 小时不唱；QQ 不收太长的语音 → 自动剪成 short_sec（默认 60 秒）再发一次
"""
import hashlib
import random
import re
import shutil
import subprocess
import time
from pathlib import Path

import clock
import safeio

ROOT = Path(__file__).resolve().parent
SONG_DIR = ROOT / "songs"
INDEX = SONG_DIR / "songs.json"
CLIP_DIR = ROOT / "data" / "voice"
STATE_FILE = ROOT / "data" / "sing_state.json"
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma")
DEFAULTS = {"enabled": True, "max_sec": 150, "short_sec": 60, "gap_sec": 60, "daily_max": 30,
            "online": True, "online_sec": 12}
SESSDATA = {"v": ""}          # 机器人那边填：B站 登录（现找歌时用）
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_cfg = dict(DEFAULTS)
_permits = {}                 # 对话 -> {"until", "left"}：这一轮有人要她唱 才能唱
_dur = {}                     # 文件路径 -> (mtime, 秒数)

# 对方让她唱
# 10-28 审查 reply-6：「来首晴天」「再来一首七里香」（没有唱 / 歌两个字）以前不认 —— 没许可、提示里也没唱歌说明
#   她照着聊天记录写了 [[唱:晴天]] 也唱不了；现在「来 / 点 + 首 + 歌名」也算（「来首诗」「后来首先」不算）
# 没有「一」的「来首 / 点首」后面跟着「都 / 尔 / 页 / 先…」是首都、首尔、首页、首先（「你来首都了吗」「点首页」10-28 审查复查 reply-6）
_LAI_SHOU = r"(^|[，。！？!?\s]|再|给我|帮我|你)(来|点)(?:一首|首(?!都|尔|页|先|次|付|选|富|充))(?!诗|词)"
ASK = re.compile(
    r"(唱|哼)(一?首|一?个|一?段|两句|几句|一下|下|首歌|个歌|给我听|来听听|两嗓子|一嗓子|歌给我听|歌听听|歌来听)"
    r"|(来|整|点)(一?首|个|段)(歌|曲儿?|小曲)|唱歌(给我|给大家|给咱)?听|(给我|给大家|给咱们?)唱|你(来)?唱"
    r"|点歌|献唱|清唱|开嗓|再唱|接着唱|唱(一遍|完)|" + _LAI_SHOU + r"[^\s，。！？!?]")
# 只是聊到唱歌（问会不会唱、唱得怎么样…）：告诉她会唱哪些 唱不唱她自己看
TALK = re.compile(r"唱|歌|" + _LAI_SHOU)
# 刚唱完他说「再来一首 / 再来一个 / 还想听 / 安可」：也是让她唱（10-28 审查 media-4：以前不认 她照着记录写了标记却没许可 唱不出来也不补话）
ENCORE = re.compile(r"再来一?(?:首|个|遍|曲)|再来亿首|还想听|还要听|安可|encore|再整一?首|再唱一?(?:首|个|遍)|接着唱", re.I)
ENCORE_SEC = 600                  # 唱完多久以内说「再来一个」还算在要歌
# 不想听
NEG = re.compile(r"(别|不要|不用|甭|少|停)(再)?唱了?|唱得?(太)?难听|别哼了")
MARK = re.compile(r"\[\[\s*(?:唱歌|唱)\s*(?:[:：]\s*([^\[\]\n]{0,40}))?\]\]")


def configure(cfg):
    _cfg.update({k: v for k, v in (cfg or {}).items() if k in DEFAULTS})


def cfg():
    return _cfg


def short_sec():
    return int(_cfg.get("short_sec", 60))


def ffmpeg():
    return shutil.which("ffmpeg")


# ───────────── 歌单 ─────────────
def _sid(file_name):
    return "s" + hashlib.md5(file_name.encode("utf-8")).hexdigest()[:8]


def _read_index():
    try:
        d = safeio.read_json(INDEX, None, print, busy_raise=True)   # 读坏了先另存 .bad
    except safeio.Busy:            # 一直打不开：这次只按文件夹里的算、不存（10-28 Gemini 复查：以前当成空的 一存 歌名、别名、截取的开始结束、默认歌全没了）
        return safeio.Unsaved(songs=[])
    if not isinstance(d, dict):
        d = {}
    d.setdefault("songs", [])
    return d


def index():
    """songs.json + 文件夹里新放进来的（还没登记的按文件名当歌名）"""
    d = _read_index()
    seen = {s.get("file") for s in d["songs"]}
    if SONG_DIR.exists():
        for p in sorted(SONG_DIR.iterdir()):
            if p.is_file() and p.suffix.lower() in AUDIO_EXT and p.name not in seen:
                d["songs"].append({"id": _sid(p.name), "name": p.stem, "file": p.name, "aliases": [],
                                   "credit": "", "enabled": True, "auto": True})
    for s in d["songs"]:
        s.setdefault("id", _sid(str(s.get("file") or s.get("name") or "")))
        s["exists"] = bool(s.get("file")) and (SONG_DIR / s["file"]).is_file()
    return d


def save_index(d):
    """只有控制台调（机器人只读）"""
    if isinstance(d, safeio.Unsaved):
        return
    SONG_DIR.mkdir(parents=True, exist_ok=True)
    out = {"songs": [{k: v for k, v in s.items() if k not in ("exists", "auto", "secs")} for s in d.get("songs") or []]}
    safeio.write_json(INDEX, out)


def songs():
    """她现在会唱的（开着的、文件在的）"""
    if not _cfg.get("enabled", True):
        return []
    return [s for s in index()["songs"] if s.get("enabled", True) and s.get("exists")]


def _norm(t):
    t = re.sub(r"[《》<>「」『』\"'“”‘’()（）\[\]【】\s·・,，.。!！?？~～\-_]", "", str(t or "").lower())
    return re.sub(r"(大肥鱼|翻唱|完整|真完整|版|deepseek|cover)", "", t)


def find(name, pool=None):
    """按歌名/别名找（差一点也认）；找不到 None"""
    pool = pool if pool is not None else songs()
    if not pool:
        return None
    want = _norm(name)
    if not want:
        return default(pool)
    names = [(s, [_norm(s.get("name"))] + [_norm(a) for a in s.get("aliases") or []]) for s in pool]
    for s, ns in names:
        if want in ns:
            return s
    for s, ns in names:
        # 歌单名完整出现在点的歌名里（带了歌手、多写了几个字）算；反过来 点的只是歌单名的一部分 要差不多一样长才算
        # （10-28 审查 media-11：以前「勇者」⊂「孤勇者」「猫」⊂「人是猫」都直接唱了歌单里那首 不去网上找）
        if any(n and len(n) >= 2 and (n in want or (want in n and len(want) / len(n) >= 0.8)) for n in ns):
            return s
    return None


def default(pool=None):
    pool = pool if pool is not None else songs()
    return next((s for s in pool if s.get("default")), pool[0] if pool else None)


def secs(song):
    """歌有多长（秒）；量不出来返回 0"""
    p = SONG_DIR / str(song.get("file") or "")
    try:
        m = p.stat().st_mtime
    except OSError:
        return 0
    k = str(p)
    if k in _dur and _dur[k][0] == m:
        return _dur[k][1]
    v = 0.0
    ff = ffmpeg()
    if ff:
        try:
            r = subprocess.run([ff, "-hide_banner", "-i", str(p)], capture_output=True, timeout=30,
                               creationflags=NO_WINDOW)
            mm = re.search(rb"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or b"")
            if mm:
                v = int(mm.group(1)) * 3600 + int(mm.group(2)) * 60 + float(mm.group(3))
        except Exception:
            v = 0.0
    if not v and p.suffix.lower() == ".wav":
        try:
            import wave
            with wave.open(str(p), "rb") as w:
                v = w.getnframes() / float(w.getframerate() or 1)
        except Exception:
            v = 0.0
    _dur[k] = (m, v)
    return v


def span(song, short=False):
    """唱哪一段 → (开始, 结束) 秒"""
    total = secs(song)
    clips = [c for c in song.get("clips") or [] if isinstance(c, (list, tuple)) and len(c) == 2]
    if clips:
        a, b = random.choice(clips)
    else:
        a, b = song.get("start") or 0, song.get("end") or 0
    a = max(0.0, float(a or 0))
    b = float(b or 0)
    lim = short_sec() if short else int(_cfg.get("max_sec", 150))
    if b <= a:
        b = a + lim
    b = min(b, a + lim)
    if total:
        b = min(b, total)
        if a >= total:
            a, b = 0.0, min(total, lim)
    return round(a, 2), round(b, 2)


def cut(song, a, b):
    """剪 a~b 秒 → 24k 单声道 wav（NapCat 能直接转成 QQ 语音）；剪不了返回 None"""
    src = SONG_DIR / str(song.get("file") or "")
    if not src.is_file():
        return None
    try:
        mt = int(src.stat().st_mtime)
    except OSError:
        return None
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    out = CLIP_DIR / f"song_{song.get('id')}_{int(a * 10)}_{int(b * 10)}_{mt}.wav"
    if out.exists() and out.stat().st_size > 2000:
        return out
    ff = ffmpeg()
    if not ff:
        return None
    dur = max(1.0, b - a)
    total = secs(song)
    af = []
    if a > 0.5:
        af.append("afade=t=in:st=0:d=0.25")
    if not total or b < total - 0.5:                       # 从中间截断的：最后两秒淡出
        fo = min(2.0, dur / 4)
        af.append(f"afade=t=out:st={dur - fo:.2f}:d={fo:.2f}")
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.2f}", "-t", f"{dur:.2f}", "-i", str(src),
           "-vn", "-ac", "1", "-ar", "24000", "-sample_fmt", "s16"] + (["-af", ",".join(af)] if af else []) + [str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=NO_WINDOW)
    except Exception:
        return None
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 2000:
        try:
            out.unlink()
        except OSError:
            pass
        return None
    _prune()
    return out


def _prune(keep=40):
    try:
        fs = sorted(CLIP_DIR.glob("song_*.wav"), key=lambda p: p.stat().st_mtime)
        for p in fs[:-keep]:
            p.unlink()
    except Exception:
        pass


# ───────────── 每天几次 / 刚唱过 ─────────────
def _state():
    try:
        s = safeio.read_json(STATE_FILE, None, print, busy_raise=True)   # 读坏了先另存 .bad（10-28 Gemini 复查：以前悄悄当成空的）
    except safeio.Busy:            # 一直打不开：谁说过「别唱了」、今天唱了几次都不知道 → 这一轮不唱、不存
        return safeio.Unsaved(day=clock.strf("%Y-%m-%d"), count=10 ** 6, conv={})
    s = s if isinstance(s, dict) else {}
    day = clock.strf("%Y-%m-%d")
    if s.get("day") != day:
        s.update(day=day, count=0)
    s.setdefault("conv", {})
    return s


def _save(s):
    if isinstance(s, safeio.Unsaved):
        return
    try:
        safeio.write_json(STATE_FILE, s)   # 原子写（10-28 Gemini 复查：以前直接覆盖着写 被硬杀在半中间 所有「别唱了」和今天的次数清掉）
    except Exception:
        pass


def muted_convs(now=None):
    """现在还在「别唱了」里的对话（控制台快捷操作用 10-27）"""
    now = now or time.time()
    return [c for c, v in (_state().get("conv") or {}).items() if isinstance(v, dict) and float(v.get("mute_until") or 0) > now]


def unmute_all():
    """控制台「解除别唱了」：所有对话都放开 → 放开了几个 / None（状态文件一时打不开 没改上）"""
    s = _state()
    if isinstance(s, safeio.Unsaved):      # 一时打不开：什么都没放开（别说放开了 10-28 Gemini 复查；第二轮复查：返回 None 让控制台照实说）
        return None
    n = 0
    for v in (s.get("conv") or {}).values():
        if isinstance(v, dict) and v.pop("mute_until", None):
            n += 1
    if n:
        _save(s)
    return n


# ───────────── 这一轮 ─────────────
def plan(conv, scene, texts):
    """这一轮跟唱歌有没有关系 → None / sing（让她唱）/ talk（聊到唱歌）/ none（一首都不会）/ wait（刚唱过）/ cap / muted / off"""
    joined = " ".join(t for t in (texts or [])[-3:] if t)
    if not joined:
        return None
    enc = bool(ENCORE.search(joined))
    if not (enc or TALK.search(joined)):
        return None
    s = _state()
    cs = s["conv"].setdefault(conv, {})
    now = time.time()
    encore = enc and now - float(cs.get("last", 0) or 0) < ENCORE_SEC    # 刚唱过 他说「再来一首」
    if enc and not encore and not TALK.search(joined):
        return None                                    # 没唱过的「再来一个」是在说别的
    if NEG.search(joined):
        cs["mute_until"] = now + 6 * 3600
        _save(s)
        _permits.pop(conv, None)
        return "muted"
    asked = bool(ASK.search(joined)) or encore
    if not _cfg.get("enabled", True):
        return "off" if asked else None
    if not songs() and not _cfg.get("online", True):
        return "none" if asked else None
    if now < cs.get("mute_until", 0):
        if not asked:
            return None
        cs.pop("mute_until", None)                     # 说过别唱 现在又点名要：唱
        _save(s)
    if int(s.get("count", 0)) >= int(_cfg.get("daily_max", 30)):
        return "cap" if asked else None
    if now - cs.get("last", 0) < int(_cfg.get("gap_sec", 120)):
        return "wait" if asked else None
    _permits[conv] = {"until": now + 300, "left": 1}
    return "sing" if asked else "talk"


def _list(pool):
    out = []
    for s in pool[:8]:
        c = (s.get("credit") or "").strip()
        out.append(f"- 《{s.get('name')}》" + (f"（{c[:60]}）" if c else ""))
    return "\n".join(out)


def block(mode, texts=None):
    """给她看的「唱歌」说明（按这一轮）"""
    if not mode:
        return ""
    online = bool(_cfg.get("online", True))
    if mode == "off":
        return "\n# 唱歌\n唱歌现在关着：有人让你唱就直说现在唱不了 别假装唱 别写歌词。\n"
    if mode == "none":
        return "\n# 唱歌\n对方想听你唱歌 但你还一首歌都没学会（还没给你装歌）：直说现在唱不了 别假装唱 别写歌词。\n"
    if mode == "muted":
        return "\n# 唱歌\n对方说了别唱：这次别唱。\n"
    if mode == "cap":
        return "\n# 唱歌\n对方想听你唱歌 但你今天唱太多了 嗓子累了：这次不唱 可以说明天再唱。别写唱歌标记。你是会唱歌的 别说你不会。\n"
    if mode == "wait":
        return ("\n# 唱歌\n对方想听你唱歌 但你刚唱过一首：这次先不唱 跟他说等一小会儿（一分钟左右）再点。"
                "别写唱歌标记；你是会唱歌的 别说你不会、别说没这功能。\n")
    pool = songs()
    joined = " ".join(t for t in (texts or [])[-3:] if t)
    hit = next((x for x in pool if any(n and n in joined for n in [x.get("name")] + list(x.get("aliases") or []))), None)
    known = ("你会唱的（有人问你会唱什么 就报这几首）：\n" + _list(pool) + "\n") if pool else "你自己录好的歌：还没有。\n"
    how = ("单独一行写 [[唱:歌名]]（知道是谁唱的就写 [[唱:歌名 - 歌手]]）。上面你会的就唱那首；"
           + ("你没学过的 系统会去网易云/B站找原唱 唱十来秒高潮 —— 照他点的歌名写 千万别换成你会的那几首。" if online
              else "你没学过的就直说这首还不会 别换成你会的那几首。"))
    s = "\n# 唱歌\n" + known
    if mode == "sing":
        s += ("对方想听你唱。\n"
              f"- 他点了歌名：{how}" + (f"（他点的是你会的《{hit.get('name')}》）" if hit else "") + "\n"
              "- 他没说唱哪首（只说「唱首歌」「唱一个」）：先问他想听什么 这次别唱\n"
              "前后可以各说一句很短的话（比如「咳咳 献丑了」）；别写歌词 别描述自己在唱。\n")
    else:
        s += (f"这轮有人聊到唱歌：有人问你会唱什么 就报你会的；有人点了歌名让你唱 {how}"
              "只是聊到唱歌 就正常聊。\n")
    s += "有人问歌是谁唱的、谁做的 就照括号里的出处说 别编。\n"
    return s


def extract(raw):
    """回复里的 [[唱:歌名]] 拿出来 → (标记前面的话, {"name": 歌名} 或 None, 标记后面的话)；多的标记都去掉"""
    m = MARK.search(raw or "")
    if not m:
        return raw, None, ""
    before = raw[:m.start()]
    after = MARK.sub("", raw[m.end():])
    return before.rstrip(), {"name": (m.group(1) or "").strip()}, after.strip()


def strip(text):
    return MARK.sub("", text or "")


_fail = {}                    # 对话 -> 这次没唱成的原因（机器人那边拿去说一声）


def render(req, conv, log=print, short=False, song=None):
    """要唱了：查许可 → 歌单里找 → 没有就去网上现找 → 剪 → (QQ 语音消息段, 歌, 开始, 结束)
    唱不了返回 None（原因写日志；现找没找到的 记在 _fail 里 机器人会补一句「没找到」）"""
    pm = _permits.get(conv)
    name = str((req.get("name") if isinstance(req, dict) else req) or "").strip()
    if not song:
        if not pm or pm["until"] < time.time() or pm["left"] <= 0:
            log(f"[唱歌] {conv} 没人要她唱 这次不唱（她自己写了唱歌标记）")
            _fail[conv] = {"name": name, "why": "没许可"}      # 机器人那边补一句（10-28 审查 reply-6：以前一句不补）
            return None
        if not name:
            log(f"[唱歌] {conv} 没说唱哪首 → 不唱（该先问对方想听什么）")
            _fail[conv] = {"name": "", "why": "没歌名"}
            return None
        song = find(name)
        pm["left"] -= 1
        if not song:
            if not _cfg.get("online", True):
                log(f"[唱歌] {conv} 她要唱「{name}」 歌单里没有 现找关着 → 不唱")
                _fail[conv] = {"name": name, "why": "还不会"}
                return None
            import music
            t0 = time.time()
            try:
                got = music.fetch(name, log, int(_cfg.get("online_sec", 12)), SESSDATA["v"])
            except Exception as e:             # 10-26 晚：现找出错以前一路抛到 bot 那边 连「没找到」都不补 她只说了句「咳咳」
                log(f"[唱歌] {conv} 现找「{name}」出错：{repr(e)[:150]}")
                _fail[conv] = {"name": name, "why": "出错了", "err": True}
                return None
            if not isinstance(got, dict) or got.get("fail") or not got.get("path"):
                _fail[conv] = {"name": name, "why": (got.get("fail") if isinstance(got, dict) else "") or "没找到",
                               "ne_abroad": bool(getattr(music, "ne_abroad", lambda: False)())}
                return None
            p = got["path"]
            log(f"[唱歌] {conv} 唱网上找的《{got.get('title')}》{got.get('artist') or ''}（{got.get('source')}，"
                f"一共用了 {time.time() - t0:.1f}s）")
            artist = got.get("artist") or ""
            if got.get("source") == "B站" and artist and artist == got.get("up"):
                artist = ""                    # 以前把 B站 UP 主记成了歌手（UP 不一定是唱的人）：聊天记录里别写错
            meta = {"name": got.get("title") or name, "artist": artist, "online": True,
                    "source": got.get("source"), "up": got.get("up") or "", "video": got.get("video") or ""}
            return {"type": "record", "data": {"file": p.resolve().as_uri()}}, meta, 0.0, float(_cfg.get("online_sec", 12))
    a, b = span(song, short)
    t0 = time.time()
    p = cut(song, a, b)
    if not p:
        log(f"[唱歌] 《{song.get('name')}》剪不出来（" + ("没装 ffmpeg" if not ffmpeg() else "ffmpeg 出错 / 文件坏了") + "）")
        if not short:                      # 第一次就剪不出来：让机器人补一句没唱成（剪短重发那次失败 机器人那边自己会处理）
            _fail[conv] = {"name": song.get("name") or name, "why": "剪不出来", "err": True}
        return None
    log(f"[唱歌] {conv} 唱《{song.get('name')}》{a:.0f}~{b:.0f} 秒（剪好用了 {time.time() - t0:.1f}s）")
    return {"type": "record", "data": {"file": p.resolve().as_uri()}}, song, a, b


def pop_fail(conv):
    return _fail.pop(conv, None)


def done(conv, ok):
    """唱完记一笔（发出去了才算）"""
    if not ok:
        return
    s = _state()
    s["count"] = int(s.get("count", 0)) + 1
    s["conv"].setdefault(conv, {})["last"] = time.time()
    _save(s)


def history_line(song):
    if song.get("online"):
        return f"[[唱:{song.get('name')}" + (f" - {song['artist']}" if song.get("artist") else "") + "]]"
    return f"[[唱:{song.get('name')}]]"


def status():
    pool = songs()
    if not _cfg.get("enabled", True):
        return "关了"
    if not pool:
        return "还没有歌（控制台「唱歌」页加）"
    return f"会唱 {len(pool)} 首（{'、'.join(s.get('name', '') for s in pool[:4])}）" + ("" if ffmpeg() else " · 没装 ffmpeg 剪不了")


# ───────────── 控制台用：加歌 ─────────────
def safe_name(t, n=40):
    t = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", str(t or "")).strip().strip(".")
    return t[:n] or "歌"


def add_file(name, ext, data, meta=None):
    """存一首歌 → 歌单里的那一条（歌单一时打不开 → RuntimeError：别把歌放进去了却登记不上 10-28 Gemini 复查）"""
    d = index()                            # 读一次一直用这份（10-28 Gemini 第二轮复查：以前写完歌再读第二遍 那次打不开就悄悄没登记）
    if isinstance(d, safeio.Unsaved):
        raise RuntimeError("songs.json 正被别的程序占着 没登记上（等一下再传）")
    SONG_DIR.mkdir(parents=True, exist_ok=True)
    ext = ext.lower() if ext.lower() in AUDIO_EXT else ".mp3"
    base = safe_name(name)
    p = SONG_DIR / f"{base}{ext}"
    i = 2
    while p.exists():
        p = SONG_DIR / f"{base}_{i}{ext}"
        i += 1
    p.write_bytes(data)
    d["songs"] = [s for s in d["songs"] if s.get("file") != p.name]
    e = {"id": _sid(p.name), "name": str(name or base)[:40], "file": p.name, "aliases": [], "credit": "",
         "enabled": True, "added": clock.strf("%Y-%m-%d %H:%M")}
    e.update({k: v for k, v in (meta or {}).items() if v not in (None, "")})
    if not any(s.get("default") for s in d["songs"] if s.get("exists")):
        e["default"] = True
    d["songs"].append(e)
    save_index(d)
    return e


def remove(sid):
    """删一首：文件挪到 songs\\_删掉的（找得回来）"""
    d = index()
    if isinstance(d, safeio.Unsaved):      # 歌单一时打不开：别挪了文件却删不掉那条（10-28 Gemini 复查）
        return None                        # None = 没改上（False = 没有这首歌）（10-28 Gemini 第二轮复查：以前控制台说「没有这首歌」）
    s = next((x for x in d["songs"] if x.get("id") == sid), None)
    if not s:
        return False
    src = SONG_DIR / str(s.get("file") or "")
    if src.is_file():
        trash = SONG_DIR / "_删掉的"
        trash.mkdir(exist_ok=True)
        dst = trash / src.name
        if dst.exists():
            dst = trash / f"{src.stem}_{int(time.time())}{src.suffix}"
        src.replace(dst)
    d["songs"] = [x for x in d["songs"] if x.get("id") != sid]
    save_index(d)
    return True


BV = re.compile(r"(BV[0-9A-Za-z]{10})")
AV = re.compile(r"(?:^|[^A-Za-z])av(\d{5,})|aid=(\d{5,})", re.I)


def song_name_from_title(title):
    m = re.search(r"《([^《》]{1,30})》", title or "")
    return (m.group(1) if m else re.split(r"[，,|｜【】\[\]（）()]", title or "歌")[0])[:30].strip() or "歌"


async def import_bili(link, sessdata="", log=print):
    """B站视频 → 音轨存成 m4a 加进歌单（用机器人那套 B站 登录和 cookie）。失败抛 RuntimeError（中文原因）"""
    import bili2
    link = str(link or "").strip()
    mb, ma = BV.search(link), AV.search(link)
    if not (mb or ma):
        raise RuntimeError("没认出 BV 号或 av 号（把视频链接整个粘过来就行）")
    mp = re.search(r"[?&]p=(\d+)", link)
    page = int(mp.group(1)) if mp else 1
    c = bili2.Client(sessdata)
    try:
        try:
            await c.ready()
        except Exception as e:
            raise RuntimeError(f"B站 连不上 / 被拦了（{e}）过一会儿再试")
        params = {"bvid": mb.group(1)} if mb else {"aid": ma.group(1) or ma.group(2)}
        import music
        data, v = await music.bili_audio(c, params, page)
        bvid, title = v.get("bvid"), v.get("title") or ""
        up = (v.get("owner") or {}).get("name") or ""
        name = song_name_from_title(title)
        meta = {"credit": f"B站 {up}《{title[:40]}》".strip(), "source": f"https://www.bilibili.com/video/{bvid}/",
                "up": up, "title": title[:80], "desc": (v.get("desc") or "")[:300]}
        e = add_file(name, ".m4a", data, meta)
        log(f"[唱歌] 从 B站 加了《{name}》（{up}）{len(data) // 1024} KB")
        return e
    finally:
        try:
            await c.close()
        except Exception:
            pass
