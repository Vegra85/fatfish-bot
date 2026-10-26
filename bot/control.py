#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
控制台的开关在机器人这边生效的地方
  data/control.json（控制台写、机器人读，改了马上生效 不用重启）：
    groups : {群号: {"mode": "normal|quiet|at_only|mute", "mute_until": 时间戳,
                     "admin": {群管设的档位 见 groupadmin.py}, "qq_ban": {QQ 里被禁言到几点}}}
             normal=正常  quiet=少说（不主动插话/冒泡/收尾 有人跟她说照常回）
             at_only=只在有人 @/叫名字/引用她时说话  mute=完全不说话（消息照样记）
             mute_until：临时静音到这个时间（过了自动恢复 mode）
             实际生效的 = 控制台的 mode、群管设的 admin、QQ 禁言 三个里最严的那个（group_mode）
    blocked: {QQ号: 备注}  完全不理（消息也不记）
    bots   : {QQ号: 备注}  别的机器人：只有 @/叫名字/引用她 才回，10 分钟最多回 3 次，不算接话
    self_off: {QQ号: {"until": 时间戳, ...}}  群友说「别接我的话」：到点前不主动接他的话（他 @她 照样回）
    full   : {"until": 时间戳 或 -1(一直开), "since": 开的时间, "by": 谁开的}  火力全开（见下面 full_on）
  另外开一个只给本机控制台用的小接口（127.0.0.1，要带 token）：查状态、代发、改记忆和好感、重启
