#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
她能拉黑、删好友（11-02 v1.17.6 ROADMAP C80）—— 公开（只有名单、记账和门槛 不带人设）
  小明 11-02：「她应当有自己删除别人的能力 如果一个人特别讨厌 或者一直和他讨论不当内容 她有权利拉黑删除对方
               当然她也有权利自己加感兴趣的人的好友」
  接口：NapCat 有 delete_friend（4.18 还带 temp_block 加 QQ 黑名单 / temp_both_del 双向删 —— 都没用：拉黑放在机器人这边、控制台能撤）；
        没有主动发好友申请的接口 →「加感兴趣的人」不写代码：她只能开口邀请 别人加她时照旧自动通过
  拉黑 = 机器人这边永久不理：所有群和私聊都不回、不主动找、他的消息不进记忆（跟控制台「不理的人」走同一个 control.blocked）；
        他再加她好友 → 自动拒绝（别人照旧自动通过）。名单在 data/blocklist.json 的 blocked：{QQ: {t, name, why, conv, cites, deleted, case}}
        （control.blocked 一起看它）—— 控制台「群和人 → 她拉黑的人」能看、能撤；删数据时名单留着（不然删数据 = 解封）、他的名字和原话抹掉
  E1：给她的只有状态（冷却到几点、法庭怎么判的、为什么没升级），不写台词

11-02 v1.17.10 阶段1（复核 B1–B12）：拉黑 / 删好友走法庭 —— 小明 批的：「法庭只能往轻判，判重了代码会压回来；
  代码不会判得比法庭更重。不可逆的事要法庭和代码都同意」
  她：状态行写 "block": "<原因>" = 立案。马上冷却：所有对话先不理他 cool_hours（24）小时 —— 这是她的决定：法庭驳回也照样冷却到期；控制台能撤
  谁不能被告（gate；好感、圈子按这一轮扣分之前的算 B2）：老板 → 什么都不做；最亲圈 / 好感 ≥ protect_favor（60）→ 只冷却、不立案
  法庭（court.py 私有；公开版没有 = 只冷却）：Opus 看卷宗判 驳回 / 冷却 / 拉黑 / 删好友，必须引用卷宗里他的话的编号；调不通 / 超时 /
        格式不对 = 没判成：一律不升级（停在冷却）、绝不退回 DeepSeek
  核对引用（verify）：只算编号对应的那条是 他本人发的、冲她说的（卷宗标 本人→她）、不是引用别人 / 转发、在 window_days 天内、在赦免之后
  封顶（caps）：拉黑要 ≥ block_min（2）条核实过的引用；删好友要 ≥ delete_min（3）条、分布在 ≥ delete_days（2）个不同的北京日子、
        是她的 QQ 好友、今天删的（影子模式「本来会删」也算）没到 delete_daily（1）；不够降一级：删好友 → 拉黑 → 冷却
  记账 marks 只当线索 / 她的感受，不计入任何门槛（B1 / B3 / B5）：
        线索：sexy 擦边 / jailbreak 改设定 / fake_owner 冒充老板（正则 只看他冲她说的原话 B4 / B12）—— 一条消息命中几类只记一条（kinds 合并、带 mid）
        她的感受：favor_min 她给他的好感 −3、ignore 她说过不理他 —— 只放进卷宗「她的感受」一栏
        同一个人同一类 dedup_min（10）分钟只记一次（带 mid 的按消息记）；留 keep_days（30）天；赦免（控制台撤销）之前的不算
  删好友影子模式 delete_mode："shadow"（默认）= 法庭照判、照告诉老板「本来会删谁」、不调 delete_friend（人照样拉黑）；"auto" = 真删
  执行（bot.py）：全局锁；删好友前先落盘「进行中」（pending：幂等键 = QQ + 北京日期）；超时 / 出错 → 查一次好友列表 记 删了 / 没删 / 不确定；
        每个案子都私聊告诉老板；控制台撤销（unblock）= 解除拉黑和冷却 + 记赦免时间（之前的线索、案子不再算）
  data/blocklist.json 新字段：cool {QQ: {t, until, why, conv, name, case, by}}、cases [案子]、pardons {QQ: 撤销的时间}、pending {QQ:日期: {…}}
  文件读坏了：只读不写（B17：以前读坏了从空表开始写回 名单就没了）
11-03 v1.17.13 小明 批的新流程（一审 / 冷却中再审 / 二审；bot.py _her_block 起）：
  立案：她写 block → 先不回他（占位：冷却 hold_sec 秒）等法庭 → 一审只判 放过（马上恢复、什么都不留）/ 冷却（从判完起 cool_hours 小时）；
        没判成（没有法庭 / 关着 / 调不通 / 超时 / 格式不对）→ 冷却 cool_hours 小时、不升级
  冷却中：他冲她说的话只当证据收进案子（cases[…].ev 最多 EV_MAX 条；不进聊天记录 / 存档 / 记忆）；他说完安静 retrial_quiet_sec 秒 → 再审：
        删除 / 不动（冷却期间核实 ≥ 1 句、加上立案时核实过的一共 ≥ retrial_min_total 句才删；像道歉的不算）；一个案子最多再审 retrial_max 次
  冷却到点、没被删 → 「冷却过」（cooled，不按 keep_days 过期）；冷却过的人再被写 block → 直接二审：删除（这次核实 ≥ second_min 句）/ 保留
        （保留：这次核实过至少 cool_min 句 → 再冷却；一句都没有 → 放过）
  删除 = 拉黑（永久）+ 删好友（是好友才删；delete_mode）+ 清他的数据（wipe_mode：shadow 只记「本来会清」/ auto 调删数据的流程）；
        每天最多 delete_daily 个（超了只拉黑）；判冷却 / 删除 → 他的好感 −cool_favor_drop（galgame.court_drop；公开空壳没有 = 不扣）
  最亲圈 / 好感 ≥ protect_favor：也走一审（放过 / 冷却），冷却中不收证据、不再审、不进二审 —— 永远不会被判删除
  控制台撤销（unblock）= 解除拉黑和冷却 + 清「冷却过」+ 记赦免（冷却早就到期的、只剩「冷却过」的也能撤 Gemini 2-F1）
  data/blocklist.json 新字段：cooled {QQ: {t, last, case, n}}；案子多了 stage（一审 / 二审）、evok（冷却中收不收证据）、ev [证据]、ev_last、
        retrials [再审]、quotes（核实过的原话 每句 ≤60 字）、favor（扣了多少）；state 多一种「冷却中」（判了冷却、还在收证据 / 等再审）
11-03 v1.17.15 申诉（小明 定的「她的名字也加申诉」；bot.py _appeal_msg / _appeal、handle_request）：被她拉黑（永久）/ 冷却中的人有一条路让老板知道
  他想申诉（以前只能老板自己去控制台撤，他们什么办法都没有）—— 她本人照旧不跟他们说话、不知道有人申诉
  两条路：① 加她好友时写的验证消息：永久拉黑的 → 好友申请照旧自动拒绝，验证消息里写了字就算申诉；冷却中的 → 申请照旧留着，验证消息明说申诉才算
        ② 私聊里明说申诉（is_appeal：开头是「申诉」，「我要申诉」「我想申诉」也算）：不回他、不进记忆 / 对话；冷却中的这句照旧也当证据收
        别的话照旧（永久拉黑的不读）；群里说的不算申诉；控制台「不理的人」、别人家的 AI 不转
  只私聊转给老板（谁、哪个案子、当时判的什么、原因、他写的话、怎么撤）：同一个人 appeal_days（7）天最多转一次（两条路一起算；没发出去的不算）；
        给老板的是通知：不进她跟老板的聊天记录、不进她的提示（E1）；appeal_ack（默认关）= 转成了给他回一条固定的系统提示（APPEAL_ACK，不是她说话）
  data/blocklist.json 新字段：appeals {QQ: [{id, t, via, what, case, text ≤200 字, sent, no, mid}]}（每人最近 APPEAL_MAX 条）；控制台撤销 / 删数据时整个删
