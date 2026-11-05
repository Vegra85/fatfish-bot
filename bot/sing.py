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
  点歌升级（11-01 v1.16.0）：
    他点歌时发了音乐链接（B站 / YouTube / 网易云 / QQ 音乐 / 酷狗，music.find_url 认）→ 算点名要唱、照这个链接拿歌（music.fetch_url）
    网上找到好几个拿不准（music.fetch(ask=True) 回 {"ask"}）→ 这次不唱、记下几个选项 → 她用自己的话问他要哪个
      → 他回「1 / 第二个 / 周杰伦的 / 要原唱 / 不要现场版」→ 认出是哪个 → 唱那个（music.fetch_pick）
      → choice_sec（默认 120）秒没人选 → 机器人那边唱第一个（只发语音）；他又点了别的 / 说不要了 → 不问了
    谁能选：点歌的那个人（群里别人回「1」多半在说别的事 不算）；记不住是谁点的（承诺表到点唱的）→ 谁回都算
  唱整首（11-01 v1.16.5 小明：「唱歌功能拓展全曲 … 每个人一天只能点一次 按照圈子排 关系越好越不"不情愿" 点歌数量也能增加 最高档关系无限制 也不用求」）
    触发：点歌时明说「整首 / 全曲 / 完整版 / 唱完 / 从头唱到尾 / 唱全」（full_ask；「整首都好听」这种夸歌的不算）—— 这一轮得本来就能唱（plan 给了许可）
    按关系圈（bot 那边用 social.py C59 判：老板 / 最亲 / 熟人 / 认识的 / 陌生人 / 别人家的鱼；判不出来当认识的）查 config sing.full.circles：
      daily 每天几次整首（-1 不限、0 不唱）/ beg 第一次先不答应、要他 beg_min 分钟内再求一次 / grudging 嘴上先不太情愿 / tsundere 嘴硬不承认特意给他唱
    full_plan → {"state": grant / beg / begged / cap / none / busy, …} → full_block 给她的状态（E1：只给状态和规矩 不写例句、不写她要说的话）
    名额只在整首真发出去以后才扣（full_done；按人、按她的一天 clock.her_day）；正在弄的那首也算占着（同一个人同时要两首不会突破名额）
    标记不改（还是 [[唱:歌名]]）：这一轮有整首许可（full_permit）→ bot 那边后台做（render_full：自己录的歌 = 整个文件 上限 full_max_sec；
      网上的 = 完整音频不剪 只去头尾静音 超了截到上限；换声超时按长度放宽 full_cover_timeout）→ 先发语音 → 发不出去发音频文件（mp3）→ 再不行剪一段发
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
DEFAULTS = {"enabled": True, "max_sec": 150, "short_sec": 60, "gap_sec": 60, "ask_gap_sec": 20, "daily_max": 30,
            "online": True, "online_sec": 18, "cover": {"enabled": False},   # cover 以前没写进这里 → configure 把它滤掉了 换声从没开过（10-31 v1.15.14）
            # 11-01 v1.16.0：找歌的来源顺序（music.py 用；没登录 / 用不了的自动跳过）、拿不准先问、问了多久没人选就唱第一个
            "sources": ["qq", "netease", "kugou", "bili", "youtube"], "ask_choice": True, "choice_sec": 120,
            "full": {}}                # 唱整首（11-01 v1.16.5）：见 FULL_DEFAULTS（config 里没写的照默认）
SESSDATA = {"v": ""}          # 机器人那边填：B站 登录（现找歌时用）
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_cfg = dict(DEFAULTS)
_permits = {}                 # 对话 -> {"until", "left"}：这一轮有人要她唱 才能唱
_wait_left = {}               # 对话 -> 还要等几秒才能再唱（「wait」那条告诉她真实的数 10-31 v1.15.15）
_wait_why = {}                # 对话 -> 「wait」是因为什么（"full" = 这个对话里给人唱的整首还在后台弄 11-02 v1.17.1；"full_q" = 他在问那首整首好了没 v1.17.3）
_full_conv = {}               # 对话 -> 正在后台弄的整首数（11-02 v1.17.1：这时候这个对话里再点歌先等整首发出去）
_wait_job = {}                # 对话 -> (这一轮问的人, full_job_in 的结果)：「他在问整首好了没」那句状态用（11-02 v1.17.3）
_covering = {"full": 0}       # 正在换声的整首数（换声服务一次只做一段：这时候别的一段别去排队干等 90 秒 11-02 v1.17.1）
_want = {}                    # 对话 -> (秒数, 到期)：他点名要唱几秒（10-29 巡检：「随机唱15秒」她唱了整首 124 秒 还唱了两遍）
_last_online = {}             # 对话 -> 刚唱的网上现找的那首（谁说「不是这首」好记下这个版本别再用 11-01 v1.15.18）
COMPLAIN_SEC = 600            # 唱完多久以内说「不对」算在说这首
_link = {}                    # 对话 -> {"t", "link"(music.find_url 的结果), "by"}：他这轮点歌发了音乐链接（11-01 v1.16.0）
_choice = {}                  # 对话 -> {"t", "req", "options", "by", "src"}：网上找到好几个拿不准 她问了他要哪个
_picked = {}                  # 对话 -> {"t", "opt", "n", "by", "how"}：他选了哪个（或者到点没人选 → 第一个）
_asker = {}                   # 对话 -> (点歌人 uid, 时间)：render 拿不到是谁点的 plan 的时候记下
KEEP_SEC = 300                # 链接 / 选好的那个 多久内还算数（跟许可一样长）
# 「不是这首 / 唱错了 / 不是原唱 / 是现场版 / 全是怪音 / 版本不对」—— 只在她刚唱完网上现找的歌之后才看（11-01 v1.15.18：
#   点 See You Again 一直唱小饭桌现场版 全是怪音 说了也没用 下次照样那个）
COMPLAIN = re.compile(r"不是这首|不是这个歌|不是这歌|唱错了?|(歌|版本|曲子)(不对|错了)|不对的?(歌|版本|曲子)|不是原(唱|版|曲)|"
                      r"是(现场|live|翻唱|伴奏)(版)?|全是(怪音|杂音|噪音)|一堆(怪音|杂音)|识别错|找错了", re.I)
_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
WANT = re.compile(r"(\d{1,3}|[一二两三四五六七八九十]{1,3})\s*(?:秒|s(?![a-z])|sec)|(半分钟)|([一两二三]|\d)\s*分钟", re.I)


def _cn_num(t):
    if t.isdigit():
        return int(t)
    if "十" in t:
        a, _, b = t.partition("十")
        return (_CN.get(a, 1) if a else 1) * 10 + (_CN.get(b, 0) if b else 0)
    return _CN.get(t, 0)


def want_sec(text):
    """「唱15秒」「唱个半分钟」「唱一分钟」→ 秒数；没说返回 0（太短的按 5 秒）"""
    m = WANT.search(text or "")
    if not m:
        return 0
    if m.group(1):
        n = _cn_num(m.group(1))
    elif m.group(2):
        n = 30
    else:
        n = _cn_num(m.group(3)) * 60
    return max(5, n) if n else 0
_dur = {}                     # 文件路径 -> (mtime, 秒数)

# 对方让她唱
# 10-28 审查 reply-6：「来首晴天」「再来一首七里香」（没有唱 / 歌两个字）以前不认 —— 没许可、提示里也没唱歌说明
#   她照着聊天记录写了 [[唱:晴天]] 也唱不了；现在「来 / 点 + 首 + 歌名」也算（「来首诗」「后来首先」不算）
# 没有「一」的「来首 / 点首」后面跟着「都 / 尔 / 页 / 先…」是首都、首尔、首页、首先（「你来首都了吗」「点首页」10-28 审查复查 reply-6）
_LAI_SHOU = r"(^|[，。！？!?\s]|再|给我|帮我|你)(来|点)(?:一首|首(?!都|尔|页|先|次|付|选|富|充))(?!诗|词)"
ASK = re.compile(
    r"(唱|哼)(一?首|一?个|一?段|两句|几句|一下|下|首歌|个歌|给我听|来听听|两嗓子|一嗓子|歌给我听|歌听听|歌来听)"
    r"|(来|整|点)(一?首|个|段)(歌|曲儿?|小曲)|唱歌(给我|给大家|给咱)?听|(给我|给大家|给咱们?)唱|你(来)?唱"
    r"|点歌|献唱|清唱|开嗓|再唱|接着唱|唱(一遍|完)|" + _LAI_SHOU + r"[^\s，。！？!?]"
    # 「@她的名字 唱司凤版雨爱」「唱雨爱dj」：一句话开头（或「你 / 再 / 给我 / 帮我」后面）直接「唱 + 歌名」（10-31 v1.15.15 以前不认 → 没许可 她推说发不了语音）
    r"|(?:^|[\s，。！？!?]|你|再|给我|帮我)唱(?!歌|得|的|地|过|功|片|吧|啊|呀|呢|么|吗|什么|啥|完了|不了|不出)[^\s，。！？!?]{2,}")
# 只是聊到唱歌（问会不会唱、唱得怎么样…）：告诉她会唱哪些 唱不唱她自己看
TALK = re.compile(r"唱|歌|" + _LAI_SHOU)
# 刚唱完他说「再来一首 / 再来一个 / 还想听 / 安可」：也是让她唱（10-28 审查 media-4：以前不认 她照着记录写了标记却没许可 唱不出来也不补话）
ENCORE = re.compile(r"再来一?(?:首|个|遍|曲)|再来亿首|还想听|还要听|安可|encore|再整一?首|再唱一?(?:首|个|遍)|接着唱", re.I)
ENCORE_SEC = 600                  # 唱完多久以内说「再来一个」还算在要歌
# 不想听
NEG = re.compile(r"(别|不要|不用|甭|少|停)(再)?唱了?|唱得?(太)?难听|别哼了")
MARK = re.compile(r"\[\[\s*(?:唱歌|唱)\s*(?:[:：]\s*([^\[\]\n]{0,40}))?\]\]")
# 他自己打的字（11-02 v1.17.12）：认「要唱 / 要整首 / 选哪个 / 别唱了 / 不是这首」只看这个 ——
# 图 / 表情包 / 视频的描述、他引用的那条、转发的聊天记录、分享卡片、文件名 都不是他说的
# （v1.17.11 上线后：私聊一张桌面照 识图写了「三个完整的棕色鸡蛋」→「完整的」被认成要整首 给了整首许可）
# 语音转出来的字（[语音 N秒：…]）是他说的 留着；链接另外从原文找（分享卡片里的歌链接照样认）
# 11-03 v1.17.13（Gemini 4-F5）：冒号后面允许套一层方括号 —— 分享卡片的来源常带「[QQ小程序]」「[分享]」
#   （以前整段去不掉：B站小程序卡片标题里的「完整版」被当成他要整首）
_NOT_HIS = re.compile(r"\[(?:图片|表情包|动画表情|表情|视频|文件|分享卡片|群邀请卡片|聊天记录|转发的聊天记录[^：:\[\]]*)"
                      r"(?:[：:](?:[^\[\]]|\[[^\[\]]*\])*)?\]")
