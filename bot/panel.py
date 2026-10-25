#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大肥鱼控制台（本机网页）
  双击 C:\\QQBot\\控制台.bat → 自动打开 http://127.0.0.1:8765
  只有这台电脑能打开；关掉黑窗口就关掉控制台（机器人不受影响）
  机器人开着：通过它的本机接口操作（代发、改记忆好感、重启、试音…）
  机器人没开：照样能改设置、看日志、看记忆、看统计
  自动更新（10-26）：控制台.bat 起的是一个「管家」进程 真正的网页服务是它的子进程；
    panel.py（或它用到的 control.py/budget.py…）改了 → 子进程自己退出 管家马上用新代码再起一个（网页不用关）
    双击 控制台.bat 时 已经开着一个旧版本/卡住的控制台 → 把旧的关掉换成新的（只关命令行里有 panel.py 的进程）
"""
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import budget  # noqa: E402  （省 token 的曲线和机器人共用）
import stats  # noqa: E402  （DeepSeek 峰谷时段和机器人共用）
import control  # noqa: E402  （记忆/好感的改法和机器人共用）
import wipe  # noqa: E402  （删一个人的数据：机器人开着让它删 没开就直接删文件）
import sing  # noqa: E402  （她会唱的歌：songs 文件夹 + songs.json）

ROOT = Path(__file__).resolve().parent
QQBOT = ROOT.parent
CONFIG = ROOT / "config.json"
DATA = ROOT / "data"
MEM_DIR = DATA / "memory"
LOG = ROOT / "bot.log"
CONTROL = DATA / "control.json"
CACHE = DATA / "panel_cache.json"
STATS = DATA / "stats.json"
FAVOR = DATA / "favor.json"
STICKERS = ROOT / "stickers"
BACKUP = ROOT / "backup_20261024" / "panel_backups"
HTML = ROOT / "panel.html"
HEARTBEAT = DATA / "heartbeat.json"
NAPCAT_LOG = QQBOT / "napcat.log"
QRCODE = QQBOT / "NapCat" / "cache" / "qrcode.png"
SECRET = re.compile(r"key|token|sessdata|secret|password", re.I)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
DETACHED = (0x00000008 | 0x00000200 | 0x08000000) if os.name == "nt" else 0
_lock = threading.Lock()
RELOAD_RC, ALREADY_RC, GIVEUP_RC = 3, 4, 5     # 子进程退出码：3 代码更新了重新加载 / 4 已经开着同一个版本 / 5 换不掉旧的 放弃
START_TS = time.time()
_busy = {"n": 0, "reload": False, "supervised": False}
_busy_lock = threading.Lock()

DEFAULT_PRICES = {
    "_说明": "人民币（10-26 改）。DeepSeek（deepseek-flash）每百万 token：空闲时段 输入没命中缓存 / 命中缓存 / 输出；"
            "高峰时段（北京时间 周一到周五 9-12、14-18）× peak_mult；DeepSeek 搜索、读网页 按每千次成功请求；"
            "MiniMax 语音按每百万字（这个是美元）；usd_cny 美元换人民币。2026-10 官网价 变了自己改",
    "cny_miss": 1.0, "cny_hit": 0.02, "cny_out": 4.0, "peak_mult": 2.0, "search_cny_per_k": 5.0, "fetch_cny_per_k": 5.0,
    "tts_per_m_chars": 60, "usd_cny": 7.1}
OLD_PRICE_KEYS = ("ds_miss", "ds_hit", "ds_out", "search_per_k")      # 10-26 以前的美元价（换成人民币的峰谷价了）
STAGES = [(0, "陌生人"), (20, "认识的人"), (40, "熟人"), (60, "好朋友"), (80, "特别的人")]


# ───────────── 文件小工具 ─────────────
def jread(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def jwrite(p, data, indent=1):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, p)


def backup(p, tag):
    """改之前留一份（每种最多留 30 份）"""
    p = Path(p)
    if not p.exists():
        return
    BACKUP.mkdir(parents=True, exist_ok=True)
    dst = BACKUP / f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}{p.suffix}"
    dst.write_bytes(p.read_bytes())
    old = sorted(BACKUP.glob(f"{tag}_*{p.suffix}"))
    for x in old[:-30]:
        try:
            x.unlink()
        except Exception:
            pass


def cfg():
    return jread(CONFIG, {}) or {}


def panel_cfg():
    return cfg().get("panel") or {}


# ───────────── 和机器人说话 ─────────────
_noproxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def bot_api(action, params=None, timeout=10):
    """调机器人的本机接口；机器人没开返回 None"""
    P = panel_cfg()
    if not P.get("token"):
        return None
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(P.get('api_port', 8766))}/api",
        data=json.dumps({"action": action, "params": params or {}}, ensure_ascii=False).encode("utf-8"),
        headers={"X-Token": str(P["token"]), "Content-Type": "application/json"}, method="POST")
    try:
        with _noproxy.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception:
        return None


def port_open(host, port, timeout=0.6):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def napcat_addr():
    m = re.match(r"wss?://([^:/]+):(\d+)", (cfg().get("onebot") or {}).get("url") or "ws://127.0.0.1:3001")
    return (m.group(1), int(m.group(2))) if m else ("127.0.0.1", 3001)


def napcat_up():
    return port_open(*napcat_addr())


def _dec(b):
    """Windows 命令的输出可能是 GBK（中文系统）也可能是 UTF-8"""
    for enc in ("utf-8", "oem" if os.name == "nt" else "latin-1"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return b.decode("utf-8", "replace")


def run_cmd(args, timeout=12):
    r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.returncode, _dec(r.stdout or b""), _dec(r.stderr or b"")


_procs = {"t": 0, "v": []}


def bot_procs():
    """有没有 bot.py 的进程（控制台不是管理员时 可能看不到管理员开的那个 所以只当参考）"""
    if os.name != "nt":
        return []
    if time.time() - _procs["t"] < 10:
        return _procs["v"]
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'py*' -and $_.CommandLine -like '*bot.py*' }"
          " | ForEach-Object { $_.ProcessId }")
    try:
        v = [int(x) for x in run_cmd(["powershell", "-NoProfile", "-Command", ps])[1].split() if x.isdigit()]
    except Exception:
        v = []
    _procs.update(t=time.time(), v=v)
    return v


_ws = {"t": 0, "v": []}


def ws_clients():
    """谁连着 NapCat 的端口（连着的就是机器人 新旧版本都算；不用管理员权限也看得到）"""
    if os.name != "nt":
        return []
    if time.time() - _ws["t"] < 5:
        return _ws["v"]
    port = napcat_addr()[1]
    v = []
    try:
        for line in run_cmd(["netstat", "-ano", "-p", "TCP"], timeout=10)[1].splitlines():
            p = line.split()
            if len(p) >= 5 and p[0].upper() == "TCP" and p[2].endswith(f":{port}") and p[-1].isdigit() \
                    and p[-1] != "0" and "TIME_WAIT" not in line:
                v.append(int(p[-1]))
    except Exception:
        v = []
    _ws.update(t=time.time(), v=v)
    return v


_qq = {"t": 0, "v": None}


def qq_running():
    """QQ.exe 在不在（看不出来就返回 None）"""
    if os.name != "nt":
        return None
    if time.time() - _qq["t"] < 10:
        return _qq["v"]
    try:
        out = run_cmd(["tasklist", "/FI", "IMAGENAME eq QQ.exe", "/FO", "CSV", "/NH"], timeout=10)[1]
        v = "qq.exe" in out.lower()
    except Exception:
        v = None
    _qq.update(t=time.time(), v=v)
    return v


def heartbeat():
    h = jread(HEARTBEAT, {}) or {}
    try:
        h["fresh"] = time.time() - float(h.get("ts") or 0) < 45
    except (TypeError, ValueError):
        h["fresh"] = False
    return h


# ───────────── QQ 登录状态（看 NapCat 的日志和二维码） ─────────────
_NAPLINE = re.compile(r"^(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d) \[\w+\] (.*)$")


def _nap_ts(m):
    now = time.localtime()
    try:
        t = time.mktime((now.tm_year, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)),
                         int(m.group(5)), 0, 0, -1))
    except (OverflowError, ValueError):
        return 0
    return t if t <= time.time() + 2 * 86400 else t - 365 * 86400    # 跨年


def napcat_events():
    """NapCat 日志最后一段里：上次登上是什么时候、有没有被踢下线、快速登录为什么失败"""
    try:
        size = NAPCAT_LOG.stat().st_size
        with NAPCAT_LOG.open("rb") as f:
            f.seek(max(0, size - 1_000_000))
            raw = f.read().decode("utf-8", errors="replace")
    except Exception:
        return {}
    ev = {"online": 0, "kicked": None, "qerr": None, "first_qr": 0, "account": ""}
    for line in ANSI.sub("", raw).splitlines():
        m = _NAPLINE.match(line.strip())
        if not m:
            continue
        msg = m.group(6)
        if "WebSocket Server] Server Started" in msg or "OneBot11 适配器初始化完成" in msg:
            ev.update(online=_nap_ts(m), kicked=None, qerr=None, first_qr=0)
        elif "KickedOffLine" in msg or "[下线通知]" in msg:
            ev["kicked"] = (_nap_ts(m), re.sub(r"^.*?\[下线通知\]\s*", "", msg).strip()[:80])
        elif "快速登录错误" in msg:
            ev["qerr"] = (_nap_ts(m), msg.split("快速登录错误", 1)[1].lstrip("：: ").strip()[:80])
        elif "二维码已保存到" in msg:
            if not ev["first_qr"]:
                ev["first_qr"] = _nap_ts(m)
        else:
            a = re.search(r"正在快速登录\s+(\d{5,12})", msg)
            if a:
                ev["account"] = a.group(1)
    return ev


def qq_status():
    """ok=登着  scan=要扫码（有二维码）  off=QQ 没开  starting=QQ 开着 还在启动/登录"""
    ev = napcat_events()
    acct = ev.get("account") or str(heartbeat().get("self_id") or "")
    if napcat_up():
        return {"state": "ok", "since": ev.get("online") or 0, "account": acct}
    kicked, qerr = ev.get("kicked"), ev.get("qerr")
    since = (kicked[0] if kicked else 0) or ev.get("first_qr") or (qerr[0] if qerr else 0)
    seen = [x for x in (kicked, qerr) if x]
    reason = max(seen, key=lambda x: x[0])[1] if seen else ""
    try:
        qr_t = QRCODE.stat().st_mtime
    except OSError:
        qr_t = 0
    if qr_t and time.time() - qr_t < 200 and qr_t >= (ev.get("online") or 0):
        state = "scan"
    else:
        state = "off" if qq_running() is False else "starting"
    return {"state": state, "since": since, "reason": reason, "kicked": bool(kicked), "qr_t": qr_t,
            "account": acct}


def start_bot():
    if bot_api("ping", timeout=3):
        return True, "机器人已经在运行了"
    if heartbeat().get("fresh"):
        return False, "机器人在运行 只是接口暂时没响应 等几秒再看；一直这样就点「强制重启机器人」"
    if bot_procs() or ws_clients():
        return False, "已经有一个机器人连着 QQ（多半是还没换成新版本的那个）。点「强制重启机器人」换成新版本"
    if napcat_up():
        loop = ROOT / "botloop.bat"
        if os.name == "nt" and loop.exists():          # 带守护循环启动：挂了会自己再拉起来
            subprocess.Popen(["cmd", "/c", str(loop)], cwd=str(ROOT), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x08000000 | 0x00000200)
        else:
            out = open(LOG, "ab")
            subprocess.Popen([sys.executable, str(ROOT / "bot.py")], cwd=str(ROOT), stdout=out,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=DETACHED)
        _procs["t"] = _ws["t"] = 0
        return True, "机器人启动中（QQ 本来就在线 十几秒就好）"
    st = qq_status()["state"]
    if st == "scan":
        return False, "QQ 在等扫码登录：先用手机 QQ 扫总览上的二维码。登上以后 机器人没开的话再点一次「启动机器人」"
    if st == "starting":
        return False, "QQ 还在启动 / 登录 等半分钟再试"
    return start_all()


def start_all():
    try:
        code, out, err = run_cmd(["schtasks", "/run", "/tn", "QQBot_Service"], timeout=20)
        if code == 0:
            _procs["t"] = _ws["t"] = _qq["t"] = 0
            return True, "QQ 和机器人一起启动中（大概 30 秒）"
        return False, "启动失败：" + (out + err).strip()[:200]
    except Exception as e:
        return False, f"启动失败：{e}"


def run_bat(name, ok_msg):
    bat = QQBOT / name
    if not bat.exists():
        return False, f"找不到 {name}"
    try:
        os.startfile(str(bat))          # 会弹管理员确认
        _procs["t"] = _ws["t"] = _qq["t"] = 0
        return True, ok_msg
    except Exception as e:
        return False, f"运行 {name} 失败：{e}"


def stop_all():
    return run_bat("停止.bat", "已经运行 停止.bat（电脑上会弹一个管理员确认 点「是」）")


def restart_hard():
    """机器人卡住 / 还是旧版本（没有控制台接口）时：用管理员权限只重启机器人 QQ 不动"""
    return run_bat("重启机器人.bat", "已经运行 重启机器人.bat（电脑上会弹一个管理员确认 点「是」；QQ 不会掉线）")


# ───────────── 设置 ─────────────
def mask(o):
    if isinstance(o, dict):
        return {k: ({"__secret__": True, "set": bool(v)} if SECRET.search(k) and isinstance(v, str) else mask(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [mask(x) for x in o]
    return o


def _walk(c, path):
    cur = c
    parts = path.split(".")
    for k in parts[:-1]:
        if not isinstance(cur, dict):
            return None, None
        cur = cur.setdefault(k, {})
    return cur, parts[-1]


def apply_changes(changes):
    """[{path, value}] → 写回 config.json（类型要和原来的一致；先备份）"""
    with _lock:
        c = cfg()
        done, bad = [], []
        for ch in changes or []:
            path, val = str(ch.get("path") or ""), ch.get("value")
            if not path or path.startswith("panel.token") or path.startswith("_"):
                bad.append(path)
                continue
            parent, k = _walk(c, path)
            if parent is None:
                bad.append(path)
                continue
            old = parent.get(k)
            if SECRET.search(k):
                if not isinstance(val, str) or not val.strip():
                    bad.append(path)
                    continue
                parent[k] = val.strip()
            elif old is None:
                parent[k] = val
            elif isinstance(old, bool):
                parent[k] = bool(val)
            elif isinstance(old, (int, float)) and not isinstance(old, bool):
                try:
                    num = float(val)
                except (TypeError, ValueError):
                    bad.append(path)
                    continue
                parent[k] = int(num) if isinstance(old, int) and num == int(num) else num
            elif isinstance(old, list):
                if not isinstance(val, list):
                    bad.append(path)
                    continue
                parent[k] = val
            else:
                parent[k] = "" if val is None else str(val)
            done.append(path)
        if done:
            backup(CONFIG, "config")
            jwrite(CONFIG, c)
        return done, bad


# ───────────── 群和人 ─────────────
def control_data():
    d = jread(CONTROL, {}) or {}
    d.setdefault("groups", {})
    d.setdefault("blocked", {})
    d.setdefault("bots", {})
    return d


def save_control(d):
    with _lock:
        jwrite(CONTROL, d)


def people():
    cache = jread(CACHE, {}) or {}
    rg = bot_api("groups", timeout=8)
    online = bool(rg and rg.get("ok"))
    if online:
        cache["groups"] = {g["id"]: {"name": g.get("name") or "", "members": g.get("members")} for g in rg["data"]}
        rf = bot_api("friends", timeout=8)
        if rf and rf.get("ok"):
            cache["friends"] = {f["id"]: f.get("name") or "" for f in rf["data"]}
        cache["t"] = time.time()
    try:
        jwrite(CACHE, cache)
    except Exception:
        pass
    ctl = control_data()
    groups = dict(cache.get("groups") or {})
    for p in MEM_DIR.glob("group_*.json"):
        groups.setdefault(p.stem.split("_", 1)[1], {"name": "", "members": None})
    for gid in ctl["groups"]:
        groups.setdefault(gid, {"name": "", "members": None})
    out = []
    now = time.time()
    for gid, g in groups.items():
        m = jread(MEM_DIR / f"group_{gid}.json", {}) or {}
        c = ctl["groups"].get(gid) or {}
        st = control.group_state(gid, now)     # 群管设的档位 / QQ 禁言 / 实际按哪档（10-26 群管说话算数）
        out.append({"id": gid, "name": g.get("name") or "", "members": g.get("members"),
                    "last": m.get("last_active") or 0, "mode": c.get("mode") or "normal",
                    "mute_until": c.get("mute_until") if float(c.get("mute_until") or 0) > now else 0,
                    "admin": st["admin"], "ban": st["ban"], "eff": st["mode"]})
    out.sort(key=lambda x: -(x["last"] or 0))
    friends = dict(cache.get("friends") or {})
    for p in MEM_DIR.glob("private_*.json"):
        uid = p.stem.split("_", 1)[1]
        if uid not in friends:
            m = jread(p, {}) or {}
            friends[uid] = next((h.get("name") for h in reversed(m.get("history") or []) if h.get("role") == "user"), "")
    return {"groups": out, "friends": [{"id": k, "name": v} for k, v in friends.items()],
            "blocked": ctl["blocked"], "bots": ctl["bots"], "online": online,
            "self_off": control.self_off_list(now),
            "gadm_hours": (cfg().get("group_admin") or {}).get("default_hours", 3)}


# ───────────── 记忆和好感 ─────────────
def conv_name(key, m, cache):
    if key.startswith("group_"):
        return ((cache.get("groups") or {}).get(key[6:]) or {}).get("name") or ""
    return next((h.get("name") for h in reversed(m.get("history") or []) if h.get("role") == "user"), "") or \
        (cache.get("friends") or {}).get(key[8:], "")


def memory_list():
    cache = jread(CACHE, {}) or {}
    out = []
    for p in MEM_DIR.glob("*.json"):
        m = jread(p, {}) or {}
        facts = [f for f in m.get("facts") or [] if isinstance(f, dict) and f.get("status") == "active"]
        out.append({"key": p.stem, "type": m.get("type") or p.stem.split("_")[0], "name": conv_name(p.stem, m, cache),
                    "facts": len(facts), "history": len(m.get("history") or []), "last": m.get("last_active") or 0,
                    "closeness": (m.get("rel") or {}).get("closeness")})
    out.sort(key=lambda x: -(x["last"] or 0))
    return out


def memory_detail(key, n=60):
    if not re.fullmatch(r"(private|group)_\d+", key or ""):
        return None
    m = jread(MEM_DIR / f"{key}.json")
    if not m:
        return None
    cache = jread(CACHE, {}) or {}
    facts = [f for f in m.get("facts") or [] if isinstance(f, dict)]
    return {"key": key, "name": conv_name(key, m, cache), "type": m.get("type"),
            "facts": sorted([f for f in facts if f.get("status") == "active"], key=lambda f: -(f.get("ts") or 0)),
            "old_facts": len([f for f in facts if f.get("status") != "active"]),
            "rel": m.get("rel") or {}, "summary": m.get("summary") or "", "alias": m.get("alias") or {},
            "history": [{k: h.get(k) for k in ("role", "name", "text", "ts")} for h in (m.get("history") or [])[-n:]],
            "last": m.get("last_active") or 0}


def memory_edit(action, key, **kw):
    if not re.fullmatch(r"(private|group)_\d+", key or ""):
        return {"ok": False, "error": "对话不对"}
    r = bot_api(action, dict(kw, key=key))
    if r is not None:
        return r
    p = MEM_DIR / f"{key}.json"            # 机器人没开：直接改文件
    with _lock:
        m = jread(p)
        if not m:
            return {"ok": False, "error": "没有这个对话"}
        res = control.forget_fact(m, kw.get("id")) if action == "forget" else \
            control.add_fact(m, kw.get("text"), int(kw.get("imp") or 4))
        if res:
            backup(p, "mem_" + key)
            jwrite(p, m)
        return {"ok": bool(res), "offline": True}


def wipe_preview(uid):
    if not re.fullmatch(r"\d{5,12}", uid or ""):
        return {"ok": False, "error": "QQ 号不对"}
    pv = wipe.preview(DATA, uid)
    return {"ok": True, "data": pv, "text": wipe.describe(pv)}


def wipe_person(uid):
    """真删：机器人开着就让它删（它拿着记忆的锁）；没开就直接删文件"""
    if not re.fullmatch(r"\d{5,12}", uid or ""):
        return {"ok": False, "error": "QQ 号不对"}
    r = bot_api("wipe", {"uid": uid}, timeout=60)
    if r is not None:
        return r
    hb = jread(HEARTBEAT, {}) or {}
    if time.time() - float(hb.get("ts") or 0) < 60:
        return {"ok": False, "error": "机器人开着但这会儿没回话 等一会儿再删（怕跟它同时改文件）"}
    with _lock:
        rep = wipe.run_offline(DATA, uid, [BACKUP])
    return {"ok": True, "offline": True, "report": rep, "text": wipe.describe(rep)}


def favor_list():
    d = jread(FAVOR, {}) or {}
    out = []
    for uid, u in d.items():
        f = int(u.get("favor") or 0)
        out.append({"uid": uid, "name": u.get("name") or "", "favor": f, "stage": [s for t, s in STAGES if f >= t][-1],
                    "chats": u.get("chats") or 0, "events": u.get("events") or [], "first": u.get("first") or 0})
    out.sort(key=lambda x: -x["favor"])
    return out


def favor_set(uid, value):
    r = bot_api("favor", {"uid": uid, "favor": value})
    if r is not None:
        return r
    with _lock:
        d = jread(FAVOR, {}) or {}
        u = control.set_favor(d, uid, value)
        jwrite(FAVOR, d)
        return {"ok": True, "favor": u["favor"], "offline": True}


# ───────────── 统计 ─────────────
def prices():
    p = dict(DEFAULT_PRICES)
    p.update({k: v for k, v in (panel_cfg().get("prices") or {}).items() if k not in OLD_PRICE_KEYS})
    for k, v in list(p.items()):
        if not k.startswith("_"):
            try:
                p[k] = float(v)
            except (TypeError, ValueError):
                p[k] = float(DEFAULT_PRICES.get(k) or 0)
    p["usd_cny"] = p["usd_cny"] if p["usd_cny"] > 0 else 7.1
    return p


def migrate_prices(P):
    """config 里还是 10-26 以前的美元价 → 换成人民币峰谷价（语音价、汇率留着）。改了返回 True"""
    old = P.get("prices")
    if not isinstance(old, dict) or ("cny_miss" in old or not any(k in old for k in OLD_PRICE_KEYS)):
        return False
    P["prices"] = dict(DEFAULT_PRICES, **{k: old[k] for k in ("tts_per_m_chars", "usd_cny") if k in old})
    return True


def peak_view():
    """DeepSeek 现在是不是高峰（价格翻倍）"""
    pk = cfg().get("peak") or {}
    stats.configure_peak(pk)
    return {"now": stats.peak_now(), "text": stats.peak_text(), "enabled": bool(pk.get("enabled", True)),
            "defer": bool(pk.get("defer", True)), "hours": pk.get("hours") or [[9, 12], [14, 18]]}


def _ds_cny(x, p):
    """DeepSeek 花了多少（人民币）：高峰时段那部分 × peak_mult。x = 一天（tok_*）或一个功能（miss/hit/out）
    10-26 以前没分高峰时段 都按空闲价算"""
    def g(k):
        return float(x.get(k, x.get("tok_" + k, 0)) or 0)
    base = g("miss") * p["cny_miss"] + g("hit") * p["cny_hit"] + g("out") * p["cny_out"]
    peak = g("miss_pk") * p["cny_miss"] + g("hit_pk") * p["cny_hit"] + g("out_pk") * p["cny_out"]
    return (base + peak * (p["peak_mult"] - 1)) / 1e6


def _ds_cost(x, p):
    """DeepSeek 花了多少（美元 控制台统一按美元显示）"""
    return _ds_cny(x, p) / p["usd_cny"]


def _search_cost(x, p):
    """DeepSeek 搜索 + 读网页（美元）"""
    return (x.get("searches", 0) * p["search_cny_per_k"] + x.get("fetches", 0) * p["fetch_cny_per_k"]) / 1000 / p["usd_cny"]


def _peak_share(x, p):
    """DeepSeek 的钱里 高峰时段花的（美元）"""
    pk = {k: x.get("tok_" + k + "_pk", x.get(k + "_pk", 0)) for k in ("miss", "hit", "out")}
    return (pk["miss"] * p["cny_miss"] + pk["hit"] * p["cny_hit"] + pk["out"] * p["cny_out"]) * p["peak_mult"] / 1e6 / p["usd_cny"]


def cost_of(d, p):
    ds = _ds_cost(d, p)
    tts = d.get("voice_chars", 0) * p["tts_per_m_chars"] / 1e6
    se = _search_cost(d, p)
    return {"deepseek": round(ds, 4), "voice": round(tts, 4), "search": round(se, 4), "total": round(ds + tts + se, 4),
            "peak": round(_peak_share(d, p), 4)}


def budget_view(s=None):
    """省 token：最近 N 小时花了多少、现在的回复意愿（和机器人用同一条曲线）"""
    B = dict(budget.DEFAULTS)
    B.update({k: v for k, v in (cfg().get("budget") or {}).items() if not str(k).startswith("_")})
    powers = dict(budget.DEFAULTS["powers"])
    powers.update(B.get("powers") or {})
    s = s if s is not None else (jread(STATS, {}) or {})
    wh = max(1, int(B.get("window_hours") or 24))
    now = time.time()
    keys = {time.strftime("%Y-%m-%d %H", time.localtime(now - i * 3600)) for i in range(wh)}
    u = sum(v for k, v in (s.get("hours") or {}).items() if k in keys)
    w = budget.curve(u, B.get("half_usd"), B.get("shape"), B.get("floor"))
    return {"enabled": bool(B.get("enabled", True)), "spent": round(u, 4), "will": round(w, 3),
            "half_usd": float(B.get("half_usd")), "shape": float(B.get("shape")), "floor": float(B.get("floor")),
            "window_hours": wh, "powers": powers, "full": control.full_state(),
            "full_default_hours": float((cfg().get("full_power") or {}).get("default_hours", 3))}


def stats_view(days=14):
    s = jread(STATS, {}) or {}
    p = prices()
    all_days = s.get("days") or {}
    keys = [time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400)) for i in range(days - 1, -1, -1)]
    rows = []
    tot = {}
    for k in keys:
        d = all_days.get(k) or {}
        c = cost_of(d, p)
        rows.append(dict({a: b for a, b in d.items() if isinstance(b, (int, float))}, day=k, cost=c))
        for a, b in d.items():
            if isinstance(b, (int, float)):
                tot[a] = tot.get(a, 0) + b
    cut = keys[0]
    mood = sorted((k, v) for k, v in (s.get("mood") or {}).items() if k[:10] >= cut)
    m2 = jread(DATA / "mood2.json", {}) or {}
    return {"days": rows, "total": dict(tot, cost=cost_of(tot, p)), "prices": p, "mood": mood,
            "timeline": (m2.get("timeline") or [])[-40:], "now_mood": m2.get("mood"), "emos": m2.get("emos") or {},
            "budget": budget_view(s), "peak": peak_view()}


# ───────────── 用量（DeepSeek / MiniMax） ─────────────
_bal = {"t": 0, "v": None}
_mmq = {"t": 0, "v": None}


def _get_json(url, headers, timeout=12):
    req = urllib.request.Request(url, headers=dict(headers, Accept="application/json"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def deepseek_balance(force=False):
    """DeepSeek 官方查余额接口 GET /user/balance（5 分钟查一次；key 只在本机用 不发给网页）"""
    if not force and _bal["v"] is not None and time.time() - _bal["t"] < 300:
        return _bal["v"]
    d = (cfg().get("llm") or {}).get("deepseek") or {}
    key = d.get("api_key")
    if not key:
        v = {"ok": False, "error": "config 里没填 DeepSeek key"}
    else:
        base = re.sub(r"/v1/?$", "", (d.get("base_url") or "https://api.deepseek.com").rstrip("/"))
        try:
            j = _get_json(base + "/user/balance", {"Authorization": f"Bearer {key}"})
            v = {"ok": True, "available": bool(j.get("is_available")),
                 "infos": [{k: x.get(k) for k in ("currency", "total_balance", "granted_balance", "topped_up_balance")}
                           for x in (j.get("balance_infos") or []) if isinstance(x, dict)]}
        except urllib.error.HTTPError as e:
            v = {"ok": False, "error": f"DeepSeek 返回 HTTP {e.code}" + ("（key 不对）" if e.code == 401 else "")}
        except Exception as e:
            v = {"ok": False, "error": f"查不到（{type(e).__name__}）"}
    v["t"] = time.time()
    _bal.update(t=time.time(), v=v)
    return v


def minimax_quota(force=False):
    """MiniMax 按量付费没有查余额的接口（只能去官网看）；如果是 Token Plan 的 key 能查到剩余次数"""
    if not force and _mmq["v"] is not None and time.time() - _mmq["t"] < 1800:
        return _mmq["v"]
    m = (cfg().get("voice") or {}).get("minimax") or {}
    domestic = "minimax.io" not in (m.get("base_url") or "api.minimaxi.com")
    v = {"ok": False, "console": "https://platform.minimaxi.com/user-center/payment/balance" if domestic
         else "https://platform.minimax.io/user-center/payment/balance"}
    if m.get("api_key") and domestic:
        try:
            j = _get_json("https://www.minimaxi.com/v1/api/openplatform/coding_plan/remains",
                          {"Authorization": f"Bearer {m['api_key']}"})
            rem = j.get("model_remains")
            if isinstance(rem, list) and rem:
                v.update(ok=True, remains=[{"model": str(x.get("model_name") or x.get("model") or "?")[:40],
                                            "remaining": x.get("remaining", x.get("current_interval_remain")),
                                            "total": x.get("total", x.get("current_interval_total_count")),
                                            "reset_ms": x.get("remains_time")} for x in rem[:10] if isinstance(x, dict)])
        except Exception:
            pass
    _mmq.update(t=time.time(), v=v)
    return v


def usage_view(force=False):
    s = jread(STATS, {}) or {}
    days = s.get("days") or {}
    p = prices()

    def agg(n):
        tot, by = {}, {}
        for i in range(n):
            d = days.get(time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))) or {}
            for a, b in d.items():
                if isinstance(b, (int, float)):
                    tot[a] = tot.get(a, 0) + b
            for tag, x in (d.get("by") or {}).items():
                t = by.setdefault(tag, {})
                for a, b in (x or {}).items():
                    t[a] = t.get(a, 0) + b
        ds = _ds_cost(tot, p)
        mm = tot.get("voice_chars", 0) * p["tts_per_m_chars"] / 1e6
        se = _search_cost(tot, p)
        rows = sorted(({"tag": k, "calls": v.get("calls", 0), "miss": v.get("miss", 0), "hit": v.get("hit", 0),
                        "out": v.get("out", 0), "cost": round(_ds_cost(v, p), 5)} for k, v in by.items()),
                      key=lambda r: -r["cost"])
        return {"calls": tot.get("llm_calls", 0), "miss": tot.get("tok_miss", 0), "hit": tot.get("tok_hit", 0),
                "out": tot.get("tok_out", 0), "searches": tot.get("searches", 0), "fetches": tot.get("fetches", 0),
                "tts_calls": tot.get("tts_calls", 0), "chars": tot.get("voice_chars", 0), "voice_msgs": tot.get("voice", 0),
                "ds_cost": round(ds, 5), "mm_cost": round(mm, 5), "search_cost": round(se, 5),
                "peak_cost": round(_peak_share(tot, p), 5), "total": round(ds + mm + se, 5), "by": rows}

    daily = []
    for i in range(13, -1, -1):
        k = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        d = days.get(k) or {}
        daily.append({"day": k, "calls": d.get("llm_calls", 0), "tok": d.get("tok_miss", 0) + d.get("tok_hit", 0),
                      "out": d.get("tok_out", 0), "ds_cost": round(_ds_cost(d, p), 5), "chars": d.get("voice_chars", 0),
                      "mm_cost": round(d.get("voice_chars", 0) * p["tts_per_m_chars"] / 1e6, 5),
                      "searches": d.get("searches", 0) + d.get("fetches", 0)})
    return {"ok": True, "deepseek": deepseek_balance(force), "minimax": minimax_quota(force), "prices": p,
            "today": agg(1), "d7": agg(7), "d30": agg(30), "daily": daily, "budget": budget_view(s),
            "anim": anim_view(), "peak": peak_view()}


def anim_view():
    """做动画（火山方舟）：花了多少、还能花多少、最近做了什么（data/anim_state.json + 心跳里的状态）"""
    st = jread(DATA / "anim_state.json", {}) or {}
    a = cfg().get("anim") or {}
    hb = jread(DATA / "heartbeat.json", {}) or {}
    today = (st.get("days") or {}).get(time.strftime("%Y-%m-%d")) or {}
    return {"has_key": bool(a.get("api_key")), "enabled": a.get("enabled", True),
            "model": a.get("model", "doubao-seedance-2-0-mini-260615"), "resolution": a.get("resolution", "480p"),
            "duration": a.get("duration", 5), "price": a.get("price_cny_per_m", 23),
            "budget": float(a.get("budget_cny", 190)), "spent": float(st.get("spent") or 0),
            "count": int(st.get("count") or 0), "running": len(st.get("tasks") or {}),
            "today": {k: today.get(k, 0) for k in ("owner", "others", "proactive", "made", "failed", "cny")},
            "last_error": st.get("last_error", ""), "history": (st.get("history") or [])[-8:][::-1],
            "status": hb.get("anim") or {}, "console": "https://console.volcengine.com/"}


# ───────────── 日志 ─────────────
def log_tail(n=400, tags=None, q=""):
    try:
        size = LOG.stat().st_size
        with LOG.open("rb") as f:
            f.seek(max(0, size - 1_500_000))
            raw = f.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    lines = raw.splitlines()[1:] if size > 1_500_000 else raw.splitlines()
    if tags:
        want = [t for t in tags if t != "错误"]
        err = "错误" in tags
        lines = [x for x in lines if any(f"[{t}" in x for t in want) or
                 (err and re.search(r"出错|失败|Traceback|Error|异常|超时", x))]
    if q:
        lines = [x for x in lines if q in x]
    return collapse(lines)[-int(n):]


_LOGTS = re.compile(r"^\[(\d\d:\d\d:\d\d)\]\s?")


def collapse(lines):
    """一模一样（除了时间）连着出现的行 合成一行：…  ×37（最后一次 21:04:30）"""
    out, key, cnt, last = [], None, 0, ""

    def flush():
        if cnt > 1:
            out[-1] += f"  ×{cnt}（最后一次 {last}）"

    for x in lines:
        m = _LOGTS.match(x)
        k = x[m.end():] if m else x
        if k == key:
            cnt += 1
            last = m.group(1) if m else ""
            continue
        flush()
        out.append(x)
        key, cnt, last = k, 1, (m.group(1) if m else "")
    flush()
    return out


# ───────────── 表情包 ─────────────
def sticker_list():
    idx = jread(STICKERS / "index.json", {}) or {}
    out = []
    for name, v in (idx.get("stickers") or {}).items():
        f = v.get("file")
        if not f:
            for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                if (STICKERS / f"{name}{ext}").is_file():
                    f = f"{name}{ext}"
                    break
        out.append({"name": name, "emo": v.get("emo") or "", "scene": v.get("scene") or "", "text": v.get("text") or "",
                    "file": f or "", "off": bool(v.get("off")), "pack": v.get("pack") or "", "note": v.get("note") or ""})
    return out


def sticker_toggle(name, off):
    with _lock:
        p = STICKERS / "index.json"
        idx = jread(p)
        if not idx or name not in (idx.get("stickers") or {}):
            return False
        if off:
            idx["stickers"][name]["off"] = True
        else:
            idx["stickers"][name].pop("off", None)
        jwrite(p, idx)
        return True


# ───────────── 总览 ─────────────
def overview():
    ping = bot_api("status", timeout=4)
    st = {"bot": "offline", "napcat": napcat_up(), "qq": qq_status()}
    if ping and ping.get("ok"):
        st.update(bot="online", info=ping)
    elif ping is not None:
        st.update(bot="error", error=ping.get("error"))
    elif heartbeat().get("fresh") or bot_procs() or (st["napcat"] and ws_clients()):
        st.update(bot="stuck")
    today = time.strftime("%Y-%m-%d")
    s = jread(STATS, {}) or {}
    d = (s.get("days") or {}).get(today) or {}
    st["today"] = dict({a: b for a, b in d.items() if isinstance(b, (int, float))}, cost=cost_of(d, prices()))
    st["budget"] = budget_view(s)
    if (_bal["v"] is None or time.time() - _bal["t"] > 300) and not _bal.get("busy"):
        def _refresh():                        # 余额在后台查 不卡总览
            _bal["busy"] = True
            try:
                deepseek_balance(True)
            finally:
                _bal["busy"] = False
        threading.Thread(target=_refresh, daemon=True).start()
    st["ds_balance"] = _bal["v"]
    m2 = jread(DATA / "mood2.json", {}) or {}
    mood = jread(ROOT / "mood.json", {}) or {}
    st["mood"] = {"value": m2.get("mood"), "emos": m2.get("emos") or {}, "label": mood.get("label") or "",
                  "note": mood.get("note") or "", "nick": mood.get("nickname") or "",
                  "timeline": (m2.get("timeline") or [])[-24:]}
    st["recent"] = log_tail(14, ["接话", "表情包", "语音", "补回", "控制台", "冒泡", "节奏", "QQ", "启动", "错误"])
    st["has_token"] = bool(panel_cfg().get("token"))
    hb = heartbeat()
    st["boot"] = (st.get("info") or {}).get("boot") or (hb.get("boot") if hb.get("fresh") else None)
    return st


# ───────────── 唱歌：歌单 ─────────────
SUGGEST_SONGS = [   # 10-26 小明 在找的「网友都喜欢的 DeepSeek 拟人唱的歌」：B站 搜到最火的几首（导入前自己先试听 确认是想要的那首）
    {"name": "人是猫（大肥鱼翻唱 真完整版）", "link": "https://www.bilibili.com/video/av117422694599001/",
     "note": "星落落_oi · 98.8 万播放 · 原曲 张卡斯"},
]


def songs_view():
    sing.configure(cfg().get("sing") or {})
    d = sing.index()
    out = []
    for x in d["songs"]:
        e = {k: x.get(k) for k in ("id", "name", "file", "aliases", "credit", "start", "end", "enabled", "default",
                                   "source", "up", "title", "desc", "added", "exists", "auto")}
        e["secs"] = round(sing.secs(x), 1) if x.get("exists") else 0
        out.append(e)
    return {"ok": True, "songs": out, "ffmpeg": bool(sing.ffmpeg()), "cfg": sing.cfg(), "dir": str(sing.SONG_DIR),
            "suggest": SUGGEST_SONGS}


def song_save(b):
    with _lock:
        d = sing.index()
        x = next((s for s in d["songs"] if s.get("id") == b.get("id")), None)
        if not x:
            return {"ok": False, "error": "没有这首歌（刷新一下）"}
        if "name" in b and str(b["name"]).strip():
            x["name"] = str(b["name"]).strip()[:40]
        if "aliases" in b:
            al = b["aliases"] if isinstance(b["aliases"], list) else re.split(r"[,，、/ ]+", str(b["aliases"] or ""))
            x["aliases"] = [a.strip()[:30] for a in al if a.strip()][:10]
        if "credit" in b:
            x["credit"] = str(b["credit"] or "").strip()[:120]
        for k in ("start", "end"):
            if k in b:
                try:
                    v = float(b[k]) if str(b[k]).strip() != "" else 0
                except (TypeError, ValueError):
                    return {"ok": False, "error": "开始/结束要填秒数"}
                x[k] = max(0.0, v)
        if "enabled" in b:
            x["enabled"] = bool(b["enabled"])
        if b.get("default"):
            for s2 in d["songs"]:
                s2["default"] = s2 is x
        x.pop("auto", None)
        sing.save_index(d)
    return {"ok": True}


def song_upload(b):
    import base64
    data = str(b.get("data") or "")
    if "," in data[:100]:
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data)
    except Exception:
        return {"ok": False, "error": "文件读不出来"}
    if len(raw) < 2000:
        return {"ok": False, "error": "文件是空的"}
    fn = str(b.get("filename") or "song.mp3")
    ext = Path(fn).suffix.lower()
    if ext not in sing.AUDIO_EXT:
        return {"ok": False, "error": "只收 mp3 / m4a / wav / flac / ogg 这些音频"}
    name = str(b.get("name") or "").strip() or Path(fn).stem
    with _lock:
        e = sing.add_file(name, ext, raw, {"credit": str(b.get("credit") or "").strip()[:120]})
    return {"ok": True, "song": e}


def _run_async(coro):
    """在请求线程里跑一个协程（这个线程里正好有事件循环的话 另开一个线程跑）"""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box = {}

    def go():
        try:
            box["v"] = asyncio.run(coro)
        except BaseException as ex:          # noqa: B036  原样抛回去
            box["e"] = ex
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box.get("v")


def song_import(b):
    sess = (cfg().get("bili") or {}).get("sessdata") or ""
    try:
        e = _run_async(sing.import_bili(b.get("link") or "", sess, print))
    except RuntimeError as ex:
        return {"ok": False, "error": str(ex)}
    except Exception as ex:
        return {"ok": False, "error": "导入失败：" + repr(ex)[:160]}
    return {"ok": True, "song": e}


def song_preview(b):
    sing.configure(cfg().get("sing") or {})
    d = sing.index()
    x = next((s for s in d["songs"] if s.get("id") == b.get("id") and s.get("exists")), None)
    if not x:
        return {"ok": False, "error": "没有这首歌的文件"}
    x = dict(x)
    for k in ("start", "end"):
        if k in b and str(b[k]).strip() != "":
            try:
                x[k] = float(b[k])
            except (TypeError, ValueError):
                pass
    if not sing.ffmpeg():
        return {"ok": False, "error": "没装 ffmpeg 剪不了（双击 安装视频支持.bat）"}
    a, e = sing.span(x)
    p = sing.cut(x, a, e)
    if not p:
        return {"ok": False, "error": "剪不出来（文件坏了？）"}
    return {"ok": True, "url": f"/songclip?f={p.name}", "start": a, "end": e}


# ───────────── 版本 / 自动更新 ─────────────
PANEL_LOG = DATA / "panel.log"


def plog(msg):
    """控制台自己的启动/换版本记录：打印到黑窗口 + 写 data/panel.log（只留最近 100KB）"""
    print(msg, flush=True)
    try:
        line = time.strftime("%m-%d %H:%M:%S ") + f"[{os.getpid()}] " + str(msg).strip() + "\n"
        if PANEL_LOG.exists() and PANEL_LOG.stat().st_size > 200_000:
            PANEL_LOG.write_text(PANEL_LOG.read_text(encoding="utf-8", errors="replace")[-100_000:], encoding="utf-8")
        with open(PANEL_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _sig(p):
    try:
        st = Path(p).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _code_version():
    import hashlib
    try:
        return hashlib.md5((ROOT / "panel.py").read_bytes()).hexdigest()[:10]
    except OSError:
        return "?"


VERSION = _code_version()


def watched_files():
    """控制台自己用到的 .py（改了要重新加载）：panel.py + 它 import 的本目录模块"""
    out = {(ROOT / "panel.py").resolve()}
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if f and str(f).endswith(".py"):
            try:
                fp = Path(f).resolve()
            except OSError:
                continue
            if fp.parent == ROOT.resolve():
                out.add(fp)
    return sorted(out)


def version_info():
    h = _sig(HTML)
    return {"ok": True, "version": VERSION, "pid": os.getpid(), "started": START_TS,
            "html": f"{h[0]}-{h[1]}" if h else "", "auto_reload": bool(_busy["supervised"])}


def _compile_errors(files):
    bad = []
    for f in files:
        try:
            compile(Path(f).read_text(encoding="utf-8"), str(f), "exec")
        except Exception as e:
            bad.append(f"{Path(f).name}: {e!r}"[:200])
    return bad


def _watch(srv, files, every=2.0):
    """代码文件改了（而且写完了、能编译）→ 等手上的请求做完 → 停掉服务 让管家用新代码重起"""
    base = {f: _sig(f) for f in files}
    pending = bad = None
    while True:
        time.sleep(every)
        now = {f: _sig(f) for f in files}
        if now == base:
            pending = None
            continue
        if now != pending:             # 还在写（一次部署会连着改好几个文件）等它稳定一轮
            pending = now
            continue
        if now == bad:
            continue
        errs = _compile_errors([f for f in files if now[f] and now[f] != base[f]])
        if errs:
            plog("[控制台] 代码改了 但是有错 先不换（还用现在的）：" + "；".join(errs))
            bad = now
            continue
        plog(time.strftime("[%H:%M:%S]") + " [控制台] 代码更新了 → 自动换成新版本（网页不用关 刷新一下就是新的）")
        for _ in range(60):            # 最多等 15 秒 让正在处理的请求做完
            if _busy["n"] <= 0:
                break
            time.sleep(0.25)
        _busy["reload"] = True
        srv.shutdown()
        return


def _listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def _peer(port):
    """已经开着的控制台：dict（新版本 带版本号）/ "old"（旧版本 没有 /api/version）/ None（没响应）"""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/version")
        with _noproxy.open(req, timeout=4) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return "old" if e.code == 404 else None
    except Exception:
        return None


def _ps_json(cmd, timeout=20):
    try:
        out = run_cmd(["powershell", "-NoProfile", "-Command", cmd], timeout=timeout)[1].strip()
        return json.loads(out) if out else None
    except Exception:
        return None


def _listen_pids(port):
    """谁在听这个端口（Windows）"""
    pids = set()
    v = _ps_json(f"@(Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | "
                 "Select-Object -ExpandProperty OwningProcess) | ConvertTo-Json -Compress")
    for x in (v if isinstance(v, list) else [v]):
        if isinstance(x, int):
            pids.add(x)
    if not pids:                       # 老系统没有 Get-NetTCPConnection：看 netstat（状态那一列各语言不一样 用对方地址认）
        try:
            out = run_cmd(["netstat", "-ano", "-p", "TCP"], timeout=20)[1]
        except Exception:
            out = ""
        for line in out.splitlines():
            t = line.split()
            if len(t) >= 5 and t[1].endswith(f":{port}") and t[2] in ("0.0.0.0:0", "[::]:0") and t[-1].isdigit():
                pids.add(int(t[-1]))
    pids.discard(0)
    pids.discard(os.getpid())
    if not pids:
        plog(f"[控制台] 查不到是哪个进程在听端口 {port}（PowerShell 和 netstat 都没查到）")
    return pids


def _proc(pid):
    v = _ps_json(f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' | "
                 "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress")
    return v if isinstance(v, dict) else None


def _is_panel(info):
    return bool(info) and str(info.get("Name") or "").lower().startswith("py") and \
        "panel.py" in str(info.get("CommandLine") or "").lower()


def _takeover(port):
    """把占着端口的旧控制台关掉（只关命令行里有 panel.py 的 python 进程）→ (成没成, 说明)"""
    if os.name != "nt":
        return False, "不是 Windows"
    pids = _listen_pids(port)
    if not pids:
        return False, "找不到是哪个进程占着端口"
    plog(f"[控制台] 端口 {port} 上在听的进程：{sorted(pids)}（我是 {_whoami()}）")
    kill = []
    for pid in pids:
        info = _proc(pid)
        plog(f"[控制台]   进程 {pid}：{(info or {}).get('Name')} | {str((info or {}).get('CommandLine') or '看不到命令行')[:100]}"
             f" | 父进程 {(info or {}).get('ParentProcessId')}")
        if not _is_panel(info):
            name = (info or {}).get("Name") or "不知道是什么"
            return False, f"端口 {port} 被别的程序占着（进程 {pid} {name}）"
        kill.append(int(pid))
        par = _proc(info.get("ParentProcessId") or 0)
        if _is_panel(par) and int(par.get("ProcessId") or 0) not in (os.getpid(), os.getppid()):
            kill.append(int(par["ProcessId"]))     # 旧的「管家」进程也关掉 不然它会把旧的再起起来
    for pid in kill:
        try:
            rc, out, err = run_cmd(["taskkill", "/PID", str(pid), "/F"], timeout=15)
            plog(f"[控制台]   taskkill {pid} → {rc} {(out or err or '').strip()[:160]}")
        except Exception as e:
            plog(f"[控制台]   taskkill {pid} 出错 {e!r}"[:200])
    for _ in range(40):
        if not _listening(port):
            return True, f"关掉了旧的（进程 {'、'.join(map(str, kill))}）"
        time.sleep(0.25)
    left = _listen_pids(port)                     # 还在听：看看是谁 换个法子再关一次（记下来 好查原因）
    plog(f"[控制台] 关了以后端口还被占着：{sorted(left)}")
    for pid in left:
        info = _proc(pid)
        plog(f"[控制台]   进程 {pid}：{(info or {}).get('Name')} | {str((info or {}).get('CommandLine') or '看不到命令行')[:100]}")
        if _is_panel(info):
            try:
                rc, out, err = run_cmd(["powershell", "-NoProfile", "-Command",
                                        f"Stop-Process -Id {int(pid)} -Force -ErrorAction Continue"], timeout=20)
                plog(f"[控制台]   Stop-Process {pid} → {rc} {(err or out or '').strip()[:160]}")
            except Exception as e:
                plog(f"[控制台]   Stop-Process {pid} 出错 {e!r}"[:200])
    for _ in range(20):
        if not _listening(port):
            return True, "关掉了旧的（第二次才关掉）"
        time.sleep(0.25)
    return False, "旧的关不掉"


def _whoami():
    try:
        import ctypes
        adm = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        adm = None
    return f"{os.environ.get('USERNAME', '?')} 会话{os.environ.get('SESSIONNAME', '?')}" + \
        ("（管理员）" if adm else "（普通权限）" if adm is False else "")


# ───────────── 网页 ─────────────
class H(BaseHTTPRequestHandler):
    server_version = "FatFishPanel/1.0"

    def log_message(self, fmt, *a):
        pass

    def handle_one_request(self):
        with _busy_lock:
            _busy["n"] += 1
        try:
            super().handle_one_request()
        finally:
            with _busy_lock:
                _busy["n"] -= 1

    def _host_ok(self):
        port = self.server.server_address[1]
        return (self.headers.get("Host") or "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype=None, cache=True):
        try:
            data = Path(path).read_bytes()
        except Exception:
            return self._json({"ok": False, "error": "没有这个文件"}, 404)
        ext = Path(path).suffix.lower()
        ctype = ctype or {".html": "text/html; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
                          ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
                          ".wav": "audio/wav", ".mp3": "audio/mpeg"}.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if not cache or ext in (".html", ".wav") else "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._host_ok():
            return self._json({"ok": False, "error": "forbidden"}, 403)
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self._file(HTML)
            if u.path == "/api/overview":
                return self._json(overview())
            if u.path == "/api/version":
                return self._json(version_info())
            if u.path == "/api/songs":
                return self._json(songs_view())
            if u.path == "/songclip":
                name = re.sub(r"[^\w.\-]", "", q.get("f") or "")
                if not name.startswith("song_"):
                    return self._json({"ok": False}, 403)
                return self._file(sing.CLIP_DIR / name, "audio/wav", cache=False)
            if u.path == "/api/config":
                return self._json({"ok": True, "config": mask(cfg())})
            if u.path == "/api/people":
                return self._json(people())
            if u.path == "/api/memory":
                return self._json({"ok": True, "data": memory_list()})
            if u.path == "/api/memory/detail":
                d = memory_detail(q.get("key"), int(q.get("n") or 60))
                return self._json({"ok": bool(d), "data": d})
            if u.path == "/api/favor":
                return self._json({"ok": True, "data": favor_list()})
            if u.path == "/api/wipe/preview":
                return self._json(wipe_preview(q.get("uid") or ""))
            if u.path == "/api/stats":
                return self._json(stats_view(max(1, min(90, int(q.get("days") or 14)))))
            if u.path == "/api/usage":
                return self._json(usage_view(q.get("force") == "1"))
            if u.path == "/api/logs":
                tags = [t for t in (q.get("tags") or "").split(",") if t]
                return self._json({"ok": True, "lines": log_tail(int(q.get("n") or 400), tags, q.get("q") or "")})
            if u.path == "/api/persona":
                f = ROOT / (cfg().get("persona_file") or "persona.md")
                return self._json({"ok": True, "text": f.read_text(encoding="utf-8") if f.exists() else ""})
            if u.path == "/api/stickers":
                return self._json({"ok": True, "data": sticker_list()})
            if u.path == "/sticker":
                f = (STICKERS / (q.get("f") or "")).resolve()
                if STICKERS.resolve() not in f.parents:
                    return self._json({"ok": False}, 403)
                return self._file(f)
            if u.path == "/voicefile":
                name = re.sub(r"[^\w.\-]", "", q.get("f") or "")
                return self._file(DATA / "voice" / name)
            if u.path == "/qrcode":
                return self._file(QRCODE, "image/png", cache=False)
        except Exception as e:
            return self._json({"ok": False, "error": repr(e)[:300]}, 500)
        return self._json({"ok": False, "error": "没有这个页面"}, 404)

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Panel") != "1":
            return self._json({"ok": False, "error": "forbidden"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        limit = 120_000_000 if urlparse(self.path).path == "/api/songs/upload" else 5_000_000   # 上传歌：音频 base64 能到几十 MB
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if 0 < n < limit else {}
        except Exception:
            return self._json({"ok": False, "error": "请求格式不对"}, 400)
        u = urlparse(self.path).path
        try:
            return self._json(self.route(u, body))
        except Exception as e:
            return self._json({"ok": False, "error": repr(e)[:300]}, 500)

    def route(self, u, b):
        if u == "/api/bot":
            act = b.get("action")
            if act == "start":
                ok, msg = start_bot()
            elif act == "start_all":
                ok, msg = start_all()
            elif act == "stop_all":
                ok, msg = stop_all()
            elif act == "restart_hard":
                ok, msg = restart_hard()
            elif act in ("restart", "shutdown"):
                r = bot_api(act, timeout=5)
                ok = bool(r and r.get("ok"))
                msg = ("机器人重启中（QQ 不动 十几秒就好）" if act == "restart" else "机器人停了（QQ 还在线）") if ok else \
                    "机器人没响应（没开 或者还是旧版本）"
                _procs["t"] = 0
            else:
                return {"ok": False, "error": "不认识的操作"}
            return {"ok": ok, "msg": msg}
        if u == "/api/config":
            done, bad = apply_changes(b.get("changes"))
            return {"ok": bool(done) and not bad, "done": done, "bad": bad}
        if u == "/api/control":
            d = control_data()
            kind = b.get("kind")
            if kind == "group":
                gid = str(b.get("id") or "")
                if not gid.isdigit():
                    return {"ok": False, "error": "群号不对"}
                g = d["groups"].setdefault(gid, {})
                if "mode" in b:
                    g["mode"] = b["mode"] if b["mode"] in control.LEVELS else "normal"
                if "mute_min" in b:
                    mins = float(b.get("mute_min") or 0)
                    g["mute_until"] = time.time() + mins * 60 if mins > 0 else 0
                if b.get("clear_admin"):           # 撤销群管设的档位
                    g.pop("admin", None)
                if b.get("clear_ban"):             # QQ 禁言记录（解了但没收到通知时 手动清）
                    old = g.pop("qq_ban", None)
                    if old:
                        g["qq_ban_last"] = {"t": old.get("t"), "lifted": time.time(), "whole": bool(old.get("whole"))}
            elif kind == "self_off":           # 群友「别接我的话」：提前放开
                uid = str(b.get("id") or "").strip()
                if not uid.isdigit():
                    return {"ok": False, "error": "QQ 号不对"}
                (d.get("self_off") or {}).pop(uid, None)
            elif kind == "full":           # 火力全开：hours>0 开几小时 / -1 一直开 / 0 关
                try:
                    h = float(b.get("hours") or 0)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "时长不对"}
                if h > 0:
                    d["full"] = {"until": time.time() + min(h, 72) * 3600, "since": time.time(), "by": "控制台"}
                elif h < 0:
                    d["full"] = {"until": -1, "since": time.time(), "by": "控制台"}
                else:
                    d.pop("full", None)
                save_control(d)
                control._c["m"] = None
                return {"ok": True, "full": control.full_state(), "text": control.full_left_text()}
            elif kind in ("blocked", "bots"):
                uid = str(b.get("id") or "").strip()
                if not uid.isdigit():
                    return {"ok": False, "error": "QQ 号不对"}
                if b.get("remove"):
                    d[kind].pop(uid, None)
                    if kind == "bots":             # 取消了 AI 标记的 = 真人：以后别再自动标（aidetect.py）
                        d.setdefault("humans", {})[uid] = "控制台里取消了 AI 标记 " + time.strftime("%m-%d %H:%M")
                else:
                    d[kind][uid] = str(b.get("note") or "")[:40]
                    if kind == "bots":
                        d.setdefault("humans", {}).pop(uid, None)
            else:
                return {"ok": False, "error": "不认识的设置"}
            save_control(d)
            control._c["m"] = None
            return {"ok": True}
        if u == "/api/send":
            r = bot_api("send", {k: b.get(k) for k in ("scene", "target", "text", "sticker", "voice", "emo", "remember")},
                        timeout=60)
            return r if r is not None else {"ok": False, "error": "机器人没开 发不了（先在总览里启动）"}
        if u == "/api/memory/forget":
            return memory_edit("forget", b.get("key"), id=b.get("id"))
        if u == "/api/memory/add":
            return memory_edit("add_fact", b.get("key"), text=b.get("text"), imp=b.get("imp") or 4)
        if u == "/api/favor":
            return favor_set(str(b.get("uid")), int(b.get("favor")))
        if u == "/api/wipe":
            return wipe_person(str(b.get("uid") or ""))
        if u == "/api/persona":
            text = b.get("text")
            if not isinstance(text, str) or len(text) < 50:
                return {"ok": False, "error": "人设太短了 没保存（防止误删）"}
            f = ROOT / (cfg().get("persona_file") or "persona.md")
            with _lock:
                backup(f, "persona")
                f.write_text(text, encoding="utf-8")
            return {"ok": True}
        if u == "/api/stickers/toggle":
            return {"ok": sticker_toggle(str(b.get("name")), bool(b.get("off")))}
        if u == "/api/tts":
            r = bot_api("tts", {"text": b.get("text"), "emo": b.get("emo")}, timeout=60)
            return r if r is not None else {"ok": False, "error": "机器人没开 试不了音"}
        if u == "/api/prices":
            with _lock:
                c = cfg()
                P = c.setdefault("panel", {})
                migrate_prices(P)
                p = P.setdefault("prices", dict(DEFAULT_PRICES))
                for k, v in (b.get("prices") or {}).items():
                    if k in DEFAULT_PRICES and not k.startswith("_"):
                        try:
                            p[k] = float(v)
                        except (TypeError, ValueError):
                            pass
                backup(CONFIG, "config")
                jwrite(CONFIG, c)
            return {"ok": True}
        if u == "/api/songs/save":
            return song_save(b)
        if u == "/api/songs/upload":
            return song_upload(b)
        if u == "/api/songs/import":
            return song_import(b)
        if u == "/api/songs/preview":
            return song_preview(b)
        if u == "/api/songs/delete":
            with _lock:
                ok = sing.remove(str(b.get("id") or ""))
            return {"ok": ok} if ok else {"ok": False, "error": "没有这首歌"}
        if u == "/api/tool":
            name = b.get("name")
            if name == "open_songs":
                sing.SONG_DIR.mkdir(parents=True, exist_ok=True)
            files = {"import_stickers": QQBOT / "导入表情包.bat", "install_voice": QQBOT / "安装语音.bat",
                     "open_logs": ROOT, "open_data": DATA, "open_backups": BACKUP, "open_songs": sing.SONG_DIR}
            f = files.get(name)
            if not f:
                return {"ok": False, "error": "不认识的工具"}
            if not f.exists():
                return {"ok": False, "error": f"找不到 {f}"}
            os.startfile(str(f))
            return {"ok": True, "msg": "已经在电脑上打开了"}
        return {"ok": False, "error": "没有这个操作"}


def ensure_token():
    """第一次用：在 config 里生成控制台和机器人之间的口令"""
    with _lock:
        c = cfg()
        P = c.setdefault("panel", {})
        changed = False
        for k, v in (("port", 8765), ("api_port", 8766)):
            if k not in P:
                P[k] = v
                changed = True
        if not P.get("token"):
            P["token"] = secrets.token_hex(16)
            changed = True
        if "prices" not in P:
            P["prices"] = dict(DEFAULT_PRICES)
            changed = True
        elif migrate_prices(P):               # 10-26：美元价 → 人民币峰谷价
            changed = True
        if changed:
            backup(CONFIG, "config")
            jwrite(CONFIG, c)
            return True
    return False


def _bind(port, url):
    """开网页服务。端口上已经有控制台了：同一个版本 → None（直接打开网页就行）；旧版本/卡住了 → 关掉旧的再开
    （Windows 上两个程序能同时占一个端口 所以不能靠「端口被占用」报错来判断 要先连一下看看）"""
    for _ in range(3):
        if not _listening(port):
            return ThreadingHTTPServer(("127.0.0.1", port), H)
        peer = _peer(port)
        if isinstance(peer, dict) and peer.get("version") != VERSION and peer.get("auto_reload"):
            for _ in range(10):          # 新版的控制台 自己会换成新代码 等它一下
                time.sleep(1)
                peer = _peer(port)
                if not isinstance(peer, dict) or peer.get("version") == VERSION:
                    break
        if isinstance(peer, dict) and peer.get("version") == VERSION:
            plog(f"控制台已经开着了（另一个黑窗口里）直接打开网页：{url}")
            return None
        why = {"old": "旧版本", None: "没响应"}.get(peer if not isinstance(peer, dict) else "x", "另一个版本")
        plog(f"[控制台] 发现一个{why}的控制台还开着 → 换成新版本（旧的黑窗口会显示 Control panel stopped，关掉它就行）")
        ok, msg = _takeover(port)
        plog(f"[控制台] {msg}")
        if not ok:
            return False
        time.sleep(0.5)
    return False


def serve(quiet=False, supervised=False):
    """真正的网页服务。返回退出码（RELOAD_RC = 代码更新了 要重起）"""
    _busy["supervised"] = supervised
    new = ensure_token()
    port = int(panel_cfg().get("port", 8765))
    url = f"http://127.0.0.1:{port}/"
    try:
        srv = _bind(port, url)
    except OSError as e:
        plog(f"[控制台] 开不了端口 {port}：{e}")
        srv = False
    if srv is None:
        webbrowser.open(url)
        return ALREADY_RC
    if srv is False:
        plog("  请先关掉旧的「FatFish control panel」黑窗口，再双击 C:\\QQBot\\控制台.bat")
        return GIVEUP_RC
    plog(f"[控制台] 网页服务开在 {url}（版本 {VERSION}{'，机器人帮你换的' if BY_BOT else ''}）")
    if not quiet:
        print("=" * 52)
        print(f"  大肥鱼控制台：{url}")
        print("  只有这台电脑能打开。关掉这个窗口 = 关掉控制台（机器人不受影响）")
        if supervised:
            print("  控制台的代码更新了会自己换成新版本 不用关这个窗口")
        if new:
            print("  第一次用：已经生成了控制台口令。重启一次机器人 控制台才能操作它")
        print("=" * 52)
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    else:
        print(time.strftime("[%H:%M:%S]") + f" [控制台] 新版本开好了（{VERSION}）"
              + ("：机器人发现你开着的控制台是旧版本 帮你换成了这个（旧的关掉了）这个窗口别关" if BY_BOT else ""))
    if supervised:
        threading.Thread(target=_watch, args=(srv, watched_files()), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        srv.server_close()
    return RELOAD_RC if _busy["reload"] else 0


def _py_sigs():
    return {p.name: _sig(p) for p in ROOT.glob("*.py")}


def supervise(extra):
    """管家：一直用最新的代码起网页服务（子进程）；子进程说代码更新了（退出码 3）就马上再起一个
    --no-browser：第一次也不开浏览器（机器人帮忙换控制台时用）"""
    me = str((ROOT / "panel.py").resolve())
    first, fails = True, []
    quiet_first = "--no-browser" in extra
    by_bot = "--by-bot" in extra
    extra = [a for a in extra if a not in ("--no-browser", "--by-bot")]
    plog("[控制台] 管家起来了（" + ("机器人发现控制台是旧版本 帮忙换的" if by_bot else "双击 控制台.bat") + f"；{_whoami()}）")
    while True:
        cmd = [sys.executable, me, "--serve"] + ([] if first and not quiet_first else ["--quiet"]) + list(extra) \
            + (["--by-bot"] if first and by_bot else [])
        try:
            rc = subprocess.call(cmd, cwd=str(ROOT))
        except KeyboardInterrupt:
            return 0
        except OSError as e:
            plog(f"[控制台] 起不了子进程（{e}）→ 这次不自动更新 直接在这个窗口里跑")
            return serve(quiet=not first)
        if rc == RELOAD_RC:
            first, fails = False, []
            continue
        if rc in (0, ALREADY_RC):
            return 0
        if rc == GIVEUP_RC:
            return 1
        fails = [t for t in fails if time.time() - t < 120] + [time.time()]
        plog(time.strftime("[%H:%M:%S]") + f" [控制台] 网页服务出错退出了（退出码 {rc}）")
        if len(fails) >= 3:
            plog("  连着出错了 等代码改好了再自动试（也可以关掉这个窗口 再双击 控制台.bat）")
            sig = _py_sigs()
            try:
                while _py_sigs() == sig:
                    time.sleep(3)
            except KeyboardInterrupt:
                return 1
        else:
            time.sleep(3)
        first = False


BY_BOT = "--by-bot" in sys.argv


def main():
    args = sys.argv[1:]
    if "--serve" in args:
        sys.exit(serve(quiet="--quiet" in args, supervised=True))
    if "--plain" in args:                       # 不要管家（调试用）
        sys.exit(serve())
    sys.exit(supervise([a for a in args if a not in ("--serve", "--quiet", "--plain")]))


if __name__ == "__main__":
    main()