config → blocklist（见 DEFAULTS）
"""
import copy
import re
import time
import uuid
from pathlib import Path

import clock
import safeio

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULTS = {"enabled": True,
            "block_min": 2,          # 拉黑：法庭引用、代码核实过的他的话至少几条（11-02 v1.17.10 阶段1：以前是「记账几次」）
            #   ↑ block_min / delete_min / delete_days：11-03 v1.17.13 起只有老判词的封顶（caps，回话自检的老情景）用；新流程看 cool_min / retrial_min_total / second_min
            "delete_min": 3,         # 删好友：核实过的引用至少几条
            "delete_days": 2,        # 删好友：这几条至少分布在几个不同的北京日子（11-02 v1.17.10 阶段1）
            "window_days": 7,
            "delete_daily": 1,       # 每天最多删几个好友（北京时间；影子模式「本来会删」也算）
            "protect_favor": 60,     # 好感到这么多（好朋友）的只冷却、不立案
            "dedup_min": 10,         # 同一个人同一类 几分钟只记一次（不带消息 id 的）
            "keep_days": 30,
            "cool_hours": 24,        # 她写 block → 马上冷却几小时（11-02 v1.17.10 阶段1）
            "court": True,           # 立案以后送法庭（false = 只冷却）
            "delete_mode": "shadow", # shadow：法庭判删好友也不真删 只告诉老板「本来会删」；auto：真删
            "court_timeout": 90,     # 法庭（Opus）最多等几秒
            "case_msgs": 40,         # 卷宗里最多放他几条话
            "court_daily": 10,       # 每天（北京时间）最多送法庭几个案子；超了只冷却（11-02 v1.17.10 阶段1 复核 R15）
            # 11-03 v1.17.13 新流程（一审 / 冷却中再审 / 二审）
            "hold_sec": 180,         # 立案 → 先不理他这么多秒等法庭（占位；至少按 court_timeout + 60 算）
            "cool_min": 1,           # 一审判冷却 / 二审保留以后冷却：代码核实过的他冲她说的话至少几句（不够 → 放过）
            "retrial_quiet_sec": 300,  # 冷却中他说完、安静这么多秒 → 再审一次
            "retrial_max": 3,        # 一个案子冷却期间最多再审几次
            "retrial_min_total": 2,  # 再审判删除：冷却期间核实的（至少 1 句）+ 立案时核实过的 一共至少几句
            "second_min": 2,         # 二审判删除：这次卷宗里核实过的至少几句
            "wipe_mode": "shadow",   # 删除时清他的数据：shadow 只记「本来会清」、告诉老板；auto 真清（调机器人删数据的流程）
            "cool_favor_drop": 10,   # 判冷却 / 删除（核实过至少 1 句）→ 他的好感扣这么多（最低 0；放过不扣）
            # 11-03 v1.17.15 申诉（小明：「她的名字也加申诉」）
            "appeal": True,          # 被她拉黑 / 冷却中的人申诉（加好友的验证消息 / 私聊明说「申诉」）→ 转给老板；false = 不收不转（照旧不理）
            "appeal_days": 7,        # 同一个人几天里最多转给老板一次（两条路一起算；0 = 每次都转）
            "appeal_ack": False}     # 私聊申诉转给老板以后 给他回一条固定的系统提示（不是她说话）；默认关 = 什么都不回
C = dict(DEFAULTS)
KINDS = {"sexy": "擦边 / 色情的话", "jailbreak": "要她改设定", "fake_owner": "冒充老板", "favor_min": "她给他的好感打了最低分",
         "ignore": "她说过不理他"}
CLUE_KINDS = ("sexy", "jailbreak", "fake_owner")      # 线索（代码抓的 他说的）
FEEL_KINDS = ("favor_min", "ignore")                  # 她的感受（不是证据）
CLUE_NAMES = {"sexy": "擦边", "jailbreak": "改设定", "fake_owner": "冒充老板"}
VERDICTS = ("驳回", "冷却", "拉黑", "删好友")
# 卷宗里他每句话的标记（bot.py 拼卷宗、court.py 写给法庭看、verify 核对都用这几个）
TAG_OWN = "本人→她"          # 他本人说的、冲她说的 —— 只有这种能当证据
TAG_NOT = "本人·没冲她"
TAG_QUOTE = "引用别人"       # 开头「（回复了 别人 的「…」）」
TAG_FWD = "转发"             # 带转发的聊天记录
# 11-03 v1.17.13：三种审，每种只许两个判词（法庭的判词不在这两个里 = 没判成）；LIGHT = 轻的那个（没判成 / 不认识时按它）
STAGE1, STAGE_RE, STAGE2 = "一审", "再审", "二审"
STAGE_VERDICTS = {STAGE1: ("放过", "冷却"), STAGE_RE: ("删除", "不动"), STAGE2: ("删除", "保留")}
LIGHT = {STAGE1: "放过", STAGE_RE: "不动", STAGE2: "保留"}
EV_MAX = 40                  # 一个案子冷却期间最多收他几句（新的挤掉旧的）
# 冷却期间他的话里像道歉的：不能当再审删除的证据（只会少删、不会多删；法庭自己也按「道歉不算越线」判）
SORRY = re.compile(r"对不起|对唔住|抱歉|我错了|是我不对|我的错|不好意思|原谅我|道歉|我检讨|sorry", re.I)


def configure(cfg=None, data_dir=None):
    global DATA
    C.clear()
    C.update(DEFAULTS)
    for k, v in (cfg or {}).items():
        if not str(k).startswith("_") and k in DEFAULTS:
            C[k] = v
    if data_dir:
        DATA = Path(data_dir)


def on():
    return bool(C.get("enabled", True))


def _f(k):
    try:
        return float(C.get(k, DEFAULTS[k]))
    except (TypeError, ValueError):
        return float(DEFAULTS[k])


def delete_mode():
    """删好友：shadow（默认 只告诉老板本来会删谁）/ auto（真删）—— 写错了一律当 shadow"""
    return "auto" if str(C.get("delete_mode") or "").strip().lower() == "auto" else "shadow"


def wipe_mode():
    """删除时清他的数据（11-03 v1.17.13）：shadow（默认 只告诉老板本来会清）/ auto（真清）—— 写错了一律当 shadow"""
    return "auto" if str(C.get("wipe_mode") or "").strip().lower() == "auto" else "shadow"


def path():
    return DATA / "blocklist.json"


EMPTY = {"blocked": {}, "marks": {}, "deletes": [], "tries": [], "cool": {}, "cases": [], "pardons": {}, "pending": {}, "off": {},
         "cooled": {}, "appeals": {}}
# appeals（11-03 v1.17.15）：{QQ: [申诉]} 被拉黑 / 冷却中的人的申诉（每人最近 APPEAL_MAX 条；控制台撤销 / 删数据时整个删）
# cooled（11-03 v1.17.13）：{QQ: {t 第一次, last 最近一次冷却到几点, case, n 次数}} 冷却过的人（冷却到点才算 再被写 block 走二审）
# off：{QQ: [[开始, 结束 或 null], …]} 他被冷却 / 拉黑的时间段（只有时间 没有文字）—— 开机补回时这段时间里他发的话不补（复核 R3）


def _fill(d):
    for k, v in EMPTY.items():
        if not isinstance(d.get(k), type(v)):
            d[k] = type(v)()
    return d


def _load():
    d = safeio.read_json(path(), None)
    return _fill(d if isinstance(d, dict) else {})


_BAD = object()


def _update(fn):
    """读来改写 blocklist.json（11-02 v1.17.10 阶段1 B17）→ 改好的数据 / None（没写）
    文件在、但读坏了（不是 JSON / 不是 dict）→ 只读不写（以前从空表开始写回 = 名单、冷却全没了）；一直打不开 → 不写；fn 返回 False → 不写"""
    p = path()
    try:
        d = safeio.read_json(p, _BAD, busy_raise=True)
    except safeio.Busy:
        return None
    if d is _BAD and not p.exists():
        d = {}
    if not isinstance(d, dict):
        return None
    _fill(d)
    if fn(d) is False:
        return d
    safeio.write_json(p, d)
    _B["m"] = None
    return d


_B = {"m": None, "p": None, "s": set(), "c": {}}


def _cache():
    """拉黑名单 + 冷却到几点（按文件修改时间缓存 —— control.blocked 每条消息都会问）"""
    p = path()
    try:
        m = p.stat().st_mtime_ns
    except OSError:
        _B.update(m=None, p=p, s=set(), c={})
        return _B
    if m != _B["m"] or p != _B["p"]:
        d = safeio.read_json(p, None)
        if isinstance(d, dict):
            cool = {}
            for u, e in (d.get("cool") or {}).items():
                try:
                    cool[str(u)] = float((e or {}).get("until") or 0)
                except (TypeError, ValueError, AttributeError):
                    pass
            _B.update(s={str(k) for k in (d.get("blocked") or {})}, c=cool)
        _B.update(m=m, p=p)                # 读坏了：照旧用上一次的（别每条消息都再读一遍坏文件）
    return _B


def is_blocked(uid, now=None):
    """她拉黑的、或者正在冷却的（11-02 v1.17.10 阶段1：冷却也算 —— handle 丢消息、到点的事作废这些现有逻辑自动管到冷却）"""
    uid = str(uid or "")
    if not uid:
        return False
    b = _cache()
    return uid in b["s"] or b["c"].get(uid, 0) > float(now or time.time())


def is_blocked_forever(uid):
    """永久拉黑（不算冷却）"""
    uid = str(uid or "")
    return bool(uid) and uid in _cache()["s"]


def cooling(uid, now=None):
    """正在冷却 → 冷却到几点（时间戳）；没在冷却 → 0"""
    uid = str(uid or "")
    if not uid:
        return 0
    u = _cache()["c"].get(uid, 0)
    return u if u > float(now or time.time()) else 0


def blocked_list():
    """控制台：她拉黑的人 {QQ: 那一项}"""
    return dict(_load().get("blocked") or {})


def cool_list(now=None):
    """控制台：正在冷却的人 {QQ: 那一项}（到期的不列）"""
    now = float(now or time.time())
    return {u: e for u, e in (_load().get("cool") or {}).items() if isinstance(e, dict) and float(e.get("until") or 0) > now}


def add_block(uid, ent):
    """进拉黑名单 → 记上了没有（文件一直打不开 / 读坏了 → False）"""
    def upd(d):
        d["blocked"][str(uid)] = dict(ent)
        _off_add(d, uid, float(ent.get("t") or time.time()), None)
    r = _update(upd)
    _B["m"] = None
    return r is not None and str(uid) in (r.get("blocked") or {})


def _off_add(d, uid, start, end):
    """记一段「这段时间她不理他」（冷却 / 拉黑）：最多留 20 段、结束超过 keep_days 天的扔掉"""
    xs = d["off"].setdefault(str(uid), [])
    xs.append([int(start), int(end) if end else None])
    keep = time.time() - _f("keep_days") * 86400
    d["off"][str(uid)] = [x for x in xs if isinstance(x, list) and len(x) == 2 and (x[1] is None or float(x[1]) >= keep)][-20:]


def was_off(uid, t):
    """t 那一刻她是不是在冷却 / 拉黑他（11-02 v1.17.10 阶段1 复核 R3：冷却到期后重启 别把冷却期间他发的话补回来回）
    看三样：记下的时间段（撤销时会截到撤销那一刻）、现在的冷却、现在的拉黑名单"""
    uid = str(uid or "")
    if not uid:
        return False
    t = float(t or 0)
    d = _load()
    for x in d["off"].get(uid) or []:
        try:
            if float(x[0]) <= t and (x[1] is None or t < float(x[1])):
                return True
        except (TypeError, ValueError, IndexError):
            continue
    c = d["cool"].get(uid) if isinstance(d["cool"].get(uid), dict) else {}
    if c and float(c.get("t") or 0) <= t < float(c.get("until") or 0):
        return True
    e = d["blocked"].get(uid) if isinstance(d["blocked"].get(uid), dict) else {}
    return bool(e) and float(e.get("t") or 0) <= t


def mark_deleted(uid, now=None):
    def upd(d):
        e = d["blocked"].get(str(uid))
        if not isinstance(e, dict):
            return False
        e["deleted"] = int(float(now or time.time()))
    _update(upd)


def start_cool(uid, name="", why="", conv="", now=None, hours=None, case=""):
    """她写 block 的那一刻：马上冷却（11-02 v1.17.10 阶段1）→ 冷却到几点（时间戳；写不进去 → 0）
    已经在冷却、到得更晚 → 照旧（不缩短）"""
    uid = str(uid or "")
    if not uid:
        return 0
    now = float(now or time.time())
    until = int(now + (_f("cool_hours") if hours is None else float(hours)) * 3600)
    out = {"u": 0}

    def upd(d):
        old = d["cool"].get(uid) if isinstance(d["cool"].get(uid), dict) else {}
        if float(old.get("until") or 0) > until:
            out["u"] = int(old["until"])
            return False
        d["cool"][uid] = {"t": int(now), "until": until, "name": _clip(name, 20), "why": _clip(why, 40), "conv": str(conv or ""),
                          "case": str(case or ""), "by": "她"}
        _off_add(d, uid, now, until)
        keep = now - _f("keep_days") * 86400
        for k in [k for k, e in d["cool"].items() if not isinstance(e, dict) or float(e.get("until") or 0) < keep]:
            d["cool"].pop(k, None)
        out["u"] = until
    r = _update(upd)
    return out["u"] if r is not None else 0


def unblock(uid, by="控制台", now=None):
    """撤销（控制台）：解除拉黑 + 冷却、记赦免时间（之前的线索、案子不再算 11-02 v1.17.10 阶段1 B6）→ 撤掉的那一项 / None
    删过的好友不会自己加回来（要他重新加她）；读坏了 / 打不开 → None（不写）
    11-03 v1.17.13：一起清掉「冷却过」；还开着的案子（审理中 / 冷却中）收的证据作废、冷却中的结案；
      冷却早就到期的、只剩「冷却过」的也能撤（Gemini 2-F1：以前直接返回、不记赦免 —— 之前的话照样进下一次的卷宗、照样直接二审）；
      名单、冷却、冷却过里都没有他 → None"""
    uid = str(uid or "")
    now = float(now or time.time())
    out = {}

    def upd(d):
        e = d["blocked"].pop(uid, None)
        c = d["cool"].get(uid) if isinstance(d["cool"].get(uid), dict) else None
        cd = d["cooled"].pop(uid, None)
        cd = cd if isinstance(cd, dict) else None
        live = c is not None and float(c.get("until") or 0) > now
        if e is None and c is None and cd is None:
            return False
        d["appeals"].pop(uid, None)        # 他的申诉（11-03 v1.17.15）：撤销了就了结了（以后再被拉黑 / 冷却重新算、马上又能转给老板）
        if live:
            d["cool"].pop(uid, None)       # 早就到期的冷却：记录留着（只剩时间段 已经不挡他了）
        if e is not None:
            out["e"] = e
        else:
            out["e"] = dict(c or {}, cooling=True)
            if not live:
                out["e"]["expired"] = True     # 冷却早就到期了 / 只剩「冷却过」（11-03 v1.17.13 Gemini 2-F1）
        if cd is not None:
            out["e"]["cooled"] = dict(cd)
        d["pardons"][uid] = round(now, 3)          # 到毫秒（复核 R6：存整秒 同一秒里立的案会被当成撤销之后的）
        for x in d["off"].get(uid) or []:           # 撤销那一刻起就不算了（R3）
            if isinstance(x, list) and len(x) == 2 and (x[1] is None or float(x[1]) > now):
                x[1] = int(now)
        for cs in d["cases"]:                      # 还开着的案子：冷却期间收的证据作废、冷却中的结案（11-03 v1.17.13）
            if isinstance(cs, dict) and str(cs.get("uid")) == uid and cs.get("state") in ("审理中", "冷却中"):
                cs["ev"] = []
                cs["pardoned"] = int(now)
                if cs.get("state") == "冷却中":
                    cs.update(state="结案", end="撤销了", closed=int(now))
        what = "拉黑" if e is not None else ("冷却" if live else ("冷却过的记录" if cd is not None else "冷却（早就到期了）"))
        d["tries"].append({"t": int(now), "uid": uid, "name": (out["e"] or {}).get("name") or "",
                           "why": f"{by}撤销了{what}", "block": False, "delete": False, "n": 0, "no": ""})
        d["tries"] = d["tries"][-100:]
    _update(upd)
    _B["m"] = None
    return out.get("e")


def pardoned_at(uid):
    """控制台撤销（赦免）的时间；没撤销过 → 0"""
    try:
        return float((_load().get("pardons") or {}).get(str(uid)) or 0)
    except (TypeError, ValueError):
        return 0.0


def _clip(t, n=40):
    return re.sub(r"\s+", " ", str(t or "")).strip()[:n]


def kinds_of(m):
    """一条记录的几类（老记录只有 kind）"""
    ks = m.get("kinds") if isinstance(m.get("kinds"), list) else None
    return [k for k in (ks or [m.get("kind")]) if k]


# ───────────── 记账（线索 / 她的感受）─────────────
def mark(uid, kind, conv="", text="", now=None, name="", mid=None):
    """记一笔（11-02 v1.17.10 阶段1：只当线索 / 她的感受 不计入任何门槛）→ 记上了没有
    kind：一类或几类（list）—— 同一条消息命中几类只记一条（kinds 合并 B3）；带 mid 的：同一条消息再记 → 并进那一条
    没带 mid 的：同一个人同一类 dedup_min 分钟只记一次（老规矩）"""
    uid = str(uid or "")
    ks = [kind] if isinstance(kind, str) else list(kind or [])
    ks = [k for k in dict.fromkeys(ks) if k in KINDS]
    if not on() or not uid or not ks:
        return False
    now = float(now or time.time())
    out = {"ok": False}

    def upd(d):
        ms = d["marks"].setdefault(uid, [])
        if mid is not None:
            hit = next((m for m in ms if m.get("mid") is not None and str(m.get("mid")) == str(mid)), None)
            if hit:
                old = kinds_of(hit)
                new = [k for k in ks if k not in old]
                if not new:
                    return False
                hit["kinds"] = old + new
                hit["kind"] = hit["kinds"][0]
                out["ok"] = True
                return None
            fresh = ks
        else:
            fresh = [k for k in ks if not any(k in kinds_of(m) and now - float(m.get("t") or 0) < _f("dedup_min") * 60 for m in ms)]
        if not fresh:
            return False
        rec = {"t": int(now), "kind": fresh[0], "kinds": fresh, "conv": str(conv or ""), "text": _clip(text), "name": _clip(name, 20)}
        if mid is not None:
            rec["mid"] = str(mid)
        ms.append(rec)
        keep = now - _f("keep_days") * 86400
        ms[:] = [m for m in ms if float(m.get("t") or 0) >= keep][-60:]
        out["ok"] = True
    try:
        _update(upd)
    except Exception:
        return False
    return out["ok"]


def marks(uid, now=None, days=None, kinds=None):
    """他 days 天（默认 window_days）里被记的（赦免之前的不算；kinds 给了只要这几类；新的在后）"""
    now = float(now or time.time())
    d = _load()
    lim = max(now - (_f("window_days") if days is None else float(days)) * 86400,
              float((d.get("pardons") or {}).get(str(uid)) or 0) + 0.001)
    out = [m for m in (d.get("marks") or {}).get(str(uid), []) if isinstance(m, dict) and float(m.get("t") or 0) >= lim]
    if kinds:
        out = [m for m in out if set(kinds_of(m)) & set(kinds)]
    return out


def evidence_text(ms, n=4):
    """记录（给老板看的 / 日志）：几号几点 哪几类「原话」"""
    return "；".join(f"{clock.strf('%m-%d %H:%M', m.get('t'))} " + "、".join(KINDS.get(k, k) for k in kinds_of(m))
                    + (f"「{m.get('text')}」" if m.get("text") else "") for m in list(ms)[-n:])


# ───────────── 谁不能被告 / 核对引用 / 封顶 ─────────────
def protected(uid, owner_qq="", favor=0.0, circle=""):
    """受保护的 → 为什么；不受 → \"\""""
    uid = str(uid or "")
    if not uid:
        return "不知道是谁"
    if owner_qq and uid == str(owner_qq):
        return "是老板"
    if circle in ("老板", "最亲"):
        return "是你最亲的人"
    try:
        if float(favor or 0) >= _f("protect_favor"):
            return f"你对他的好感有 {int(float(favor))}（好朋友）"
    except (TypeError, ValueError):
        pass
    return ""