_QUOTED = re.compile(r"^\s*（回复了 .*?」）", re.S)


def his_words(texts):
    """→ 跟 texts 一样长的 list：每句去掉不是他说的那些（去完空了就是 ""）"""
    out = []
    for t in list(texts or []):
        s = _QUOTED.sub(" ", str(t or ""), count=1)
        for _ in range(3):                # 转发的聊天记录里还套着 [图片] 这种：由里往外去
            s2 = _NOT_HIS.sub(" ", s)
            if s2 == s:
                break
            s = s2
        out.append(s.strip())
    return out


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


def span(song, short=False, want=0):
    """唱哪一段 → (开始, 结束) 秒；want = 他要唱几秒（0 = 没说）"""
    total = secs(song)
    clips = [c for c in song.get("clips") or [] if isinstance(c, (list, tuple)) and len(c) == 2]
    if clips:
        a, b = random.choice(clips)
    else:
        a, b = song.get("start") or 0, song.get("end") or 0
    a = max(0.0, float(a or 0))
    b = float(b or 0)
    lim = short_sec() if short else int(_cfg.get("max_sec", 150))
    if want and want > 0:
        lim = min(lim, want)
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
def complaint(conv, texts, log=print, now=None):
    """她刚唱完网上现找的歌（COMPLAIN_SEC 内）有人说不是这首 / 不是原唱 / 全是怪音 → 这个版本记成不要（music.mark_bad）
    → 给她看的那段状态（没有就 ""）。只认一次（记过就清掉）"""
    now = now or time.time()
    last = _last_online.get(conv)
    joined = " ".join(t for t in his_words(texts)[-3:] if t)       # 图的描述、引用的那条不算（11-02 v1.17.12）
    if not last or now - float(last.get("t") or 0) > COMPLAIN_SEC or not joined:
        return ""
    m = COMPLAIN.search(joined)
    if not m:
        return ""
    _last_online.pop(conv, None)
    try:
        import music
        ok = music.mark_bad(last, m.group(0), log)
    except Exception as e:
        log(f"[唱歌] 记「不是这首」出错：{repr(e)[:80]}")
        ok = False
    if not ok:
        return ""
    return (f"\n# 唱歌\n你刚才唱的《{last.get('title')}》（网上找的{last.get('source') or ''}版本）他说不对（「{m.group(0)}」）："
            "这个版本系统已经记下以后不用了，他再点会换一个找。照实认一句 别硬说没错。\n")


# ───────────── 链接点歌 / 拿不准先问（11-01 v1.16.0） ─────────────
URL = re.compile(r"https?://\S+|(?:b23\.tv|163cn\.tv|youtu\.be)/\S+", re.I)
PLAT = {"bili": "B站", "youtube": "YouTube", "netease": "网易云", "qq": "QQ 音乐", "kugou": "酷狗"}
# 选哪个：整句就是个数字（「1」「2吧」「就第二个」）/ 句子里有「第 N 个 / 首」/「最后一个」/「随便 / 都行」= 第一个
PICK_NUM = re.compile(r"^\s*(?:就|要|选|唱|听|那|那就|还是)?\s*(?:第\s*)?([1-9一二两三四五])\s*(?:个|首|号|种|版)?\s*"
                      r"(?:吧|呀|啊|哈|嘛|呗|的|就行|好了)?\s*[。.!！~～]*\s*$")
PICK_NTH = re.compile(r"第\s*([1-9一二两三四五])\s*(?:个|首|种|号|版)")
PICK_LAST = re.compile(r"最后(?:一|那)?(?:个|首)")
PICK_ANY = re.compile(r"^\s*(?:随便|都行|都可以|哪个都行|哪个都可以|你挑|你定|你选|你看着办)")
CANCEL = re.compile(r"算了|不要了|不听了|不用了|不唱了|不想听了|当我没说|不点了")
# 版本：「不要现场版 / 别要翻唱」→ 去掉带这些词的；「要原唱 / 要原版」→ 去掉所有带这些词的；「要现场版」→ 只要带的
VER = {"live": r"现场|live|演唱会|音乐会|巡演", "cover": r"翻唱|翻自|cover", "inst": r"伴奏|karaoke|off ?vocal|消音|纯音乐",
       "remix": r"dj|remix|混音|抖音"}
VER_WORD = re.compile(r"(" + "|".join(VER.values()) + r")", re.I)
NOT_WANT = re.compile(r"(?:不要|别要?|不想要|不是|别给我)\s*(?:那个|要|听|唱)?\s*(" + "|".join(VER.values()) + r")", re.I)
WANT_ORIG = re.compile(r"原唱|原版|录音室|官方版|正式版")
WANT_VER = re.compile(r"(?:要|听|唱)\s*(?:那个)?\s*(" + "|".join(VER.values()) + r")", re.I)
FILLER = re.compile(r"那个|这个|那首|这首|版本|的|要|吧|呀|啊|嘛|呗|就|唱|听|选")
# 11-03 v1.17.13（Gemini 4-F3）：「不要第一个」「别唱第一个」「不要周杰伦的」「算了不听周杰伦了」是在说不要它 —— 以前当成选了它
_NEG_PICK = re.compile(r"(?:不要|别要?|不想要|不是|不听|不唱|别唱|别放)(?:那个|要|听|唱|放)?\s*(?:第\s*)?$")
_NEG_AFTER = re.compile(r"^(?:的|那个|那首|那版|版)*(?:不要|别要|不想要|不听|不唱|别唱|别放|算了|不行)")


def choice_sec():
    return max(5, int(_cfg.get("choice_sec", 120) or 120))


def _flat(t):
    return re.sub(r"[\W_]+", "", str(t or "").lower())


def find_link(text, log=print):
    """他这句里的音乐链接 → music.find_url 的结果 {"platform", "id", "url"} / None
    （music 还没有 find_url、出错 → 当没有 照旧按歌名找；日志写一行）"""
    if not text or not URL.search(text):
        return None
    try:
        import music
        fu = getattr(music, "find_url", None)
        if not callable(fu):
            log("[唱歌] 他发了链接 但 music.py 还不会认链接（没有 find_url）→ 照旧按歌名找")
            return None
        got = fu(text)
    except Exception as e:
        log(f"[唱歌] 认音乐链接出错：{repr(e)[:100]} → 照旧按歌名找")
        return None
    return got if isinstance(got, dict) and (got.get("url") or got.get("id")) else None


def opt_label(o):
    o = o or {}
    if o.get("label"):
        return str(o["label"])[:40]
    return (f"{o.get('artist')}《{o.get('title')}》" if o.get("artist") else f"《{o.get('title') or o.get('video') or '?'}》")[:40]


def _hay(o):
    return " ".join(str(o.get(k) or "") for k in ("label", "title", "artist", "video", "up")).lower()


# 看着是在选版本、但对不上（只认挑版本的说法：原唱 / 原版 / 哪个版 / 第几个 / 单个数字；「那来首稻香听听」这种点别的歌不算）
CHOICE_TRY = re.compile(r"原唱|原版|翻唱|现场|伴奏|版本|哪个版|那个版|这个版|[男女]版|第\s*[一二三四五六七八九1-9]|^[一二三四五六七八九1-9]$")
_miss = {}                      # 对话 -> 他回的对不上这几个选项（choice_miss 那一轮给她看）
_need_list = {}                 # 对话 -> 她这一轮要把选项列出来（她没列 系统补上）


def opts_compact(opts):
    return "；".join(f"{i}. {opt_label(o)}" for i, o in enumerate(opts or [], 1))


def _opt_keys(o):
    out = []
    for k in (o.get("artist"), o.get("up")):
        k = re.sub(r"\s+", "", str(k or ""))
        if not k:
            continue
        out += [k, re.sub(r"[(（][^)）]*[)）]", "", k)] + re.findall(r"[(（]([^)）]+)[)）]", k)
    return [x for x in out if len(x) >= 2] or [re.sub(r"\s+", "", opt_label(o))]


def opts_listed(text, opts):
    """她这句里每个选项都提到了（歌手 / UP / 标签）—— 他才选得了"""
    flat = re.sub(r"\s+", "", str(text or ""))
    return all(any(k in flat for k in _opt_keys(o)) for o in (opts or []) if isinstance(o, dict))


def with_options(text, opts):
    """她问「要哪个」却没把几个版本列出来（11-02 v1.17.11 现场：「云水谣搜出来三个诶 你要哪个呀」）→ 系统把选项补在后面（只补事实 不改她的话）"""
    if not opts or not str(text or "").strip() or opts_listed(text, opts):
        return text
    return str(text).rstrip() + f"（{opts_compact(opts)}）"


def ensure_options(conv, text):
    """choice_miss 那一轮：她这句要把选项列出来；没列 → 补上（只补一次）"""
    v = _need_list.pop(str(conv), None)
    if not v or time.time() - float(v.get("t") or 0) > 300 or "[[不说]]" in str(text or ""):
        return text
    return with_options(text, v.get("options"))


def _neg_who(text, options):
    """他说了不要哪个歌手 / UP 的（「不要周杰伦的」「算了不听周杰伦了」「周杰伦的不要」）→ 那个选项的下标；没有 / 说了好几个 → None
    （11-03 v1.17.13 Gemini 4-F3）"""
    flat = _flat(re.sub(r"@\S+", " ", URL.sub(" ", str(text or ""))))
    hits = []
    for i, o in enumerate([o for o in (options or []) if isinstance(o, dict)]):
        w = next((w for w in (_flat(o.get("artist")), _flat(o.get("up"))) if len(w) >= 2 and w in flat), "")
        if w:
            hits.append((i, w))
    if len(hits) != 1:
        return None
    i, w = hits[0]
    k = flat.find(w)
    return i if (_NEG_PICK.search(flat[:k]) or _NEG_AFTER.match(flat[k + len(w):])) else None


