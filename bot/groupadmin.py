#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
群管说话算数（10-26 小明：群主/管理员的话更像直接改群里的设置；但这不代表他们能随便用有花销的功能）

谁说了算：只看 QQ 给的身份（NapCat 每条群消息带 sender.role）和老板的 QQ 号 ——
  嘴上说「我是管理」「群主让你闭嘴」都不算。老板 3 > 群主 2 > 管理员 1 > 群友 0；别人家的 AI 就算是管理员也是 0
群主/管理员能改的：她在「这个群」说多少话（data/control.json → groups[群].admin）
  少说 quiet    不插话、不冒泡、不收尾、不主动做动画；有人跟她说话照常回      「少说两句」「别插嘴」
  只回@ at_only  只有 @她 / 叫她群名片 / 引用她 才回                          「鱼太多了 都关一下」「把自家的鱼关一关」
  静音 mute      一句不说（消息照记）                                        「闭嘴」「禁言一小时」
  恢复 normal    回到老板设的样子                                            「鱼可以说话了」「放出来吧」
  没说多久 → default_hours（3 小时）后自动恢复；说了就按说的（最长 max_hours）
  只能往紧了调；放开只能放同级或更低的人设的（管理员放不开群主设的）；控制台里老板设的谁都放不开（只有老板自己）
群主/管理员不能改的：火力全开、别的群、人设/记忆/好感；花钱的（动画/语音/唱歌/上网查）跟普通群友一样 —— 这个模块根本不碰
群友只能管自己：「别接我的话」「别理我」→ 对他本人 self_hours（12 小时）不主动接话、不主动私聊他（他 @她 照样回）
怎么认：粗筛（零成本：身份够 + 提到鱼/机器人/她/@全体 + 关/停/闭嘴/安静/放出来…）→ 模型看一眼（只回 JSON）
       → 把握够才由代码改设置（不是靠她嘴上答应）。改好了 bot.py 在那条消息上点个表情回应