def gate(uid, owner_qq="", favor=0.0, circle=""):
    """她写 block 时（11-02 v1.17.10 阶段1）→ {"level", "why"}：
    owner 老板：什么都不做（不冷却、不立案）；cool 最亲圈 / 好感 ≥ protect_favor：只冷却、不立案；case 立案送法庭
    favor / circle 要按这一轮扣分之前的（B2：以前先扣分再查 好感 61 的人同一轮 −3 就掉出保护）"""
    uid = str(uid or "")
    if not uid:
        return {"level": "none", "why": "不知道是谁"}
    if (owner_qq and uid == str(owner_qq)) or circle == "老板":
        return {"level": "owner", "why": "是老板"}
    p = protected(uid, "", favor, circle)
    if p:
        return {"level": "cool", "why": p}
    return {"level": "case", "why": ""}


def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    m = re.fullmatch(r"\s*#?\s*(\d{1,4})\s*", str(x)) if isinstance(x, str) else None
    return int(m.group(1)) if m else None


def verify(cites, table, uid, now=None, pardon=None):
    """法庭引用的编号 → 核实过的那几条（11-02 v1.17.10 阶段1）
    只算：编号在卷宗里、那条是他本人发的、冲她说的（标 本人→她：不是引用别人 / 转发 / 没冲她）、在 window_days 天内、在赦免之后
    table：{编号: {"uid", "t", "tag", "mid", "conv", "text"}}（court.block_input 给的）"""
    now = float(now or time.time())
    lim = now - _f("window_days") * 86400
    pd = pardoned_at(uid) if pardon is None else float(pardon or 0)
    tb = {}
    for k, v in (table or {}).items():
        n = _num(k)
        if n is not None and isinstance(v, dict):
            tb[n] = v
    out, seen, mids = [], set(), set()
    for c in cites or []:
        n = _num(c)
        m = tb.get(n) if n is not None else None
        if not m or n in seen:
            continue
        if str(m.get("uid")) != str(uid) or m.get("tag") != TAG_OWN:
            continue
        try:
            t = float(m.get("t") or 0)
        except (TypeError, ValueError):
            continue
        if t < lim or t <= pd or t > now + 120:
            continue
        # 同一条只算一次（复核 R10）：同一个消息 id；或者同一个对话里 60 秒内一字不差的（老存档没 mid 存了两遍 / 连发同一句）
        if m.get("mid") is not None and str(m.get("mid")) in mids:
            continue
        core = re.sub(r"\s+", "", str(m.get("text") or ""))
        if any(str(x.get("conv")) == str(m.get("conv")) and re.sub(r"\s+", "", str(x.get("text") or "")) == core
               and abs(float(x.get("t") or 0) - t) <= 60 for x in out):
            continue
        seen.add(n)
        if m.get("mid") is not None:
            mids.add(str(m.get("mid")))
        out.append(dict(m, n=n))
    return out