def pick_option(text, options):
    """他回的这句选的是第几个 → (下标, 怎么认出来的)；认不出 → (None, "")
    数字 / 第几个 / 最后一个 / 随便；歌手名、UP 名；「要原唱 / 不要现场版」；标题里只有一个选项有的词"""
    opts = [o for o in (options or []) if isinstance(o, dict)]
    n = len(opts)
    t = URL.sub(" ", str(text or ""))
    t = re.sub(r"@\S+", " ", t).strip()      # 11-02 v1.17.11：「@她的名字 1」→「1」（以前带着 @ 认不出数字 落到「标题里的词」认错成第 3 个）
    if not t or n == 0:
        return None, ""
    m = PICK_NUM.match(t) or PICK_NTH.search(t)
    if m:
        if _NEG_PICK.search(t[:m.start(1)]) or _NEG_AFTER.match(t[m.end():].lstrip()):
            return None, ""                   # 「不要第一个」「别唱第一个」「第一个不要」：不是选它（11-03 v1.17.13）→ 认不出、让她再问一次
        k = _cn_num(m.group(1))
        return (k - 1, f"第 {k} 个") if 1 <= k <= n else (None, "")
    if PICK_LAST.search(t):
        return n - 1, "最后一个"
    if PICK_ANY.match(t):
        return 0, "随便（第一个）"
    hays = [_hay(o) for o in opts]
    flat = _flat(t)
    who = [i for i, o in enumerate(opts)
           if any(len(w) >= 2 and w in flat for w in (_flat(o.get("artist")), _flat(o.get("up"))))]
    if len(who) == 1:
        d = _neg_who(t, opts)                  # 「不要周杰伦的」「算了不听周杰伦了」（11-03 v1.17.13：以前当成选了周杰伦）
        if d is None:
            return who[0], "说了歌手"
        if CANCEL.search(t):
            return None, ""                    # 算了 不听了：plan 那边收起来
        left = [i for i in range(n) if i != d]
        nm = str(opts[d].get("artist") or opts[d].get("up") or "")[:12]
        return (left[0], f"不要{nm}的") if len(left) == 1 else (None, "")   # 剩两个以上：认不出（plan 划掉他不要的那个 再问一次）
    nw = NOT_WANT.search(t)
    if nw or WANT_ORIG.search(t):
        pat = re.compile(next(v for v in VER.values() if re.fullmatch(v, nw.group(1), re.I)) if nw else VER_WORD.pattern, re.I)
        left = [i for i in range(n) if not pat.search(hays[i])]
        if nw and 0 < len(left) < n:
            return left[0], "不要" + nw.group(1)
        if not nw and len(left) == 1 and n > 1:       # 「要原唱」：只剩一个不带版本词的才算选了它（11-03 v1.17.13 Codex F7）
            return left[0], "要原唱"                  # 剩两个以上（不同歌手 / 不同人传的）= 还是拿不准 → 认不出（choice_miss 再问一次）
        return None, ""
    wv = WANT_VER.search(t)
    if wv:
        pat = re.compile(next(v for v in VER.values() if re.fullmatch(v, wv.group(1), re.I)), re.I)
        has = [i for i in range(n) if pat.search(hays[i])]
        if 0 < len(has) < n:
            return has[0], "要" + wv.group(1)
    # 标题里的词：他说的话里有一段只出现在一个选项里（「钢琴的」「抖音那个」）；最长的那段说了算
    core = FILLER.sub("", flat)[:30]
    fh = [_flat(h) for h in hays]
    for L in range(len(core), 1, -1):
        one = set()
        for a in range(0, len(core) - L + 1):
            w = core[a:a + L]
            if not re.search(r"[一-鿿]", w) and L < 4:      # 两三个英文字母到处都撞上
                continue
            hit = [i for i in range(n) if w in fh[i]]
            if len(hit) == 1:
                one.add(hit[0])
        if len(one) == 1:
            return one.pop(), "说了标题里的词"
        if len(one) > 1:
            return None, ""
    return None, ""


def choice_of(conv):
    return _choice.get(conv)


def _can_pick(ch, by):
    return not ch.get("by") or not by or str(ch["by"]) == str(by)


def picking(conv, by, text):
    """群里没 @ 她的一句：是不是在回她问的「要哪个」（点歌的人 choice_sec 秒内回的、认得出选哪个 / 说不要了）"""
    ch = _choice.get(conv)
    if not ch or time.time() - float(ch.get("t") or 0) > choice_sec() or not _can_pick(ch, by):
        return False
    return pick_option(text, ch.get("options"))[0] is not None or bool(CANCEL.search(str(text or "")))


def drop_choice(conv):
    return _choice.pop(conv, None)


def take_choice(conv, ch, log=print):
    """到点了没人选（机器人后台那个任务调）：这个还是那次问的 → 收起来、唱第一个（给一次许可 + 记成「选了第一个」）→ 选项；
    已经选了 / 又点了别的 / 说不要了（_choice 换了或没了）/ 说过别唱 / 今天唱够了 → None"""
    if _choice.get(conv) is not ch:
        return None
    _choice.pop(conv, None)
    s = _state()
    cs = s["conv"].get(conv) or {}
    now = time.time()
    if now < float(cs.get("mute_until") or 0):
        log(f"[唱歌] {conv} 拿不准那首没人选 但这个对话说过别唱 → 不唱")
        return None
    if int(s.get("count", 0)) >= int(_cfg.get("daily_max", 30)):
        log(f"[唱歌] {conv} 拿不准那首没人选 但今天唱够了 → 不唱")
        return None
    opt = (ch.get("options") or [None])[0]
    if not isinstance(opt, dict):
        return None
    _picked[conv] = {"t": now, "opt": opt, "n": 1, "by": ch.get("by") or "", "how": "没人选 唱第一个", "auto": True}
    _link.pop(conv, None)
    _permits[conv] = {"until": now + 120, "left": 1}
    return opt


def _fresh(d, conv, sec=KEEP_SEC):
    v = d.get(conv)
    if v and time.time() - float(v.get("t") or 0) <= sec:
        return v
    if v:
        d.pop(conv, None)
    return None


# 他在问那首整首好了没（「唱完了吗」「收到没」「好了没」「歌呢」「咋还没」）—— 11-02 v1.17.3 C74 问题 7：整首还在后台弄的时候这么问
# 以前没有任何状态（只有再点歌才有 v1.17.1 那句）→ 她以为自己没发 改口再唱一段（可能重复发）
FULL_Q = re.compile(r"唱完了?(?:吗|么|没|嘛)|唱好了?(?:吗|么|没)|收到(?:了)?(?:吗|么|没)|没收到|还没收到|好了(?:吗|么|没)|好没好|"
                    r"弄好了?(?:吗|么|没)|做好了?(?:吗|么|没)|发了(?:吗|么|没)|发(?:过来)?了没|(?:歌|整首|那首)(?:呢|在哪|去哪)|"
                    r"(?:咋|怎么|为啥|为什么)还没|还要多久|要等多久|还得多久|多久好")


def full_job_in(conv):
    """这个对话里正在后台弄的那首整首 → (点歌人的 QQ, {"conv", "who", "song", "t"}) / None（sing_state.json 的 full_jobs）"""
    s = _state()
    jobs = s.get("full_jobs") if isinstance(s.get("full_jobs"), dict) else {}
    got = [(u, j) for u, lst in jobs.items() for j in (lst or []) if isinstance(j, dict) and j.get("conv") == str(conv)]
    return max(got, key=lambda x: float(x[1].get("t") or 0)) if got else None


def plan(conv, scene, texts, her_last="", her_t=0, force=False, by="", log=print):
    """这一轮跟唱歌有没有关系（正文在 _plan）
    11-03 v1.17.13（Codex F5）：回复的那一轮（by = 这一轮说话的人、不是 force）不是 sing / talk → 回复那几轮给的、没用掉的唱歌许可收掉
      （「这一轮有人要她唱 才能唱」—— 以前返回 None / wait 的那一轮不收：上一轮剩的许可 300 秒内她在无关的一轮写了标记照样唱）
      talk 照旧给许可（唱不唱她自己看）。不收的：到点唱（_task_say 不带 by：它 force 给的、别的到点的事那一轮也不收）、
      拿不准没人选唱第一个（take_choice 给的 没有 by）、整首（_full_ok 另一套）—— 这几条拿到许可马上就用 别被同时进来的一轮收走
      上一轮刹车停下、没唱成的（他又补了一句 下一轮一起回 → bot 那边 hold）：留给他自己的下一轮一次（别人那几轮也不收）"""
    m = _plan(conv, scene, texts, her_last, her_t, force, by, log)
    if not force and by and m not in ("sing", "talk"):
        pm = _permits.get(conv)
        if isinstance(pm, dict) and pm.get("by"):
            h = str(pm.get("hold") or "")
            if h and float(pm.get("until") or 0) > time.time():
                if h == str(by):
                    pm.pop("hold", None)
                    log(f"[唱歌] {conv} 上一轮刹车停下、没唱成的许可 → 留给他这一轮")
            else:
                _permits.pop(conv, None)
    return m


def hold(conv, uid, now=None):
    """这一轮给了许可、她也写了唱歌标记 但发之前刹车停了（他又补了一句 → 下一轮一起回）→ 许可留给他下一轮（11-03 v1.17.13）
    普通许可：他下一轮 plan 不是 sing / talk 也先不收（只留这一次）；整首许可：他下一轮不是要整首的那一轮也先不收（full_kept 只留这一次）
    → 留了几种（0 = 没有能留的）"""
    now = float(now or time.time())
    n = 0
    pm = _permits.get(conv)
    if isinstance(pm, dict) and float(pm.get("until") or 0) > now and int(pm.get("left", 1) or 0) > 0:
        pm["hold"] = str(uid)
        n += 1
    fo = _full_ok.get(str(conv))
    if isinstance(fo, dict) and float(fo.get("until") or 0) > now and str(fo.get("uid") or "") == str(uid):
        fo["hold"] = True
        n += 1
    return n


