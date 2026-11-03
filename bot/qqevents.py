#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQ 事件感知（11-01 v1.16.3）—— 全用 NapCat 官方的事件和动作
  ① 群友给她的话贴表情（notice group_msg_emoji_like）：只认贴在她自己发的消息上的；不触发回复 ——
     她下次在这个对话里回话时（emoji_like_min 分钟内）当状态告诉她一次（同一句合并；告诉过就不再说）
  ② 她的话被设成精华（notice essence，sub_type=add，sender_id 是她）：下次她在这个群回话时告诉她一次；心情小涨（mood2.bump）
  ③ 资料卡被赞（notice notify，sub_type=profile_like）：每人每天最多回赞一次（send_like times=1）、全天上限；
     拉黑的、认出来的别家机器人不回；profile_hours 小时内她跟这个人说话时告诉她一次
  ④ 在线状态（set_online_status）按「模式」改 —— 11-02 v1.17.8（C81）起基础状态一直是 10「在线」，用扩展状态（ext_status）表达她在干嘛：
     以前 sleep / busy / slow → 请勿打扰 70 / 忙碌 50 / 离开 30：QQ 在这几个状态下会替她给私聊她的人自动回
     「[自动回复]你好，我现在有事不在，一会再和你联系。」（11-02 小明 截图：他私聊她 收到两次 —— 不是她说的 是 QQ 自己发的）
     sleep → 10 + 1016 睡觉中；busy / slow → 按日程这一段在做什么挑（status_auto 词表 从上往下第一个对上的：听歌 1028 听歌中 /
     看番·打游戏 1027 TIMI中 / 读·写·整理 1018 学习中 / 发呆·摸鱼·待着 1300 摸鱼中 / 熬夜 1032 熬夜中；凌晨（clock 熬夜中 / 该睡了）
     挑不出来 → status_late 1032 熬夜中；别的挑不出来 → 10 + 0）；free → 10 + 0
     config status_map：模式 → 扩展状态码（"auto" = 按这一段在做什么挑）；老写法（10 / 30 / 50 / 70 这种基础码）不认 按默认的扩展状态
     只在状态（基础 + 扩展）变了时改、每天最多 status_daily_max 次、连续失败 status_fail_max 次当天停用
     模式从哪来：mode_source（一个函数 → "free" / "slow" / "busy" / "sleep"）；没接就按 clock 的作息（在睡觉 → sleep，别的 → free）
     这一段在做什么：what_source（一个函数 → 字符串）；每日生活（life.py）接上：qqevents.mode_source = life.mode_now、what_source = life.what_now
  ⑤ 正在输入：收（notice notify，sub_type=input_status，私聊）→ 攒一攒多等一会儿；
     发（set_input_status，event_type=1 正在输入）→ 她开始回私聊时让对方看到「正在输入」（同一个人 typing_send_gap 秒最多一次）
  ⑥ 她发出去的消息 id → 原话、时间（最近 SAID_MAX 条）：认得出别人贴表情 / 设精华的是不是她的话
  ⑦ QQ 替对方发的自动回复（11-02 v1.17.8 C81）：私聊里以「[自动回复]」开头的 = 对方的 QQ 在他离开 / 忙碌时自己发的、不是他在说话 →
     autoreply()；bot.py 收到时只记进聊天记录（标「（自动回复：…）」）：不触发回复、不进记忆提炼 / 前情提要 / 翻记录、不算他找她
字段出处（11-01 核过，NapCat 4.18.x；小明 电脑上的 napcat.mjs 是 4.18.41，同样的类和字段）：
  NapNeko/NapCatQQ packages/napcat-onebot/event/notice/OB11MsgEmojiLikeEvent.ts（group_id / user_id / message_id / likes[{emoji_id, count}] / is_add）
  OB11GroupEssenceEvent.ts（message_id / sender_id / operator_id / sub_type）、OB11ProfileLikeEvent.ts（operator_id / operator_nick / times）、
  OB11InputStatusEvent.ts（user_id / group_id / event_type / status_text）；action/extends/SetOnlineStatus.ts（状态码：在线 10 / 离开 30 / 隐身 40 /
  忙碌 50 / Q我吧 60 / 请勿打扰 70）、action/extends/SetInputStatus.ts（user_id 字符串、event_type 数字；1 = 正在输入）、action/user/SendLike.ts
  扩展状态码（11-02 v1.17.8）：NapCatQQ 文档 extended-actions「设置在线状态」—— status 10 + ext_status：1016 睡觉中 / 1028 听歌中 /
  1018 学习中 / 1027 TIMI中 / 1300 摸鱼中 / 1032 熬夜中 / 1401 emo中（battery_status 0）
  表情编号 → 名字：对着 NapCat 的 face_config.json（QSid → QDes）挑了常用的；Unicode 表情（编号是码位）直接写那个字；认不出 → 「一个表情」