def deleted_today(now=None):
    """今天（北京时间）删了几个好友：真删了的 + 影子模式「本来会删」的 + 不确定删没删的（宁可多算）
    11-03 v1.17.13：判删除的（记录上有 wipe：清了 / 本来会清 —— 不是好友、没删好友的也算）也算一个"""
    day = clock.strf("%Y-%m-%d", now)
    d = _load()
    return sum(1 for x in d.get("deletes") or [] if isinstance(x, dict) and x.get("day") == day
               and (x.get("ok") or x.get("shadow") or x.get("result") == "不确定" or x.get("wipe"))) \
        + sum(1 for x in (d.get("pending") or {}).values() if isinstance(x, dict) and x.get("day") == day and x.get("state") == "进行中")   # R13


def caps(verdict, ok, is_friend=False, now=None, deleted=None):
    """法庭的判词 + 核实过的引用 → 最后怎么办（只往轻里压、不往重里加 11-02 v1.17.10 阶段1）
    → {"result": 驳回 / 冷却 / 拉黑 / 删好友, "why": 降级的原因, "n": 核实过几条, "days": 分布在几个北京日子}"""
    ok = list(ok or [])
    n = len(ok)
    days = len({clock.strf("%Y-%m-%d", float(m.get("t") or 0)) for m in ok})
    res = verdict if verdict in VERDICTS else "冷却"
    why = []
    bmin = max(1, int(_f("block_min")))                 # 下限（复核 R12：配成 0 = 0 条也拉黑；删好友的门槛不能比拉黑低）
    dmin = max(bmin, int(_f("delete_min")))
    ddays = max(1, int(_f("delete_days")))
    daily = max(0, int(_f("delete_daily")))
    if res == "删好友":
        dn = deleted_today(now) if deleted is None else int(deleted)
        if n < dmin:
            why.append(f"核实过的引用 {n} 条（删好友要 {dmin} 条）")
        elif days < ddays:
            why.append(f"核实过的引用只在 {days} 天里（删好友要分布在 {ddays} 天）")
        elif not is_friend:
            why.append("不是你的 QQ 好友（不用删）")
        elif dn >= daily:
            why.append(f"今天已经删了 {dn} 个（每天最多 {daily} 个）")
        if why:
            res = "拉黑"
    if res == "拉黑" and n < bmin:
        why.append(f"核实过的引用 {n} 条（拉黑要 {bmin} 条）")
        res = "冷却"
    return {"result": res, "why": "；".join(why), "n": n, "days": days}