def _plan(conv, scene, texts, her_last="", her_t=0, force=False, by="", log=print):
    """这一轮跟唱歌有没有关系 → None / sing（让她唱）/ talk（聊到唱歌）/ none（一首都不会）/ wait（刚唱过）/ cap / muted / off
    force：她答应过要唱、到点了（承诺表）—— 当成有人点名要（10-31 v1.15.17）
    by：这一轮说话的人（11-01 v1.16.0：记下谁点的歌；她问了要哪个版本 只认点歌的人选）"""
    raw = " ".join(str(t) for t in (texts or [])[-3:] if t)
    if not raw:
        return None
    texts = his_words(texts)               # 11-02 v1.17.12：认「要唱 / 选哪个 / 别唱了」只看他自己打的字（图的描述、引用的那条不算）
    joined = " ".join(t for t in texts[-3:] if t)
    now = time.time()
    if _full_conv.get(conv) and not force and FULL_Q.search(joined):   # 整首还在后台弄 他在问好了没（11-02 v1.17.3）：给状态 这一轮不唱别的
        _wait_left.pop(conv, None)
        _wait_why[conv] = "full_q"
        _wait_job[conv] = (str(by or ""), full_job_in(conv))
        log(f"[唱歌] {conv} 整首还在后台弄 他在问好了没 → 告诉她还在弄（这一轮不唱别的）")
        return "wait"
    # 11-01 v1.16.0：她刚问了要哪个版本（choice_sec 秒内）→ 他回的这句选了哪个 / 说不要了 / 又点了别的
    ch, pick = _choice.get(conv), None
    if ch and now - float(ch.get("t") or 0) > choice_sec() + 60:
        _choice.pop(conv, None)                         # 早过期了（后台那个任务没起来 比如重启过）
        ch = None
    if ch and now - float(ch.get("t") or 0) <= choice_sec() and _can_pick(ch, by):
        for t in reversed([x for x in (texts or [])[-3:] if x]):
            i, how = pick_option(t, ch.get("options"))
            if i is not None:
                pick = {"t": now, "opt": ch["options"][i], "n": i + 1, "by": str(by or ch.get("by") or ""), "how": how,
                        "req": ch.get("req") or ""}
                break
        if pick:
            _choice.pop(conv, None)
            log(f"[唱歌] {conv} 他选了第 {pick['n']} 个（{how}）：{opt_label(pick['opt'])}")
        elif CANCEL.search(joined) or (NEG.search(joined) and not CHOICE_TRY.search(re.sub(r"[\s\W_]", "", joined))):
            # 11-03 v1.17.13（Gemini 4-F3）：「别唱第一个」是在挑（下面 choice_miss 再问一次）—— 以前收起来以后又被当成「别唱了」静音 6 小时
            _choice.pop(conv, None)
            log(f"[唱歌] {conv} 问了要哪个版本 他说不要了 → 不问了")
    link = find_link(raw, log)                       # 链接从原文找：分享卡片里的歌照样认（11-02 v1.17.12）
    if ch and not pick and conv in _choice and (link or ASK.search(joined)):
        _choice.pop(conv, None)                         # 没选 又点了别的（或者发了链接）：按新点的来
        log(f"[唱歌] {conv} 问了要哪个版本 他又点了别的 → 不问了 按新的来")
    # 11-02 v1.17.11：他回了、看着是在选、但对不上这几个（「原版的」—— 几个里分不出哪个是原版）→ 以前这里不管：
    # 她说「原版收到」又写唱歌标记 → 系统再搜一遍、再问一遍（选项在她那句「收到」后面才出来）→ 顺序全乱。现在让她再问一次（带上选项）、这一轮不唱
    if ch and not pick and conv in _choice and _can_pick(ch, by) and now - float(ch.get("t") or 0) <= choice_sec():
        said = next((str(x) for x in reversed(list(texts or [])[-3:]) if x), "")
        core = re.sub(r"@\S+|[\s\W_]", "", said)
        drop = _neg_who(said, ch.get("options"))      # 「不要周杰伦的」、剩的不止一个（11-03 v1.17.13）：划掉他不要的那个 再问一次（到点也不唱它）
        if core and (CHOICE_TRY.search(core) or drop is not None) and not CANCEL.search(said):
            if drop is not None and len(ch.get("options") or []) > 2:
                ch["options"] = [o for i, o in enumerate(ch.get("options") or []) if i != drop]
            ch["t"] = now                               # 重新计时（到点唱第一个的那个后台任务每次都现读这个）
            _miss[conv] = {"t": now, "said": re.sub(r"@\S+", "", said).strip()[:30], "options": list(ch.get("options") or [])}
            _need_list[conv] = {"t": now, "options": list(ch.get("options") or [])}
            _permits.pop(conv, None)                    # 这一轮别再写标记去搜（会再问一遍）
            log(f"[唱歌] {conv} 问了要哪个版本 他回的「{_miss[conv]['said']}」对不上这几个 → 让她再问一次（带上选项 这一轮不唱）")
            return "choice_miss"
    enc = bool(ENCORE.search(joined))
    # 她刚（3 分钟内）说到要唱 / 在聊唱歌 他接着回「不退不退」「来吧」这种没带唱字的 → 也算在聊唱歌（10-31 盲测：她说开嗓了 却没唱）
    ctx_talk = bool(her_last and now - float(her_t or 0) < 180 and TALK.search(her_last))
    if not (force or enc or TALK.search(joined) or ctx_talk or pick):
        return None
    s = _state()
    cs = s["conv"].setdefault(conv, {})
    encore = enc and now - float(cs.get("last", 0) or 0) < ENCORE_SEC    # 刚唱过 他说「再来一首」
    if enc and not encore and not TALK.search(joined) and not pick:
        return None                                    # 没唱过的「再来一个」是在说别的
    _wait_why.pop(conv, None)
    if NEG.search(joined):
        cs["mute_until"] = now + 6 * 3600
        _save(s)
        _permits.pop(conv, None)
        return "muted"
    # 点歌时发了音乐链接（「唱这个 <链接>」/ 她刚问想听什么 他甩了个链接）= 点名要唱（11-01 v1.16.0）
    asked = bool(ASK.search(joined)) or encore or force or bool(pick) or bool(link and ctx_talk)
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
    if _full_conv.get(conv) and asked:                 # 11-02 v1.17.1：这个对话里给人唱的整首还在后台弄（几分钟）→ 这会儿先不唱别的
        _wait_left.pop(conv, None)                     # （以前照样给许可：一段先发出去、整首后到；换声服务正忙着整首 这一段干等 90 秒再发原唱）
        _wait_why[conv] = "full"
        if pick and ch:
            _choice[conv] = ch
        return "wait"
    gap = int(_cfg.get("ask_gap_sec", 20)) if asked else int(_cfg.get("gap_sec", 120))   # 有人点名要：只隔 ask_gap_sec（10-31 v1.15.15）
    if now - cs.get("last", 0) < gap:
        _wait_left[conv] = max(1, int(gap - (now - cs.get("last", 0))))
        if pick and ch:
            _choice[conv] = ch                         # 选了但现在还不能唱：问的那次留着（到点照样唱第一个 / 过会儿再选）
        return "wait" if asked else None
    _permits[conv] = {"until": now + 300, "left": 1, "by": str(by or "")}   # by：谁那一轮给的（到点唱 = 空 11-03 v1.17.13）
    # 这一轮的链接 / 选的那个：只认这一轮的（上一首的链接别带到下一首 11-01 v1.16.0）
    if pick:
        _picked[conv] = pick
    else:
        _picked.pop(conv, None)
    if link and not pick:
        _link[conv] = {"t": now, "link": link, "by": str(by or "")}
        log(f"[唱歌] {conv} 他发了{PLAT.get(link.get('platform'), '')}链接点歌：{str(link.get('url') or link.get('id'))[:60]}")
    else:
        _link.pop(conv, None)
    _asker[conv] = (str(by or ""), now)                # 拿不准要问的时候 记成是他点的（只认他选）
    w = want_sec(URL.sub(" ", joined))               # 链接里的「t=30s」不算他要唱几秒
    if w:
        _want[conv] = (w, now + 600)
    else:
        _want.pop(conv, None)
    return "sing" if asked else "talk"


def _list(pool):
    out = []
    for s in pool[:8]:
        c = (s.get("credit") or "").strip()
        out.append(f"- 《{s.get('name')}》" + (f"（{c[:60]}）" if c else ""))
    return "\n".join(out)


def block(mode, texts=None, conv=None):
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
    if mode == "choice_miss":                      # 11-02 v1.17.11：他回的对不上这几个版本
        m = _miss.get(conv) or {}
        return (f"\n# 唱歌\n你刚问他要哪个版本：{opts_compact(m.get('options'))}。他回的「{m.get('said', '')}」对不上这几个"
                "（系统分不出他要的是哪个）：再问他一次 这几个照样列给他 让他回数字或者歌手；这一轮别写唱歌标记。\n")
    if mode == "cap":
        return "\n# 唱歌\n对方想听你唱歌 但你今天唱太多了 嗓子累了：这次不唱 可以说明天再唱。别写唱歌标记。你是会唱歌的 别说你不会。\n"
    if mode == "wait" and conv and _wait_why.get(conv) == "full_q":
        by, job = _wait_job.get(conv) or ("", None)
        uid, j = job if job else ("", {})
        who = "他" if (not uid or str(uid) == str(by)) else (str((j or {}).get("who") or "别人") or "别人")
        song = str((j or {}).get("song") or "").strip()
        mins = int((time.time() - float((j or {}).get("t") or time.time())) // 60)
        return (f"\n# 唱整首\n给{who}唱的整首" + (f"《{song}》" if song else "") + "还在录"       # 11-04 v1.17.16 小明：说「弄」像在处理东西 → 照她在唱、在录写
                + (f"（{mins} 分钟前开始录的" if mins >= 1 else "（刚开始录") + "，录好了系统自己发出去）：还没发出去 不是没唱、也不是发丢了。"
                "这一轮别再唱一段、别写唱歌标记。\n")
    if mode == "wait" and conv and _wait_why.get(conv) == "full":
        return ("\n# 唱歌\n对方想听你唱歌 但你给人唱的一首整首还在录（录好了系统自己发出去）：这会儿先不唱别的 让他等整首发出去再点。"
                "别写唱歌标记；你是会唱歌的 别说你不会、别说没这功能。\n")
    if mode == "wait":
        left = _wait_left.get(conv) if conv else None
        return ("\n# 唱歌\n对方想听你唱歌 但你上一首刚发出去" + (f"：还差 {left} 秒才能再唱" if left else "") +
                " —— 只是两首之间要隔一下（嗓子、声音都好好的 不是唱不了）。这次先不唱 让他过一会儿再点。"
                "别写唱歌标记；你是会唱歌的 别说你不会、别说没这功能。\n")
    pool = songs()
    joined = " ".join(t for t in his_words(texts)[-3:] if t)
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
              "前后可以各说一句很短的话；别写歌词 别描述自己在唱。\n")      # 11-02 v1.17.3 E1：以前写了例句「咳咳 献丑了」她照抄
    else:
        s += (f"这轮有人聊到唱歌：有人问你会唱什么 就报你会的；有人点了歌名让你唱 {how}"
              "只是聊到唱歌 就正常聊。\n")
    s += "有人问歌是谁唱的、谁做的 就照括号里的出处说 别编。\n"
    s += _net_note(conv)
    return s


def _net_note(conv):
    """这一轮他选了哪个版本 / 发了链接点歌：告诉她（只给状态 她自己说 11-01 v1.16.0）"""
    if not conv:
        return ""
    pk, lk = _fresh(_picked, conv), _fresh(_link, conv)
    if pk:
        o = pk.get("opt") or {}
        nm = (str(o.get("title") or "") + (f" - {o.get('artist')}" if o.get("artist") else "")).strip(" -") or opt_label(o)
        return (f"你刚才问他要哪个版本 他选了第 {pk.get('n')} 个：{opt_label(o)}（{pk.get('how')}）。"
                f"就唱这个：单独一行写 [[唱:{nm}]]（系统照他选的那个去拿 不用再问）。\n")
    if lk:
        pl = PLAT.get((lk.get("link") or {}).get("platform"), "音乐")
        return (f"他发了一个{pl}的链接要你唱（系统照这个链接去拿歌 不用问他是哪首）："
                "单独一行写 [[唱:链接里的歌名]]，不知道歌名就只写 [[唱]]。\n")
    return ""


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


# ───────────── 唱整首（11-01 v1.16.5）─────────────
FULL_DEFAULTS = {"enabled": True, "beg_min": 10, "full_max_sec": 360, "full_cover_timeout": 600,
                 "circles": {"老板": {"daily": -1, "beg": False, "grudging": False, "tsundere": False},
                             "最亲": {"daily": -1, "beg": False, "grudging": False, "tsundere": True},
                             "熟人": {"daily": 2, "beg": False, "grudging": True, "tsundere": False},
                             "认识的": {"daily": 1, "beg": True, "grudging": False, "tsundere": False},
                             "陌生人": {"daily": 1, "beg": True, "grudging": False, "tsundere": False},
                             "别人家的鱼": {"daily": 0, "beg": False, "grudging": False, "tsundere": False}}}
