#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
恭喜（10-30 v1.15.1，小明：「有人说今天是我生日 可以发生日快乐；说法很奇怪 可以跟随别人说生日快乐；
这是一套可以写死的 恭喜的时候用的 不只是生日快乐」「合成动图吧」）

  这里的话是**写死的**（小明 明说可以）：生日 →「生日快乐」，别的好事 →「恭喜」，再加一张恭喜动图
  （data/congrats/congrats.gif；没有这个文件就只发字）。别的地方照旧守 E1（不写死台词）。

  observe() 每句群聊 / 私聊都看一眼，认三种：
    1 本人说自己的好事：「今天我生日」「我上岸了」「拿到 offer 了」「转正了」「脱单了」……
       - 说法正常 → 带头（lead）：过几秒说一句 + 发动图（冲她说的：她自己会回 这里只补动图）
       - 说法奇怪（1000 岁、第三次过生日、天天过生日、刚有人这么说过、他去年说的生日不是今天…）→ 等（wait）：
         3 分钟里有 2 个以上的真人（不是他自己、不是别人家的 AI）跟着说「生日快乐 / 恭喜」→ 她跟一句；没人跟就不说
    2 说别人的生日（「今天是 @某某 生日」）→ 当成等（谁的生日她不认识 跟着大家走）
    3 群里一阵祝福（5 分钟里 3 个以上的真人说「生日快乐 / 恭喜」）→ 她也跟一句
  不说：问句（「你生日吗」）、否定 / 反话（「没过」「挂了」「才怪」「梦里」）、别人家的 AI、群里静音 / 少说 / 只回 @、屏蔽的人、
        上线补看的旧消息
  频率：同一个人同一件事一天一次；动图每个群 gif_gap_min 分钟一次（中间再有就只发字）；每个群每天最多 daily_max 次
  记录 data/congrats.json：谁说过自己哪天生日（下次对不上 = 奇怪）、发过什么；删数据 forget(uid)
  日志 [恭喜]；config → congrats（enabled / gif_gap_min / daily_max / wait_sec / follow_need / wave_need / lead_delay）