def trial_caps(stage, verdict, ok, prior=0):
    """新流程的封顶（11-03 v1.17.13）：法庭的判词 + 核实过的引用（verify 出来的）→ 最后怎么办（只往轻里压、不往重里加）
    一审：冷却 要核实 ≥ cool_min 句，不够 → 放过
    再审：删除 要冷却期间核实 ≥ 1 句（像道歉的不算）、而且加上立案时核实过的（prior）一共 ≥ retrial_min_total 句，不够 → 不动
    二审：删除 要这次核实 ≥ second_min 句，不够 → 保留；保留：这次核实过 ≥ cool_min 句 → 冷却，一句都没有 → 放过
    判词不认识 → 按轻的那个（LIGHT）
    → {"result": 放过 / 冷却 / 删除 / 不动, "verdict": 按哪个判词算的, "why": 降级的原因, "n": 算数的几句, "days", "ok": 算数的那几句}"""
    ok = [m for m in ok or [] if isinstance(m, dict)]
    stage = stage if stage in STAGE_VERDICTS else STAGE1
    vd = verdict if verdict in STAGE_VERDICTS[stage] else LIGHT[stage]
    sorry = []
    if stage == STAGE_RE:
        sorry = [m for m in ok if m.get("sorry") or SORRY.search(str(m.get("text") or ""))]
        ok = [m for m in ok if m not in sorry]
    n = len(ok)
    days = len({clock.strf("%Y-%m-%d", float(m.get("t") or 0)) for m in ok})
    cmin = max(1, int(_f("cool_min")))
    why, res = [], vd
    if stage == STAGE_RE and vd == "删除":
        tot = max(2, int(_f("retrial_min_total")))
        pr = max(0, int(prior or 0))
        if n < 1 or n + pr < tot:
            why.append((f"冷却期间的话核实 {n} 句（删除要至少 1 句）" if n < 1 else
                        f"冷却期间核实 {n} 句 + 立案时 {pr} 句（删除一共要 {tot} 句）")
                       + (f"；像道歉的 {len(sorry)} 句不算" if sorry else ""))
            res = "不动"
    elif stage == STAGE2:
        if vd == "删除":
            smin = max(2, int(_f("second_min")))
            if n < smin:
                why.append(f"这次核实 {n} 句（删除要 {smin} 句）")
                res = "保留"
        if res == "保留":
            res = "冷却" if n >= cmin else "放过"
    elif vd == "冷却" and n < cmin:
        why.append(f"核实 {n} 句（冷却要 {cmin} 句）")
        res = "放过"
    return {"result": res, "verdict": vd, "why": "；".join(why), "n": n, "days": days, "ok": ok}


# ───────────── 案子 ─────────────
def new_cid(now=None):
    """案号：月日-时分-4 位随机（11-03 v1.17.13：立案前先定好 —— 占位的冷却要记上是哪个案子的）"""
    return clock.strf("%m%d-%H%M", float(now or time.time())) + "-" + uuid.uuid4().hex[:4]


def _sweep(d, now):
    """冷却中的案子：冷却到点了 → 结案（冷却到点）→ 结了几个（11-03 v1.17.13：立案 / 开机时顺手扫一遍；再审的去抖那边看到到点了也会结）"""
    k = 0
    for c in d["cases"]:
        if isinstance(c, dict) and c.get("state") == "冷却中" and float(c.get("cool_until") or 0) <= now:
            c.update(state="结案", end=c.get("end") or "冷却到点", closed=int(float(c.get("cool_until") or now)))
            k += 1
    return k


def sweep(now=None):
    now = float(now or time.time())
    out = {"k": 0}

    def upd(d):
        out["k"] = _sweep(d, now)
        pr = _ap_prune(d, now)             # 过期的申诉（11-03 v1.17.15：超过 max(keep_days, appeal_days) 天的）顺手扔掉
        return None if (out["k"] or pr) else False
    _update(upd)
    return out["k"]


def open_case(uid, name="", why="", conv="", now=None, cool_until=0, stage="", evok=False, cid=""):
    """立案（状态「审理中」）→ 案号；写不进去 → \"\"
    11-03 v1.17.13：stage 一审 / 二审；evok 冷却中收不收证据、再不再审（有法庭、他不是最亲的人）；cid 先定好的案号（new_cid）"""
    now = float(now or time.time())
    cid = str(cid or new_cid(now))
    rec = {"id": cid, "uid": str(uid), "name": _clip(name, 20), "t": int(now), "conv": str(conv or ""), "why": _clip(why, 40),
           "state": "审理中", "cool_until": int(cool_until or 0)}
    if stage:
        rec["stage"] = str(stage)
    if evok:
        rec["evok"] = True

    def upd(d):
        _sweep(d, now)
        d["cases"].append(rec)
        keep = now - _f("keep_days") * 86400
        d["cases"] = [c for c in d["cases"] if isinstance(c, dict) and (float(c.get("t") or 0) >= keep or c.get("state") in ("审理中", "冷却中"))][-200:]
    r = _update(upd)
    return cid if r is not None else ""


def update_case(cid, **fields):
    """改案子的几项（不结案；11-03 v1.17.13 判了冷却 → state 冷却中）→ 改上了没有"""
    out = {"ok": False}

    def upd(d):
        for c in d["cases"]:
            if isinstance(c, dict) and c.get("id") == cid:
                c.update(fields)
                out["ok"] = True
                return None
        return False
    _update(upd)
    return out["ok"]


def close_case(cid, **fields):
    """结案：写判词、核实、结果、模型、耗时、花费 → 写上了没有"""
    out = {"ok": False}

    def upd(d):
        for c in d["cases"]:
            if isinstance(c, dict) and c.get("id") == cid:
                c.update(fields)
                c["state"] = "结案"
                c["closed"] = int(time.time())
                out["ok"] = True
                return None
        return False
    _update(upd)
    return out["ok"]


def case(cid):
    return next((dict(c) for c in _load()["cases"] if isinstance(c, dict) and c.get("id") == cid), None)


def cases(uid=None, after=None, n=None):
    """案子（旧的在前）：uid 给了只要他的；after 给了只要这之后立的（赦免之前的不算）"""
    out = [c for c in _load()["cases"] if isinstance(c, dict)]
    if uid is not None:
        out = [c for c in out if str(c.get("uid")) == str(uid)]
    if after:
        out = [c for c in out if float(c.get("t") or 0) > float(after)]
    return out[-int(n):] if n else out


def cases_on(day=None):
    """这一天（北京日期 默认今天）立了几个案（送法庭的；复核 R15 每天上限用）"""
    day = day or clock.strf("%Y-%m-%d")
    return sum(1 for c in _load()["cases"] if isinstance(c, dict) and clock.strf("%Y-%m-%d", float(c.get("t") or 0)) == day)


def stuck_cases():
    """开机：卡在「审理中」的（上次庭审到一半程序重启了）"""
    return [c for c in _load()["cases"] if isinstance(c, dict) and c.get("state") == "审理中"]


# ───────────── 11-03 v1.17.13：占位 / 冷却中收证据 / 再审 / 冷却过 ─────────────
def open_case_of(uid, now=None):
    """他还在审理中（占位）的案子 → 那一项 / None（审理中又被写 block：不另开庭 2-存疑1）
    一个小时以前立的还挂着「审理中」= 收尾没写上（开机会收）→ 不算（别让他永远立不了案）"""
    now = float(now or time.time())
    return next((dict(c) for c in reversed(_load()["cases"]) if isinstance(c, dict) and str(c.get("uid")) == str(uid)
                 and c.get("state") == "审理中" and now - float(c.get("t") or 0) < 3600), None)


def cooling_cases():
    """开机：冷却中的案子（还在收证据 / 等再审的）"""
    return [dict(c) for c in _load()["cases"] if isinstance(c, dict) and c.get("state") == "冷却中"]


def keep_hold(uid, case, until, now=None):
    """法庭判完了、等锁的工夫（11-03 v1.17.13 合并复查）：把这个案子的占位续到 until → 续上了没有
    只续这个案子的那一项、案子还在审理中（撤销了 / 判过了 / case 对不上 / 已经更晚 → 不动）；「这段时间她不理他」跟着续"""
    uid, case = str(uid or ""), str(case or "")
    now = float(now or time.time())
    until = int(until)
    out = {"ok": False}

    def upd(d):
        c = d["cool"].get(uid)
        if not uid or not case or not isinstance(c, dict) or str(c.get("case") or "") != case or float(c.get("until") or 0) >= until:
            return False
        cs = next((x for x in d["cases"] if isinstance(x, dict) and str(x.get("id")) == case), None)
        if not cs or cs.get("state") != "审理中" or cs.get("pardoned"):
            return False
        c["until"] = until
        _off_add(d, uid, now, until)
        out["ok"] = True
    _update(upd)
    _B["m"] = None
    return out["ok"]


def end_cool(uid, case="", now=None):
    """法庭判放过：占位的冷却马上结束（只结束这个案子的那一项；case 对不上 / 没有 → 不动）→ 结束了没有
    「这段时间她不理他」那一段截到现在（开机补回时这段时间里他发的照样不补 R3）"""
    uid = str(uid or "")
    now = float(now or time.time())
    out = {"ok": False}

    def upd(d):
        c = d["cool"].get(uid)
        if not isinstance(c, dict) or (case and str(c.get("case") or "") != str(case)):
            return False
        d["cool"].pop(uid, None)
        for x in d["off"].get(uid) or []:
            if isinstance(x, list) and len(x) == 2 and x[1] is not None and float(x[1]) > now:
                x[1] = int(now)
        out["ok"] = True
    _update(upd)
    _B["m"] = None
    return out["ok"]


