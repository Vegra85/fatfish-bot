#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
版本更新播报（10-31 v1.15.5，小明：「如果更新版本了可以在几个活跃的群和熟人之间发版本更新 v几点几 然后用户体验有什么不同
当然要先看自己在那些群里是被禁言的」）

  什么时候：开机后（等 start_delay 秒）看 version.py 的版本 —— 比上次播报过的「大」才播：
    levels = "minor"（默认）：1.x 或 2.0 这种变了才播（平时部署只涨补丁号 1.x.y 的不播）；"all"：每次版本号变了都播；"off"：不播
    第一次装（没有记录）：只记下当前版本当起点 不播（免得新装的人一开机就群发）
  播给谁（机器人那边挑 这里只给算法）：
    群：最近 active_days 天存档里真人说了 ≥ min_lines 句的群，按热闹程度取前 groups_max 个；发之前现查 QQ：被禁言 / 全员禁言 / 静音 / 只在被@时说话 / 少说 都跳过
    熟人：好感 ≥ friend_favor、最近 friend_days 天私聊过的，按好感取前 friends_max 个
  说什么（E1：上游给状态、她自己说）：从更新日志里取「上次播过的版本之后 ~ 这一版」那几节 → 模型先挑出「群友用起来感觉得到的变化」（只要事实 不要说法）
    → 每个群 / 每个人各让她自己说一遍（不想说可以 [[不说]]）
  不刷屏：每个群 / 人同一个版本只发一次；一个发完隔 gap_sec 再发下一个；她那边 quiet 时段（默认 20~23 点）不发、等过了再接着发
  存 data/announce.json：{"base": 起点（只涨补丁号时跟着挪）, "said": 上次真播完的版本, "since": 正在播的这次从哪版算起,
                         "ver": 正在播的版本, "done": {目标: 时间}, "changes": [...]}
  11-02 v1.17.1：上一版（同一个 1.x）还没播完、只涨了补丁号又重启 → 接着播剩下的（播过的不再播；以前 done 清空 同一个群说两遍）
  11-02 v1.17.0：「从哪版算起」改成上次真播完的那版（said）—— 以前只涨补丁号时起点挪上来、下次播 1.x.0 只取得到 1.x.0 那一节，
     中间那几个补丁号的变化（1.16.3–1.16.5 的每日生活、唱整首）就漏了；老记录没有 said：上次播完的是 ver（有 finished）就从它算
  config → announce（enabled / levels / groups_max / friends_max / active_days / min_lines / friend_favor / friend_days / gap_sec / quiet / start_delay）
"""
import json
import re
import time
from pathlib import Path

import clock

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULTS = {"enabled": True, "levels": "minor", "groups_max": 6, "friends_max": 5, "active_days": 3, "min_lines": 30,
            "friend_favor": 60, "friend_days": 7, "gap_sec": [180, 420], "quiet": [20, 23], "start_delay": 300}
C = dict(DEFAULTS)


def configure(cfg=None, data_dir=None):
    global DATA
    for k, v in (cfg or {}).items():
        if not str(k).startswith("_") and k in DEFAULTS:
            C[k] = v
    if data_dir:
        DATA = Path(data_dir)


def on():
    return bool(C.get("enabled", True)) and str(C.get("levels", "minor")) != "off"


def _file():
    return DATA / "announce.json"


def load():
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d):
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_file())
    except OSError:
        pass


def vtuple(v):
    nums = [int(x) for x in re.findall(r"\d+", str(v or ""))[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def _said(d):
    """上次真播完的版本（since 从它算）：有 said 用 said；老记录（11-02 以前）上次播完的是 ver（有 finished、不比 base 大）；都没有 → base"""
    if d.get("said"):
        return str(d["said"])
    v, b = d.get("ver"), d.get("base")
    if d.get("finished") and v and b and vtuple(v) <= vtuple(b):
        return str(v)
    return str(b or "")


def pending(ver):
    """开机时调：这一版要不要播 → 要播的版本号 / None（第一次装：记下起点 不播）"""
    if not on():
        return None
    d = load()
    if not d.get("base"):
        d["base"] = d["said"] = str(ver)
        save(d)
        return None
    if d.get("ver") == str(ver) and d.get("ver") != d.get("base"):
        return str(ver)                    # 上次没播完（重启了）→ 接着播
    b, v = vtuple(d["base"]), vtuple(ver)
    if v <= b:
        return None
    if str(C.get("levels")) == "minor" and v[:2] == b[:2]:
        d["base"] = str(ver)               # 只涨了补丁号：不播 起点挪上来
        save(d)
        return None
    old = d.get("ver")
    if old and old != str(ver) and not d.get("finished") and str(C.get("levels")) == "minor" and vtuple(old)[:2] == v[:2]:
        d["ver"] = str(ver)                # 11-02 v1.17.1：上一版（同一个 1.x）还没播完就只涨了补丁号重启：接着播剩下的地方 —— 以前 done 清空、
        save(d)                            # 已经说过的群 / 人又从头说一遍（11-01 07:11 v1.16.1 → v1.16.2 那次同一个群说了两遍）；挑好的变化照用
        return str(ver)
    if d.get("ver") != str(ver):
        d["since"] = d.get("since") or _said(d)   # 先记下从哪版算起（下面改了 ver 就认不出上次播的是哪版了）；上一版没播完就被新版顶掉 → 还从那次的起点算
        d["ver"], d["done"], d["changes"] = str(ver), {}, None
        d.pop("finished", None)
        save(d)
    return str(ver)


def base():
    """这次播报从哪版算起（上次真播完的那版）"""
    d = load()
    return str(d.get("since") or _said(d) or "")


def changelog_text(ver, since, paths):
    """更新日志里「since 之后 ~ ver」那几节（## v1.2.3 开头的一节一节）"""
    for p in paths:
        try:
            t = Path(p).read_text(encoding="utf-8")
        except OSError:
            continue
        secs = re.split(r"(?m)^## +v?(?=\d)", t)
        out = []
        for s in secs[1:]:
            m = re.match(r"(\d+(?:\.\d+){0,2})", s)
            if not m:
                continue
            v = vtuple(m.group(1))
            if vtuple(since) < v <= vtuple(ver):
                out.append("## v" + s.strip())
        if out:
            return "\n\n".join(out)[:6000]       # 几个补丁号连着（11-02：1.16.3 ~ 1.17.0 四节）4000 字会截掉后面的
    return ""