config → qq_events（见 DEFAULTS）
"""
import re
import time
from pathlib import Path

import clock
import safeio

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULTS = {"enabled": True,
            "emoji_like": True, "emoji_like_min": 60,           # 贴表情：多少分钟内她在那个对话回话才告诉她
            "essence": True, "essence_hours": 24,
            "profile_like": True, "like_back": True, "like_back_daily_max": 20, "profile_hours": 24,
            "online_status": True, "status_daily_max": 16, "status_fail_max": 3,
            "status_map": {"sleep": 1016, "busy": "auto", "slow": "auto", "free": 0},   # 模式 → 扩展状态码（11-02 v1.17.8：基础一直是 10 在线）
            "status_auto": [["听歌|听音乐|听.{0,6}歌|歌单|音乐", 1028],                     # "auto"：按这一段在做什么挑（从上往下 第一个对上的）
                            ["看番|追番|补番|刷番|番剧|看动画|打游戏|玩游戏|游戏|开黑|上分", 1027],
                            ["读|写|整理|看书|学习", 1018],
                            ["发呆|摸鱼|待着|呆着|放空|躺", 1300],
                            ["熬夜|通宵", 1032]],
            "status_late": 1032,                                                      # 凌晨（clock 熬夜中 / 该睡了）挑不出来的 → 熬夜中
            "typing_recv": True, "typing_recent_sec": 6, "typing_wait_extra": 8,
            "typing_send": True, "typing_send_gap": 8}
C = dict(DEFAULTS)
SAID_MAX = 500
MODES = ("free", "slow", "busy", "sleep")
MODE_NAME = {"free": "空闲", "slow": "慢", "busy": "忙", "sleep": "睡觉"}      # 11-02 v1.17.8：模式只是她的状态 不再一一对应 QQ 的离开 / 忙碌 / 请勿打扰
STATUS_NAME = {10: "在线", 30: "离开", 40: "隐身", 50: "忙碌", 60: "Q我吧", 70: "请勿打扰"}
BASE_STATUS = 10      # 11-02 v1.17.8 C81：基础状态一直「在线」—— 离开 30 / 忙碌 50 / 请勿打扰 70 时 QQ 会替她自动回「[自动回复]…」
EXT_NAME = {0: "", 1016: "睡觉中", 1018: "学习中", 1027: "TIMI中", 1028: "听歌中", 1032: "熬夜中", 1300: "摸鱼中", 1401: "emo中"}
# 别人的 QQ 替他发的自动回复（11-02 v1.17.8 C81）：「[自动回复]你好，我现在有事不在，一会再和你联系。」这种 —— 不是他在说话
AUTO_REPLY = re.compile(r"^\s*[\[［【]\s*自动回复\s*[\]］】]")

# QQ 系统表情编号 → 名字（常用的；对着 NapCat face_config.json 的 QSid / QDes 核过）
FACE = dict(x.split(":", 1) for x in (
    "0:惊讶,2:色,4:得意,5:流泪,6:害羞,8:睡,9:大哭,10:尴尬,12:调皮,13:呲牙,14:微笑,20:偷笑,21:可爱,23:傲慢,26:惊恐,27:流汗,"
    "28:憨笑,32:疑问,33:嘘,34:晕,38:敲打,39:再见,41:发抖,46:猪头,49:拥抱,53:蛋糕,59:便便,60:咖啡,63:玫瑰,64:凋谢,66:爱心,"
    "67:心碎,74:太阳,75:月亮,76:赞,77:踩,78:握手,79:胜利,85:飞吻,89:西瓜,96:冷汗,97:擦汗,98:抠鼻,99:鼓掌,100:糗大了,101:坏笑,"
    "104:哈欠,106:委屈,107:快哭了,109:左亲亲,111:可怜,116:示爱,118:抱拳,120:拳头,122:爱你,123:NO,124:OK,129:挥手,144:喝彩,"
    "146:爆筋,147:棒棒糖,171:茶,173:泪奔,174:无奈,175:卖萌,176:小纠结,177:喷血,178:斜眼笑,179:doge,180:惊喜,181:戳一戳,"
    "182:笑哭,183:我最美,187:幽灵,193:大笑,194:不开心,200:求求,201:点赞,202:无聊,203:托脸,204:吃,206:害怕,212:托腮,214:啵啵,"
    "262:脑阔疼,263:沧桑,264:捂脸,265:辣眼睛,266:哦哟,267:头秃,268:问号脸,269:暗中观察,270:emm,271:吃瓜,272:呵呵哒,273:我酸了,"
    "277:汪汪,281:无眼笑,282:敬礼,283:狂笑,284:面无表情,285:摸鱼,286:魔鬼笑,287:哦,289:睁眼,293:摸锦鲤,294:期待,297:拜谢,"
    "298:元宝,299:牛啊,305:右亲亲,306:牛气冲天,307:喵喵,311:打call,312:变形,314:仔细分析,317:菜汪,318:崇拜,319:比心,320:庆祝,"
    "322:拒绝,323:嫌弃,324:吃糖,325:惊吓,326:生气,332:举牌牌,333:烟花,337:花朵脸,339:舔屏,341:打招呼,342:酸Q,343:我方了,"
    "344:大怨种,346:你真棒棒,349:坚强,350:贴贴,351:敲敲,352:咦,353:拜托,354:尊嘟假嘟,355:耶,356:666,357:裂开,360:亲亲,"
    "361:狗狗笑哭,362:好兄弟,363:狗狗可怜,364:超级赞,365:狗狗生气,367:狗狗疑问,368:奥特笑哭,369:彩虹,370:祝贺,372:气呼呼,"
    "373:忙,374:波波流泪,375:超级鼓掌,376:跺脚,377:嗨,378:企鹅笑哭,379:企鹅流泪,380:真棒,381:路过,382:emo,383:企鹅爱心,"
    "384:晚安,385:太气了,386:呜呜呜,387:太好笑,388:太头疼,389:太赞了,390:太头秃,391:太沧桑,395:略略略,399:tui,400:快乐,"
    "402:别说话,407:我听听,408:臭美,409:送你花花,410:么么哒,412:开心,425:求放过,427:偷感,428:收到").split(","))

SAID = {}          # 她发出去的消息：str(message_id) → {"t", "key", "text"}（按发的先后 最多 SAID_MAX 条）
_likes = {}        # str(message_id) → {"key", "t", "said_t", "text", "people": {uid: {"name", "emo": [名字]}}, "told"}
_essence = {}      # 对话 → [{"t", "text", "op", "told"}]
_profile = {}      # QQ号 → {"t", "name", "back", "told"}
_typing = {}       # QQ号 → 最近一次「正在输入」的时间（私聊）
_typing_sent = {}  # QQ号 → 上次给他发「正在输入」的时间
mode_source = None  # 以后的日程模块接上：() → "free" / "slow" / "busy" / "sleep"
what_source = None  # 11-02 v1.17.8：日程接上：() → 她这一段在做什么（life.what_now）—— busy / slow 按它挑扩展状态
_boot = {"synced": False}   # 开机后第一次：不是「在线」就照样设一次（重开 QQ 以后状态回到在线 记录里的不一定还对）


def configure(cfg=None, data_dir=None):
    global DATA
    C.clear()
    C.update(DEFAULTS)
    for k, v in (cfg or {}).items():
        if not str(k).startswith("_") and k in DEFAULTS:
            C[k] = v
    if data_dir:
        DATA = Path(data_dir)


def on(what=None):
    if not C.get("enabled", True):
        return False
    return bool(C.get(what, True)) if what else True


def _f(k):
    try:
        return float(C.get(k, DEFAULTS[k]))
    except (TypeError, ValueError):
        return float(DEFAULTS[k])


def wants(ev):
    """这条 notice 归这里管吗（机器人收到时直接交过来：不进按对话排的队 —— 「正在输入」要快）"""
    if not isinstance(ev, dict) or ev.get("post_type") != "notice":
        return False
    nt = ev.get("notice_type")
    return nt in ("group_msg_emoji_like", "essence") or \
        (nt == "notify" and ev.get("sub_type") in ("profile_like", "input_status"))


# ───────────── 她发出去的消息 ─────────────
def remember(mid, key, text, t=None):
    if mid is None:
        return
    k = str(mid)
    SAID.pop(k, None)
    SAID[k] = {"t": float(t or time.time()), "key": str(key or ""), "text": str(text or "")[:120]}
    while len(SAID) > SAID_MAX:
        SAID.pop(next(iter(SAID)))


def mine(mid):
    return SAID.get(str(mid)) if mid is not None else None


def emoji_name(eid):
    s = str(eid if eid is not None else "").strip()
    if s in FACE:
        return FACE[s]
    if s.isdigit() and len(s) <= 7:      # Unicode 表情：编号就是码位（比如 128077 = 👍）—— 只认表情那几段
        cp = int(s)
        if 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF or 0x1F000 <= cp <= 0x1FAFF:
            return chr(cp)
    return "一个表情"


def _short(t, n=30):
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[:n] + "…"


# ───────────── ① 贴表情 ─────────────
def on_emoji_like(ev, name="", now=None):
    """→ 日志那句（贴在她的话上、记下了）/ None（不是她的话 / 关着 / 不认得）"""
    if not on("emoji_like"):
        return None
    now = float(now or time.time())
    mid = ev.get("message_id")
    m = mine(mid)
    if not m:
        return None
    uid = str(ev.get("user_id") or "")
    if not uid or uid == "0" or uid == str(ev.get("self_id") or ""):
        return None
    likes = ev.get("likes") if isinstance(ev.get("likes"), list) else []
    names = [emoji_name((x or {}).get("emoji_id")) for x in likes if isinstance(x, dict)]
    if not names:
        return None
    add = ev.get("is_add", True) is not False
    rec = _likes.get(str(mid))
    if rec is None or rec.get("told"):
        rec = _likes[str(mid)] = {"key": m["key"], "t": now, "said_t": m["t"], "text": m["text"], "people": {}, "told": False}
    p = rec["people"].setdefault(uid, {"name": str(name or uid)[:16], "emo": []})
    if name:
        p["name"] = str(name)[:16]
    for nm in names:
        if add and nm not in p["emo"]:
            p["emo"].append(nm)
        elif not add and nm in p["emo"]:
            p["emo"].remove(nm)
    if not p["emo"]:
        rec["people"].pop(uid, None)
    rec["t"] = now
    if len(_likes) > 300:
        for k in sorted(_likes, key=lambda k: _likes[k]["t"])[:100]:
            _likes.pop(k, None)
    return (f"{p['name'] if add else str(name or uid)} 给她那句「{_short(m['text'], 16)}」"
            + ("贴了" if add else "取消了") + "「" + "」「".join(names) + "」")


# ───────────── ② 精华 ─────────────
def on_essence(ev, self_id, text="", op_name="", now=None):
    """她的话被设成精华 → 记下（下次她在这个群回话时告诉她）；→ 日志那句 / None"""
    if not on("essence") or str(ev.get("sub_type") or "add") != "add":
        return None
    if not self_id or str(ev.get("sender_id") or "") != str(self_id):
        return None
    gid = str(ev.get("group_id") or "")
    if not gid:
        return None
    m = mine(ev.get("message_id"))
    text = str(text or (m or {}).get("text") or "")
    key = f"group_{gid}"
    lst = _essence.setdefault(key, [])
    lst.append({"t": float(now or time.time()), "text": text[:120], "op": str(op_name or "")[:16], "told": False})
    del lst[:-5]
    return f"群{gid} 她那句「{_short(text, 16) or '…'}」被{op_name or '有人'}设成了精华"


# ───────────── ③ 资料卡被赞 ─────────────
def _like_state(now=None):
    d = safeio.read_json(DATA / "qqevents.json", {}) or {}
    day = clock.strf("%Y-%m-%d", now)
    lk = d.get("likes") if isinstance(d.get("likes"), dict) else {}
    if lk.get("day") != day:
        lk = {"day": day, "by": {}, "n": 0}
    d["likes"] = lk
    return d


def on_profile_like(ev, blocked=False, is_bot=False, now=None):
    """→ (要不要回赞, 日志那句)；回赞成没成交给 liked_back() 记"""
    if not on("profile_like"):
        return False, None
    now = float(now or time.time())
    uid = str(ev.get("operator_id") or "")
    if not uid or uid == "0":
        return False, None
    nick = str(ev.get("operator_nick") or "")[:16] or uid
    rec = _profile[uid] = {"t": now, "name": nick, "back": None, "told": False}
    if not C.get("like_back", True):
        return False, f"{nick}({uid}) 给她资料卡点了赞（回赞关着）"
    if blocked or is_bot:
        rec["back"] = False
        return False, f"{nick}({uid}) 给她资料卡点了赞（{'拉黑的' if blocked else '别人家的机器人'} 不回赞）"
    d = _like_state(now)
    lk = d["likes"]
    if uid in lk["by"]:
        rec["back"] = True                 # 今天已经回过他了
        return False, f"{nick}({uid}) 又给她资料卡点了赞（今天已经回赞过）"
    if int(lk.get("n") or 0) >= int(_f("like_back_daily_max")):
        rec["back"] = False
        return False, f"{nick}({uid}) 给她资料卡点了赞（今天回赞满 {int(_f('like_back_daily_max'))} 个了 不回）"
    return True, f"{nick}({uid}) 给她资料卡点了赞 → 回赞一下"


def liked_back(uid, ok, now=None):
    uid = str(uid)
    rec = _profile.get(uid)
    if rec is not None:
        rec["back"] = bool(ok)
    if not ok:
        return

    def upd(d):
        day = clock.strf("%Y-%m-%d", now)
        lk = d.get("likes") if isinstance(d.get("likes"), dict) else {}
        if lk.get("day") != day:
            lk = {"day": day, "by": {}, "n": 0}
        lk["by"][uid] = float(now or time.time())
        lk["n"] = int(lk.get("n") or 0) + 1
        d["likes"] = lk
    safeio.update_json(DATA / "qqevents.json", upd, {})


# ───────────── 给她看的状态 ─────────────
def block_for(key, uid="", scene="group", now=None):
    """她这一轮在 key 这个对话回 uid：还没告诉她的事件 → 一段状态（告诉过的标上 不再说）；没有 → ""
    只给状态：谁贴了什么、哪句被设了精华、谁赞了资料卡 —— 怎么说、说不说她自己定（E1）"""
    if not on():
        return ""
    now = float(now or time.time())
    lines = []
    lim = _f("emoji_like_min") * 60
    for mid, rec in sorted(_likes.items(), key=lambda kv: kv[1].get("said_t", 0)):
        if rec.get("told") or rec.get("key") != key or not rec.get("people"):
            continue
        if now - float(rec.get("t") or 0) > lim:
            continue
        rec["told"] = True
        ppl = list(rec["people"].values())
        who = "、".join(f"{p['name']} 贴了「" + "」「".join(p["emo"]) + "」" for p in ppl[:4])
        if len(ppl) > 4:
            who += f"，还有 {len(ppl) - 4} 个人也贴了"
        mins = int(max(0.0, now - float(rec.get("said_t") or now)) // 60)
        when = f"你 {mins} 分钟前说的" if mins >= 1 else "你刚才说的"
        lines.append(f"- {when}「{_short(rec.get('text'), 40)}」：{who}")
    for rec in _essence.get(key) or []:
        if rec.get("told") or now - float(rec.get("t") or 0) > _f("essence_hours") * 3600:
            continue
        rec["told"] = True
        lines.append(f"- 你在这个群说的「{_short(rec.get('text'), 40)}」被 {rec.get('op') or '群里有人'} 设成了精华")
    rec = _profile.get(str(uid)) if uid else None
    if rec and not rec.get("told") and now - float(rec.get("t") or 0) <= _f("profile_hours") * 3600:
        rec["told"] = True
        back = {True: "（你回赞了）", False: "（你没回赞）"}.get(rec.get("back"), "")
        day = "今天" if clock.strf("%Y-%m-%d", rec["t"]) == clock.strf("%Y-%m-%d", now) else "昨天"
        lines.append(f"- {rec.get('name') or '他'} {day}给你的 QQ 资料卡点了赞{back}")
    if not lines:
        return ""
    return "\n# QQ 上的动静（系统看到的 不是谁说的话）\n" + "\n".join(lines) + "\n"


def forget(uid):
    """删数据（11-02 v1.17.1）：内存里他赞过她的资料卡、他贴过的表情、正在输入 都忘掉（文件里的 wipe 删）→ 删了几样"""
    uid, n = str(uid or ""), 0
    if not uid:
        return 0
    n += _profile.pop(uid, None) is not None
    n += _typing.pop(uid, None) is not None
    _typing_sent.pop(uid, None)
    for rec in _likes.values():
        n += (rec.get("people") or {}).pop(uid, None) is not None
    return n


# ───────────── ⑤ 正在输入 ─────────────
def on_input_status(ev, now=None):
    """私聊里对方「正在输入」→ 记时间；event_type 1 / 文字带「输入」= 在输入；别的（空的）= 停了"""
    if not on("typing_recv"):
        return None
    if str(ev.get("group_id") or "0") not in ("0", ""):
        return None
    uid = str(ev.get("user_id") or "")
    if not uid or uid == "0":
        return None
    try:
        et = int(ev.get("event_type"))
    except (TypeError, ValueError):
        et = None
    txt = str(ev.get("status_text") or "")
    if et == 1 or "输入" in txt:
        _typing[uid] = float(now or time.time())
        return True
    _typing.pop(uid, None)
    return False


def typing_recent(uid, now=None):
    if not uid or not on("typing_recv"):
        return False
    t = _typing.get(str(uid))
    return bool(t) and 0 <= float(now or time.time()) - t <= _f("typing_recent_sec")


def want_show_typing(uid, now=None):
    """要不要给他发一次「正在输入」（同一个人 typing_send_gap 秒最多一次）—— 要的话顺手记上时间"""
    if not uid or not on("typing_send"):
        return False
    now = float(now or time.time())
    if now - _typing_sent.get(str(uid), 0) < _f("typing_send_gap"):
        return False
    _typing_sent[str(uid)] = now
    if len(_typing_sent) > 2000:
        for k in sorted(_typing_sent, key=_typing_sent.get)[:500]:
            _typing_sent.pop(k, None)
    return True


# ───────────── ④ 在线状态 ─────────────
def mode_now(now=None):
    """她现在是哪种模式：日程接上了按日程；没接 → 按 clock 的作息（在睡觉 → sleep，醒着 → free）"""
    if callable(mode_source):
        try:
            m = mode_source()
            if m in MODES:
                return m
        except Exception:
            pass
    try:
        return "sleep" if clock.state()[0] == "在睡觉" else "free"
    except Exception:
        return "free"


def autoreply(text):
    """这句是 QQ 替对方发的自动回复吗（「[自动回复]」开头；11-02 v1.17.8 C81）"""
    return bool(AUTO_REPLY.match(str(text or "")))


def what_now():
    """她这一段在做什么（日程接上了才有；没接 / 出错 → ""）"""
    if callable(what_source):
        try:
            return str(what_source() or "")
        except Exception:
            return ""
    return ""


def _late():
    try:
        return clock.state()[0] in ("熬夜中", "该睡了")
    except Exception:
        return False


def pick_ext(what="", late=None):
    """busy / slow：按这一段在做什么挑扩展状态码（status_auto 从上往下第一个对上的）；挑不出来 → 凌晨 status_late / 别的 0"""
    t = str(what or "")
    rules = C.get("status_auto") if isinstance(C.get("status_auto"), list) else DEFAULTS["status_auto"]
    for r in rules:
        try:
            pat, code = r[0], int(r[1])
            if t and re.search(str(pat), t, re.I):
                return code
        except (re.error, TypeError, ValueError, IndexError, KeyError):
            continue
    if _late() if late is None else late:
        try:
            return int(C.get("status_late", DEFAULTS["status_late"]) or 0)
        except (TypeError, ValueError):
            return int(DEFAULTS["status_late"])
    return 0


def ext_for(mode, what="", late=None):
    """模式（+ busy / slow 时这一段在做什么）→ 扩展状态码（0 = 没有）
    config status_map[模式]：扩展状态码 / "auto"；老写法（10~70 的基础状态码）不认 → 用默认的"""
    sm = C.get("status_map") if isinstance(C.get("status_map"), dict) else {}
    v = sm.get(mode, DEFAULTS["status_map"].get(mode, 0))
    for _ in range(2):
        if str(v).strip().lower() == "auto":
            return pick_ext(what, late)
        try:
            n = int(v)
        except (TypeError, ValueError):
            n = -1
        if n == 0 or n >= 1000:
            return n
        v = DEFAULTS["status_map"].get(mode, 0)      # 老写法（11-02 v1.17.8 以前是基础状态码 70 / 50 / 30 / 10）/ 认不出 → 默认
    return 0


def status_for(mode, what="", late=None):
    """模式 → set_online_status 的参数：基础一直是 10 在线（11-02 v1.17.8 C81）+ 扩展状态（ext_for）"""
    return {"status": BASE_STATUS, "ext_status": ext_for(mode, what, late), "battery_status": 0}


def status_name(p):
    """set_online_status 的参数 → 「在线·睡觉中」这种（日志用）"""
    ext = int((p or {}).get("ext_status") or 0)
    base = STATUS_NAME.get((p or {}).get("status"), str((p or {}).get("status")))
    return base + (("·" + (EXT_NAME.get(ext) or str(ext))) if ext else "")


def status_info(now=None):
    d = safeio.read_json(DATA / "qqevents.json", {}) or {}
    s = d.get("status") if isinstance(d.get("status"), dict) else {}
    return dict(s)


async def set_mode(call, mode, log=print, now=None, why="", what=None):
    """按模式改 QQ 在线状态（11-02 v1.17.8：基础一直 10 在线 + 扩展状态）。→ True 改了 / False 改失败 / None 没改（一样 / 关着 / 今天停了 / 次数用完 / 没连上）
    what：busy / slow 时这一段在做什么（不给 = 问 what_source）；只在「基础 + 扩展」变了时改"""
    if not on("online_status") or mode not in MODES:
        return None
    now = float(now or time.time())
    if mode in ("busy", "slow"):
        what = what_now() if what is None else str(what or "")
    else:
        what = ""
    p = status_for(mode, what)
    code = f"{p['status']}+{p['ext_status']}"
    day = clock.strf("%Y-%m-%d", now)
    d = safeio.read_json(DATA / "qqevents.json", {}) or {}
    s = d.get("status") if isinstance(d.get("status"), dict) else {}
    if s.get("day") != day:
        s = {"day": day, "n": 0, "fails": 0, "off": False, "mode": s.get("mode"), "code": s.get("code"), "t": s.get("t", 0),
             "capped": False}
    if s.get("off"):
        return None
    if s.get("code") == code and (_boot["synced"] or code == f"{BASE_STATUS}+0"):
        _boot["synced"] = True             # 11-02 v1.17.8 以前记的是模式（没有 code）→ 部署后第一次照样设一次（换成新的码）
        return None
    if int(s.get("n") or 0) >= int(_f("status_daily_max")):
        if not s.get("capped"):
            s["capped"] = True
            d["status"] = s
            safeio.write_json(DATA / "qqevents.json", d)
            log(f"[在线状态] 今天已经改了 {s.get('n')} 次（上限 {int(_f('status_daily_max'))}）→ 今天不再改（想换成「{status_name(p)}」{code}）")
        return None
    r = await call("set_online_status", **p)
    if r is None:
        return None                        # 没连上 / 等不到回音：不算失败 下次再试
    ok = isinstance(r, dict) and r.get("retcode") == 0
    if ok:
        _boot["synced"] = True
        s.update(mode=mode, code=code, what=what[:20], n=int(s.get("n") or 0) + 1, fails=0, t=now)
        log(f"[在线状态] → {status_name(p)}（{code}；{mode}{'：' + what[:16] if what else ''}{'：' + why if why else ''}；今天第 {s['n']} 次）")
    else:
        s["fails"] = int(s.get("fails") or 0) + 1
        if s["fails"] >= int(_f("status_fail_max")):
            s["off"] = True
            log(f"[在线状态] 改成「{status_name(p)}」{code} 连着失败 {s['fails']} 次 → 今天停用（{str(r)[:80]}）")
        else:
            log(f"[在线状态] 改成「{status_name(p)}」{code} 没成（第 {s['fails']} 次）：{str(r)[:80]}")
    # （11-03 v1.17.13 Gemini R1-F7）存回去走 update_json：这会儿文件一时打不开 → 这次不存（以前读不到当成空的 整份覆盖 今天回赞过谁全丢）
    safeio.update_json(DATA / "qqevents.json", lambda d: d.__setitem__("status", s), {})
    return ok