"""
import json
import random
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULTS = {"enabled": True, "gif_gap_min": 120, "daily_max": 6, "wait_sec": 180, "follow_need": 2, "wave_need": 3,
            "wave_sec": 300, "lead_delay": [4, 12]}
C = dict(DEFAULTS)

# （11-10 v1.17.35 审查 V8-4）以前「今天 / 是 / 的」全是可选的：话里有「我生日 / 我的生日」就当成今天生日 ——
#   「我生日快到了」「我生日是 3 月」「等我生日再说」「我妈把我生日记错了」都回写死的「生日快乐」+ 动图、还把今天记成他的生日
#   （之后他真生日那天反而被当成「他另一天也说过」不带头）。现在：说的是今天才算 ——
#   带「今天 / 今儿 / 今日」的（今天我生日 / 我今天生日 / 今天是我 18 岁生日 / 我今天满 18 岁了）、或者整句就是「我过生日啦」「我生日了」；
#   「生日」后面得是这句话的边或者接着说别的（空格标点 / 啦了耶 / 你我大家都也…）——「今天我生日礼物到了」「今天是我生日的前一天」不算
_BD_DAY = r"(?:今天|今儿|今日)"
_BD_END = (r"(?:啦|了|耶|呀|哦|咯|啊|嘛|呢|哈|诶|欸)*"
           r"(?=$|[^0-9A-Za-z\u4e00-\u9fff]|[你我他她咱大都也还却但可没不就好真太竟居结所然而求请想要有来快谁怎])")
BDAY_SELF = re.compile(_BD_DAY + r"(?:是|就是|可是|也是|正是|正好是|刚好是)?我(?:的)?(?:\d{1,4}\s*岁)?(?:的)?(?:过)?生日" + _BD_END + "|"
                       r"我" + _BD_DAY + r"(?:是|就是)?(?:过|满)?(?:\d{1,4}\s*岁)?(?:的)?生日" + _BD_END + "|"
                       r"^[\W哈嘿耶啊哇呜]*我(?:过)?生日(?:啦|了|耶|呀|哦|咯)+\W*$|"
                       r"(?:" + _BD_DAY + r"我|我" + _BD_DAY + r")满\s*\d{1,4}\s*岁")
BDAY_OTHER = re.compile(r"今天(?:是)?\s*(?:@\S{1,20}|[^\s，,。！!？?]{1,12}?)\s*(?:的)?生日(?!快乐)")
NEWS = re.compile(r"我(?:考研|考公|考编|考博)?上岸了|(?:我|终于)(?:考|过|通过)了.{0,6}?(?:考试|四级|六级|驾照|科目[一二三四]|雅思|托福|答辩|面试|笔试)|"
                  r"我?(?:拿到|收到)(?:了)?\s*offer|我?转正了|我?升职了|我毕业了|我?脱单了|我?订婚了|我?结婚了|我?领证了|"
                  r"(?:我|终于)?(?:抽|出|歪)(?:到|出)?(?:了)?(?:金|ssr|SSR|六星|满命)(?:了|啦)|论文(?:中了|被接收了|录用了)|面试过了|我?(?:考上|录取)了")
WISH = re.compile(r"生日快乐|生快|happy\s*birthday|hbd|恭喜|可喜可贺|贺喜|congrat", re.I)
NEG = re.compile(r"没过|没有过|不过生日|没考上|没上岸|挂了|挂科|落榜|被拒|没拿到|不是我|才怪|梦里|做梦|如果|要是|假如|希望|想要|等我|什么时候|"
                 r"假的|开玩笑|骗你|骗人的|愚人|分手|离婚|被辞|被裁|被骂|被踢|我想|好想|想要|"
                 r"明天|后天|下周|下个月|过几天|还有\s*\d+\s*天|昨天|上周|那天|去年")
ASK = re.compile(r"[?？]\s*$|吗\s*[?？]?\s*$|是不是|有没有|哪天|几号")
ODD = re.compile(r"第\s*[二三四五六七八九十\d]+\s*次|又过|又是我|天天|每天|每年都|一年过|永远|假装|虚岁|阴历.{0,4}阳历|1000|一万|一千|八百")
AGE = re.compile(r"(\d{1,5})\s*岁")
_state = {"pend": {}, "wave": {}}      # pend：(对话, 目标) → 等着跟的；wave：对话 → [(时间, 说的人, 目标, 词)]


def configure(cfg=None, data_dir=None):
    global DATA
    for k, v in (cfg or {}).items():
        if not str(k).startswith("_") and k in DEFAULTS:
            C[k] = v
    if data_dir:
        DATA = Path(data_dir)


def on():
    return bool(C.get("enabled", True))


def gif_path():
    p = DATA / "congrats" / "congrats.gif"
    return p if p.exists() else None


def _file():
    return DATA / "congrats.json"


def load():
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("claims", {})
            d.setdefault("sent", [])
            d.setdefault("gif", {})
            return d
    except (OSError, ValueError):
        pass
    return {"claims": {}, "sent": [], "gif": {}}


def save(d):
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        d["sent"] = d.get("sent", [])[-200:]
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_file())
    except OSError:
        pass


def _day(now):
    return time.strftime("%Y-%m-%d", time.localtime(now))


def kind_of(text):
    """→ ("birthday" | "news", 是不是在说自己) 或 None"""
    t = str(text or "")[:200]
    if not t or ASK.search(t) or NEG.search(t):
        return None
    if WISH.search(t) and not BDAY_SELF.search(t.replace("生日快乐", "")):
        return None                        # 在祝别人（交给「一阵祝福」那条）
    if BDAY_SELF.search(t):
        return "birthday", True
    if BDAY_OTHER.search(t):
        return "birthday", False
    if NEWS.search(t):
        return "news", True
    return None


def odd_reason(text, uid, gid, now, d=None):
    """说法奇怪的原因（→ 先不带头 跟着大家走）；正常 → 空"""
    t = str(text or "")
    if ODD.search(t):
        return "说法奇怪：" + ODD.search(t).group(0)
    m = AGE.search(t)
    if m and (int(m.group(1)) > 100 or int(m.group(1)) < 1):
        return f"{m.group(1)} 岁"
    d = d or load()
    mmdd = time.strftime("%m-%d", time.localtime(now))
    for c in (d.get("claims") or {}).get(str(uid), []):
        if c.get("mmdd") and c["mmdd"] != mmdd and now - float(c.get("t") or 0) < 330 * 86400:
            return f"他 {c['mmdd']} 也说过是生日"
    for u, cs in (d.get("claims") or {}).items():
        if u != str(uid) and any(c.get("gid") == str(gid) and now - float(c.get("t") or 0) < 2 * 3600 for c in cs):
            return "刚有别人在这儿说过今天生日（多半在跟风）"
    return ""


def _wish_word(kind):
    return "生日快乐" if kind == "birthday" else "恭喜"


def _done_key(conv, target, kind, now):
    return f"{conv}:{target}:{kind}:{_day(now)}"


def _budget_ok(d, conv, now):
    n = sum(1 for s in d.get("sent") or [] if s.get("conv") == conv and s.get("day") == _day(now))
    return n < int(C["daily_max"])


def _gif_ok(d, conv, now):
    return gif_path() is not None and now - float((d.get("gif") or {}).get(conv, 0)) >= float(C["gif_gap_min"]) * 60


def _act(conv, scene, gid, target, name, kind, mode, now, mid=None, text_on=True, why=""):
    lo, hi = (C.get("lead_delay") or [4, 12])[:2]
    return {"conv": conv, "scene": scene, "gid": str(gid or ""), "target": str(target), "name": name, "kind": kind, "mode": mode,
            "text": _wish_word(kind) if text_on else "", "reply_mid": mid, "delay": random.uniform(float(lo), float(hi)),
            "t": now, "why": why}


def observe(conv, scene, gid, uid, name, text, now=None, mid=None, to_her=False, at_uids=(), isbot=False, allowed=True, log=None):
    """一句话 → 要发就返回一个动作 dict（bot.py 等 delay 秒后 send 前再问一次 ready()）；不发返回 None"""
    if not on() or isbot:
        return None
    now = float(now or time.time())
    uid = str(uid)
    d = load()
    out = None
    # ① 先看是不是在祝福（跟着大家走 / 一阵祝福）
    if WISH.search(str(text or "")) and not BDAY_SELF.search(str(text or "").replace("生日快乐", "")):
        out = _on_wish(conv, scene, gid, uid, text, now, at_uids, d, allowed, log)
        return out
    k = kind_of(text)
    if not k:
        return None
    kind, self_ = k
    if not allowed:
        return None
    target = uid if self_ else (str(at_uids[0]) if at_uids else f"?{_short(text)}")
    dk = _done_key(conv, target, kind, now)
    if any(s.get("key") == dk for s in d.get("sent") or []):
        return None
    why = odd_reason(text, uid, gid, now, d) if (kind == "birthday" and self_) else ("" if self_ else "说的是别人的生日")
    if kind == "birthday" and self_:
        d["claims"].setdefault(uid, []).append({"mmdd": time.strftime("%m-%d", time.localtime(now)), "t": now, "gid": str(gid or "")})
        d["claims"][uid] = d["claims"][uid][-5:]
        save(d)
    if why or scene == "group" and not self_:
        _state["pend"][(conv, target)] = {"t": now, "kind": kind, "who": set(), "uid": uid, "name": name, "mid": mid, "scene": scene,
                                          "gid": str(gid or ""), "why": why or "说的是别人的生日"}
        if log:
            log(f"[恭喜] {conv} {name}：{'生日' if kind == 'birthday' else '好事'}（{why or '说的是别人的生日'}）→ 先不带头 等大家跟")
        return None
    out = _act(conv, scene, gid, target, name, kind, "lead", now, mid=mid, text_on=not to_her)
    if to_her:
        out["delay"] += 12                 # 冲她说的：等她自己那句先回出去 再补动图
    if log:
        log(f"[恭喜] {conv} {name}：{'生日' if kind == 'birthday' else '好事'} → 带头" + ("（冲她说的：她自己回 这里只补动图）" if to_her else ""))
    return out


def _short(t):
    return re.sub(r"\s+", "", str(t or ""))[:12]


def _on_wish(conv, scene, gid, uid, text, now, at_uids, d, allowed, log):
    wave = [w for w in _state["wave"].get(conv, []) if now - w[0] <= float(C["wave_sec"])]
    word = "生日快乐" if re.search(r"生日|生快|birthday|hbd", str(text), re.I) else "恭喜"
    target = str(at_uids[0]) if at_uids else ""
    wave.append((now, uid, target, word))
    _state["wave"][conv] = wave
    if not allowed:
        return None
    # 等着跟的那件（奇怪的说法 / 别人的生日）：够人数了 → 跟一句
    for (c, tg), p in list(_state["pend"].items()):
        if c != conv:
            continue
        if now - p["t"] > float(C["wait_sec"]):
            _state["pend"].pop((c, tg), None)
            continue
        if uid == p["uid"] or uid == tg:
            continue                       # 说这件事的人 / 过生日的本人自己说的不算
        if target and not tg.startswith("?") and target != tg:
            continue
        p["who"].add(uid)
        if len(p["who"]) >= int(C["follow_need"]):
            _state["pend"].pop((c, tg), None)
            dk = _done_key(conv, tg, p["kind"], now)
            if any(s.get("key") == dk for s in d.get("sent") or []):
                return None
            if log:
                log(f"[恭喜] {conv} {p['name']}：{len(p['who'])} 个人跟着说了 → 她也跟一句")
            return _act(conv, p["scene"], p["gid"], tg, p["name"], p["kind"], "follow", now, mid=p.get("mid"))
        return None
    # 一阵祝福（没看到起因也行）
    who = {w[1] for w in wave if not target or w[2] in ("", target)}
    recent = any(s.get("conv") == conv and now - float(s.get("t") or 0) < 2 * float(C["wave_sec"]) for s in d.get("sent") or [])
    if len(who) >= int(C["wave_need"]) and not recent:     # 她刚带过头 / 跟过：这阵祝福就是冲那件事的 不再说
        tg = target or next((w[2] for w in reversed(wave) if w[2]), "") or "?wave"
        dk = _done_key(conv, tg, "birthday" if word == "生日快乐" else "news", now)
        if any(s.get("key") == dk for s in d.get("sent") or []):
            return None
        _state["wave"][conv] = []
        if log:
            log(f"[恭喜] {conv}：{len(who)} 个人在说「{word}」→ 她也跟一句")
        return _act(conv, scene, gid, tg, "", "birthday" if word == "生日快乐" else "news", "wave", now)
    return None


def ready(act, now=None):
    """发之前再问一次（等的这几秒里可能已经发过 / 超额了）→ (发不发, 带不带动图)"""
    now = float(now or time.time())
    d = load()
    dk = _done_key(act["conv"], act["target"], act["kind"], act["t"])
    if any(s.get("key") == dk for s in d.get("sent") or []) or not _budget_ok(d, act["conv"], now):
        return False, False
    return True, _gif_ok(d, act["conv"], now)


def mark_sent(act, gif, now=None):
    now = float(now or time.time())
    d = load()
    d["sent"].append({"key": _done_key(act["conv"], act["target"], act["kind"], act["t"]), "conv": act["conv"],
                      "day": _day(now), "t": now, "mode": act["mode"], "gif": bool(gif), "kind": act["kind"]})
    if gif:
        d.setdefault("gif", {})[act["conv"]] = now
    save(d)


def forget(uid, data_dir=None):
    """删数据：他说过的生日、发过的记录里带他的
    data_dir（11-10 v1.17.35 审查 V4-6 / V4-7）：给了 = 改那个目录里的 congrats.json（备份副本 / 每日包解出来的 / 机器人没开时控制台删）；
    没有这个文件 / 读坏了 / 没有他的 → 不写；不碰内存里等着发的"""
    uid = str(uid)
    fp = None if data_dir is None else Path(data_dir) / "congrats.json"
    if fp is not None:
        try:
            d = json.loads(fp.read_bytes().decode("utf-8-sig"))
        except (FileNotFoundError, ValueError):
            return 0
        if not isinstance(d, dict) or not isinstance(d.get("claims", {}), dict) or not isinstance(d.get("gif", {}), dict):
            return 0
        d0 = json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)
    else:
        d = load()
    n = len(d.get("claims", {}).pop(uid, []) or [])
    before = len(d.get("sent") or [])
    d["sent"] = [s for s in d.get("sent") or [] if f":{uid}:" not in s.get("key", "") and s.get("conv") != f"private_{uid}"]
    n += before - len(d["sent"])
    d.get("gif", {}).pop(f"private_{uid}", None)
    if fp is not None:
        if json.dumps(d, sort_keys=True, ensure_ascii=False, default=str) != d0:
            tmp = fp.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(fp)
        return n
    for k in [k for k in _state["pend"] if k[1] == uid or k[0] == f"private_{uid}"]:
        _state["pend"].pop(k, None)
    save(d)
    return n