TOP = ("老板", "最亲")            # 「最高档」：不用求、不限次（config 能改）
# 明说要整首：「整首 / 全曲 / 完整版 / 唱完（这首）/ 从头唱到尾 / 唱全 / 别只唱一段」
FULL_ASK = re.compile(r"(?:一)?整首|全曲|完整版|完整的|唱完整|唱全(?!了)|从头唱到尾|从头到尾唱|全部唱|唱完(?:整首|这首|它|吧|呗|嘛|好不好|行不行)|"
                      r"(?:别|不要)只?唱一?(?:段|小段|句)|唱长一点")
# 夸歌 / 说别的：「整首都好听」「全曲都很燃」「唱完了」「唱不完」
FULL_NOT = re.compile(r"(?:整首|全曲|完整版)(?:歌)?(?:都|也)(?!唱|给|来|发)|"
                      r"(?:整首|全曲|完整版)(?:歌)?(?:很|太|超|真|挺|特别|蛮)?(?:好听|难听|不错|一般|好燃|燃)|唱完了|唱不完|唱得完")
FULL_SURE = re.compile(r"唱(?:一)?整首|整首(?:给|唱|来)|(?:要|听|来)(?:个|一)?(?:整首|全曲|完整版)")
BEG = re.compile(r"求你|求求|求了|拜托|好不好|行不行|可不可以|就一次|唱嘛|嘛\s*$|嘛[!！~～。]")
_beg = {}                     # (对话, QQ) -> {"t", "said"}：要他再求一次的那一档 第一次要整首（beg_min 分钟内有效）
_full_ok = {}                 # 对话 -> {"uid", "until", "circle", "t", "how"}：这一轮给了整首许可（她写 [[唱:…]] 就唱整首）
_full_busy = {}               # QQ -> 正在后台弄的整首数（算进名额 免得同时要两首突破）
_which = {}                   # (对话, QQ) -> {"t"}：他要整首、没说哪首、她最近也没唱 → 问了他哪首（11-03 v1.17.13 小明 定：他回了歌名就唱整首）
WHICH_SEC = 600               # 问了哪首以后 多久内他回的那句算报了歌名


def full_cfg():
    c = _cfg.get("full") if isinstance(_cfg.get("full"), dict) else {}
    out = {k: c.get(k, v) for k, v in FULL_DEFAULTS.items() if k != "circles"}
    cc = c.get("circles") if isinstance(c.get("circles"), dict) else {}
    out["circles"] = {k: dict(v, **(cc.get(k) if isinstance(cc.get(k), dict) else {})) for k, v in FULL_DEFAULTS["circles"].items()}
    return out


def full_on():
    return bool(_cfg.get("enabled", True)) and bool(full_cfg().get("enabled", True))


# 11-03 v1.17.13（Codex F12）：以前按单个字去（来 / 你 / 好 / 想 / 一…）、剩一个字也算 —— 歌名里有这些字就被吃掉：
#   「我想听后来整首」剩「后」、「整首爱你」剩「爱」、「想听好想你的完整版」一个字不剩 → 当成刚才那首（唱错歌 / 明明报了歌名还先问哪首）
#   现在只去掉整段的套话（我想听 / 给我 / 来一首 / 唱个 / 那就 / 吧…；「你 / 能 / 要 / 好」只在后面跟着「唱 / 给 / 想…」时才算；
#   「好 / 行 / 可以 / 谢谢」只在句尾或者单独一个词时才算；笑声、语气词也算）、去完一个字都不剩才算「刚才那首」；有书名号《…》= 报了歌名
_ANA_FILL = re.compile(
    r"@\S+|从头唱到尾|从头到尾唱|从头到尾|一整首|整首|全曲|完整版|完整的|完整|全部|整个|唱长一点|(?:别|不要)只?唱一?(?:小段|段|句)|"
    r"我还想听|我想听|我要听|我想要|还想听|想听|要听|想要|我想|我要|给我|给咱们|给咱|帮我|"
    r"来一首|来一个|来一遍|来首|来个|唱一下|唱一首|唱一遍|唱一个|唱首|唱个|唱下去|唱到底|唱到尾|唱下|唱完(?!整)|唱全|唱|听|接着|继续|"
    r"那就|就|那(?=你|就|首|个)|这次|这回|这一次|一次|"
    r"(?:你|您)的(?=完整|整首|全曲|全部)|(?:你|您)(?=唱|给|来|再|把|帮|就|也|还|能|可|会|要|想)|把(?=它|这|那|歌|整|全|刚)|"
    r"(?:能不能|可不可以|可以|能|会不会|会)(?=唱|给|来|发|再|把|帮)|要(?=整|全|完|听|唱|个|首|一)|"
    r"(?:好|超|很|真的?|特别|非常|贼|巨)(?=想|要)|"
    r"(?:好不好|行不行|可不可以|能不能|成不成|中不中|可以吗|可以嘛|可以不|可以吧|可以的|行吗|行嘛|行不|行吧|好吗|好嘛|好不|成吗|成不|"
    r"谢谢你?|谢啦|多谢|可以|好|行|成|中|(?i:ok))(?=(?:[\s\W_]|[吧呗嘛啊呀哦嗯呢啦了吗么嘞捏呐哇喔噢哟呦咯哒吖])*$)|"
    r"(?<!\w)(?:好|行|成|可以|(?i:ok))(?!\w)|好的|好吧|好呀|好啊|好哒|好滴|行吧|行啊|行呀|"
    r"刚才那首|刚刚那首|刚才唱的|刚刚唱的|刚才的|刚刚的|刚才|刚刚|那首|这首|它|"
    r"求求你|求你了|求求|求了|求你|拜托|麻烦|请|再|也|还|都|一点|一些|(?i:pls|plz|please)|[哈嘿嘻呵嗷呜嘤]+|"
    r"吧|呗|嘛|啊|呀|哦|嗯|呢|啦|了|吗|么|嘞|捏|呐|哇|喔|噢|哟|呦|咯|哒|吖|的|版|歌|一下|一首|一遍|个|首|[\s\W_]")


def _anaphoric(texts):
    """要整首的那句只是在说「刚才那首」（没另外报歌名）：去掉整段的套话以后一个字都不剩（11-02 v1.17.11；11-03 v1.17.13 改）"""
    last = next((x for x in reversed(his_words(texts)) if x and FULL_ASK.search(x)), "")
    if not last or "《" in last:
        return False                     # 有书名号 = 报了歌名
    return not _ANA_FILL.sub("", last)


def full_ask(texts):
    """这几句里明说要唱整首（夸歌的「整首都好听」不算；图的描述、引用的那条不算 11-02 v1.17.12）"""
    j = " ".join(t for t in his_words(texts)[-3:] if t)
    if not j or not FULL_ASK.search(j):
        return False
    return not FULL_NOT.search(j) or bool(FULL_SURE.search(j))


def _full_day():
    return clock.her_day()


def full_used(uid, day=None):
    """他今天（她的一天）已经听了几次整首（真发出去的）"""
    s = _state()
    f = s.get("full") if isinstance(s.get("full"), dict) else {}
    if f.get("day") != (day or _full_day()):
        return 0
    return int((f.get("by") or {}).get(str(uid)) or 0)


def full_start(uid, conv=None):
    _full_busy[str(uid)] = _full_busy.get(str(uid), 0) + 1
    if conv:
        _full_conv[str(conv)] = _full_conv.get(str(conv), 0) + 1


def full_end(uid, conv=None):
    n = _full_busy.get(str(uid), 0) - 1
    if n > 0:
        _full_busy[str(uid)] = n
    else:
        _full_busy.pop(str(uid), None)
    if conv:
        m = _full_conv.get(str(conv), 0) - 1
        if m > 0:
            _full_conv[str(conv)] = m
        else:
            _full_conv.pop(str(conv), None)


def full_job_add(conv, uid, who="", song=""):
    """后台开始弄一首整首：记进 sing_state.json 的 full_jobs（按 QQ 号）—— 弄到一半程序重启了 开机时认得出没唱成（11-02 v1.17.1）"""
    s = _state()
    if isinstance(s, safeio.Unsaved):
        return
    jobs = s.get("full_jobs") if isinstance(s.get("full_jobs"), dict) else {}
    lst = [j for j in (jobs.get(str(uid)) or []) if isinstance(j, dict) and time.time() - float(j.get("t") or 0) < 6 * 3600]
    lst.append({"conv": str(conv), "who": str(who or "")[:20], "song": str(song or "")[:40], "t": round(time.time())})
    jobs[str(uid)] = lst[-3:]
    s["full_jobs"] = jobs
    _save(s)


def full_job_end(conv, uid):
    """这首整首弄完了（发出去 / 没弄出来 / 出错 都算）→ 从 full_jobs 里拿掉"""
    s = _state()
    if isinstance(s, safeio.Unsaved):
        return
    jobs = s.get("full_jobs") if isinstance(s.get("full_jobs"), dict) else {}
    lst = jobs.get(str(uid)) or []
    i = next((k for k, j in enumerate(lst) if isinstance(j, dict) and j.get("conv") == str(conv)), None)
    if i is None:
        return
    lst.pop(i)
    if lst:
        jobs[str(uid)] = lst
    else:
        jobs.pop(str(uid), None)
    s["full_jobs"] = jobs
    _save(s)


def full_jobs_left():
    """开机时：上次没弄完的整首（程序重启 / 关掉时还在后台弄的）→ [(QQ, {"conv", "who", "song", "t"})]；读完清掉"""
    s = _state()
    if isinstance(s, safeio.Unsaved):
        return []
    jobs = s.pop("full_jobs", None)
    if not isinstance(jobs, dict) or not jobs:
        return []
    _save(s)
    return [(uid, j) for uid, lst in jobs.items() for j in (lst or []) if isinstance(j, dict)
            and time.time() - float(j.get("t") or 0) < 6 * 3600]


def full_busy_in(conv):
    """这个对话里有没有正在后台弄的整首（11-02 v1.17.1）"""
    return bool(_full_conv.get(str(conv)))


