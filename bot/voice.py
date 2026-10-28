#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大肥鱼的声音（第五期）
  听：别人发的 QQ 语音 → NapCat get_record 转成 wav → 本地 SenseVoice（sherpa-onnx）听写成文字
      顺便听出语气（开心/生气/难过…）和笑声、哭腔 → 写进聊天记录给她看
  说：回复里某一条写成 [[语音:情绪]]要说的话 → MiniMax 用她的专属声音合成 → 当 QQ 语音发出去
      私聊大概每 5~6 句一句；群里只在有人要她发语音时；每天有上限；合成/发送失败就照常发文字
  第一次用：双击 安装语音.bat（装「听」）；「说」要在 config 的 voice.minimax 里填 api_key 和 voice_id
"""
import asyncio
import json
import random
import re
import shutil
import subprocess
import threading
import time
import uuid
import wave
from pathlib import Path

import httpx

import clock
import safeio

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "data" / "voice"
STATE_FILE = ROOT / "data" / "voice_state.json"
try:
    _CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
except Exception:
    _CFG = {}
C = _CFG.get("voice") or {}
MM = C.get("minimax") or {}
MODEL_DIR = Path(C.get("stt_model_dir") or (ROOT.parent / "models" / "sensevoice"))
MAX_CHARS = int(C.get("max_chars", 60))

MARK = re.compile(r"\[\[语音(?:[:：]\s*([^\[\]\s]{1,6}))?\s*\]\]")
# 她能用的声音标签 → MiniMax 的语气词标签（speech-2.8 才支持）
TAGS = {"笑": "(laughs)", "大笑": "(laughs)", "轻笑": "(chuckle)", "偷笑": "(chuckle)",
        "叹气": "(sighs)", "吸气": "(breath)", "喘气": "(breath)", "倒吸气": "(gasps)",
        "哼歌": "(humming)", "咳": "(coughs)", "咳嗽": "(coughs)"}
TAG = re.compile(r"<(" + "|".join(sorted(TAGS, key=len, reverse=True)) + r")>")
EN_TAG = re.compile(r"\((laughs|chuckle|sighs|breath|gasps|humming|coughs|sneezes)\)", re.I)
# 她写的情绪 → (MiniMax emotion, 语速倍数, 音高)
EMO = {
    "开心": ("happy", 1.03, 0), "高兴": ("happy", 1.03, 0), "得意": ("happy", 1.0, 0),
    "兴奋": ("happy", 1.08, 1), "撒娇": ("happy", 0.95, 1), "害羞": ("happy", 0.95, 1),
    "心动": ("happy", 0.95, 1), "温柔": ("calm", 0.95, 0),
    "难过": ("sad", 0.92, 0), "委屈": ("sad", 0.95, 1), "伤心": ("sad", 0.9, 0), "失落": ("sad", 0.92, 0),
    "生气": ("angry", 1.05, 0), "不爽": ("angry", 1.0, 0), "凶": ("angry", 1.05, 0),
    "嫌弃": ("disgusted", 1.0, 0), "无语": ("disgusted", 0.97, 0),
    "惊讶": ("surprised", 1.05, 1), "震惊": ("surprised", 1.05, 1),
    "害怕": ("fearful", 1.0, 0), "担心": ("fearful", 0.97, 0), "紧张": ("fearful", 1.03, 0),
    "平静": ("calm", 1.0, 0), "认真": ("calm", 1.0, 0), "困": ("calm", 0.88, -1),
    "累": ("calm", 0.9, -1), "晚安": ("calm", 0.9, 0), "慵懒": ("calm", 0.9, 0),
}
EMO_NAMES = "开心/撒娇/得意/害羞/难过/委屈/生气/嫌弃/惊讶/害怕/平静/困"
# 对方想听她说话（10-26：以前「我要听语音」「想听语音」「给我语音」「发两句语音」都认不出来 → 群里她说发不了）
_N = r"(个|条|段|句|一条|一段|一句|两条|两段|两句|几条|几句|一两句)?"
_NOT = r"(?!识别|软件|输入|通话|电话|转|包|库|房|聊天室|助手|功能|消息提醒)"   # 「语音识别」「语音软件」…不是要她的语音
# 「出声 / 开口说」前面是否定（别 / 不要 / 不准 / 禁止 / 别再…）不算在要（10-28 审查 media-1 复核：以前只挡了单字「别出声」）
_NO = r"(?<![别不少甭])(?<!不要)(?<!不准)(?<!不许)(?<!禁止)(?<!别再)(?<!不再)(?<!要再)"
ASK = re.compile(r"(发|来|整|录|说|回|(?<![在正刚])听|(?<!不)要|给我|想要)" + _N + r"(你的?)?语音" + _NOT +
                 r"|语音(回|说|发|聊|版)|用语音|"
                 # 「不想听你说话」「别出声」「笑出声」不算（10-28 审查 media-1）
                 #   挡的是「不想 / 不要 / 没」这些否定：「我想要听你的声音」「我也要听你说话」照算（10-28 审查 media-1 复核：以前单字挡了「想 / 要」这两句也认不出了）
                 r"(?<![不别没听])(?<!不想)(?<!没想)(?<!不要)(?<!不想要)(想要听|想听|要听|听听?)你(的)?(声音|说话|讲话)|你(的)?声音(什么样|好听|怎么样|啥样)|"
                 + _NO + r"(?<![笑哭])出个?声|" + _NO + r"开个?口说|"
                 r"你会说话吗|说句话(给我)?听|(说|讲)(两|几|一)?句话?(给我|让我|给大家)听|念(一下|一遍|出来|给我听)|"
                 r"^\s*(@\S+\s*)*语音(呢|啊|呀|吧|嘛|来)?\s*[!！?？~～。]*\s*$")
# 刚才在聊语音的时候 「说两句」「说句话」也算（10-26 小明 在群里 @她「说两句话」她打了字）
ASK_CTX = re.compile(r"(说|讲|来)(两|几|一)?句(话)?|开口|(?<![笑哭])出声|说话")
# 「正在聊语音」只看这几个词（10-28 审查 media-1：以前还认「说话 / 开口」—— 「别说话了」自己就成了「在聊语音」的上下文）
CTX_HINT = re.compile(r"语音|声音|嗓子")
# 叫她别出声、嫌她不好好说话、不想听她说的（10-28 审查 media-1）：不算让她「说两句」
_HUSH = re.compile(r"(别|不|少|甭|不要|不用|不准|不许|禁止|好好)(再|老|总|在)?(说|讲|开口|出声)|(不想|不要|不爱|懒得|别)(再)?听")
# 对方不想要语音（上课、图书馆、开会…）
#   10-28 审查 media-1：补上「不准 / 不许 / 禁止 / 能不能不 / 别老」和光一个「不发语音」
#   （「你怎么不发语音」「不发语音吗」是在要 不算）—— 以前这几句先被 ASK 里的「发…语音」认成在要语音
#   光一个「不发语音」只认一句话开头 / 断句后（前面最多带「先 / 今天 / 以后…」）：「怎么还不发语音」「你从来不发语音」
#   「要不发语音吧」「你不发语音我就…」是在催她发 不能当成别发（10-28 审查 media-1 复核：以前这几句会被静音 12 小时）
NEG = re.compile(r"(别|不要|不用|甭|少|不想|不想要|不爱|不听|听不了|没法听|不方便听?|不准|不许|禁止|能不能不|可不可以不)"
                 r"(再|老是?|总是?|一直|天天){0,2}(给我)?(发|来|整|用|听)?(你的?)?(个|条)?语音"     # 「别老是发语音」也算（10-28 审查 media-1 复核）
                 + _NOT + "|"
                 r"(?:^|(?<=[\s,，.。!！?？~～、；;]))(先|就|那就|今天|今晚|现在|暂时|以后)?不(再)?(发|用)(你的?)?语音" + _NOT
                 + r"(?![吗呢嘛?？]|了[吗嘛?？])|"
                 r"(在|正在)(上课|开会|图书馆).{0,6}语音")

_permits = {}            # 对话 -> {"until": 截止, "left": 还能发几条}
_pause = {"until": 0, "why": ""}
_send_fail = {"n": 0}
_http = None
_rec = None
_rec_err = ""


def log_default(*a):
    print(*a, flush=True)


# ───────────── 状态（每天条数、每个对话隔了几句） ─────────────

def _state():
    try:
        s = safeio.read_json(STATE_FILE, None, print, busy_raise=True)   # 读坏了先另存 .bad（10-28 Gemini 复查：以前悄悄当成空的）
    except safeio.Busy:            # 一直打不开：谁说过「别发语音」、今天发了几条都不知道 → 这一轮不发语音、不存
        return safeio.Unsaved(day=clock.strf("%Y-%m-%d"), count=10 ** 6, chars=0, conv={})
    s = s if isinstance(s, dict) else {}
    day = clock.strf("%Y-%m-%d")
    if s.get("day") != day:
        s.update(day=day, count=0, chars=0)
    s.setdefault("conv", {})
    return s


def _save(s):
    if isinstance(s, safeio.Unsaved):
        return
    try:
        safeio.write_json(STATE_FILE, s)   # 原子写（10-28 Gemini 复查：以前直接覆盖着写 重启时被硬杀在半中间 下次读就是坏的 → 所有「别发语音」清掉）
    except Exception:
        pass


def muted_convs(now=None):
    """现在还在「别发语音」里的对话（控制台快捷操作用 10-27）"""
    now = now or time.time()
    return [c for c, v in (_state().get("conv") or {}).items() if isinstance(v, dict) and float(v.get("mute_until") or 0) > now]


def unmute_all():
    """控制台「解除别发语音」：所有对话都放开 → 放开了几个 / None（状态文件一时打不开 没改上）"""
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


def tts_ready():
    return bool(C.get("enabled", True) and (C.get("tts") or "minimax") == "minimax"
                and MM.get("api_key") and MM.get("voice_id") and time.time() >= _pause["until"])


def _permit(conv, n):
    _permits[conv] = {"until": time.time() + 240, "left": n}


def note_line(conv):
    """她打字发出去一条（用来算「隔了几句该来一条语音了」）"""
    if not conv or not conv.startswith("private_"):
        return
    s = _state()
    cs = s["conv"].setdefault(conv, {})
    cs["lines"] = int(cs.get("lines", 0)) + 1
    _save(s)


def _note_voice(conv, chars):
    s = _state()
    s["count"] = int(s.get("count", 0)) + 1
    s["chars"] = int(s.get("chars", 0)) + chars
    cs = s["conv"].setdefault(conv or "?", {})
    cs["lines"] = 0
    cs["last"] = time.time()
    _save(s)
    return s["count"]


def asked_for(texts, context=None):
    """这几句是不是在要她的语音。context = 前面几句别人说的（在聊语音的时候「说两句」也算）"""
    texts = [t for t in (texts or [])[-3:] if t]
    if any(ASK.search(t) for t in texts):
        return True
    return bool(context) and any(ASK_CTX.search(t) and not _HUSH.search(t) for t in texts) and \
        any(CTX_HINT.search(c or "") and not NEG.search(c or "") for c in context[-6:])   # 「别发语音了」不算在聊语音（10-28 审查 media-1 复核：以前接着「说两句」就解除静音）


def plan(conv, scene, texts, last_is_voice=False, context=None):
    """这一轮回复要不要发语音 → off / ask_off / cap / muted / ask / yes / no
    context：这一轮之前几分钟里别人说的话（看是不是正在聊语音）"""
    joined = " ".join(t for t in (texts or [])[-3:] if t)
    s = _state()
    cs = s["conv"].setdefault(conv, {})
    if NEG.search(joined):
        cs["mute_until"] = time.time() + 12 * 3600
        _save(s)
        return "muted" if tts_ready() else "off"
    asked = asked_for(texts, context)
    if not tts_ready():
        return "ask_off" if asked else "off"
    if int(s.get("count", 0)) >= int(C.get("daily_max", 80)):
        return "cap" if asked else ("no" if scene == "private" else "off")
    if asked:
        if cs.pop("mute_until", None):
            _save(s)
        _permit(conv, 2)
        return "ask"
    if scene != "private" or C.get("private_auto", True) is False:
        return "off"                                   # 群里：只在被要求时发
    if time.time() < cs.get("mute_until", 0):
        return "no"
    if time.time() - cs.get("last", 0) < int(C.get("min_gap_sec", 40)):
        return "no"
    if re.search(r"https?://|```", joined) or len(joined) > 200:
        return "no"                                    # 在聊长东西/发链接 不合适
    every = max(2, int(C.get("private_every", 5)))
    lines = int(cs.get("lines", 0))
    p = min(1.0, max(0.0, (lines - (every - 3)) / 4.0))  # 隔得越久越想说一句
    if last_is_voice:
        p = max(p, 0.6)                                # 对方发语音 她也更想回语音
    if random.random() < p:
        _permit(conv, 1)
        return "yes"
    return "no"


def plan_proactive(conv):
    """主动找人那条：偶尔用语音"""
    s = _state()
    if not tts_ready() or int(s.get("count", 0)) >= int(C.get("daily_max", 80)) \
            or time.time() < s["conv"].get(conv, {}).get("mute_until", 0):
        return "off"
    if random.random() < float(C.get("proactive_prob", 0.3)):
        _permit(conv, 1)
        return "yes"
    return "off"


def block(mode):
    """给她看的「语音」说明（按这一轮的情况）"""
    if mode == "off":
        return "\n# 语音\n这轮你发不了语音：有人要就直说这次发不了 别答应 也别说发过了。\n"
    if mode == "ask_off":
        return ("\n# 语音\n对方想听你的声音 但你现在发不了语音（声音还没装好）。"
                "直说现在发不了 别假装发了 也别写语音标记。\n")
    if mode == "cap":
        return ("\n# 语音\n对方想听你的声音 但你今天说太多话 嗓子哑了（语音额度用完了）。"
                "这次打字回 可以说明天再发。别写语音标记。\n")
    if mode == "muted":
        return "\n# 语音\n对方说了现在不方便听语音：这次和接下来都别发语音 打字就好。\n"
    if mode == "no":
        return "\n# 语音\n这次别发语音 正常打字。\n"
    s = ("\n# 你的声音（QQ 语音）\n"
         f"你可以发语音：把要说出口的那一句单独一条（和别的话用 ||| 隔开）写成 [[语音:情绪]]要说的话"
         f"（情绪从 {EMO_NAMES} 里挑一个 也可以不写）。\n"
         f"语音是说出来的话：口语、短（{MAX_CHARS} 字内）、一条只说一件事；别念网址、代码、英文缩写、表情包名；"
         "不写括号动作 想笑、叹气可以用 <笑> <轻笑> <叹气> <吸气> 这几个声音标签。\n"
         "发了语音的那句别再打一遍字；其他话照常打字。\n")
    desc = (MM.get("voice_desc") or "").strip()
    if desc:
        s += f"你的声音听起来是这样的：{desc[:80]}（别人夸你/吐槽你的声音时你心里有数）\n"
    if mode == "ask":
        s += "对方想听你的声音：这次用语音回（一两条 最多两条）。\n"
    else:
        s += ("这次可以挑一句最适合说出口的（撒娇、吐槽、晚安、逗他、关心他）用语音发 只要一条 其他照常打字；"
              "要说的是长解释、清单、正经资料 就别用语音了。\n")
    return s


def strip_tags(text):
    t = TAG.sub("", text or "")
    t = EN_TAG.sub("", t)
    return re.sub(r"[ 　]{2,}", " ", t).strip()


def strip(text):
    """日志/心情判断用：去掉语音标记和声音标签"""
    return strip_tags(MARK.sub("", text or ""))


def merge_marks(segs):
    """模型有时把 [[语音:x]] 单独写一行、要说的话写在下一行 → 合成一条"""
    out = []
    i = 0
    while i < len(segs):
        s = segs[i]
        if MARK.fullmatch(s.strip()) and i + 1 < len(segs):
            out.append(s.strip() + segs[i + 1])
            i += 2
            continue
        out.append(s)
        i += 1
    return out


def _tts_text(text):
    t = MARK.sub("", text or "")
    t = re.sub(r"\[\[[^\]]*\]\]", "", t)                    # 别的标记（表情包等）
    t = re.sub(r"https?://\S+", "", t)
    t = TAG.sub(lambda m: TAGS[m.group(1)], t)
    t = re.sub(r"<[^<>]{0,8}>", "", t)                      # 不认识的尖括号标签
    t = re.sub(r"[~～]{2,}", "~", t)
    return re.sub(r"\s{2,}", " ", t).strip()


# ───────────── 说：MiniMax 合成 ─────────────

def _client():
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=40)
    return _http


HOSTS = ("https://api.minimaxi.com", "https://api-bj.minimaxi.com", "https://api.minimax.io")   # 国内版×2 / 国际版
_host = {"tried": False}


async def _post_tts(base, body):
    r = await _client().post(base + "/v1/t2a_v2", json=body,
                             headers={"Authorization": "Bearer " + MM["api_key"], "Content-Type": "application/json"})
    try:
        return r.json()
    except ValueError:
        return {"base_resp": {"status_code": -1, "status_msg": f"HTTP {r.status_code} {r.text[:60]}"}}


def _pause_for(sec, why, log):
    _pause["until"] = time.time() + sec
    _pause["why"] = why
    log(f"[语音] 先停 {sec // 60} 分钟：{why}")


def _cleanup(keep=120):
    try:
        files = sorted(OUT_DIR.glob("v*.*"), key=lambda p: p.stat().st_mtime)
        for p in files[:-keep]:
            p.unlink()
    except Exception:
        pass


def _as_wav(audio, path, rate=24000):
    """MiniMax 给的是 wav 就原样存；给的是裸 pcm 就自己包一层 wav 头"""
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        path.write_bytes(audio)
        return path
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio)
    return path


on_usage = None                        # 机器人那边设：每次合成成功 → 记一笔用量（字数）


async def _tts(say_text, emo, log):
    """调 MiniMax 合成 → (音频 bytes, extra_info)；失败返回 (None, None)"""
    e, spd, pit = EMO.get((emo or "").strip(), (None, 1.0, 0))
    vs = {"voice_id": MM["voice_id"],
          "speed": round(min(2.0, max(0.5, float(MM.get("speed", 1.0)) * spd)), 2),
          "vol": float(MM.get("vol", 1.0)),
          "pitch": int(min(12, max(-12, int(MM.get("pitch", 0)) + pit)))}
    if e:
        vs["emotion"] = e
    fmt = (MM.get("format") or "wav").lower()
    body = {"model": MM.get("model") or "speech-2.8-turbo", "text": say_text, "stream": False,
            "voice_setting": vs,
            "audio_setting": {"sample_rate": 24000, "format": fmt, "channel": 1},
            "language_boost": MM.get("language_boost") or "Chinese", "output_format": "hex"}
    if fmt == "mp3":
        body["audio_setting"]["bitrate"] = 128000
    base = (MM.get("base_url") or HOSTS[0]).rstrip("/")
    try:
        j = await _post_tts(base, body)
        code = (j.get("base_resp") or {}).get("status_code", -1)
        if code in (1004, 2049) and not _host["tried"]:
            # MiniMax 国际版（minimax.io）和国内版（minimaxi.com）的 key 不通用：别的地址挨个试一次
            _host["tried"] = True
            tried = [f"{base.split('//')[-1]}→{code}"]
            for alt in [h for h in HOSTS if h != base]:
                j2 = await _post_tts(alt, body)
                c2 = (j2.get("base_resp") or {}).get("status_code", -1)
                if c2 == 0:
                    MM["base_url"] = alt
                    log(f"[语音] 这个 key 要用 {alt}（{' '.join(tried)}）已自动改用"
                        f"（可以把 config 里 voice.minimax.base_url 改成它）")
                    j = j2
                    break
                tried.append(f"{alt.split('//')[-1]}→{c2}")
            else:
                log("[语音] 每个地址都试了：" + " ".join(tried))
    except Exception as ex:
        log("[语音] 合成失败（网络）:", repr(ex)[:120])
        return None, None
    br = j.get("base_resp") or {}
    code = br.get("status_code", -1)
    if code != 0:
        msg = str(br.get("status_msg", ""))[:80]
        if code in (1004, 2049):
            _pause_for(7200, f"MiniMax key 不对（{code} {msg}）国际版和国内版都试过了 "
                             "检查 config 里 voice.minimax.api_key 是不是完整复制的", log)
        elif code == 1008:
            _pause_for(6 * 3600, "MiniMax 余额不足 去 MiniMax 开放平台充值", log)
        elif code in (1002, 1039):
            log(f"[语音] MiniMax 限流了（{code}）这句改打字")
        elif code == 2013 or "voice" in msg.lower():
            _pause_for(3600, f"参数不对（{msg}）—— 检查 config 里 voice.minimax.voice_id 写对没有", log)
        else:
            log(f"[语音] 合成失败 {code} {msg}")
        return None, None
    try:
        audio = bytes.fromhex((j.get("data") or {}).get("audio") or "")
    except ValueError:
        log("[语音] 返回的音频解不开")
        return None, None
    if len(audio) < 200:
        log("[语音] 返回的音频是空的")
        return None, None
    info = j.get("extra_info") or {}
    if on_usage:                       # 记用量：MiniMax 按 usage_characters 收钱
        try:
            on_usage(int(info.get("usage_characters") or len(say_text)))
        except Exception:
            pass
    return audio, info


def _save_audio(audio, name):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fmt = (MM.get("format") or "wav").lower()
    if fmt in ("wav", "pcm"):
        return _as_wav(audio, OUT_DIR / f"{name}.wav")
    p = OUT_DIR / f"{name}.{fmt}"
    p.write_bytes(audio)
    return p


async def synth(text, emo="", log=log_default, conv=None):
    """合成一句 → QQ 语音消息段（失败返回 None）"""
    say_text = _tts_text(text)
    if not say_text or not tts_ready():
        return None
    t0 = time.time()
    audio, info = await _tts(say_text, emo, log)
    if not audio:
        return None
    p = _save_audio(audio, f"v{int(time.time() * 1000)}{random.randint(10, 99)}")
    _cleanup()
    n = _note_voice(conv, int(info.get("usage_characters") or len(say_text)))
    log(f"[语音] 合成 {float(info.get('audio_length') or 0) / 1000:.1f}s（{emo or '自动语气'}）"
        f"「{strip_tags(text)[:30]}」 用时 {time.time() - t0:.1f}s 今天第 {n} 条")
    return {"type": "record", "data": {"file": p.resolve().as_uri()}}


async def selftest(log=log_default):
    """启动时用她的声音试说一句（不算进每天的条数）→ data/voice/selftest.wav"""
    if not tts_ready():
        return
    t0 = time.time()
    audio, info = await _tts("本鱼开嗓啦 <#0.2#> 试音 试音", "开心", log)
    if audio:
        p = _save_audio(audio, "selftest")
        log(f"[模型自检] 语音合成 OK：{float(info.get('audio_length') or 0) / 1000:.1f}s 的试音 "
            f"用时 {time.time() - t0:.1f}s → {p}")


async def render(seg, conv, log=log_default):
    """要发的一条里有 [[语音]] → (前面要打字的部分, 语音消息段或None, 语音那句的文字, 情绪, 后面要打字的部分)
    语音只念标记后面的那一行；不让发/合成失败：那句当文字发（去掉标记和声音标签）"""
    m = MARK.search(seg or "")
    if not m:
        return seg, None, "", "", ""
    before = strip_tags(MARK.sub("", seg[:m.start()]))
    said, _, after = MARK.sub("", seg[m.end():]).strip().partition("\n")
    said, after = said.strip(), strip_tags(after)
    emo = (m.group(1) or "").strip()
    plain = strip_tags(said)
    fallback = "\n".join(x for x in (before, plain, after) if x)
    pm = _permits.get(conv)
    if not plain or not pm or pm["until"] < time.time() or pm["left"] <= 0 or not tts_ready():
        return fallback, None, "", "", ""
    if len(plain) > MAX_CHARS * 1.5:
        log(f"[语音] 这句太长（{len(plain)} 字）改打字")
        return fallback, None, "", "", ""
    pm["left"] -= 1
    rec = await synth(said, emo, log, conv)
    if not rec:
        return fallback, None, "", "", ""
    return before, rec, plain, emo, after


def sent_ok(ok, log=log_default):
    """发语音的结果：连着失败 3 次就先停一小时（多半是 NapCat 转不了格式）"""
    if ok:
        _send_fail["n"] = 0
        return
    _send_fail["n"] += 1
    if _send_fail["n"] >= 3:
        _send_fail["n"] = 0
        _pause_for(3600, "QQ 语音连着发送失败 3 次（看 napcat.log）", log)


# ───────────── 听：SenseVoice 听写 ─────────────

EMO_ZH = {"HAPPY": "开心", "SAD": "难过", "ANGRY": "生气", "FEARFUL": "害怕",
          "DISGUSTED": "嫌弃", "SURPRISED": "惊讶"}
EVENT_ZH = {"Laughter": "有笑声", "Cry": "带着哭腔", "BGM": "有背景音乐", "Applause": "有掌声",
            "Cough": "咳嗽了", "Sneeze": "打了个喷嚏", "Breath": "喘着气"}


def stt_ready():
    if (C.get("stt") or "sensevoice") == "off":
        return False
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return False
    return (MODEL_DIR / "model.int8.onnx").exists() and (MODEL_DIR / "tokens.txt").exists()


def _load():
    global _rec, _rec_err
    if _rec is not None or _rec_err:
        return _rec
    try:
        import sherpa_onnx
    except Exception:
        _rec_err = "没装 sherpa-onnx（双击 安装语音.bat）"
        return None
    model, tokens = MODEL_DIR / "model.int8.onnx", MODEL_DIR / "tokens.txt"
    if not (model.exists() and tokens.exists()):
        _rec_err = f"没找到听写模型 {MODEL_DIR}（双击 安装语音.bat）"
        return None
    try:
        try:
            _rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(model), tokens=str(tokens), num_threads=int(C.get("stt_threads", 2)),
                use_itn=True, language="auto")
        except TypeError:
            _rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(model), tokens=str(tokens),
                                                                  use_itn=True)
    except Exception as e:
        _rec_err = "听写模型加载失败 " + repr(e)[:100]
        _rec = None
    return _rec


def warmup():
    """启动时先把听写模型加载好（第一条语音就不用等）"""
    if stt_ready():
        _load()
    return status()


def status():
    if (C.get("stt") or "sensevoice") == "off":
        hear = "关了"
    elif _rec is not None:
        hear = "SenseVoice 已就绪"
    elif _rec_err:
        hear = _rec_err
    else:
        hear = "SenseVoice 可用" if stt_ready() else "没装（双击 安装语音.bat）"
    if not C.get("enabled", True):
        speak = "关了"
    elif not MM.get("api_key"):
        speak = "没填 MiniMax key（config 里 voice.minimax.api_key）"
    elif not MM.get("voice_id"):
        speak = "还没选声音（config 里 voice.minimax.voice_id）"
    elif time.time() < _pause["until"]:
        speak = "暂停中：" + _pause["why"]
    else:
        speak = f"MiniMax {MM.get('model') or 'speech-2.8-turbo'} 声音 {MM['voice_id'][:24]}"
    s = _state()
    return f"听：{hear} | 说：{speak} | 今天已发 {s.get('count', 0)} 条"


def _read_audio(path):
    """读成单声道 float32；wav 直接读 其他格式用 ffmpeg 转 16k"""
    import numpy as np
    try:
        with wave.open(str(path), "rb") as w:
            sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            raw = w.readframes(n)
        if sw == 2 and sr > 0:
            a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if ch > 1:
                a = a.reshape(-1, ch)[:, 0]
            if sr == 16000:
                return a, sr
            if not shutil.which("ffmpeg"):         # 没 ffmpeg：自己线性插值转 16k（听写够用）
                n = int(len(a) * 16000 / sr)
                if n > 0:
                    a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
                return a, 16000
            # 有 ffmpeg：交给下面的 ffmpeg 转 16k（不然 sherpa-onnx 自己转 每次往日志里打一段 Creating a resampler）
    except Exception:
        pass
    ff = shutil.which("ffmpeg")
    if not ff:
        return None, 0
    p = subprocess.run([ff, "-loglevel", "error", "-i", str(path), "-ar", "16000", "-ac", "1",
                        "-f", "s16le", "-"], capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0 or not p.stdout:
        return None, 0
    return np.frombuffer(p.stdout, dtype="<i2").astype(np.float32) / 32768.0, 16000


def _tag(v, pat):
    m = re.search(pat, v or "")
    return m.group(1) if m else ""


_REC_LOCK = threading.Lock()   # 同一个听写模型一次只听一条（10-28 Codex #9：两条语音一起来时 两个线程同时用一个模型）


def recognize(path):
    """听写一个音频文件 → {"text", "emo", "event", "secs"}；听不了返回 None"""
    with _REC_LOCK:                        # 第一次用时加载模型：别两个线程一起加载
        rec = _load()
    if rec is None:
        return None
    a, sr = _read_audio(path)
    if a is None or not len(a):
        return None
    with _REC_LOCK:
        st = rec.create_stream()
        st.accept_waveform(sr, a)
        rec.decode_stream(st)
        r = st.result
    raw = str(r)
    text = (getattr(r, "text", "") or "").strip()
    emo = _tag(getattr(r, "emotion", "") or raw, r"(HAPPY|SAD|ANGRY|NEUTRAL|FEARFUL|DISGUSTED|SURPRISED)")
    ev = _tag(getattr(r, "event", "") or raw, r"(Speech|Laughter|Cry|BGM|Applause|Cough|Sneeze|Breath)")
    text = re.sub(r"<\|[^|]*\|>", "", text).strip()
    return {"text": text, "emo": emo, "event": ev, "secs": len(a) / float(sr)}


async def hear(data, call, log=log_default):
    """一条 QQ 语音 → 「[语音 5秒：说的话（听起来开心 有笑声）]」；听不了就是「[语音]」"""
    if (C.get("stt") or "sensevoice") == "off":
        return "[语音]"
    t0 = time.time()
    path = tmp = None
    params = {k: str(data[k]) for k in ("file", "file_id") if data.get(k)}
    if params:
        try:
            r = await call("get_record", out_format="wav", **params)
            d = (r or {}).get("data") or {}
            f = d.get("file") or ""
            if f and Path(f).exists():
                path = Path(f)
            elif d.get("base64"):
                import base64
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                # 每条语音自己一个文件（10-28 Codex #9：以前都写 in_last.wav 两条一起来 后一条盖掉前一条 两条听成同一句）
                path = tmp = OUT_DIR / f"in_{uuid.uuid4().hex[:12]}.wav"
                path.write_bytes(base64.b64decode(d["base64"]))
            elif r is not None:
                log("[听语音] get_record 没给文件:", str(r)[:160])
        except Exception as e:
            log("[听语音] get_record 失败:", repr(e)[:100])
    if path is None:
        p2 = data.get("path") or ""
        if p2 and Path(p2).exists():
            path = Path(p2)
    if path is None:
        log("[听语音] 拿不到语音文件")
        return "[语音]"
    try:
        res = await asyncio.to_thread(recognize, path)
    except Exception as e:
        log("[听语音] 听写出错:", repr(e)[:120])
        return "[语音]"
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
    if res is None:
        log(f"[听语音] 没听成：{_rec_err or '读不出音频'}")
        return "[语音]"
    desc = []
    if EMO_ZH.get(res["emo"]):
        desc.append("听起来" + EMO_ZH[res["emo"]])
    if EVENT_ZH.get(res["event"]):
        desc.append(EVENT_ZH[res["event"]])
    secs = max(1, int(round(res["secs"])))
    body = res["text"] or "（没听清在说什么）"
    log(f"[听语音] {secs}s →「{res['text'][:40]}」{' '.join(desc)} 用时 {time.time() - t0:.1f}s")
    return f"[语音 {secs}秒：{body}" + (f"（{' '.join(desc)}）" if desc else "") + "]"