"""
import asyncio
import hmac
import json
import os
import time
from pathlib import Path

import memory
import clock
import safeio

ROOT = Path(__file__).resolve().parent
FILE = ROOT / "data" / "control.json"
BOT_BUDGET = (3, 600)          # 别的机器人：600 秒里最多回 3 次

_c = {"m": None, "d": {}}
_bot_log = {}                  # (群, QQ) -> [回它的时间]


def rules():
    try:
        m = FILE.stat().st_mtime
    except OSError:
        _c.update(m=None, d={})
        return {}
    if m != _c["m"]:
        try:
            _c.update(m=m, d=json.loads(FILE.read_text(encoding="utf-8")) or {})
        except Exception:
            pass                # 正好在写 下次再读
    return _c["d"]


LEVELS = ("normal", "quiet", "at_only", "mute")        # 从松到严
_LV = {k: i for i, k in enumerate(LEVELS)}


def stricter(a, b):
    return a if _LV.get(a, 0) >= _LV.get(b, 0) else b


def _alive(until, now):
    """until：-1 = 一直（要有人放开）；> now = 还没到点"""
    try:
        u = float(until or 0)
    except (TypeError, ValueError):
        return False
    return u < 0 or u > now


def admin_slot(gid, now=None):
    """群管（群主/管理员/老板在群里说的）设的档位 还没过期就返回那条记录"""
    a = ((rules().get("groups") or {}).get(str(gid)) or {}).get("admin") or {}
    return a if a.get("level") in LEVELS and _alive(a.get("until"), now or time.time()) else None


def qq_ban(gid, now=None):
    """QQ 里被禁言（或者全员禁言）还没解 → 那条记录"""
    b = ((rules().get("groups") or {}).get(str(gid)) or {}).get("qq_ban") or {}
    return b if b and _alive(b.get("until"), now or time.time()) else None


def panel_mode(gid, now=None):
    """控制台里设的（老板的）"""
    g = (rules().get("groups") or {}).get(str(gid)) or {}
    if float(g.get("mute_until") or 0) > (now or time.time()):
        return "mute"
    return g.get("mode") if g.get("mode") in LEVELS else "normal"


def group_mode(gid, now=None):
    """这个群现在实际按哪一档：控制台的、群管设的、QQ 禁言 取最严的"""
    now = now or time.time()
    mode = panel_mode(gid, now)
    a = admin_slot(gid, now)
    if a:
        mode = stricter(mode, a["level"])
    if qq_ban(gid, now):
        mode = "mute"
    return mode


def group_state(gid, now=None):
    """控制台和提示词用：{mode 实际, panel 控制台的, admin 群管设的(或 None), ban QQ 禁言(或 None), ban_last 上次禁言}"""
    now = now or time.time()
    g = (rules().get("groups") or {}).get(str(gid)) or {}
    return {"mode": group_mode(gid, now), "panel": panel_mode(gid, now), "admin": admin_slot(gid, now),
            "ban": qq_ban(gid, now), "ban_last": g.get("qq_ban_last") or None}


def self_off(uid, now=None):
    """群友自己说过「别接我的话 / 别理我」还没到点"""
    e = (rules().get("self_off") or {}).get(str(uid)) or {}
    return bool(e) and _alive(e.get("until"), now or time.time())


def self_off_list(now=None):
    now = now or time.time()
    return {u: e for u, e in (rules().get("self_off") or {}).items() if isinstance(e, dict) and _alive(e.get("until"), now)}


def _prune(d, now):
    """顺手清掉过期的群管档位 / 别接话记录（不清也不影响 只是文件干净点）"""
    for g in (d.get("groups") or {}).values():
        if isinstance(g, dict) and isinstance(g.get("admin"), dict) and not _alive(g["admin"].get("until"), now):
            g.pop("admin", None)
    so = d.get("self_off")
    if isinstance(so, dict):
        for u in [u for u, e in so.items() if not (isinstance(e, dict) and _alive(e.get("until"), now))]:
            so.pop(u, None)


def set_admin(gid, entry):
    """群管设档位（entry=None 撤销）"""
    def f(d):
        _prune(d, time.time())
        g = d.setdefault("groups", {}).setdefault(str(gid), {})
        if entry:
            g["admin"] = entry
        else:
            g.pop("admin", None)
    _edit(f)


def boss_reset(gid):
    """老板在群里说「放出来」：群管设的、控制台设的都回到正常。返回原来有没有限制"""
    hit = {"v": False}

    def f(d):
        g = d.setdefault("groups", {}).setdefault(str(gid), {})
        now = time.time()
        hit["v"] = bool((g.get("mode") or "normal") != "normal" or float(g.get("mute_until") or 0) > now
                        or (isinstance(g.get("admin"), dict) and _alive(g["admin"].get("until"), now)))
        g.pop("admin", None)
        g["mode"] = "normal"
        g.pop("mute_until", None)
    _edit(f)
    return hit["v"]


def set_ban(gid, until, by="", whole=False):
    """QQ 禁言：until > 0 到几点 / -1 一直（全员禁言 等解除）/ 0 解禁"""
    def f(d):
        g = d.setdefault("groups", {}).setdefault(str(gid), {})
        if until:
            g["qq_ban"] = {"until": until, "by": str(by or ""), "t": time.time(), "whole": bool(whole)}
        else:
            old = g.pop("qq_ban", None)
            if old:
                g["qq_ban_last"] = {"t": old.get("t"), "lifted": time.time(), "whole": bool(old.get("whole"))}
    _edit(f)


def set_self_off(uid, entry):
    """群友「别接我的话」（entry=None 撤销）"""
    def f(d):
        _prune(d, time.time())
        so = d.setdefault("self_off", {})
        if entry:
            so[str(uid)] = entry
        else:
            so.pop(str(uid), None)
    _edit(f)


def forget_person(uid):
    """删数据（wipe）：他的「别接我的话」记录删掉；他当群管时设的档位留着（那是群的设置）但名字和原话抹掉"""
    uid = str(uid)

    def f(d):
        (d.get("self_off") or {}).pop(uid, None)
        for g in (d.get("groups") or {}).values():
            if not isinstance(g, dict):
                continue
            a = g.get("admin")
            if isinstance(a, dict) and str(a.get("by")) == uid:
                a.update(by="", name="某人", said="")
            b = g.get("qq_ban")
            if isinstance(b, dict) and str(b.get("by")) == uid:
                b["by"] = ""
    _edit(f)


def blocked(uid):
    return str(uid) in (rules().get("blocked") or {})


def is_bot(uid):
    return str(uid) in (rules().get("bots") or {})


def is_human(uid):
    """控制台里取消过 AI 标记的人：以后不再自动标"""
    return str(uid) in (rules().get("humans") or {})


def _edit(fn):
    """读 control.json → fn(d) 改 → 原子写回（机器人这边改：自动认出的 AI、火力全开…）"""
    d = safeio.read_json(FILE, {}, print, busy_raise=True) or {}   # 读坏了先另存一份 .bad；被占着就别改（免得冲掉别的设置）
    if not isinstance(d, dict):
        d = {}
    fn(d)
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, FILE)
    _c["m"] = None
    return d


def add_bot(uid, note=""):
    """标成「别的机器人」（跟控制台里手动加的一样）"""
    _edit(lambda d: d.setdefault("bots", {}).__setitem__(str(uid), str(note)[:80]))


def bot_ok(gid, uid, now=None):
    """别的机器人 @ 她：10 分钟内最多回 3 次（防两个机器人互相 @ 停不下来）"""
    now = now or time.time()
    k = (str(gid), str(uid))
    t = [x for x in _bot_log.get(k, []) if now - x < BOT_BUDGET[1]]
    if len(t) >= BOT_BUDGET[0]:
        _bot_log[k] = t
        return False
    _bot_log[k] = t + [now]
    return True


# ───────────── 火力全开（10-26 小明） ─────────────
# 暂时不省 token：被 @/叫/引用、对聊、接话 都不过省 token 那一掷；
# 但也不是每条都回 —— 没人找她的话 还是按「插话」的规矩挑着接，只是更爱插嘴、更容易认出别人插进来是在跟她说
# 冒泡/主动私聊找人 不受影响（还按省 token 算）。到点自动关。
FULL_DEFAULTS = {
    "follow_window_sec": 300,     # 叫过她之后 多久内接着说的算对她说（平时 behavior.follow_window_sec 120）
    "followup_window_sec": 300,   # 她说完多久内的话考虑是不是在接她（平时 followup.window_sec 150）
    "yes_score": 3.0,             # 接话打分：到这个直接接（平时 4）
    "no_score": 0.5,              # 不到这个直接不接 中间的让模型看一眼（平时 1.5）
    "max_turns": 10,              # 不 @ 她最多连着接几轮（平时 5）
    "interject_mult": 2.5,        # 平时随便插一嘴的概率 × 这个
    "cooldown_sec": 60,           # 两次插话至少隔几秒（平时 150）
    "max_per_hour": 30,           # 每个群每小时最多说几次（平时 12）
    "rhythm_reply_prob": 0.6,     # 在线时没点名的话接的概率（平时 0.3）
    "rhythm_min_gap_sec": 20,     # 在线时主动接话两句至少隔几秒（平时 45）
    "rhythm_max_replies": 15,     # 每次在线最多主动接几句（平时 6）
    "rhythm_minutes": [15, 30],   # 每次在线多久（平时 8~15 分钟）
    "rhythm_cooldown_min": 3,     # 收尾后多久不主动插话（平时 10）
}
_full_cfg = dict(FULL_DEFAULTS)


def configure_full(cfg):
    _full_cfg.update({k: v for k, v in (cfg or {}).items() if k in FULL_DEFAULTS})


def full_cfg():
    return _full_cfg


def full_state(now=None):
    """{"on": 开着没, "until": 到几点(-1=一直), "since": 从几点, "left": 还剩几秒(-1=一直)}"""
    now = now or time.time()
    f = rules().get("full") or {}
    try:
        until = float(f.get("until") or 0)
    except (TypeError, ValueError):
        until = 0
    on = until < 0 or until > now
    return {"on": on, "until": until if on else 0, "since": float(f.get("since") or 0) if on else 0,
            "left": (-1 if until < 0 else int(until - now)) if on else 0, "by": f.get("by") or ""}


def full_on(now=None):
    return full_state(now)["on"]


def set_full(hours, by=""):
    """开/关火力全开：hours > 0 开这么多小时；< 0 一直开；0 关。返回新的状态"""
    d = safeio.read_json(FILE, {}, print, busy_raise=True) or {}
    if not isinstance(d, dict):
        d = {}
    h = float(hours or 0)
    if h == 0:
        d.pop("full", None)
    else:
        d["full"] = {"until": -1 if h < 0 else time.time() + h * 3600, "since": time.time(), "by": str(by)[:20]}
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, FILE)
    _c["m"] = None
    return full_state()


def full_left_text(st=None):
    st = st or full_state()
    if not st["on"]:
        return "没开"
    if st["left"] < 0:
        return "一直开着（要手动关）"
    m = max(1, st["left"] // 60)
    return f"还剩 {m // 60} 小时 {m % 60} 分钟" if m >= 60 else f"还剩 {m} 分钟"


def allow(scene, target, kind="auto"):
    """kind：at=有人找她  auto=她自己接话/插话  bubble=冒泡/冷场试探/收尾  proactive=主动私聊"""
    if scene == "private":
        return not blocked(target)
    mode = group_mode(target)
    if mode == "mute":
        return False
    if mode in ("at_only", "quiet"):          # 少说：冒泡/收尾/主动做动画 都不做
        return kind == "at"
    return True


# ───────────── 记忆 / 好感：机器人和控制台共用 ─────────────
def forget_fact(mem, fid):
    """忘掉一条记忆（留墓碑 以后提炼也不会再记回来）"""
    hit = False
    for f in mem.get("facts") or []:
        if isinstance(f, dict) and f.get("id") == fid:
            f["status"] = "forgotten"
            memory.tomb_add(mem, f)
            hit = True
    if hit:
        mem["facts"] = [f for f in mem["facts"] if not (isinstance(f, dict) and f.get("status") == "forgotten")]
        tom = mem.setdefault("tombstones", []) or []
        if fid not in tom:
            tom.append(fid)
        mem["tombstones"] = tom
    return hit


def add_fact(mem, text, imp=4):
    """老板手动让她记住一件事"""
    text = memory.redact(str(text or "")).strip()[:60]
    if not text:
        return None
    fid = memory.fingerprint(text)
    mem["facts"] = memory.migrate(mem.get("facts"))
    if any(f.get("id") == fid and f.get("status") == "active" for f in mem["facts"]):
        return None
    mem["tombstones"] = [t for t in (mem.get("tombstones") or []) if t != fid]
    f = {"id": fid, "text": text, "kind": "fact", "key": "", "imp": max(1, min(5, int(imp))), "emo": 1,
         "date": clock.strf("%Y-%m-%d"), "ts": time.time(), "status": "active", "hits": 1, "manual": True}
    mem["facts"].append(f)
    return f


def set_favor(d, uid, value, name=""):
    u = d.get(str(uid))
    if not u:
        u = {"favor": 5, "name": name, "first": time.time(), "events": [], "pending_event": None,
             "day": "", "gained": 0, "chats": 0}
        d[str(uid)] = u
    u["favor"] = max(0, min(100, int(value)))
    return u


# ───────────── 本机小接口 ─────────────
def _send(writer, code, obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    writer.write(f"HTTP/1.1 {code} OK\r\nContent-Type: application/json; charset=utf-8\r\n"
                 f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("latin-1") + body)


async def serve(handler, port, token, log=print):
    """handler(action, params) -> dict。只听 127.0.0.1，没带对 token 一律拒绝"""
    async def on_conn(reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin-1").split("\r\n")
            method, path = (lines[0].split(" ") + ["", ""])[:2]
            hdr = {}
            for h in lines[1:]:
                if ":" in h:
                    k, v = h.split(":", 1)
                    hdr[k.strip().lower()] = v.strip()
            n = int(hdr.get("content-length") or 0)
            body = await asyncio.wait_for(reader.readexactly(n), 10) if 0 < n <= 4_000_000 else b""
            if method != "POST" or path != "/api" or not token or \
                    not hmac.compare_digest(hdr.get("x-token", ""), token):
                _send(writer, 403, {"ok": False, "error": "forbidden"})
                return
            req = json.loads(body.decode("utf-8") or "{}")
            res = await handler(str(req.get("action") or ""), req.get("params") or {})
            _send(writer, 200, res if isinstance(res, dict) else {"ok": True, "data": res})
        except Exception as e:
            try:
                _send(writer, 500, {"ok": False, "error": repr(e)[:300]})
            except Exception:
                pass
        finally:
            try:
                await writer.drain()
                writer.close()
            except Exception:
                pass

    try:
        srv = await asyncio.start_server(on_conn, "127.0.0.1", int(port))
    except OSError as e:
        log(f"[控制台] 接口端口 {port} 打不开（{e}）控制台只能看 不能操作")
        return None
    log(f"[控制台] 本机接口已开 127.0.0.1:{port}（双击 控制台.bat 打开网页控制台）")
    return srv


_lock_fd = None


def single_instance(path, log=print, wait=12):
    """同一时间只让一个机器人跑（两个一起跑 每句话会回两遍）
    拿不到锁 = 已经有一个在跑 → False。锁本身出了别的毛病 → 不拦 照常启动（True）
    重启的时候旧的那个要过一两秒才退出 所以最多等 wait 秒"""
    global _lock_fd
    import errno
    busy = {errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLK", -1), getattr(errno, "EDEADLOCK", -1),
            getattr(errno, "EWOULDBLOCK", -1)}
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    except OSError as e:
        log(f"[启动] 单实例锁打不开（{e}）不管了 照常启动")
        return True
    deadline = time.time() + wait
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_fd = fd                     # 一直开着 进程退出时系统自动放锁
            return True
        except OSError as e:
            if e.errno not in busy:
                log(f"[启动] 单实例锁出错（{e}）不管了 照常启动")
                return True
            if time.time() >= deadline:
                os.close(fd)
                return False
            time.sleep(1)
        except Exception as e:
            log(f"[启动] 单实例锁出错（{e!r}）不管了 照常启动")
            return True


def preflight(root, timeout=90):
    """重启之前先试一下新代码能不能加载（语法错误、少模块、config 坏了 → 不重启 免得重启完起不来）"""
    import subprocess
    import sys
    code = "import sys; sys.path.insert(0, %r); import bot" % str(root)
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=str(root), capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=0x08000000 if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        return False, f"试加载超过 {timeout} 秒"
    except Exception as e:
        return False, repr(e)[:200]
    if r.returncode != 0:
        tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
        return False, " | ".join(x.strip() for x in tail)[:300] or f"退出码 {r.returncode}"
    return True, ""


def respawn(script, log_file, log=print):
    """重启自己：起一个新的 bot.py（输出接着写 bot.log）然后当前这个退出"""
    import subprocess
    import sys
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
    out = open(log_file, "ab")
    subprocess.Popen([sys.executable, str(script)], cwd=str(Path(script).parent), stdout=out,
                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)
    log("[控制台] 新的机器人已经起来了 这个退出")