def full_plan(conv, uid, texts, circle, smode, now=None, log=print):
    """这一轮跟唱整首有没有关系 → None（没关系 / 关着）/ {"state", "circle", "rule", "daily", "used", "beg_min"}
    state：grant（给许可）/ beg（第一次 先不答应 等他再求）/ begged（他又求了：给许可）/ cap（今天的名额用完）/ none（别人家的鱼 不唱整首）
           / busy（他上一首整首还在弄）
    smode：sing.plan 这一轮的结果 —— 不是 "sing"（刚唱过 / 说过别唱 / 今天唱够了…）就照原来的规矩 不管整首；
    「他又求了」那次可能没带「唱」字（plan 没给许可）→ begged + need_permit=True（bot 那边用 plan(force=True) 补许可）"""
    if not full_on():
        return None
    now = float(now or time.time())
    fc = full_cfg()
    circle = circle if circle in fc["circles"] else "认识的"
    rule = fc["circles"][circle]
    texts = his_words(texts)             # 11-02 v1.17.12：图的描述（「三个完整的鸡蛋」）、引用的那条不算他要整首
    j = " ".join(t for t in texts[-3:] if t)
    k = (str(conv), str(uid))
    pend = _beg.get(k)
    if pend and now - float(pend.get("t") or 0) > float(fc.get("beg_min") or 10) * 60:
        _beg.pop(k, None)
        pend = None
    wh = _which.get(k)                   # 问过他要哪首整首（11-03 v1.17.13）：WHICH_SEC 内他回的那句算报了歌名
    if wh and now - float(wh.get("t") or 0) > WHICH_SEC:
        _which.pop(k, None)
        wh = None
    ask = full_ask(texts)
    try:
        daily = int(rule.get("daily", 1))
    except (TypeError, ValueError):
        daily = 1
    used = full_used(uid) + _full_busy.get(str(uid), 0)
    base = {"circle": circle, "rule": dict(rule), "daily": daily, "used": used, "beg_min": int(fc.get("beg_min") or 10)}
    if pend and (ask or BEG.search(j)):
        if daily >= 0 and used >= daily:
            _beg.pop(k, None)
            return dict(base, state="cap")
        # 11-03 v1.17.13（Gemini 4-F2）：待求的这条留到真给了整首许可（full_grant 清）—— 以前这里先删了：刚唱完那段 20 秒内再求、
        #   补许可被间隔挡掉 → 这一轮没给成、待求也没了 → 过一会儿再求又从「第一次先不答应」开始
        log(f"[唱歌] {conv} {uid} 又求了一次整首（{circle}）→ 这次答应")
        return dict(base, state="begged", need_permit=smode != "sing")
    named = False
    if not ask:
        if not wh:
            return None                  # 没要整首：照原来的规矩
        if CANCEL.search(j) or NEG.search(j):
            _which.pop(k, None)
            log(f"[唱歌] {conv} {uid} 问了他要哪首整首 他说算了 → 不等了")
            return None
        if not j.strip():
            return None                  # 只发了图 / 表情包（他自己没打字）：接着等他回
        _which.pop(k, None)              # 问了他要哪首 他回了（不是算了）→ 当成报了歌名、照下面的规矩（每天几次、要不要再求）
        named = True
        log(f"[唱歌] {conv} {uid} 问了他要哪首整首 他回了「{j[-30:]}」→ 当成报了歌名")
    elif wh:
        _which.pop(k, None)              # 又说了一次要整首：照这句来
    need = False
    if smode != "sing":
        if smode not in (None, "talk"):
            return None                  # 现在本来就不能唱（刚唱过 / 说过别唱 / 唱够了 / 关着）：照原来的规矩（那边的状态会告诉她为什么）
        # 11-02 v1.17.11：「我想听整首」没带「唱」字 → 以前这里直接 None：没整首许可、却有聊唱歌的许可
        # → 她嘴上答应整首、写了标记、系统把刚才那段原样又发一遍。现在也算点歌：补一个许可（bot 那边 plan(force=True)）
        need = True
    if not named and _anaphoric(texts):  # 只说了「整首」没报歌名：多半是刚才那首
        song = last_song(conv, now, within=1800)   # 半小时内唱过的那首
        if song:
            base["song"] = song
        else:
            # 没说哪首、她最近也没唱：先问是哪首（这一轮不给许可 普通许可也收掉）—— 11-03 v1.17.13（Gemini 4-F4）：以前只管没带「唱」字的
            #   （「唱整首」那一轮漏过去：拿着整首许可、没有歌名）。小明 定：先问哪首 他回了歌名就唱整首（_which 记着 WHICH_SEC）
            _which[k] = {"t": now}
            _permits.pop(conv, None)
            log(f"[唱歌] {conv} {uid} 要整首 没说哪首、最近也没唱 → 先问是哪首（等他回 {WHICH_SEC // 60} 分钟）")
            return dict(base, state="which")
    if daily == 0:
        return dict(base, state="none")
    if _full_busy.get(str(uid)) and daily != -1:
        return dict(base, state="busy", need_permit=need)
    if daily >= 0 and used >= daily:
        return dict(base, state="cap", need_permit=need)
    if rule.get("beg"):
        _beg[k] = {"t": now, "said": j[-80:]}
        log(f"[唱歌] {conv} {uid} 要整首（{circle}）→ 第一次先不答应 等他再求（{base['beg_min']} 分钟内）")
        return dict(base, state="beg", need_permit=need)
    return dict(base, state="grant", need_permit=need)


def full_which(conv, uid, now=None):
    """问过他要哪首整首、还在等他回（WHICH_SEC 内）（11-03 v1.17.13）"""
    v = _which.get((str(conv), str(uid)))
    return bool(v) and float(now or time.time()) - float(v.get("t") or 0) <= WHICH_SEC


def full_pending(conv, uid, now=None):
    """他第一次要整首、被先不答应、还在等他再求（beg_min 分钟内）"""
    v = _beg.get((str(conv), str(uid)))
    return bool(v) and float(now or time.time()) - float(v.get("t") or 0) <= float(full_cfg().get("beg_min") or 10) * 60


def full_grant(conv, uid, fp, now=None):
    """给这一轮整首许可（她写 [[唱:…]] → 后台唱整首；300 秒内有效 跟普通许可一样）"""
    now = float(now or time.time())
    _beg.pop((str(conv), str(uid)), None)        # 等他再求的那条：真给了才清（11-03 v1.17.13 Gemini 4-F2）
    _which.pop((str(conv), str(uid)), None)
    _full_ok[str(conv)] = {"uid": str(uid), "until": now + 300, "circle": fp.get("circle"), "t": now, "how": fp.get("state")}


def full_permit(conv, uid=None):
    v = _full_ok.get(str(conv))
    if not v or float(v.get("until") or 0) < time.time():
        _full_ok.pop(str(conv), None)
        return None
    if uid is not None and v.get("uid") and str(uid) != v["uid"]:
        return None
    return v


def full_drop(conv):
    return _full_ok.pop(str(conv), None)


def full_kept(conv):
    """上一轮刹车停下时留着的整首许可（hold）：这一轮不是要整首的那一轮也先不收（只留这一次 11-03 v1.17.13）"""
    v = _full_ok.get(str(conv))
    return bool(isinstance(v, dict) and v.pop("hold", None))


def full_block(fp):
    """给她的状态（E1：只给状态和规矩；怎么说她自己定 —— 不写例句、不写她要说的话）"""
    if not fp:
        return ""
    st, c, rule = fp.get("state"), fp.get("circle") or "认识的", fp.get("rule") or {}
    slow = "整首是你现在从头唱一遍、录下来再发给他：录完要几分钟，录好了系统自己发出去。"   # 11-04 v1.17.16 小明：「应该是我现在唱 给你录之类的」
    song = str(fp.get("song") or "")
    mark = (f"他要的是你刚才唱的那首的整首：单独一行写 [[唱:{song}]]（这次系统给的是整首 不是十来秒）。" if song
            else "唱的时候照常单独一行写 [[唱:歌名]]（这次系统给的是整首 不是十来秒）。")
    if st == "which":
        return "\n# 唱整首\n他想听整首 但没说是哪首、你最近也没唱过：先问他要哪首；这一轮别写唱歌标记。\n"
    if st == "beg":
        return (f"\n# 唱整首\n他要你唱整首；你们是{c}。整首你不太情愿，这次先别答应，看他会不会再求你"
                f"（{fp.get('beg_min', 10)} 分钟内他再求，就答应）。这一轮别唱整首（唱一段可以，照常写标记就是一段）。\n")
    if st == "begged":
        return f"\n# 唱整首\n他又求了：这次答应，唱整首。{slow}{mark}\n"
    if st == "grant":
        if c in TOP:
            s = "你们关系最好，整首随时给他唱，不用他求。" + ("嘴上可以不承认是特意给他唱的。" if rule.get("tsundere") else "")
        elif rule.get("grudging"):
            s = f"整首可以唱；你们是{c}，嘴上可以先不太情愿再答应。"
        else:
            s = "整首可以唱。"
        daily = int(fp.get("daily", -1))
        left = daily - int(fp.get("used", 0))
        return f"\n# 唱整首\n{s}{slow}{mark}" + (f"（今天给他的整首还剩 {left} 次，这次算一次）" if daily > 0 else "") + "\n"
    if st == "cap":
        return (f"\n# 唱整首\n今天已经给他唱过整首了（{int(fp.get('daily', 1))} 次用完）：这次不唱整首。"
                "唱一段可以（照常写 [[唱:歌名]] 就是一段）；或者让他明天再来。\n")
    if st == "none":
        return "\n# 唱整首\n他是别人家的鱼（别人部署的机器人）：整首不给唱；唱一段照常。\n"
    if st == "busy":
        return "\n# 唱整首\n给他的上一首整首还在录（录好了系统会自己发）：这次别再答应新的整首；唱一段照常。\n"
    return ""


# 11-02 v1.17.11 小明：「他特别喜欢说自己跑调」—— 不是写死的：模型的习惯 + 前几次自己的说法带着。
# 不写死她该说什么：数她最近几次唱歌那句里有没有反复垫同一类话（自谦 / 打预防针），有就当状态告诉她（跟 style 的口头禅同一个思路）
SING_HEDGE = re.compile(r"跑调|走调|走音|不在调|音不准|五音不全|破音|献丑|别嫌弃?|别笑(?:我|话)?|凑合听|将就(?:着)?听|没练过|嗓子不行|唱得不好")
_SAID = {"t": 0.0, "rows": []}


def _said_rows(now=None, days=3, n=8):
    """存档里她最近 n 次唱歌那句（带 [[唱:…]] 的）去掉标记以后的话；10 分钟读一次存档"""
    now = float(now or time.time())
    if now - _SAID["t"] < 600 and _SAID["rows"]:
        return _SAID["rows"]
    import json as _json
    rows = []
    for i in range(days - 1, -1, -1):
        d = ROOT / "data" / "archive" / clock.strf("%Y-%m-%d", now - i * 86400)
        for f in (d.glob("*.jsonl") if d.is_dir() else []):
            try:
                for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if "[[唱:" not in ln or '"assistant"' not in ln:
                        continue
                    try:
                        e = _json.loads(ln)
                    except ValueError:
                        continue
                    if e.get("role") != "assistant":
                        continue
                    txt = re.sub(r"\[\[[^\]]*\]\]|%%STATE%%.*", " ", str(e.get("text") or ""), flags=re.S).strip()
                    if txt:
                        rows.append((float(e.get("ts") or 0), txt[:80]))
            except OSError:
                pass
    rows.sort()
    _SAID.update(t=now, rows=[x for _, x in rows][-n:])
    return _SAID["rows"]