def mark_cooled(uid, until, case="", now=None):
    """判了冷却 → 记「冷却过」：{t 第一次, last 这次冷却到几点, case, n 次数}；不按 keep_days 过期
    冷却到点才算数（cooled_of 看 last ≤ 现在）；控制台撤销时清掉；删数据只留号和时间"""
    uid = str(uid or "")
    if not uid or not until:
        return False
    now = float(now or time.time())

    def upd(d):
        old = d["cooled"].get(uid) if isinstance(d["cooled"].get(uid), dict) else {}
        d["cooled"][uid] = {"t": int(float(old.get("t") or now)), "last": int(float(until)), "case": str(case or ""),
                            "n": int(old.get("n") or 0) + 1}
    return _update(upd) is not None


def cooled_of(uid, now=None):
    """他算不算「冷却过」：冷却已经到点了（last ≤ 现在）、在最近一次控制台撤销之后 → 那一项 / None"""
    uid = str(uid or "")
    if not uid:
        return None
    now = float(now or time.time())
    d = _load()
    e = d["cooled"].get(uid)
    if not isinstance(e, dict):
        return None
    try:
        last = float(e.get("last") or 0)
        pd = float((d.get("pardons") or {}).get(uid) or 0)
    except (TypeError, ValueError):
        return None
    if not last or last > now or last <= pd:
        return None
    return dict(e)


def cooled_list(now=None):
    """控制台：冷却过的人（冷却已经到点了的）{QQ: {t, last, case, n}}"""
    now = float(now or time.time())
    out = {}
    for u, e in (_load().get("cooled") or {}).items():
        try:
            if isinstance(e, dict) and 0 < float(e.get("last") or 0) <= now:
                out[u] = e
        except (TypeError, ValueError):
            continue
    return out


def ev_case(uid, now=None):
    """他这会儿冲她说的话要不要当证据收 → 收进哪个案子（副本）/ None
    收：占位 / 冷却那一项还在（控制台撤销了、到点了就不收）；他最近的那个案子收证据（evok：有法庭、他不是最亲 / 好感 ≥ protect_favor）、
        没删过数据；而且 审理中（占位），或者 冷却中、冷却还没到点、再审还没用完（retrial_max）"""
    uid = str(uid or "")
    if not uid:
        return None
    now = float(now or time.time())
    d = _load()
    cu = d["cool"].get(uid) if isinstance(d["cool"].get(uid), dict) else {}
    if float(cu.get("until") or 0) <= now:
        return None
    c = next((x for x in reversed(d["cases"]) if isinstance(x, dict) and str(x.get("uid")) == uid), None)
    if not c or c.get("wiped") or not c.get("evok"):
        return None
    if c.get("state") == "审理中" or (c.get("state") == "冷却中" and float(c.get("cool_until") or 0) > now
                                     and len(c.get("retrials") or []) < max(0, int(_f("retrial_max")))):
        return copy.deepcopy(c)
    return None


def add_ev(cid, item, now=None):
    """审理中（占位）/ 冷却中他冲她说的一句 → 记进案子（只当证据：不进聊天记录 / 存档 / 记忆）→ 记上了没有
    item：{t, mid, conv, tag, text}（text 是他本人的话 own_text，存 ≤200 字）；同一个消息 id 只记一次；最多 EV_MAX 条（新的挤掉旧的）；
    记下最后一句的时间 ev_last（再审的去抖）；像道歉的标 sorry（不能当再审删除的证据）"""
    now = float(now or time.time())
    out = {"ok": False}
    text = _clip(item.get("text"), 200)

    def upd(d):
        c = next((x for x in d["cases"] if isinstance(x, dict) and x.get("id") == cid), None)
        if not c or c.get("wiped") or not c.get("evok") or c.get("state") not in ("审理中", "冷却中"):
            return False
        ev = [e for e in c.get("ev") or [] if isinstance(e, dict)]
        mid = item.get("mid")
        if mid is not None and any(e.get("mid") is not None and str(e.get("mid")) == str(mid) for e in ev):
            return False
        t = float(item.get("t") or now)
        rec = {"t": round(t, 3), "conv": str(item.get("conv") or ""), "tag": str(item.get("tag") or TAG_NOT), "text": text}
        if mid is not None:
            rec["mid"] = str(mid)
        if SORRY.search(text):
            rec["sorry"] = True
        ev.append(rec)
        c["ev"] = ev[-EV_MAX:]
        c["ev_last"] = round(max(t, float(c.get("ev_last") or 0)), 3)
        out["ok"] = True
    _update(upd)
    return out["ok"]


def ev_due(c):
    """这个案子有没有还没审过的、他冲她说的话（标 本人→她）—— 有才再审（11-03 v1.17.13）
    只有 @别人 / 只是提到她 / 转发、引用别人的：照样收着（下次再审时给法庭当背景），但不为它们开再审 —— 核对时它们本来就不能当证据，
    为它们开庭只会白花一次、还占掉一次再审的名额（retrial_max）"""
    return any(isinstance(e, dict) and not e.get("seen") and e.get("tag") == TAG_OWN for e in (c or {}).get("ev") or [])


def retrial_begin(cid, now=None):
    """再审开始：这次没审过的证据标上第几次再审（seen）、记「正在再审」rt_run（重启时按不动收尾）→ 案子副本 / None（没有要审的 / 用完了）
    没审过的里一句他冲她说的都没有（ev_due 假）→ 不开（11-03 v1.17.13）"""
    now = float(now or time.time())
    out = {}

    def upd(d):
        c = next((x for x in d["cases"] if isinstance(x, dict) and x.get("id") == cid), None)
        if not c or c.get("wiped") or c.get("state") != "冷却中":
            return False
        k = len(c.get("retrials") or []) + 1
        new = [e for e in c.get("ev") or [] if isinstance(e, dict) and not e.get("seen")]
        if not ev_due(c) or k > max(0, int(_f("retrial_max"))):
            return False
        for e in new:
            e["seen"] = k
        c["rt_run"] = round(now, 3)
        out["c"] = copy.deepcopy(c)
    _update(upd)
    return out.get("c")


def retrial_end(cid, rec, **fields):
    """再审完了：retrials 里记一笔 rec（不带原话）、清「正在再审」；fields：顺手改案子的几项（删除了 → 结案）→ 记上了没有"""
    out = {"ok": False}

    def upd(d):
        c = next((x for x in d["cases"] if isinstance(x, dict) and x.get("id") == cid), None)
        if not c:
            return False
        c["retrials"] = ([r for r in c.get("retrials") or [] if isinstance(r, dict)] + [dict(rec)])[-10:]
        c.pop("rt_run", None)
        c.update(fields)
        out["ok"] = True
    _update(upd)
    return out["ok"]


# ───────────── 11-03 v1.17.15：申诉 ─────────────
# 小明 11-03「她的名字也加申诉」：被她拉黑（永久）/ 冷却中的人有一条路让老板知道他想申诉（bot.py _appeal 转给老板；她本人照旧不跟他们说话）
APPEAL_RE = re.compile(r"^[\s【\[［(（<《「『#＃/／]*(?:我(?:要|想)?|要|想)?申[诉述]")
APPEAL_MAX = 5               # 每个人留最近几条申诉（最近一次转给老板的那条一直留着：限次要看它）
APPEAL_ACK = "（系统消息）你的申诉已经转给管理员了；同一个人 {d} 天内只转一次。这条是系统自动发的。"


def is_appeal(text):
    """明说申诉（11-03 v1.17.15：私聊的、冷却中加好友时的验证消息）：开头是「申诉」（打成「申述」也算）—— 前面可以有空格、括号、#、/；
    「我要申诉」「我想申诉」「我申诉」「要申诉」「想申诉」也算。只看开头：「你好 我想申诉」不算（别的话照旧；拿不准的往不转的方向）"""
    return bool(APPEAL_RE.match(str(text or "")))


def _ap_items(d, uid):
    xs = d["appeals"].get(str(uid))
    return [x for x in xs if isinstance(x, dict)] if isinstance(xs, list) else []


def _ap_target(d, uid, now):
    """他这会儿算不算能申诉的（她拉黑的 / 正在冷却的）→ 名单 / 冷却那一项 + 那个案子的情况 / None"""
    uid = str(uid)
    e, c = d["blocked"].get(uid), d["cool"].get(uid)
    if isinstance(e, dict):
        src, out = e, {"what": "拉黑", "t": e.get("t"), "until": 0, "deleted": e.get("deleted") or 0}
    elif isinstance(c, dict) and float(c.get("until") or 0) > now:
        src, out = c, {"what": "冷却", "t": c.get("t"), "until": c.get("until")}
    else:
        return None
    out.update(name=str(src.get("name") or ""), why=str(src.get("why") or ""), case=str(src.get("case") or ""))
    cs = next((x for x in reversed(d["cases"]) if isinstance(x, dict) and out["case"] and str(x.get("id")) == out["case"]), None)
    out["case_ok"] = cs is not None        # 案子的记录还在（结了的案子 keep_days 天以后就过期了 只剩名单 / 冷却那一项）
    for k in ("stage", "state", "verdict", "result", "result_why", "reason", "end", "friend", "wipe", "wiped"):
        if cs and cs.get(k):
            out[k] = cs[k]
    return out