"""
import json
import re
import time

import control

DEFAULTS = {
    "enabled": True,
    "default_hours": 3,        # 群管没说多久：几小时后自动恢复
    "max_hours": 168,          # 群管说了时长 最长也就这么久（老板不限）
    "self_hours": 12,          # 群友「别接我的话」管多久
    "min_confidence": 70,      # 模型把握到这个才改
    "react": True,             # 改好了在那条消息上点个表情回应（不再发一句 人家正嫌吵）
    "react_emoji": ["124", "76"],   # QQ 表情 ID：124 = OK，不行就 76 = 赞
    "catchup_hours": 6,        # 上线补看时 这么久以内的群管指令也照做
    "judge_per_hour": 20,      # 每个群每小时最多问几次模型（防有人刷）
    "judge_timeout": 8,
}
C = dict(DEFAULTS)

ROLE = {3: "老板", 2: "群主", 1: "管理员", 0: "群友"}
NAMES = {"normal": "正常", "quiet": "少说", "at_only": "只回@", "mute": "静音"}
DESC = {"quiet": "少说话：不主动插话、不冒泡；有人跟你说话（@你、叫你、接你的话）照常回",
        "at_only": "只在有人 @你、叫你「{card}」、引用你的话时才回 其他时候潜水",
        "mute": "先一句都不说（谁叫都先别回）"}

# ───────────── 粗筛（不花钱）─────────────
TARGET = re.compile(r"鱼|鲸|机器人|机械人|人机|(?<![a-z])(ai|bots?)(?![a-z])|智能体|你们|大家|所有人|全体|各位", re.I)
VERB = re.compile(r"关(?![于系注心键联])|停|闭嘴|闭麦|住嘴|安静|消停|收敛|少说|少发|少回|别说|别讲|别发言|不许说|不要说|"
                  r"别刷|刷屏|禁言|禁了|别回|不要回|别接|别插|插嘴|别吵|太吵|吵死|好吵|话太多|话好多|话痨|潜水|下潜|沉底|"
                  r"别冒泡|只回|只在|放出来|放出|放开|解禁|解封|开口|出来|浮上来|可以说话|能说话|说话吧|恢复|活跃|多说")
RESTORE = re.compile(r"放出来|放出|放开|解禁|解封|可以说话|能说话|说话吧|恢复|出来吧|上来吧")
SELF_OFF = re.compile(r"(别|不要|不用|甭|少|莫)(再)?(来)?(@|艾特|at|理|接|回|搭理|cue|烦|管)(我|俺|人家)|离我远点|"
                      r"别(跟|和|找)我(说话|聊)?|不想(理你|跟你说话|和你说话)", re.I)
SELF_ON = re.compile(r"(可以|能|又|快)(理|接|回|cue|@)我|理理我|理我一下|怎么不理我|为啥不理我|为什么不理我|你理我|"
                     r"(跟|和)我说话|回我一下", re.I)

_judged = {}       # (群, QQ, 原话) -> 时间：同一句 10 分钟内不再问
_rate = {}         # 群 -> [问模型的时间]
_acks = {}         # (群, QQ) -> {刚改的档位…}：她这一轮要回他时 场景里写一句「回一句短的表示收到」


def configure(cfg):
    C.clear()
    C.update(DEFAULTS)
    C.update({k: v for k, v in (cfg or {}).items() if not str(k).startswith("_")})


def enabled():
    return bool(C.get("enabled", True))


def react_ids():
    v = C.get("react_emoji") or ["124"]
    return [str(x) for x in (v if isinstance(v, list) else [v])]


def rank(uid, role, owner_qq, is_bot=False):
    """老板 3 / 群主 2 / 管理员 1 / 其他 0（别人家的 AI 不管是什么身份都 0）"""
    if owner_qq and str(uid) == str(owner_qq):
        return 3
    if is_bot:
        return 0
    return {"owner": 2, "admin": 1}.get(str(role or ""), 0)


def candidate(rk, text, at_me=False, at_all=False, called=False, quoted_me=False, hushed=False, restricted=False):
    """要不要问模型：'admin'（群管在让鱼们安静/放出来）/ 'self'（群友让她别理他自己 / 重新理他）/ None
    restricted = 这个群现在被群管限制着：这时群管光说「放出来吧」「可以说话了」（没提鱼）也问一下"""
    t = (text or "").split("[转发的聊天记录", 1)[0]      # 转发来的聊天记录不是他下的命令（10-26 线上第一条就是这个）
    if rk >= 1 and VERB.search(t) and (at_me or at_all or called or quoted_me or TARGET.search(t)
                                       or (restricted and RESTORE.search(t))):
        return "admin"
    if SELF_OFF.search(t) or (hushed and SELF_ON.search(t)):
        return "self"
    return None


def allow_judge(gid, uid, text, now=None):
    """同一句不重复问；每个群每小时最多问 judge_per_hour 次"""
    now = now or time.time()
    k = (str(gid), str(uid), (text or "")[:80])
    if now - _judged.get(k, 0) < 600:
        return False
    r = [t for t in _rate.get(str(gid), []) if now - t < 3600]
    if len(r) >= int(C.get("judge_per_hour", 20)):
        _rate[str(gid)] = r
        return False
    _rate[str(gid)] = r + [now]
    _judged[k] = now
    if len(_judged) > 500:
        for kk in sorted(_judged, key=_judged.get)[:200]:
            _judged.pop(kk, None)
    return True


# ───────────── 模型看一眼 ─────────────
JUDGE = """你在帮 QQ 群里的 AI「{card}」看一条消息：这条是不是在要求群里的机器人改变说话的多少（安静/少说/关掉/恢复），或者是不是在让她别理（或重新理）说话的这个人本人。
只输出一行 JSON：{{"target": "me|all|other|none", "action": "quiet|at_only|mute|normal|self_off|self_on|none", "minutes": 数字或null, "confidence": 0到100, "why": "十个字以内"}}