def said_block(now=None):
    """唱歌那一轮的状态：她最近几次唱歌时反复垫的同一类话（E1：只给状态 不写她该说什么）"""
    try:
        rows = _said_rows(now)
    except Exception:
        return ""
    got = [m.group(0) for m in (SING_HEDGE.search(x) for x in rows) if m]
    if len(rows) < 3 or len(got) < 3:
        return ""
    from collections import Counter as _C
    w = _C(got).most_common(1)[0][0]
    return (f"\n# 你最近唱歌时说的（状态）\n- 最近 {len(rows)} 次唱歌，有 {len(got)} 次都垫了「{w}」这类话\n"
            "这一轮除非对方先提起 别再说这类话。\n")


def full_done(conv, uid):
    """整首真发出去了：扣他今天的名额（按她的一天）、收掉许可 → 今天第几次"""
    _full_ok.pop(str(conv), None)
    s = _state()
    day = _full_day()
    f = s.get("full") if isinstance(s.get("full"), dict) else {}
    if f.get("day") != day:
        f = {"day": day, "by": {}}
    f.setdefault("by", {})[str(uid)] = int((f.get("by") or {}).get(str(uid)) or 0) + 1
    s["full"] = f
    _save(s)
    return f["by"][str(uid)]


def cut_full(song, max_sec=None):
    """自己录的歌：整首 = 整个文件（上限 full_max_sec，超了截到上限、最后两秒淡出）→ (24k 单声道 wav, 秒数)；剪不了 → None"""
    src = SONG_DIR / str(song.get("file") or "")
    if not src.is_file():
        return None
    ff = ffmpeg()
    if not ff:
        return None
    lim = float(max_sec or full_cfg().get("full_max_sec") or 360)
    total = secs(song)
    b = min(total, lim) if total else lim
    try:
        mt = int(src.stat().st_mtime)
    except OSError:
        return None
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    out = CLIP_DIR / f"full_{song.get('id')}_{int(b)}_{mt}.wav"
    if out.exists() and out.stat().st_size > 2000:
        return out, b
    af = ["afade=t=out:st=%.2f:d=2" % max(0.0, b - 2)] if total and total > lim + 0.5 else []
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-t", f"{b:.2f}", "-i", str(src), "-vn", "-ac", "1", "-ar", "24000",
           "-sample_fmt", "s16"] + (["-af", ",".join(af)] if af else []) + [str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=max(120, int(b)), creationflags=NO_WINDOW)
    except Exception:
        return None
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 2000:
        try:
            out.unlink()
        except OSError:
            pass
        return None
    _prune_full()
    return out, b


def _prune_full(keep=6):
    try:
        fs = sorted(list(CLIP_DIR.glob("full_*.wav")) + list(CLIP_DIR.glob("full_*.mp3")), key=lambda p: p.stat().st_mtime)
        for p in fs[:-keep]:
            p.unlink()
    except Exception:
        pass


def to_mp3(path, src=None):
    """整首语音发不出去 → 转成 mp3（发音频文件用）；自己录的歌从原文件转（音质好）→ 路径 / None"""
    ff = ffmpeg()
    p = Path(src or path)
    if not ff or not p.is_file():
        return None
    lim = float(full_cfg().get("full_max_sec") or 360)
    stem = Path(path).stem
    out = CLIP_DIR / ((stem if stem.startswith("full_") else "full_" + stem) + ".mp3")
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-t", f"{lim:.2f}", "-i", str(p), "-vn", "-codec:a", "libmp3lame",
           "-b:a", "128k", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300, creationflags=NO_WINDOW)
    except Exception:
        return None
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 2000:
        return None
    return out


def render_full(req, conv, log=print):
    """有整首许可、她写了唱歌标记 → (QQ 语音段, 歌, 0, 秒数, 文件路径, 原文件 / None)；唱不了 → None（原因在 _fail）
    自己录的：整个文件（上限 full_max_sec）；网上的：完整音频不剪（music.full_mode：只去头尾静音、超了截到上限）、换声超时按长度放宽"""
    fp = full_permit(conv)
    name = str((req.get("name") if isinstance(req, dict) else req) or "").strip()
    if not fp:
        _fail[conv] = {"name": name, "why": "没整首许可"}
        return None
    pm = _permits.get(conv)
    if pm and pm.get("left", 0) > 0:
        pm["left"] -= 1                     # 普通许可一起用掉（隔多久才能再唱 照旧）
    fc = full_cfg()
    lim = float(fc.get("full_max_sec") or 360)
    pk, lk = _fresh(_picked, conv), _fresh(_link, conv)
    song = None if (pk or lk) else (find(name) if name else None)
    if song:
        got = cut_full(song, lim)
        if not got:
            _fail[conv] = {"name": song.get("name") or name, "why": "剪不出来", "err": True}
            return None
        p, b = got
        log(f"[唱歌] {conv} 唱整首《{song.get('name')}》（自己录的 {b:.0f} 秒）")
        return {"type": "record", "data": {"file": p.resolve().as_uri()}}, dict(song, full=True), 0.0, b, p, SONG_DIR / song["file"]
    if not (pk or lk) and not name:
        _fail[conv] = {"name": "", "why": "没歌名"}
        return None
    if not _cfg.get("online", True):
        _fail[conv] = {"name": name, "why": "还不会"}
        return None
    import music
    _picked.pop(conv, None)
    _link.pop(conv, None)
    t0 = time.time()
    studio = bool((_cfg.get("cover") or {}).get("enabled"))
    shown = name or (opt_label(pk.get("opt")) if pk else "他发的链接里的歌" if lk else "")
    try:
        with music.full_mode(lim):
            if pk:
                got = _fetch_pick(music, pk.get("opt") or {}, log, studio)
            elif lk:
                got = _fetch_link(music, lk.get("link") or {}, name, log, studio)
            else:
                got = _fetch_name(music, name, log, studio, ask=False)
    except Exception as e:
        log(f"[唱歌] {conv} 现找整首「{shown}」出错：{repr(e)[:150]}")
        _fail[conv] = {"name": shown, "why": "出错了", "err": True}
        return None
    if not isinstance(got, dict) or got.get("fail") or not got.get("path"):
        _fail[conv] = {"name": shown, "why": (got.get("fail") if isinstance(got, dict) else "") or "没找到",
                       "ne_abroad": bool(getattr(music, "ne_abroad", lambda: False)())}
        return None
    p = Path(got["path"])
    _covering["full"] += 1
    try:
        fy = cover(p, log, timeout=float(fc.get("full_cover_timeout") or 600))
    finally:
        _covering["full"] = max(0, _covering["full"] - 1)
    if fy:
        p = fy
    sec = float(got.get("sec") or 0)
    log(f"[唱歌] {conv} 整首《{got.get('title')}》{got.get('artist') or ''}（{got.get('source')}，{sec:.0f} 秒，"
        f"一共用了 {time.time() - t0:.0f}s）" + ("· 换成她的声音了" if fy else ""))
    artist = got.get("artist") or ""
    if got.get("source") == "B站" and artist and artist == got.get("up"):
        artist = ""
    meta = {"name": got.get("title") or name or shown, "artist": artist, "online": True, "fy": bool(fy), "full": True,
            "source": got.get("source"), "up": got.get("up") or "", "video": got.get("video") or ""}
    _last_online[conv] = {"t": time.time(), "title": got.get("title") or name, "artist": got.get("artist") or "",
                          "id": got.get("id"), "source": got.get("source"), "video": got.get("video") or ""}
    return {"type": "record", "data": {"file": p.resolve().as_uri()}}, meta, 0.0, sec, p, None


def clip_of_full(meta, path, log=print):
    """整首发不出去（语音、文件都不行）→ 剪一段（自己录的照 span 剪；网上的从整首里挑一段）→ (语音段, 歌, 开始, 结束) / None"""
    if not meta.get("online") and meta.get("file"):
        a, b = span(meta, True)
        p = cut(meta, a, b)
        return ({"type": "record", "data": {"file": p.resolve().as_uri()}}, meta, a, b) if p else None
    try:
        import music
        out = CLIP_DIR / (Path(path).stem + "_clip.wav")
        got = music._make_clip(Path(path), int(_cfg.get("online_sec", 18)), out)
        if not got:
            return None
        st, length, how = got
        log(f"[唱歌] 整首发不出去 → 从里面剪一段（{how} {st:.0f}~{st + length:.0f} 秒）")
        return {"type": "record", "data": {"file": out.resolve().as_uri()}}, meta, st, st + length
    except Exception as e:
        log(f"[唱歌] 从整首里剪一段出错：{repr(e)[:100]}")
        return None


_fail = {}                    # 对话 -> 这次没唱成的原因（机器人那边拿去说一声）


def render(req, conv, log=print, short=False, song=None):
    """要唱了：查许可 → 歌单里找 → 没有就去网上现找 → 剪 → (QQ 语音消息段, 歌, 开始, 结束)
    唱不了返回 None（原因写日志；现找没找到的 记在 _fail 里 机器人会补一句「没找到」）
    11-01 v1.16.0：这一轮他发了链接 → 照链接拿；他选了哪个版本 → 照选的拿；网上拿不准 → 不唱、记下选项（_fail why=拿不准）"""
    pm = _permits.get(conv)
    name = str((req.get("name") if isinstance(req, dict) else req) or "").strip()
    if not song:
        if not pm or pm["until"] < time.time() or pm["left"] <= 0:
            log(f"[唱歌] {conv} 没人要她唱 这次不唱（她自己写了唱歌标记）")
            _fail[conv] = {"name": name, "why": "没许可"}      # 机器人那边补一句（10-28 审查 reply-6：以前一句不补）
            return None
        if _choice.pop(conv, None):        # 问了要哪个版本、这会儿她又要唱（他点了别的 / 选了）：那次就不等了（到点别再唱第一个）
            log(f"[唱歌] {conv} 问了要哪个版本 这会儿又要唱了 → 那次不等了")
        pk, lk = _fresh(_picked, conv), _fresh(_link, conv)
        if pk or lk:                       # 他选了哪个 / 发了链接：照这个拿（歌名可以空、她写的歌名不拿去歌单里找）
            pm["left"] -= 1
            _picked.pop(conv, None)
            _link.pop(conv, None)
            if not _cfg.get("online", True):
                log(f"[唱歌] {conv} 要照" + ("他选的那个" if pk else "他发的链接") + "去网上拿 但现找关着 → 不唱")
                _fail[conv] = {"name": name or ("他选的那个" if pk else "他发的链接里的歌"), "why": "现找关着", "link": bool(lk)}
                return None
            return _net(conv, name, log, pick=pk, link=lk)
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
            return _net(conv, name, log)
    w = _want.get(conv)
    w = w[0] if w and w[1] > time.time() else 0
    a, b = span(song, short, w)
    t0 = time.time()
    p = cut(song, a, b)
    if not p:
        log(f"[唱歌] 《{song.get('name')}》剪不出来（" + ("没装 ffmpeg" if not ffmpeg() else "ffmpeg 出错 / 文件坏了") + "）")
        if not short:                      # 第一次就剪不出来：让机器人补一句没唱成（剪短重发那次失败 机器人那边自己会处理）
            _fail[conv] = {"name": song.get("name") or name, "why": "剪不出来", "err": True}
        return None
    log(f"[唱歌] {conv} 唱《{song.get('name')}》{a:.0f}~{b:.0f} 秒（剪好用了 {time.time() - t0:.1f}s）")
    return {"type": "record", "data": {"file": p.resolve().as_uri()}}, song, a, b