def appeal_target(uid, now=None):
    """他现在能不能申诉（11-03 v1.17.15）→ 他这会儿的情况（给老板的通知用）/ None
    只认她自己拉黑的（blocked 永久）和正在冷却的（cool 那一项还没到点：审理中的占位 / 判了冷却 / 没判成只冷却）；控制台「不理的人」不归这里管
    → {what 拉黑 / 冷却, t 拉黑 / 冷却从什么时候, until 冷却到几点（拉黑 0）, name, why, case, deleted（好友删了）,
       stage, state, verdict, result, result_why, reason, end, friend, wipe, wiped —— 案子过期了 / 老名单没有案子 → 案子那几项没有}"""
    uid = str(uid or "")
    if not uid:
        return None
    try:
        return _ap_target(_load(), uid, float(now or time.time()))
    except (TypeError, ValueError):
        return None


def appeal_add(uid, via, text, now=None, mid=None, what="", case=""):
    """记一条申诉（11-03 v1.17.15）→ {"send": 这条要不要转给老板, "why": 不转的原因, "id": 这一条的编号, "text": 存下的话, "last": 上次转给老板的时间}
    / None（appeal 关着 / 同一个消息 id 记过了 / blocklist.json 写不进去）
    限次：同一个人 appeal_days 天里最多转一次（两条路一起算；转成功的才算 —— 没发出去 appeal_sent(…, False) 把名额退回来；控制台撤销之前的不算）
    存在 appeals {QQ: [{id, t, via, what, case, text（≤200 字）, sent, no 没转的原因, mid}]}：每人最近 APPEAL_MAX 条（最近一次转了的那条一直留着）、
    超过 max(keep_days, appeal_days) 天的扔掉；控制台撤销（unblock）、删数据（forget）整个删"""
    uid = str(uid or "")
    if not uid or not bool(C.get("appeal", True)):
        return None
    now = float(now or time.time())
    days = max(0.0, _f("appeal_days"))
    out = {}

    def upd(d):
        _ap_prune(d, now)                  # 所有人过期的申诉顺手扔掉
        xs = _ap_items(d, uid)
        if mid is not None and any(x.get("mid") is not None and str(x.get("mid")) == str(mid) for x in xs):
            return False
        pd = float((d.get("pardons") or {}).get(uid) or 0)
        last = max([float(x.get("t") or 0) for x in xs if x.get("sent") and float(x.get("t") or 0) > pd] or [0.0])
        send = not (last and days > 0 and now - last < days * 86400)
        rec = {"id": uuid.uuid4().hex[:8], "t": round(now, 3), "via": _clip(via, 10), "what": _clip(what, 6), "case": str(case or ""),
               "text": _clip(text, 200), "sent": send}
        if not send:
            rec["no"] = f"{days:g} 天内转过了（{clock.strf('%m-%d %H:%M', last)}）"
        if mid is not None:
            rec["mid"] = str(mid)
        xs = xs + [rec]
        top = xs[-APPEAL_MAX:]
        hit = next((x for x in reversed(xs) if x.get("sent")), None)
        if hit is not None and hit not in top:         # 最近一次转了的那条别挤掉（不然他连发几条 7 天的限次就没了）
            top = [hit] + xs[-(APPEAL_MAX - 1):]
        d["appeals"][uid] = top
        out.update(send=send, why=rec.get("no", ""), id=rec["id"], text=rec["text"], last=last)
    r = _update(upd)
    return dict(out) if (r is not None and out) else None


def _ap_prune(d, now):
    """过期的申诉扔掉（超过 max(keep_days, appeal_days) 天的；一个人的都过期了 → 那一项删掉）→ 动了没有
    appeal_add（有人申诉时）和 sweep（开机收尾、冷却到点结案时）都顺手调"""
    keep = now - max(_f("keep_days"), _f("appeal_days")) * 86400
    hit = False
    for u in list(d["appeals"]):
        raw = d["appeals"].get(u)
        ys = [x for x in _ap_items(d, u) if float(x.get("t") or 0) >= keep]
        if not ys:
            d["appeals"].pop(u, None)
            hit = True
        elif not isinstance(raw, list) or len(ys) != len(raw):
            d["appeals"][u] = ys
            hit = True
    return hit


def appeal_sent(uid, aid, ok, why=""):
    """转给老板以后（11-03 v1.17.15）：发出去了 → 这条算转了；没发出去 → 这条记成没转（不算进限次：他下次申诉照样转）→ 改上了没有"""
    uid = str(uid or "")
    res = {"ok": False}

    def upd(d):
        x = next((x for x in _ap_items(d, uid) if x.get("id") == aid), None)
        if x is None:
            return False
        if ok:
            x["sent"] = True
            x.pop("no", None)
        else:
            x["sent"] = False
            x["no"] = _clip(why or "没发出去", 40)
        res["ok"] = True
    _update(upd)
    return res["ok"]


def _ap_view(d, now):
    out = {}
    for uid in list(d["appeals"]):
        tg = _ap_target(d, str(uid), now)
        if not tg:
            continue
        t0 = float(tg.get("t") or 0) - 1
        pd = float((d.get("pardons") or {}).get(str(uid)) or 0)
        xs = [x for x in _ap_items(d, uid) if float(x.get("t") or 0) > pd
              and ((tg["case"] and str(x.get("case") or "") == tg["case"]) or float(x.get("t") or 0) >= t0)]
        if not xs:
            continue
        sent = [float(x.get("t") or 0) for x in xs if x.get("sent")]
        out[str(uid)] = {"n": len(xs), "last": {k: xs[-1].get(k) for k in ("t", "via", "what", "text", "sent", "no") if k in xs[-1]},
                         "sent_t": max(sent) if sent else 0}
    return out


def appeals_view(now=None):
    """控制台「她拉黑的人」（11-03 v1.17.15）：拉黑 / 冷却中的人有没有申诉 →
    {QQ: {"n": 这次的几条, "last": 最近那条 {t, via, what, text, sent, no}, "sent_t": 最近一次转给老板的时间}}（没申诉过的不列）
    只算这次拉黑 / 冷却的（这个案子的、或者这次开始以后的；以前冷却过、早就到期那次的不算；控制台撤销之前的不算）"""
    try:
        return _ap_view(_load(), float(now or time.time()))
    except (TypeError, ValueError):
        return {}


# ───────────── 删好友：进行中（幂等）─────────────
def pending_start(uid, day, rec=None, now=None):
    """删好友之前先落盘「进行中」（幂等键 = QQ + 北京日期）→ True 这次可以删 / False 今天已经在删或删过他了 / 写不进去"""
    k = f"{uid}:{day}"
    out = {"ok": False}

    def upd(d):
        if k in d["pending"]:
            return False
        d["pending"][k] = dict(rec or {}, uid=str(uid), day=str(day), t=int(float(now or time.time())), state="进行中")
        if len(d["pending"]) > 100:
            for x in sorted(d["pending"], key=lambda x: float((d["pending"][x] or {}).get("t") or 0))[:len(d["pending"]) - 100]:
                if (d["pending"][x] or {}).get("state") != "进行中":
                    d["pending"].pop(x, None)
        out["ok"] = True
    _update(upd)
    return out["ok"]


def pending_end(uid, day, result, err=""):
    k = f"{uid}:{day}"

    def upd(d):
        e = d["pending"].get(k)
        if not isinstance(e, dict):
            return False
        e.update(state="完了", result=str(result), err=_clip(err, 80), end=int(time.time()))
    _update(upd)


def pending_open():
    """开机：还「进行中」的删好友（调用期间程序重启了）→ [{uid, day, …}]"""
    return [dict(v, key=k) for k, v in (_load().get("pending") or {}).items() if isinstance(v, dict) and v.get("state") == "进行中"]


# ───────────── 记一笔 ─────────────
def note_try(uid, name, why, res, now=None):
    """她想拉黑的每一次都记一笔（控制台看得到她拉黑过 / 想拉黑没拉成）"""
    now = float(now or time.time())

    def upd(d):
        d["tries"].append({"t": int(now), "uid": str(uid), "name": _clip(name, 20), "why": _clip(why, 40),
                           "block": bool(res.get("block")), "delete": bool(res.get("delete")), "n": int(res.get("n") or 0),
                           "no": _clip(res.get("why"), 60)})
        d["tries"] = d["tries"][-100:]
    try:
        _update(upd)
    except Exception:
        pass