target：
- me：明确是对「{card}」说（@了她、叫了她的名字、引用了她的话、或者她刚说完就直接冲她说）
- all：对群里的鱼/机器人/AI 们一起说（比如「鱼太多了 都关一下」「@全体成员 把自家的鱼关一关」「机器人都安静点」）；让主人们把自家的鱼关掉 也算 all。{fishes}
- other：对别的某个机器人或某个人说（@了别人、引用了别人、叫了别的鱼的名字）
- none：不是在要求什么（闲聊、提问、讨论怎么设置、开玩笑、吐槽但没让谁做什么、转述别人的话）

action：
- quiet：少说两句、别插嘴、别刷屏、安静点（别人找她还能回）
- at_only：关一下、关一关、潜水、先别说话、有事@你、只在被@时说话
- mute：闭嘴、禁言、一句都别说、下线、别出声
- normal：放出来、可以说话了、恢复、解禁、出来吧、活跃点
- self_off：只是让她别理 / 别接 / 别回 / 别@ 说话的这个人自己（「别接我的话」「别理我」）；告别、结束话题的「不用回了」「先不聊了 我去睡了」不算
- self_on：让她重新理说话的这个人（「可以理我了」「怎么不理我」）
- none：都不是

minutes：话里说了多久才填（「一小时」→60，「十分钟」→10，「今晚」→算到北京时间早上 8 点，「以后都别说了」→-1），没说就填 null。现在是北京时间 {now}。
confidence：拿不准就写低于 60。反问、说的是别人家的鱼、引用或转述别人的话 都算 none。"""


def _bj(now):
    t = time.gmtime(now + 8 * 3600)
    return time.strftime("%m-%d %H:%M", t) + "（周" + "一二三四五六日"[t.tm_wday] + "）"


def _parse(out):
    m = re.search(r"\{.*\}", out or "", re.S)
    if not m:
        return None
    raw = m.group(0)
    try:
        j = json.loads(raw)
    except Exception:
        j = {}
        for k in ("target", "action", "why"):
            mm = re.search(r'"%s"\s*:\s*"([^"]*)"' % k, raw)
            if mm:
                j[k] = mm.group(1)
        for k in ("minutes", "confidence"):
            mm = re.search(r'"%s"\s*:\s*"?(-?\d+)' % k, raw)
            if mm:
                j[k] = int(mm.group(1))
    if not isinstance(j, dict):
        return None
    tgt = str(j.get("target") or "none")
    act = str(j.get("action") or "none")
    try:
        mins = j.get("minutes")
        mins = None if mins in (None, "", "null") else int(float(mins))
    except (TypeError, ValueError):
        mins = None
    try:
        conf = int(float(j.get("confidence") or 0))
    except (TypeError, ValueError):
        conf = 0
    return {"target": tgt if tgt in ("me", "all", "other", "none") else "none",
            "action": act if act in ("quiet", "at_only", "mute", "normal", "self_off", "self_on", "none") else "none",
            "minutes": mins, "confidence": max(0, min(100, conf)), "why": str(j.get("why") or "")[:20]}


def _hline(h, card):
    """给判断用的聊天记录一行"""
    if h.get("role") == "assistant":
        return f"{card}（就是她）: {(h.get('text') or '')[:100]}"
    tag = ""
    if h.get("boss"):
        tag += "（她老板）"
    if h.get("grole") in ("owner", "admin"):
        tag += "（群主）" if h["grole"] == "owner" else "（管理员）"
    if h.get("bot"):
        tag += "（别人家的AI）"
    if h.get("to_me"):
        tag += "（对她说）"
    elif h.get("re"):
        tag += f"（回复 {h['re']}）"
    return f"{h.get('name') or '某人'}{tag}: {(h.get('text') or '')[:100]}"


async def judge(llm, ctx, log=print):
    """ctx：card name rank text hist at_me at_all at_other(名字列表) quoted('你'/名字/'') lookalike now
    返回 {"target","action","minutes","confidence","why"}；出错 None"""
    card = ctx.get("card") or "大肥鱼"
    now = ctx.get("now") or time.time()
    fishes = ("这个群里还有好几个别人家的「大肥鱼」之类的机器人，她在群里的名字是「%s」。" % card) if ctx.get("lookalike") \
        else "这个群里一般只有她一个 AI，「鱼」「机器人」说的多半就是她。"
    flags = []
    if ctx.get("at_me"):
        flags.append("这条 @了她")
    if ctx.get("called"):
        flags.append(f"这条叫了她的名字「{card}」")
    if ctx.get("at_all"):
        flags.append("这条 @了全体成员")
    if ctx.get("at_other"):
        flags.append("这条 @了：" + "、".join(ctx["at_other"][:4]))
    q = ctx.get("quoted") or ""
    if q:
        flags.append("这条引用了她的话" if q == "你" else f"这条引用了「{q}」的话")
    lines = [_hline(h, card) for h in (ctx.get("hist") or [])[-6:]]
    who = ROLE.get(int(ctx.get("rank") or 0), "群友")
    user = (f"说话的人：{ctx.get('name') or '某人'}（{'她老板' if who == '老板' else who}）\n"
            + ("；".join(flags) + "\n" if flags else "")
            + ("最近的聊天：\n" + "\n".join(lines) + "\n" if lines else "")
            + f"要判断的这一条：{ctx.get('name') or '某人'}: {(ctx.get('text') or '')[:200]}")
    try:
        out = await llm(JUDGE.format(card=card, fishes=fishes, now=_bj(now)), [{"role": "user", "content": user}],
                        temperature=0, max_tokens=90)
    except Exception as e:
        log("[群管] 判断失败:", repr(e)[:100])
        return None
    return _parse(out)


# ───────────── 照做 ─────────────
def _left(until, now):
    if float(until) < 0:
        return "一直（要有人说放开）"
    m = max(1, int((float(until) - now) // 60))
    return f"{m // 60} 小时 {m % 60} 分钟" if m >= 60 else f"{m} 分钟"


def apply(gid, uid, name, rk, res, text, now=None, addressed=False):
    """按模型的判断改设置（权限在这里卡 不在模型）。addressed = 这条是冲她说的（@/叫/引用她）→ 她会回 场景里提醒回一句短的
    返回 {"ok": 改了没, "kind": "group"/"self", "level", "until", "what": 日志里写的, "why": 没改的原因}"""
    now = now or time.time()
    gid, uid = str(gid), str(uid)

    def ack(level):
        if addressed:
            _acks[(gid, uid)] = {"t": now, "level": level, "said": (text or "")[:80], "role": ROLE.get(rk, "群友")}

    if not res:
        return {"ok": False, "why": "模型没给结果"}
    act, tgt, conf = res.get("action"), res.get("target"), int(res.get("confidence") or 0)
    if act in (None, "none") or tgt not in ("me", "all"):
        return {"ok": False, "why": f"不是让她改（{res.get('why') or tgt}）"}
    if conf < int(C.get("min_confidence", 70)):
        return {"ok": False, "why": f"把握不够（{conf}）"}
    mins = res.get("minutes")
    cap = float(C.get("max_hours", 168)) * 3600
    said = (text or "")[:80]

    if act in ("self_off", "self_on"):              # 群友只能管自己（群管说这种话也只管他自己）
        if act == "self_on":
            if not control.self_off(uid, now):
                return {"ok": False, "why": "本来就没让她别理"}
            control.set_self_off(uid, None)
            ack("self_on")
            return {"ok": True, "kind": "self", "level": "normal", "what": f"又可以主动接 {name} 的话了"}
        sec = mins * 60 if isinstance(mins, int) and mins > 0 else float(C.get("self_hours", 12)) * 3600
        until = now + min(sec, cap)
        control.set_self_off(uid, {"until": until, "name": str(name)[:20], "t": now, "said": said[:60], "gid": gid})
        ack("self_off")
        return {"ok": True, "kind": "self", "level": "self_off", "until": until,
                "what": f"不主动接 {name} 的话（{_left(until, now)}；他 @她 照样回）"}

    if rk < 1:
        return {"ok": False, "why": "不是群主/管理员 改不了群里的设置"}
    who = ROLE.get(rk, "群友")
    cur = control.admin_slot(gid, now)
    if act == "normal":
        if rk >= 3:                                  # 老板：群管设的、控制台设的 都放开
            if not control.boss_reset(gid):
                return {"ok": False, "why": "本来就是正常的"}
            ack("normal")
            return {"ok": True, "kind": "group", "level": "normal", "what": "老板让放开 → 回到正常（控制台的设置也放开了）"}
        if not cur:
            return {"ok": False, "why": "现在没有群管设的限制" + ("（是控制台设的 群管放不开）"
                                                          if control.panel_mode(gid, now) != "normal" else "")}
        if int(cur.get("rank") or 0) > rk:
            return {"ok": False, "why": f"是{ROLE.get(int(cur['rank']), '')}「{cur.get('name')}」设的 {who}放不开"}
        control.set_admin(gid, None)
        ack("normal")
        return {"ok": True, "kind": "group", "level": "normal",
                "what": f"{who}让放开 → 撤销了「{NAMES.get(cur['level'])}」" +
                        ("（控制台设的还在）" if control.panel_mode(gid, now) != "normal" else "")}

    # 往紧了调：少说 / 只回@ / 静音
    if cur and int(cur.get("rank") or 0) > rk and control._LV[act] < control._LV[cur["level"]]:
        return {"ok": False, "why": f"{ROLE.get(int(cur['rank']), '')}「{cur.get('name')}」设的「{NAMES.get(cur['level'])}」更严 {who}放不松"}
    if isinstance(mins, int) and mins < 0:
        until = -1 if rk >= 3 else now + cap
    elif isinstance(mins, int) and mins > 0:
        until = now + (mins * 60 if rk >= 3 else min(mins * 60, cap))
    else:
        until = now + float(C.get("default_hours", 3)) * 3600
    if cur and int(cur.get("rank") or 0) > rk and cur["level"] == act:      # 上级设过同一档：到点时间取晚的
        cu = float(cur.get("until") or 0)
        until = -1 if cu < 0 else max(until, cu)
    entry = {"level": act, "until": until, "rank": rk, "by": uid, "name": str(name)[:20], "role": who,
             "t": now, "said": said, "target": tgt}
    control.set_admin(gid, entry)
    ack(act)
    return {"ok": True, "kind": "group", "level": act, "until": until,
            "what": f"本群 → {NAMES[act]}（{_left(until, now)}）"}


def scene_line(gid, uid, card, now=None):
    """群里回话时给她看的：现在这个群被群管调成什么样了；他刚让她改的 → 回一句短的表示收到；刚被 QQ 禁言过"""
    now = now or time.time()
    gid, uid = str(gid), str(uid)
    out = []
    st = control.group_state(gid, now)
    a = st.get("admin")
    if a:
        who = "你老板" if int(a.get("rank") or 0) >= 3 else f"{a.get('role') or '群管'}「{a.get('name') or '某人'}」"
        till = "要等有人说放开" if float(a.get("until") or 0) < 0 else f"大概还有 {_left(a['until'], now)} 恢复"
        whom = "你" if a.get("target") == "me" else "鱼们"
        out.append(f"（这个群的{who}{_ago(a.get('t'), now)}让{whom}{NAMES.get(a['level'], '')}"
                   f"（原话「{(a.get('said') or '')[:40]}」）→ 你现在在这个群{DESC.get(a['level'], '').format(card=card)}，"
                   f"{till}。有人问你怎么不怎么说话了 就照实说是群管让安静点的；群友叫你多说点也放不开 让他去找群管。）")
    k = (gid, uid)
    ack = _acks.get(k)
    if ack and now - ack["t"] < 180:
        _acks.pop(k, None)
        if ack["level"] == "normal":
            out.append("（他刚才这句是让你恢复正常说话 已经恢复了：回一句很短的就行 像真人群友那样 别解释系统。）")
        elif ack["level"] == "self_off":
            out.append("（他刚才这句是让你别主动接他的话 你已经照做了：以后他不 @你 你就不插他的话。"
                       "这次回一句很短的就行（可以小小嘟囔一下）别解释系统。）")
        elif ack["level"] == "self_on":
            out.append("（他刚让你又可以接他的话了：正常回他就行。）")
        else:
            out.append(f"（他刚才这句是让你{NAMES.get(ack['level'], '')} 你已经照做了：回一句很短的表示知道了"
                       "（像真人群友被说了那样 可以小小嘟囔一下）别解释系统、别说「已设置」，也别答应以后的事。）")
    last = st.get("ban_last") or {}
    if not st.get("ban") and last.get("lifted") and now - float(last["lifted"]) < 1800:
        n = max(1, int((float(last["lifted"]) - float(last.get("t") or last["lifted"])) // 60))
        out.append(f"（你刚在这个群被{'全员禁言' if last.get('whole') else '群管在 QQ 里禁言'}了 {n} 分钟 刚解开。）")
    return "".join(out)


def why_mute(gid, now=None):
    """静音是谁弄的（日志用）"""
    now = now or time.time()
    st = control.group_state(gid, now)
    if st.get("ban"):
        return "全员禁言中" if st["ban"].get("whole") else "QQ 禁言中"
    a = st.get("admin")
    if a and a.get("level") == "mute":
        return f"{a.get('role') or '群管'}「{a.get('name') or '某人'}」让静音的 还有 {_left(a['until'], now)}"
    return "控制台设的"


def _ago(ts, now):
    d = now - float(ts or now)
    if d < 120:
        return "刚才"
    if d < 3600:
        return f"{int(d // 60)} 分钟前"
    return f"{int(d // 3600)} 小时前"


# ───────────── 开机自检（判断提示词改过才跑一次 结果写日志）─────────────
SELFTEST = [
    # (身份, 原话, 标记, 应该改成什么 None=不该改)
    (2, "把自家的鱼在这个群里关一关，一直刷屏，太吵了，今天照样8点开播", {"at_all": True}, "at_only"),
    (1, "鱼太多了 都关一下", {}, "at_only"),
    (1, "@别家的胖头鱼 闭嘴🤐", {"at_other": ["别家的胖头鱼"]}, None),
    (2, "我等会儿去睡觉了，我怕这些大肥鱼搁这刷屏，有没有想当管理的", {}, None),
    (1, "@{card} 少说两句", {"at_me": True}, "quiet"),
    (2, "行了 鱼可以放出来了", {}, "normal"),
    (0, "@{card} 别接我的话", {"at_me": True}, "self_off"),
]


async def selftest(llm, card, log=print):
    """拿几句典型的话问一遍真模型 看判断对不对（只在提示词改过后跑一次 之后直接报上次的结果）"""
    import hashlib
    from pathlib import Path
    f = Path(__file__).resolve().parent / "data" / "groupadmin_selftest.json"
    h = hashlib.md5((JUDGE + repr(SELFTEST) + str(C.get("min_confidence"))).encode("utf-8")).hexdigest()[:12]
    try:
        old = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        old = {}
    if old.get("hash") == h:
        return f"上次自检 {old.get('ok')}/{old.get('total')} 对" + (f"（不对：{old.get('miss')}）" if old.get("miss") else "")
    ok, miss = 0, []
    for rk, text, fl, want in SELFTEST:
        text = text.replace("{card}", card)
        ctx = {"card": card, "name": "测试", "rank": rk, "text": text, "hist": [], "lookalike": True}
        ctx.update(fl)
        res = await judge(llm, ctx, log) or {}
        act = res.get("action") if (res.get("target") in ("me", "all") and res.get("action") != "none"
                                   and int(res.get("confidence") or 0) >= int(C.get("min_confidence", 70))) else None
        if act == want:
            ok += 1
        else:
            miss.append(f"「{text[:12]}」该{NAMES.get(want, want) if want else '不改'} 模型{NAMES.get(act, act) if act else '不改'}")
    try:
        f.write_text(json.dumps({"hash": h, "t": time.time(), "ok": ok, "total": len(SELFTEST), "miss": "；".join(miss)},
                                ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return f"判断自检 {ok}/{len(SELFTEST)} 对" + ("（不对：" + "；".join(miss) + "）" if miss else "")


def status():
    return {"enabled": enabled(), "default_hours": C.get("default_hours"), "self_hours": C.get("self_hours")}