def _fetch_name(music, name, log, studio, ask):
    """按歌名找；ask=True 且 config 开着：拿不准时让 music 回 {"ask"}（music 还不认 ask → 照旧直接找 日志写一行）"""
    sec, skw = int(_cfg.get("online_sec", 18)), ({"studio": True} if studio else {})
    if ask and _cfg.get("ask_choice", True):
        try:
            return music.fetch(name, log, sec, SESSDATA["v"], ask=True, **skw)
        except TypeError as e:
            if "ask" not in str(e):
                raise
            log("[唱歌] music.fetch 还不认 ask（旧版）→ 拿不准也不问 照旧直接找")
    return music.fetch(name, log, sec, SESSDATA["v"], **skw)


def _fetch_pick(music, opt, log, studio):
    """照他选的那个拿（music.fetch_pick）；没有这个函数 / 出错 → 按那个的歌名歌手再找一次（不再问）"""
    fp = getattr(music, "fetch_pick", None)
    if callable(fp):
        try:
            return fp(opt, log, int(_cfg.get("online_sec", 18)), SESSDATA["v"], studio=studio)
        except Exception as e:
            log(f"[唱歌] 照选的那个拿（fetch_pick）出错：{repr(e)[:100]} → 按歌名歌手再找")
    else:
        log("[唱歌] music.py 还没有 fetch_pick → 按选的那个的歌名歌手找")
    q = str(opt.get("title") or opt.get("video") or "") + (f" - {opt['artist']}" if opt.get("artist") else "")
    return _fetch_name(music, q.strip(" -"), log, studio, ask=False) if q.strip(" -") else {"fail": "选的那个没有歌名"}


def _fetch_link(music, link, name, log, studio):
    """照他发的链接拿（music.fetch_url）；没有这个函数 / 出错 → 她写了歌名就按歌名找（不再问）"""
    fu = getattr(music, "fetch_url", None)
    if callable(fu):
        try:
            return fu(link, log, int(_cfg.get("online_sec", 18)), SESSDATA["v"], studio=studio)
        except Exception as e:
            log(f"[唱歌] 照链接拿（fetch_url）出错：{repr(e)[:100]}" + (f" → 按她写的「{name}」找" if name else ""))
    else:
        log("[唱歌] music.py 还没有 fetch_url" + (f" → 按她写的「{name}」找" if name else " 她也没写歌名 → 唱不了"))
    return _fetch_name(music, name, log, studio, ask=False) if name else {"fail": "链接里的歌没拿到"}


def _net(conv, name, log, pick=None, link=None):
    """网上现找（以前 render 里那段 11-01 v1.16.0 拆出来）：按歌名 / 照他选的 / 照链接 → 剪好的 (语音段, 歌, 0, 秒数) / None"""
    import music
    t0 = time.time()
    skw = {"studio": True} if (_cfg.get("cover") or {}).get("enabled") else {}   # 要换声：现场版往后排
    studio = bool(skw)
    shown = name or (opt_label(pick.get("opt")) if pick else "他发的链接里的歌" if link else "")
    try:
        if pick:
            got = _fetch_pick(music, pick.get("opt") or {}, log, studio)
        elif link:
            got = _fetch_link(music, link.get("link") or {}, name, log, studio)
        else:
            got = _fetch_name(music, name, log, studio, ask=True)
    except Exception as e:                 # 10-26 晚：现找出错以前一路抛到 bot 那边 连「没找到」都不补 她只说了句「咳咳」
        log(f"[唱歌] {conv} 现找「{shown}」出错：{repr(e)[:150]}")
        _fail[conv] = {"name": shown, "why": "出错了", "err": True}
        return None
    if isinstance(got, dict) and isinstance(got.get("ask"), dict):     # 拿不准是哪个：不唱 记下选项 她问他（11-01 v1.16.0）
        a = got["ask"]
        opts = [o for o in (a.get("options") or []) if isinstance(o, dict) and (o.get("label") or o.get("title"))][:3]
        if len(opts) >= 2:
            who = _asker.get(conv) or ("", 0)
            _choice[conv] = {"t": time.time(), "req": str(a.get("req") or name), "options": opts, "src": a.get("src") or "",
                             "by": who[0] if time.time() - who[1] < KEEP_SEC else ""}
            _fail[conv] = {"name": name, "why": "拿不准", "options": opts}
            log(f"[唱歌] {conv} 「{name}」网上找到 {len(opts)} 个拿不准 → 先问他要哪个：" + " / ".join(opt_label(o) for o in opts))
            return None
        try:
            got = _fetch_pick(music, opts[0], log, studio) if opts else {"fail": "没找到"}
        except Exception as e:
            log(f"[唱歌] {conv} 现找「{shown}」出错：{repr(e)[:150]}")
            _fail[conv] = {"name": shown, "why": "出错了", "err": True}
            return None
    if not isinstance(got, dict) or got.get("fail") or not got.get("path"):
        _fail[conv] = {"name": shown, "why": (got.get("fail") if isinstance(got, dict) else "") or "没找到",
                       "ne_abroad": bool(getattr(music, "ne_abroad", lambda: False)()),
                       "link": bool(link), "plat": PLAT.get((link or {}).get("link", {}).get("platform"), "") if link else ""}
        return None
    p = Path(got["path"])
    fy = cover(p, log)             # 换成她的声音（开着、换成了才换）
    if fy:
        p = fy
    log(f"[唱歌] {conv} 唱网上找的《{got.get('title')}》{got.get('artist') or ''}（{got.get('source')}，"
        f"一共用了 {time.time() - t0:.1f}s）"
        + ("· 照他发的链接" if link else f"· 他选的第 {pick.get('n')} 个" if pick else "")
        + (f"· 选的视频《{str(got.get('video'))[:40]}》：{got.get('pick_why')}" if got.get("pick_why") else ""))
    artist = got.get("artist") or ""
    if got.get("source") == "B站" and artist and artist == got.get("up"):
        artist = ""                    # 以前把 B站 UP 主记成了歌手（UP 不一定是唱的人）：聊天记录里别写错
    meta = {"name": got.get("title") or name or shown, "artist": artist, "online": True, "fy": bool(fy),
            "source": got.get("source"), "up": got.get("up") or "", "video": got.get("video") or ""}
    _last_online[conv] = {"t": time.time(), "title": got.get("title") or name, "artist": got.get("artist") or "",
                          "id": got.get("id"), "source": got.get("source"), "video": got.get("video") or ""}
    return {"type": "record", "data": {"file": p.resolve().as_uri()}}, meta, 0.0, float(got.get("sec") or _cfg.get("online_sec", 18))


def cover(path, log=print, timeout=None):
    """网上现找的那段原唱 → 本地唱歌换声服务（feiyu_voice 常驻 cover_daemon，9882）换成她的声音 → 新文件；不行 → None（照旧发原唱）
    config → sing.cover {enabled, url, key_file, timeout}（10-31 v1.15.13 实验）；timeout：整首按长度放宽（11-01 v1.16.5 full_cover_timeout）"""
    c = _cfg.get("cover") or {}
    if not c.get("enabled"):
        return None
    try:
        import json as _json
        import urllib.request
        src = Path(path).resolve()
        dst = src.with_name(src.stem + "_fy.wav")
        if dst.exists() and dst.stat().st_size > 1000 and dst.stat().st_mtime >= src.stat().st_mtime:
            log("[唱歌] 这段以前换过她的声音 → 直接用")                     # 同一首又点：不再等十几秒
            return dst
        if timeout is None and _covering["full"] > 0:          # 11-02 v1.17.1：换声服务正在换一首整首（一次只做一段 要好几分钟）
            log("[唱歌] 换声服务正忙着换一首整首 → 这一段不去排队干等（照旧发原唱）")   # 以前排队等满 90 秒超时 再照旧发原唱
            return None
        key = Path(c.get("key_file") or "").read_text(encoding="utf-8").strip()
        req = urllib.request.Request(str(c.get("url") or "http://127.0.0.1:9882").rstrip("/") + "/cover",
                                     data=_json.dumps({"input": str(src), "output": str(dst)}).encode("utf-8"), method="POST",
                                     headers={"X-API-Key": key, "Content-Type": "application/json"})
        t0 = time.time()
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # 本机服务：别走系统代理
        with op.open(req, timeout=max(float(c.get("timeout", 90)), float(timeout or 0))) as f:
            r = _json.loads(f.read().decode("utf-8"))
        if r.get("ok") and dst.exists() and dst.stat().st_size > 1000:
            log(f"[唱歌] 换成她的声音了（{time.time() - t0:.1f}s：分离 {r.get('sep_s')}s 换声 {r.get('rvc_s')}s"
                + ("、男声升了八度" if r.get("shift") else "")
                + f"；主唱音高中位数 {r.get('median_f0')} Hz、升 {r.get('shift')} 半音、去和声 {r.get('karaoke')}"   # 10-31 v1.15.15：唱得诡异时好查
                + ("、分离常驻" if r.get("sep_resident") else "") + "）")
            return dst
        log(f"[唱歌] 换声没成（{str(r)[:120]}）→ 照旧发原唱")
    except Exception as e:
        log(f"[唱歌] 换声服务连不上 / 出错（{repr(e)[:100]}）→ 照旧发原唱")
    return None


def pop_fail(conv):
    return _fail.pop(conv, None)


def done(conv, ok, song=None):
    """唱完记一笔（发出去了才算）；song：刚唱的那首（11-02 v1.17.11：他接着说「整首」时知道是哪首）"""
    if not ok:
        return
    s = _state()
    s["count"] = int(s.get("count", 0)) + 1
    cs = s["conv"].setdefault(conv, {})
    cs["last"] = time.time()
    if isinstance(song, dict) and song.get("name"):
        cs["last_song"] = (str(song.get("name")) + (f" - {song['artist']}" if song.get("online") and song.get("artist") else ""))[:80]
    _save(s)


def last_song(conv, now=None, within=None):
    """这个对话里刚唱过的那首（within 秒内，默认 ENCORE_SEC）→ 歌名（带歌手）/ ""（11-02 v1.17.11）"""
    cs = (_state().get("conv") or {}).get(conv) or {}
    now = float(now or time.time())
    if not cs.get("last_song") or now - float(cs.get("last", 0) or 0) > float(within or ENCORE_SEC):
        return ""
    return str(cs.get("last_song"))


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