def note_delete(uid, name, why, ok, err="", ms=(), now=None, result="", shadow=False, case="", wipe=""):
    """删好友的记录：result 删了 / 没删 / 不确定 / 本来会删（影子模式 shadow=True）/ 不是好友（11-03 v1.17.13 判删除、没删好友）
    wipe（11-03 v1.17.13）：判删除的清数据那一步（本来会清 / 清数据中 / 清了）—— 有它的也算进每天的上限"""
    now = float(now or time.time())

    def upd(d):
        d["deletes"].append({"t": int(now), "day": clock.strf("%Y-%m-%d", now), "uid": str(uid), "name": _clip(name, 20),
                             "why": _clip(why, 40), "ok": bool(ok), "err": _clip(err, 80), "result": str(result or ("删了" if ok else "没删")),
                             "shadow": bool(shadow), "case": str(case or ""),
                             "evidence": "；".join(f"{clock.strf('%m-%d %H:%M', m.get('t'))}「{_clip(m.get('text'), 40)}」"
                                                   for m in list(ms)[-4:])})
        if wipe:
            d["deletes"][-1]["wipe"] = _clip(wipe, 40)
        d["deletes"] = d["deletes"][-100:]
    try:
        _update(upd)
    except Exception:
        pass


def note_wipe(cid, wipe):
    """判删除的清数据那一步（11-03 v1.17.13）：记到这个案子最近那条删好友记录上（每天的上限也数它）"""
    def upd(d):
        x = next((x for x in reversed(d["deletes"]) if isinstance(x, dict) and str(x.get("case") or "") == str(cid)), None)
        if not x:
            return False
        x["wipe"] = _clip(wipe, 40)
    try:
        _update(upd)
    except Exception:
        pass


def entry(uid, name, why, conv="", cites=(), now=None, case=""):
    """拉黑名单（blocklist.json 的 blocked）里的那一项：cites = 法庭引用、核实过的他的话"""
    return {"t": int(float(now or time.time())), "name": _clip(name, 20), "why": _clip(why, 40), "conv": str(conv or ""),
            "cites": [{"t": m.get("t"), "text": _clip(m.get("text"), 60)} for m in list(cites)[-5:]], "by": "她",
            "case": str(case or "")}


# ───────────── 删数据 ─────────────
def forget(uid, names=(), data_dir=None):
    """删他的数据：记账里他的整条删；拉黑名单 / 冷却里他那一项留着（不然删数据 = 解封）但名字、原因、原话抹掉；
    案子 / 删好友进行中里他的：只留号 + 时间 + 结果（11-02 v1.17.10 阶段1）；赦免时间留着（之前的不再算）；
    她想拉黑的记录和删好友的记录里他的名字 / 原话抹掉；别人那几条里出现的他的名字换「某人」→ 动了几条
    文件读坏了 → 不写（0）"""
    uid = str(uid or "")
    p = Path(data_dir) / "blocklist.json" if data_dir else path()
    d = safeio.read_json(p, None)
    if not isinstance(d, dict):
        return 0
    _fill(d)
    n = len(d["marks"].pop(uid, []) or [])
    e = d["blocked"].get(uid)
    if isinstance(e, dict):
        d["blocked"][uid] = {"t": e.get("t"), "by": e.get("by") or "她", "why": "（删数据时抹掉了）", "name": "", "conv": "",
                             "cites": [{"t": m.get("t")} for m in e.get("cites") or [] if isinstance(m, dict)],
                             "marks": [{"t": m.get("t"), "kind": m.get("kind")} for m in e.get("marks") or [] if isinstance(m, dict)],
                             **({"deleted": e["deleted"]} if e.get("deleted") else {}), **({"case": e["case"]} if e.get("case") else {})}
        n += 1
    c = d["cool"].get(uid)
    if isinstance(c, dict):
        d["cool"][uid] = {"t": c.get("t"), "until": c.get("until"), "by": c.get("by") or "她", "why": "（删数据时抹掉了）", "name": "",
                          "conv": "", **({"case": c["case"]} if c.get("case") else {})}
        n += 1
    cd = d["cooled"].get(uid)
    if isinstance(cd, dict):               # 「冷却过」只留号和时间（11-03 v1.17.13：还算冷却过 —— 删数据不等于洗白）
        d["cooled"][uid] = {k: cd.get(k) for k in ("t", "last") if k in cd}
        n += 1
    if d["appeals"].pop(uid, None) is not None:    # 他的申诉（11-03 v1.17.15）：整个删（他写的话、什么时候、走哪条路都不留）
        n += 1
    nm = [str(x) for x in names or [] if x and len(str(x)) >= 2]

    def sc(s):
        s = str(s or "")
        for x in nm:
            s = s.replace(x, "某人")
        return s
    for i, cs in enumerate(d["cases"]):
        if not isinstance(cs, dict):
            continue
        if str(cs.get("uid")) == uid:
            d["cases"][i] = {k: cs.get(k) for k in ("id", "uid", "t", "state", "verdict", "result", "cool_until", "closed", "n_ok", "days",
                                                    "stage", "end") if k in cs}       # 冷却中收的证据、核实的原话、再审的理由都不留（11-03 v1.17.13）
            d["cases"][i]["wiped"] = int(time.time())
            n += 1
        else:
            for f in ("why", "reason", "result_why", "name"):
                if f in cs:
                    cs[f] = sc(cs[f])
            for m in (cs.get("quotes") or []) + (cs.get("ev") or []):     # ev：别人案子里冷却期间收的话（11-03 v1.17.13）
                if isinstance(m, dict):
                    m["text"] = sc(m.get("text"))
            for r in cs.get("retrials") or []:
                if isinstance(r, dict):
                    for f in ("reason", "why"):
                        if f in r:
                            r[f] = sc(r[f])
    for k, v in list(d["pending"].items()):
        if isinstance(v, dict) and str(v.get("uid")) == uid:
            d["pending"][k] = {f: v.get(f) for f in ("uid", "day", "t", "state", "result", "end") if f in v}
            n += 1
    for k in ("tries", "deletes"):
        for x in d.get(k) or []:
            if not isinstance(x, dict):
                continue
            if str(x.get("uid")) == uid:
                x.update(name="", why="（删数据时抹掉了）", evidence="", no=sc(x.get("no")))
                n += 1
            else:
                for f in ("why", "evidence", "no"):
                    if f in x:
                        x[f] = sc(x[f])
    for ms in (d.get("marks") or {}).values():
        for m in ms:
            m["text"], m["name"] = sc(m.get("text")), sc(m.get("name"))
    for k2, e2 in (d.get("blocked") or {}).items():
        if isinstance(e2, dict) and k2 != uid:
            e2["why"] = sc(e2.get("why"))
            for m in (e2.get("marks") or []) + (e2.get("cites") or []):
                if isinstance(m, dict) and "text" in m:
                    m["text"] = sc(m.get("text"))
    for k2, c2 in (d.get("cool") or {}).items():
        if isinstance(c2, dict) and k2 != uid:
            c2["why"] = sc(c2.get("why"))
    for xs in d["appeals"].values():       # 别人的申诉里出现他的名字 → 「某人」（11-03 v1.17.15）
        for x in xs if isinstance(xs, list) else []:
            if isinstance(x, dict) and x.get("text"):
                x["text"] = sc(x.get("text"))
    safeio.write_json(p, d)
    _B["m"] = None
    return n


# ───────────── 控制台 ─────────────
def view(now=None):
    """控制台 / 排查：冷却中、拉黑中、最近的案子、线索（7 天、赦免之后）、她想拉黑的记录、删好友记录、进行中"""
    d = _load()
    now = float(now or time.time())
    lim = now - _f("window_days") * 86400
    pd = d.get("pardons") or {}
    ms = {u: [m for m in v if isinstance(m, dict) and float(m.get("t") or 0) >= max(lim, float(pd.get(u) or 0) + 0.001)]
          for u, v in (d.get("marks") or {}).items()}
    return {"enabled": on(), "config": {k: C.get(k) for k in DEFAULTS}, "delete_mode": delete_mode(),
            "blocked": d.get("blocked") or {},
            "cooling": {u: e for u, e in (d.get("cool") or {}).items() if isinstance(e, dict) and float(e.get("until") or 0) > now},
            "cases": list(reversed((d.get("cases") or [])[-30:])), "marks": {u: v for u, v in ms.items() if v},
            "tries": (d.get("tries") or [])[-30:], "deletes": (d.get("deletes") or [])[-30:], "pardons": pd,
            "cooled": d.get("cooled") or {}, "wipe_mode": wipe_mode(), "appeals": appeals_view(now),
            "pending": {k: v for k, v in (d.get("pending") or {}).items() if isinstance(v, dict) and v.get("state") == "进行中"}}