PICK = """下面是一个 QQ 聊天机器人的更新日志（可能好几个版本）。请挑出「群友 / 私聊的人用起来能感觉到的变化」，最多 4 条：
- 每条一句平实的话，说现在她能做什么 / 哪里不一样了（站在用户那边说）
- 不要文件名、配置名、技术细节、测试、修 bug 的内部原因；用户感觉不到的（日志、控制台、内部重构）不要
- 只输出 JSON 数组，例如 ["……", "……"]；一条都没有就输出 []"""


async def pick_changes(llm, text):
    if not text.strip():
        return []
    raw = await llm(PICK, [{"role": "user", "content": text}])
    m = re.search(r"\[.*\]", str(raw or ""), re.S)
    try:
        arr = json.loads(m.group(0)) if m else []
    except ValueError:
        arr = []
    return [str(x).strip()[:80] for x in arr if str(x).strip()][:4]


def set_changes(ver, changes):
    d = load()
    if d.get("ver") == str(ver):
        d["changes"] = list(changes)
        save(d)


def changes(ver):
    d = load()
    return d.get("changes") if d.get("ver") == str(ver) else None


def done(ver, target):
    d = load()
    return d.get("ver") == str(ver) and str(target) in (d.get("done") or {})


def mark(ver, target, ok=True):
    d = load()
    if d.get("ver") != str(ver):
        return
    d.setdefault("done", {})[str(target)] = {"t": round(time.time()), "ok": bool(ok)}
    save(d)


def finish(ver):
    d = load()
    if d.get("ver") == str(ver):
        d["base"] = d["said"] = str(ver)
        d["finished"] = round(time.time())
        d.pop("since", None)               # 播完了：下次从 said（这一版）算
        save(d)


def quiet(hour):
    q = C.get("quiet") or []
    return len(q) == 2 and int(q[0]) <= int(hour) < int(q[1])


def active_groups(data_dir=None, now=None, is_bot=None):
    """最近 active_days 天：每个群真人说了几句（存档）→ [(gid, 句数)] 多的在前，只要 ≥ min_lines 的"""
    data = Path(data_dir or DATA)
    now = now or time.time()
    cnt = {}
    for i in range(int(C["active_days"])):
        day = clock.strf("%Y-%m-%d", now - i * 86400)   # 存档按北京日期分（11-02 v1.17.1：以前按这台电脑的日期 北京 0~8 点那天的读不到）
        for p in (data / "archive" / day).glob("group_*.jsonl"):
            gid = p.stem[6:]
            try:
                for ln in p.read_text(encoding="utf-8").splitlines():
                    try:
                        e = json.loads(ln)
                    except ValueError:
                        continue
                    if e.get("role") == "user" and not e.get("bot") and not (is_bot and is_bot(str(e.get("uid") or ""))):
                        cnt[gid] = cnt.get(gid, 0) + 1
            except OSError:
                pass
    rows = sorted(((g, n) for g, n in cnt.items() if n >= int(C["min_lines"])), key=lambda x: -x[1])
    return rows[:int(C["groups_max"])]


def friends(favor, data_dir=None, now=None, owner=""):
    """熟人：好感够、最近私聊过 → [(uid, 名字, 好感)]（老板也算 —— 他想看看她怎么说）"""
    data = Path(data_dir or DATA)
    now = now or time.time()
    recent = set()
    for i in range(int(C["friend_days"])):
        day = clock.strf("%Y-%m-%d", now - i * 86400)
        for p in (data / "archive" / day).glob("private_*.jsonl"):
            recent.add(p.stem[8:])
    rows = []
    for uid, f in (favor or {}).items():
        try:
            fv = float((f or {}).get("favor") or 0)
        except (TypeError, ValueError):
            continue
        if str(uid) in recent and (fv >= float(C["friend_favor"]) or str(uid) == str(owner)):
            rows.append((str(uid), (f or {}).get("call") or (f or {}).get("name") or "", fv))
    rows.sort(key=lambda x: -x[2])
    return rows[:int(C["friends_max"])]


def block(ver, since, changes, repo="", scene="group"):
    """给她看的状态（不是台词）"""
    lines = "\n".join(f"- {c}" for c in changes) if changes else "- （日志里没挑出用户感觉得到的变化）"
    where = "这个群" if scene == "group" else "他"
    return ("\n# 你刚更新了（状态 不是台词）\n"
            f"你刚从 v{since} 更新到 v{ver}。这一版用起来能感觉到的变化：\n{lines}\n"
            + (f"完整的更新日志在 {repo.rstrip('/')}/blob/main/CHANGELOG.md\n" if repo else "")
            + f"你想跟{where}说一声：版本号和一两条变化就够，用你自己的话说；"
              "这里正聊着别的、不合适开口，就只回 [[不说]]。\n")
