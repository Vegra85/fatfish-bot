#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大肥鱼的表情包系统（第二版：几百张也不怕）
  索引 stickers/index.json：每张 {emo 情绪, scene 什么时候用, kw 关键词, text 图上的字, file 文件, note 提醒}
  每次回复前：按最近的聊天 + 她的心情 从全部表情包里检索最贴的几张给她看（不再把所有名字塞进提示词）
  她写 [[表情:名字]] 发指定那张；也可以只写情绪 [[表情:无语]]，由代码按聊天内容挑一张
  代码负责：认名字（写得不完全对也认）、限频、防重复、换成真图

  图片来源（都是非商业分享 保留署名）：
    「大肥鱼」「大肥鱼和GPT娘」动图：B站 可以叫我云大人 / 雾雪 绘制（kiriyuki.com）
        标注数据：dsh-nekowhale-stickers（github.com/Loping151/dsh-nekowhale-stickers，CC BY-NC 4.0）
    大肥鱼形象设计 ZipZipPipe、上善无形；GPT娘形象设计 ZipZipPipe
    大肥鱼图片包（溟月 © 上善无形 / 女仆版 ZipZipPipe，CC BY-NC-SA 4.0）
    DeepSeek 鲸鱼娘表情包合集（网友整理）
"""
import json
import math
import os
import random
import re
import time
from pathlib import Path

import clock

ROOT = Path(__file__).resolve().parent
DIR = ROOT / "stickers"
INDEX = DIR / "index.json"
STATE = ROOT / "data" / "sticker_state.json"

try:
    _CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
except Exception:
    _CFG = {}
_B = _CFG.get("behavior") or {}
STICKER_GAP = int(_B.get("sticker_gap_sec", 180))   # 同一个对话里两张表情包至少隔多久
PHOTO_GAP = 20 * 3600                                # 生活照一天最多一张
NO_REPEAT = 40                                       # 同一个对话里最近发过的这么多张不重复
SHOW = 8                                             # 每次给她看几张

MARK = re.compile(r"\[\[(表情|照片)[:：]\s*([^\[\]\n]{1,24}?)\s*\]\]")

CREDIT = ("动图是 B站「可以叫我云大人」（雾雪）画的 网站 kiriyuki.com；大肥鱼的形象来自 ZipZipPipe 和 上善无形。"
          "如实说 别说是你自己画的")
_ASK_SRC = re.compile(r"(表情包?|动图|这图|图).{0,8}(哪来|哪里来|哪儿来|谁画|谁做|出处|作者|哪下|哪找|哪弄|哪搞)"
                      r"|谁画的|作者是谁")

# 对方点名要表情包（这时候「刚发过」也照发）
ASK_STK = re.compile(r"(发|来|整|给我|甩|丢|上|再来)(个|张|一张|一个|几张|两张|点)?(你的)?(表情包?|动图|斗图)|表情包呢|斗图|有表情包吗|没表情包")
# 她嘴上说了要给一张（「这张给你」「给你配张表情包」）：这时候表情包不能不发（10-26 小明：她说要发 结果没发）
PROMISE = re.compile(r"这张|这个表情|这表情|表情包|给你(配|来|发|整|一)张|送你一张|看图|如图|上图|甩你|糊你一脸")
# 老聊天记录里她写过的拼音名（以前的表情包文件名）→ 情绪
PINYIN = {"weiqu": "委屈", "haixiu": "害羞", "shuijiao": "困", "kun": "困", "haochi": "饿", "e": "饿", "chan": "饿",
          "qixiao": "嘲讽", "maozhua": "卖萌", "kaixin": "开心", "daxiao": "大笑", "xiao": "大笑", "deyi": "得意",
          "maimeng": "卖萌", "keai": "卖萌", "xihuan": "喜欢", "aixin": "喜欢", "jingya": "惊讶", "wuyu": "无语",
          "xianqi": "嫌弃", "shengqi": "生气", "nanguo": "难过", "ku": "哭", "bailan": "摆烂", "jujue": "拒绝",
          "daoqian": "道歉", "anwei": "安慰", "jiayou": "加油", "zantong": "赞同", "sikao": "思考", "yihuo": "疑惑",
          "haipa": "害怕", "chaofeng": "嘲讽", "bengkui": "崩溃", "lei": "疲惫", "ganxie": "感谢", "gandong": "感动",
          "qidai": "期待", "dazhaohu": "打招呼", "gaoguai": "搞怪", "xingfen": "兴奋", "mobai": "赞同", "dian": "赞同"}

# ───────────── 情绪：她能直接写的情绪词 + 各种说法 ─────────────
EMOS = ("开心 大笑 兴奋 得意 卖萌 喜欢 害羞 感动 感谢 赞同 打招呼 期待 疑惑 思考 惊讶 害怕 "
        "无语 嫌弃 嘲讽 生气 委屈 难过 哭 崩溃 疲惫 困 摆烂 拒绝 道歉 安慰 加油 饿 搞怪").split()
_SYN = {
    "开心": "高兴 快乐 欢呼 满足 庆祝 好耶 幸福 乐 嘻嘻 耶",
    "大笑": "笑 笑死 哈哈 哈哈哈 爆笑 笑出声 绷不住 乐死",
    "兴奋": "激动 上头 嗨 狂喜 期待死了",
    "得意": "自信 骄傲 嘚瑟 炫耀 拿捏 自豪 臭屁",
    "卖萌": "撒娇 可爱 装可怜 萌 求求 嘤嘤",
    "喜欢": "心动 爱 比心 贴贴 花痴 爱了 亲亲 亲 么么",
    "害羞": "脸红 羞 不好意思 羞耻",
    "感动": "暖 泪目 感人",
    "感谢": "谢谢 感激 谢 多谢 感恩",
    "赞同": "同意 点赞 认可 好的 收到 ok 夸 夸奖 厉害 牛 赞 对 支持 好评",
    "打招呼": "你好 招呼 问好 早安 冒泡 欢迎 嗨 hi hello 在吗 来了",
    "期待": "盼 等待 想要 等 羡慕 馋别人",
    "疑惑": "疑问 问号 不懂 好奇 懵 困惑 啥 ? ？ 迷惑",
    "思考": "想想 思索 琢磨 想一想",
    "惊讶": "震惊 吃惊 惊 卧槽 哇 惊呆",
    "害怕": "惊吓 吓 恐惧 慌 怕 救命 吓死",
    "无语": "无奈 服了 汗 尴尬 平静 ... 。。。 呃",
    "嫌弃": "恶心 呕 鄙视 辣眼睛 嫌",
    "嘲讽": "阴阳 阴阳怪气 调侃 坏笑 腹黑 挑衅 呵 嘲笑 损 呵呵",
    "生气": "愤怒 气 哼 抗议 暴躁 不爽 怒 气死 火大 烦 烦死了",
    "委屈": "可怜 冤 冤枉",
    "难过": "伤心 失落 破防 emo 心碎 丧 难受 哭了",
    "哭": "大哭 呜呜 呜呜呜 哭哭 泪",
    "崩溃": "晕 抓狂 发疯 绝望 裂开 寄 崩",
    "疲惫": "累 好累 虚脱 肝 干活 加班 累死",
    "困": "睡 睡觉 晚安 想睡 瞌睡 zzz 困了",
    "摆烂": "躺平 摸鱼 懒 不想动 偷懒",
    "拒绝": "不要 不行 不 别 禁止 不给",
    "道歉": "对不起 抱歉 认错 错了 我错了",
    "安慰": "抱抱 摸头 心疼 摸摸 不哭",
    "加油": "冲 打气 坚定 努力 冲冲冲",
    "饿": "饿了 干饭 吃饭 馋 吃 饭 好饿",
    "搞怪": "整活 抽象 调皮 恶作剧 发癫",
}
SYN = {}
for _e, _ws in _SYN.items():
    SYN[_e] = _e
    for _w in _ws.split():
        SYN.setdefault(_w.lower(), _e)
# 聊天里出现就算「对方/她现在是这个情绪」的词：两个字以上 + 几个很明确的单字
_CTX_WORDS = sorted([w for w in SYN if len(w) >= 2 or w in "饿困哭累晕怕丧"], key=len, reverse=True)
# 她写的一句描述里 这些单字也能看出情绪（「捂脸笑」→ 大笑）
_EMO_CHARS = [w for w in "笑哭气怒怕吓惊饿困累晕羞爱乐丧烦哼呕嫌懵睡馋" if w in SYN]


# 一种情绪的图都刚发过时 借用相近的情绪
RELATED = {"大笑": "开心", "哭": "难过", "难过": "哭", "委屈": "哭", "崩溃": "疲惫", "疲惫": "困", "困": "疲惫",
           "兴奋": "开心", "得意": "开心", "卖萌": "喜欢", "害羞": "喜欢", "感动": "感谢", "感谢": "感动",
           "赞同": "得意", "打招呼": "开心", "惊讶": "疑惑", "害怕": "惊讶", "无语": "嫌弃", "嫌弃": "无语",
           "嘲讽": "得意", "生气": "嫌弃", "摆烂": "疲惫", "拒绝": "嫌弃", "道歉": "委屈", "安慰": "喜欢",
           "加油": "赞同", "饿": "期待", "搞怪": "开心", "期待": "兴奋", "思考": "疑惑", "疑惑": "思考", "开心": "兴奋",
           "喜欢": "卖萌"}


def canon(e):
    """各种情绪说法 → 上面 EMOS 里的一个（认不出返回空）"""
    return SYN.get((e or "").strip().lower(), "")


def _emos_in(text):
    t = (text or "").lower()
    return {SYN[w] for w in _CTX_WORDS if w in t}


# ───────────── 索引 ─────────────
_SPLIT = re.compile(r"[\s,，、/|;；]+")
_CJK = re.compile(r"[㐀-鿿]")
_STOP = set("的时 时候 时用 或者 对方 自己 表示 表达 一下 一个 这个 那个 就是 还是 然后 但是 因为 所以 我们 你们 "
            "他们 没有 不是 可以 知道 觉得 现在 有点 一点 什么 怎么 这么 那么 大肥 肥鱼 鲸鱼 鱼娘 一起 别人 看到 "
            "聊天 时发 起来 出来 一样 好像 已经 还有 这样 那样 东西 娘的 女仆 你又 我又 你是 我是 是不 你这 我这 "
            "你的 我的 他的 了吗 了吧 的人 的话 是你 是我 给你 你个 个人 大家 真的 应该 可能 为什 这么 一直 "
            "被说 被人 用来 当作 朋友 时候 的时 之后 以后 的事 事情 一些 有些 有人 别的 其实 不过 而且 用于 "
            "很多 好多 太多 怎样 如何 知道 告诉 问你 问我 你们 咱们 今天 那就 就是 也是 都是 还有 只是".split())
# 单个字的关键词一般太泛 这些字除外（出现了就很说明问题）
_STRONG = set("胖肥饿困哭累晕气怕丑穷笨傻菜帅美甜辣冷热疼痛烦懒急馋醉吐呕寄草绝")
_c = {"m": -1, "t": 0}


def _bigrams(s):
    s = re.sub(r"\s+", "", (s or "").lower())
    return {s[i:i + 2] for i in range(len(s) - 1)
            if _CJK.match(s[i]) and _CJK.match(s[i + 1])} - _STOP


def _path(name, info):
    f = (info or {}).get("file")
    if f:
        p = DIR / f
        return p if p.is_file() else None
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        p = DIR / f"{name}{ext}"
        if p.is_file():
            return p
    return None


def _doc(name, info):
    text = (info.get("text") or "").lower()
    use = (info.get("scene") or "").lower()
    return {"name": name.lower(),
            "tags": [w.lower() for w in _SPLIT.split(info.get("kw") or "") if w],
            "emo": canon(info.get("emo")),
            "text": text, "nb": _bigrams(name), "tb": _bigrams(text), "ub": _bigrams(use),
            "db": _bigrams(info.get("cap") or "")}


def _load():
    """读索引并建好检索用的结构（改了 index.json 不用重启 自动重新读）"""
    try:
        m = INDEX.stat().st_mtime
    except OSError:
        m = None
    if _c.get("m") == m and time.time() - _c["t"] < 300:
        return _c
    try:
        idx = json.loads(INDEX.read_text(encoding="utf-8")) if m else {}
    except Exception:
        if "files" in _c:
            return _c                 # 可能正好在写 下次再读
        idx = {}
    st, ph = idx.get("stickers") or {}, idx.get("photos") or {}
    files, docs, low, groups = {}, {}, {}, {}
    for name, info in st.items():
        if info.get("off"):              # 控制台里关掉的
            continue
        p = _path(name, info)
        if not p:
            continue
        files[name] = p
        docs[name] = d = _doc(name, info)
        low[name.lower()] = name
        for a in info.get("alias") or []:
            low.setdefault(str(a).lower(), name)
        if d["emo"]:
            groups.setdefault(d["emo"], []).append(name)
    pfiles = {}
    for k, v in ph.items():
        p = _path(k, v)
        if p:
            pfiles[k] = p
    _c.update(m=m, t=time.time(), idx=idx, st=st, ph=ph, files=files, pfiles=pfiles,
              docs=docs, low=low, emo=groups)
    return _c


def index():
    return _load().get("idx") or {"stickers": {}, "photos": {}}


def names():
    return set(_load()["files"])


# ───────────── 记录：最近发过的（重启也记得） ─────────────
_last = {}       # (对话, 类型) -> 时间
_shown = {}      # 对话 -> 这轮给她看过的几张
_ctx = {}        # 对话 -> 这轮的聊天内容（她只写情绪的时候 按这个挑）
_asked = {}      # 对话 -> 截止时间：对方点名要表情包（这时候「刚发过」也照发）


def _read_state():
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return {"recent": d.get("recent") or {}, "uses": d.get("uses") or {}}


_st = _read_state()


def _recent(conv):
    return _st["recent"].get(str(conv), []) if conv else []


def _remember(conv, name):
    r = _st["recent"].setdefault(str(conv), [])
    r.append(name)
    del r[:-NO_REPEAT]
    _st["uses"][name] = _st["uses"].get(name, 0) + 1
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_st, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, STATE)
    except Exception:
        pass


def _log(*a):
    print(clock.strf("[%H:%M:%S]"), *a, flush=True)


# ───────────── 检索：这次聊天最对得上的几张 ─────────────

def _cfg_names():
    """config 里她的名字（bot_name / group_card / profile.nickname）—— 改名了不用改代码"""
    try:
        import json as _j
        from pathlib import Path as _P
        c = _j.loads((_P(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
        out = [c.get("bot_name"), c.get("group_card"), (c.get("profile") or {}).get("nickname")]
        return {str(x).strip() for x in out if x and len(str(x).strip()) >= 2}
    except Exception:
        return set()

_NAMES = re.compile("|".join(re.escape(x) for x in sorted({"大肥鱼", "肥鱼", "鲸鱼娘", "她的小名"} | _cfg_names(),
                                                          key=len, reverse=True)))


def _clean(text):
    t = MARK.sub(" ", (text or "").lower())
    t = re.sub(r"https?://\S+", " ", t)
    return _NAMES.sub(" ", t)          # 她自己的名字别误触（「不许叫我大肥鱼」那种）


def _score(d, ctx, cbig, cem, mood):
    s = 0.0
    if len(d["name"]) >= 2 and d["name"] in ctx:
        s += 6
    for t in d["tags"]:
        if t in ctx:
            s += 5 if len(t) >= 2 else (2.5 if t in _STRONG else 0.3)
    if len(d["text"]) >= 2 and d["text"] in ctx:
        s += 4
    hit = len(d["nb"] & cbig)                    # 名字里的词（一个词碰上 只对短名字算数）
    if hit >= 2 or (hit == 1 and len(d["name"]) <= 3):
        s += 3 * min(2, hit)
    hit = len(d["tb"] & cbig)                    # 图上的字
    if hit >= 2:
        s += min(3, hit)
    s += 0.8 * min(3, len(d["ub"] & cbig))       # 什么时候用
    hit = len(d["db"] & cbig)                    # 画面描述
    if hit >= 2:
        s += 0.3 * min(4, hit)
    if d["emo"]:
        if d["emo"] in cem:                      # 聊天里带着这个情绪
            s += 3
        if d["emo"] in mood:                     # 她自己现在的心情（只算加分 不单独入选）
            s += 0.8
    return s


def _wear(name):
    """发得越多的 越往后排一点（避免老是那几张）"""
    return 0.4 * math.log1p(_st["uses"].get(name, 0))


def suggest(context, mood_label="", conv=None, k=SHOW):
    c = _load()
    ctx = _clean(context)
    cbig, cem, mood = _bigrams(ctx), _emos_in(ctx), _emos_in(mood_label)
    recent = set(_recent(conv))
    scored = []
    for name, d in c["docs"].items():
        if name in recent:
            continue
        s = _score(d, ctx, cbig, cem, mood)
        if s >= 1.5:
            scored.append(((s - _wear(name)) * random.uniform(1.0, 1.2), name))
    scored.sort(reverse=True)
    out, per = [], {}
    for _, name in scored:
        e = c["docs"][name]["emo"]
        if per.get(e, 0) >= 3:           # 同一种情绪最多三张 留位置给别的
            continue
        out.append(name)
        per[e] = per.get(e, 0) + 1
        if len(out) >= k:
            break
    if len(out) < 4:                     # 聊天内容对不上：补几张常用反应
        for e in random.sample(["大笑", "无语", "疑惑", "赞同", "惊讶", "卖萌", "得意", "嘲讽"], 4):
            g = [n for n in c["emo"].get(e, []) if n not in recent and n not in out]
            if g and len(out) < 6:
                out.append(random.choice(g))
    if not out:                          # 还是空的（抽到的几种情绪正好没图）：随便给几张没刚发过的 别让菜单空着
        pool = [n for n in c["docs"] if n not in recent and n in c["files"]]
        out = random.sample(pool, min(4, len(pool)))
    return out


def _line(name, info):
    t = (info.get("text") or "").replace("\n", " ").strip()
    t = f"「{t[:14]}{'…' if len(t) > 14 else ''}」" if t else ""
    note = f" ⚠{info['note']}" if info.get("note") else ""
    use = info.get("scene") or info.get("cap") or ""
    return f"- {name}：{use}（{info.get('emo') or '其他'}）{t}{note}"


def cooldown(conv, now=None):
    """这个对话还要等几秒才能再发表情包（0 = 现在能发）"""
    left = STICKER_GAP - ((now or time.time()) - _last.get((str(conv), "表情"), 0))
    return max(0, int(left))


def asked(conv, now=None):
    return (now or time.time()) < _asked.get(str(conv), 0)


def block(context="", mood_label="", conv=None, ask_text=None):
    c = _load()
    if not c["files"]:
        return ""
    now = time.time()
    if ask_text is not None and ASK_STK.search(ask_text or ""):
        _asked[str(conv)] = now + 150                    # 对方要表情包：这轮和接下来一会儿 刚发过也照发
    left = 0 if asked(conv, now) else cooldown(conv, now)
    if left > 10:
        # 刚发过一张：这轮压根发不出去 —— 别让她写、也别让她嘴上说「这张给你」（以前写了被限频吞掉 看起来像说话不算话）
        _shown[str(conv)] = []
        return ("\n# 你的表情包\n"
                f"这轮发不了表情包（刚发过一张 还要等 {left} 秒）：别写 [[表情:…]]，也别说「发张表情包」「这张给你」"
                "「给你配一张」这种话；想表达情绪就直接用字。\n") + _photo_block(c)
    cand = suggest(context, mood_label, conv)
    _shown[str(conv)] = cand
    _ctx[str(conv)] = context or ""
    emos = " ".join(e for e in EMOS if c["emo"].get(e))
    s = ("\n# 你的表情包\n"
         f"你有 {len(c['files'])} 张自己的表情包（基本都是你本人 有些是你和 GPT娘 一起；名字带「猫」的是你学猫的样子）。"
         "想发就单独一条写 [[表情:名字]]；没有正合适的 也可以只写情绪 比如 [[表情:无语]] 会自动挑一张。\n"
         "**别每条都发**：大概四五次回复配一张就够。梗刚好接上、对方情绪很强、或者你自己情绪到了才发；拿不准就别发。"
         "一次最多一张。表情包是补语气的 不能代替回答；对方在认真说事或者真难过的时候 别发搞笑的。"
         "图上已经有字的 那句话就别再打一遍。\n")
    if cand:
        s += "这次和聊天最对得上的几张：\n" + "\n".join(_line(n, c["st"][n]) for n in cand) + "\n"
    if emos:
        s += f"能直接写的情绪：{emos}\n"
    if _ASK_SRC.search(context or ""):
        s += f"有人在问表情包是哪来的：{CREDIT}\n"
    if asked(conv, now):
        s += "对方想要表情包：这次发一张（单独一条写 [[表情:名字]]）。\n"
    s += "说了要给的就一定写标记：嘴上说「这张给你」却不写 [[表情:…]] 就是空话。\n"
    return s + _photo_block(c)


def _photo_block(c):
    ph = [f"{k}（{v.get('cap', '')}）" for k, v in c["ph"].items() if k in c["pfiles"]]
    if not ph:
        return ""
    return ("\n# 你的生活照（拍立得）\n别人问你在干嘛、要看你照片的时候 可以发一张 写 [[照片:名字]]。"
            "一天最多一张。\n" + " ".join(ph) + "\n")


# ───────────── 她写的标记 → 真图 ─────────────
def _rank(cands, conv):
    c = _load()
    ctx = _clean(_ctx.get(str(conv), ""))
    cbig, cem = _bigrams(ctx), _emos_in(ctx)
    sc = sorted(((_score(c["docs"][n], ctx, cbig, cem, set()) - _wear(n) + random.random(), n) for n in cands),
                reverse=True)
    return [n for _, n in sc]


def _best(e, conv, exclude=()):
    """某个情绪里挑一张：先看这轮给她看过的 再按聊天内容排；这种情绪的都刚发过 就借相近的情绪"""
    c = _load()
    bad = set(_recent(conv)) | set(exclude)
    for e2 in (e, RELATED.get(e)):
        if not e2:
            continue
        for n in _shown.get(str(conv)) or []:
            if n in c["docs"] and c["docs"][n]["emo"] == e2 and n not in bad:
                return n
        g = [n for n in c["emo"].get(e2, []) if n not in bad]
        if g:
            return random.choice(_rank(g, conv)[:4])
    return None


def pick(ref, conv=None):
    """她写的名字/情绪 → 真正要发的那张。返回 (名字, 怎么认出来的)；认不出 (None, '')"""
    c = _load()
    r = (ref or "").strip().strip("「」『』\"'“”")
    r = re.sub(r"\.(gif|png|jpe?g|webp)$", "", r, flags=re.I).strip().lower()
    if not r:
        return None, ""
    recent = set(_recent(conv))
    shown = _shown.get(str(conv)) or []
    e = canon(r)
    n = c["low"].get(r)
    if not n and not e and r in PINYIN:            # 以前的拼音文件名（weiqu / haixiu …）
        e = PINYIN[r]
    if n and (not e or n in shown):            # 写的就是这张的名字（名字刚好也是情绪词时 要是这轮给她看过的）
        if n not in recent:
            return n, "名字"
        alt = _best(e or c["docs"][n]["emo"], conv, exclude={n})
        return (alt, "这张刚发过 换了同情绪的") if alt else (None, "")
    if e:
        alt = _best(e, conv)
        if alt:
            return alt, f"按情绪「{e}」挑的"
    cands = [x for x, d in c["docs"].items() if x not in recent and len(d["text"]) >= 2
             and (d["text"] == r or (len(r) >= 2 and r in d["text"]))]
    if cands:                                  # 写的是图上的字（比如「V我50」）
        return _rank(cands, conv)[0], "按图上的字认的"
    if len(r) >= 2:                            # 名字写得差一点
        cands = [x for x in c["docs"] if x not in recent
                 and (r in x.lower() or (len(x) >= 2 and x.lower() in r))]
        if cands:
            return _rank(cands, conv)[0], "名字差不多"
        cands = [x for x, d in c["docs"].items() if x not in recent
                 and any(t == r or (len(t) >= 2 and (t in r or r in t)) for t in d["tags"])]
        if cands:
            return random.choice(_rank(cands, conv)[:3]), "按关键词挑的"
    es = _emos_in(r) or {SYN[w] for w in _EMO_CHARS if w in r}
    for e2 in es:                              # 写的是一句描述：里面带情绪词
        alt = _best(e2, conv)
        if alt:
            return alt, f"按情绪「{e2}」挑的"
    if n or e:
        return None, "同类的最近都发过了"
    return None, ""


def known(ref):
    """这个名字或情绪能不能认出来（换立绘用）"""
    return bool(pick(ref)[0])


def resolve(seg, conv_key):
    """把一段文字里的标记换成图片：返回 (剩下的文字, 图片段或 None)"""
    text, data, _ = resolve2(seg, conv_key)
    return text, data


def resolve2(seg, conv_key, said=""):
    """同 resolve 多返回聊天记录里该记的那个标记（真发出去的那张 [[表情:名字]]；没发 None）
    said：这一轮她别的话（看她是不是嘴上说了要给一张）"""
    m = MARK.search(seg)
    if not m:
        return seg, None, None
    kind, ref = m.group(1), m.group(2).strip()
    text = MARK.sub("", seg).strip()
    c = _load()
    k = (str(conv_key), kind)
    now = time.time()
    if kind == "照片":
        name = ref if ref in c["pfiles"] else next((x for x in c["pfiles"] if x.lower() == ref.lower()), None)
        if not name or now - _last.get(k, 0) < PHOTO_GAP:
            return text, None, None
        _last[k] = now
        return text, {"type": "image", "data": {"file": c["pfiles"][name].resolve().as_uri()}}, f"[[照片:{name}]]"
    promised = bool(PROMISE.search(f"{text} {said}"))
    if now - _last.get(k, 0) < STICKER_GAP:
        if asked(conv_key, now):
            _log(f"[表情包] {conv_key} 离上一张太近 但对方要表情包 → 照发")
        elif promised:
            _log(f"[表情包] {conv_key} 离上一张太近 但她说了要给（「{(text or said)[:16]}」）→ 照发")
        else:
            _log(f"[表情包] {conv_key} 离上一张太近 这张「{ref}」不发")
            return text, None, None
    name, how = pick(ref, conv_key)
    if not name:
        recent = set(_recent(conv_key))
        alt = next((n for n in _shown.get(str(conv_key)) or [] if n in c["files"] and n not in recent), None)
        if alt:
            name, how = alt, "认不出来 发了这轮给她看的那张"
        elif promised or asked(conv_key, now):          # 说了要给 / 对方要：同类的刚发过也照发 别食言
            r = ref.strip().lower()
            e = canon(r) or PINYIN.get(r)
            pool = [x for x, d in c["docs"].items() if e and d.get("emo") == e and x in c["files"]] \
                or [x for x in (_shown.get(str(conv_key)) or []) if x in c["files"]]
            if pool:
                name, how = random.choice(pool), "说了要给 同类的刚发过也照发"
        if not name:
            _log(f"[表情包] 「{ref}」不发：{how or '没认出来'}")
            return text, None, None
    _last[k] = now
    _remember(conv_key, name)
    _log(f"[表情包] {conv_key} 发「{name}」" + ("" if how == "名字" else f"（她写的「{ref}」 {how}）"))
    data = {"file": c["files"][name].resolve().as_uri(), "sub_type": 1, "summary": "[动画表情]"}
    return text, {"type": "image", "data": data}, f"[[表情:{name}]]"


def strip(text):
    return MARK.sub("", text or "").strip()
