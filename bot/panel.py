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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import budget  # noqa: E402  （省 token 的曲线和机器人共用）
import stats  # noqa: E402  （DeepSeek 峰谷时段和机器人共用）
import control  # noqa: E402  （记忆/好感的改法和机器人共用）
import wipe  # noqa: E402  （删一个人的数据：机器人开着让它删 没开就直接删文件）
import sing  # noqa: E402  （她会唱的歌：songs 文件夹 + songs.json）
import clock  # noqa: E402  （北京时间 10-27：以后总时区以北京时间为准）
import commit  # noqa: E402  （说到做到 / 承诺表：控制台「日历」）
import memory  # noqa: E402  （遗忘曲线 10-27：每条记忆清不清楚、还能记多久）
import qzone  # noqa: E402  （QQ 空间 10-27 v1.3.0：控制台「QQ 空间」页）
import prank  # noqa: E402  （恶作剧 / 恶趣味 10-28 v1.10.0：控制台「恶趣味」页）
import safeio  # noqa: E402  （读改写数据文件：读坏了先另存 .bad、一时打不开就不改 10-28 审查 panel-1）

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
AVATAR = ROOT / "avatar.png"          # 她的头像（有就显示在控制台上；公开版没有 → 显示默认的鱼）
try:
    from version import __version__ as APP_VERSION   # 发布用的版本号（控制台左下角显示）
except Exception:
    APP_VERSION = ""
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
        return json.loads(Path(p).read_bytes().decode("utf-8-sig"))     # 记事本存的「UTF-8 带 BOM」也认（10-28 审查 panel-1）
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
    dst = BACKUP / f"{tag}_{clock.strf('%Y%m%d_%H%M%S')}{p.suffix}"
    dst.write_bytes(p.read_bytes())
    old = sorted(BACKUP.glob(f"{tag}_*{p.suffix}"))
    for x in old[:-30]:
        try:
            x.unlink()
        except Exception:
            pass


def cfg():
    return jread(CONFIG, {}) or {}


class ConfigBroken(ValueError):
    """config.json 读不出来（手改多打了个逗号 / 一直打不开）：要改了再写回的地方别把它当成空的（10-28 审查 panel-1）"""


def cfg_strict():
    """要改 config.json 再写回的地方（还有设置页）用：没有这个文件 → {}；读不出来 → 抛 ConfigBroken（说清楚第几行）
    cfg() 读坏了当成 {}：以前在 {} 上补几项再写回 → key、老板 QQ、所有设置一下全没了、口令也换了（10-28 审查 panel-1）"""
    raw = None
    for i in range(6):
        try:
            raw = CONFIG.read_bytes()
            break
        except FileNotFoundError:
            return {}
        except OSError as e:                   # Windows：正被别的程序占着 等一下再读
            if i == 5:
                raise ConfigBroken(f"config.json 打不开（{e.strerror or e}）等一下再试") from e
            time.sleep(0.05 * (i + 1))
    try:
        c = json.loads(raw.decode("utf-8-sig"))           # 记事本 / PowerShell 存的「UTF-8 带 BOM」也认
    except UnicodeDecodeError as e:
        raise ConfigBroken("config.json 不是 UTF-8 编码（另存为时编码选 UTF-8）先修好") from e
    except json.JSONDecodeError as e:
        raise ConfigBroken(f"config.json 第 {e.lineno} 行读不出来（{e.msg}）先修好") from e
    if not isinstance(c, dict):
        raise ConfigBroken("config.json 最外面不是 { } 先修好")
    return c


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
    host, port = napcat_addr()
    local = {"127.0.0.1", "localhost", "::1", "[::1]"}
    want = local if host.lower() in local else {host.lower()}
    if host.lower() not in local and not re.fullmatch(r"[\d.]+", host):    # 写的是主机名：netstat 只给数字地址 → 先解析（解析不出来就只比端口）
        try:
            want |= {ai[4][0] for ai in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)}
        except OSError:
            want = None
    me = str(os.getpid())
    v = []
    try:
        for line in run_cmd(["netstat", "-ano", "-p", "TCP"], timeout=10)[1].splitlines():
            p = line.split()
            if len(p) < 5 or p[0].upper() != "TCP" or not p[-1].isdigit() or p[-1] in ("0", me) or "TIME_WAIT" in line:
                continue
            rhost, _, rport = p[2].rpartition(":")
            # 对端要是 NapCat 那个地址 + 端口（10-28 Gemini 复查 #24：以前只比端口 —— 浏览器开着局域网里别的 3001 端口的网页
            # 控制台就说「机器人卡住了」、不让启动）；控制台自己不算；状态那一列各语言不一样 不按 ESTABLISHED 认
            if rport == str(port) and (want is None or rhost.lower().strip("[]") in {w.strip("[]") for w in want}):
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
    """[{path, value}] → 写回 config.json（类型要和原来的一致；先备份）
    config.json 读不出来 → 抛 ConfigBroken 一项都不改（10-28 审查 panel-1：以前当成空的改完写回 整份 config 只剩改的那一项）"""
    with _lock:
        c = cfg_strict()
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


# ───────────── 日历：承诺表（说到做到 10-27） ─────────────
def tasks_view():
    """机器人开着：问她（她手里的最新）；没开：直接读 data/commit.json"""
    r = bot_api("tasks", timeout=8)
    if isinstance(r, dict) and r.get("ok"):
        r["online"] = True
        return r
    with _lock:
        commit.reload()
        v = commit.tasks_view()
    v.update(ok=True, online=False)
    return v


def task_action(b):
    """打勾 / 取消 / 加 / 改：机器人开着让她改（免得跟她手里的冲突）；没开就直接改文件"""
    act = str(b.get("action") or "")
    if act not in ("done", "cancel", "add", "edit"):
        return {"ok": False, "error": "不认识的操作"}
    params = {k: b.get(k) for k in ("n", "id", "key", "scene", "target", "who", "who_name", "what", "at", "ts",
                                    "repeat", "via") if b.get(k) not in (None, "")}
    r = bot_api("task_" + act, params, timeout=10)
    if r is not None:
        return r
    with _lock:
        commit.reload()
        why = ""
        if act == "done":
            t = commit.mark_done(params.get("n") or params.get("id"), "控制台", plog)
        elif act == "cancel":
            t0 = commit.find_task(params.get("n") or params.get("id"))
            t = commit.cancel_task(t0, "控制台", plog) if t0 and t0.get("status") == "pending" else None
        elif act == "add":
            t, why = commit.panel_add(params, plog)
        else:
            t = commit.panel_edit(params, plog)
        commit.reload()
    return {"ok": bool(t), "task": t, "offline": True, "error": why or ("" if t else "没找到这件事（或者已经做完 / 取消了）")}


# ───────────── 群和人 ─────────────
def control_data():
    """只读来显示（读坏了当成空的）；要改 control.json 走 control._edit（10-28 审查 panel-1）"""
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
    _fg_cfg()                                  # 按 config 的半衰期算（10-28 审查 panel-6）
    cache = jread(CACHE, {}) or {}
    out = []
    for p in MEM_DIR.glob("*.json"):
        m = jread(p, {}) or {}
        facts = [f for f in m.get("facts") or [] if isinstance(f, dict) and f.get("status") == "active"]
        ov = {}
        if _fg_on():
            try:
                ov = memory.overview(m)
            except Exception:
                ov = {}
        out.append({"key": p.stem, "type": m.get("type") or p.stem.split("_")[0], "name": conv_name(p.stem, m, cache),
                    "facts": len(facts), "history": len(m.get("history") or []), "last": m.get("last_active") or 0,
                    "closeness": (m.get("rel") or {}).get("closeness"), "fuzzy": ov.get("fuzzy", 0),
                    "faint": ov.get("faint", 0), "faded": ov.get("faded", 0)})
    out.sort(key=lambda x: -(x["last"] or 0))
    return out


def memory_detail(key, n=60):
    if not re.fullmatch(r"(private|group)_\d+", key or ""):
        return None
    m = jread(MEM_DIR / f"{key}.json")
    if not m:
        return None
    _fg_cfg()                                  # 按 config 的半衰期算（10-28 审查 panel-6）
    cache = jread(CACHE, {}) or {}
    facts = [f for f in m.get("facts") or [] if isinstance(f, dict)]
    now = time.time()
    faded = [f for f in facts if f.get("status") == "archived" and "淡忘" in str(f.get("why") or "")]
    inferred = [f for f in facts if f.get("status") == "archived" and "推断" in str(f.get("why") or "")]   # 10-27 记忆卫生
    sup = sorted([f for f in facts if f.get("status") == "superseded"], key=lambda f: -(f.get("superseded_ts") or 0))
    tab = []
    if key.startswith("private_"):
        u = (jread(FAVOR, {}) or {}).get(key[8:]) or {}
        tab = [x for x in (u.get("taboo") or []) if isinstance(x, dict) and x.get("text")]
    return {"key": key, "name": conv_name(key, m, cache), "type": m.get("type"), "forget": _fg_on(),
            "inferred": [_fact_view(f, now) for f in sorted(inferred, key=lambda f: -(f.get("faded_ts") or 0))][:60],
            "superseded": [_fact_view(f, now) for f in sup[:20]], "taboos": tab,
            "facts": [_fact_view(f, now) for f in sorted([f for f in facts if f.get("status") == "active"],
                                                          key=lambda f: -(f.get("ts") or 0))],
            "faded": [_fact_view(f, now) for f in sorted(faded, key=lambda f: -(f.get("faded_ts") or 0))][:40],
            "old_facts": len([f for f in facts if f.get("status") != "active"]) - len(faded) - len(inferred),
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
        busy = list(rep.get("busy") or [])
        try:                               # 说到做到 / 控制台规则里的他：在线删会删 离线这条路以前漏了（10-28 Gemini 第三轮复查）
            commit.reload()
            strong = rep["strong_names"] if "strong_names" in rep else (rep.get("names") or [])
            commit.forget_person(uid, strong, weak=set(rep.get("names") or []) - set(strong))
        except Exception as e:
            plog(f"[删数据] commit.json 里 {uid} 的记录没删掉: {e!r:.100}")
            busy.append("commit.json")
        try:
            control.forget_person(uid)
        except Exception as e:
            plog(f"[删数据] control.json 里 {uid} 的记录没删掉: {e!r:.100}")
            busy.append("control.json")
        if not prank.forget_person(uid):   # 恶作剧的记录 / 免整名单 / 等着整他的单（10-28 v1.10.0）
            busy.append("prank_state.json")
        if busy:
            rep["busy"] = busy
    text = wipe.describe(rep)
    if rep.get("busy"):                    # 一直打不开的文件里他的东西没删到（10-28 Gemini 第二轮复查：以前照样说删完了）
        text += f"；这几个这会儿打不开 没删到：{'、'.join(rep['busy'])[:120]}（等一下再删一次）"
    if rep.get("mark_fail"):
        text += "；删除时间没记上（wiped.json 打不开）：机器人重启补看消息时可能把他删之前的私聊又记回来 —— 等一下再删一次"
    return {"ok": True, "offline": True, "report": rep, "partial": bool(rep.get("busy") or rep.get("mark_fail")), "text": text}


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
        try:                               # 读坏了先另存 .bad；一时打不开就不改（10-28 审查 panel-1：以前当成空的写回 所有人的好感、外号全没）
            d = safeio.read_json(FAVOR, {}, plog, busy_raise=True) or {}
        except safeio.Busy:
            return {"ok": False, "error": "favor.json 正被别的程序占着 没改上（等一下再改）"}
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
    keys = {clock.strf("%Y-%m-%d %H", now - i * 3600) for i in range(wh)}
    u_all = sum(v for k, v in (s.get("hours") or {}).items() if k in keys)
    u = budget.usage(now, s.get("hours") or {}, wh)        # 控制台点过「从现在重新算」→ 只算点了以后花的（10-27）
    w = budget.curve(u, B.get("half_usd"), B.get("shape"), B.get("floor"))
    return {"enabled": bool(B.get("enabled", True)), "spent": round(u, 4), "will": round(w, 3),
            "reset": budget.reset_state(now, wh), "spent_all": round(u_all, 4),
            "half_usd": float(B.get("half_usd")), "shape": float(B.get("shape")), "floor": float(B.get("floor")),
            "window_hours": wh, "powers": powers, "full": control.full_state(),
            "full_default_hours": float((cfg().get("full_power") or {}).get("default_hours", 3))}


def _hour_ts(k):
    """stats 里的小时键「2026-10-27 22」→ 时间戳：键是机器人按 clock 的时区写的 这边按同一个时区换回去；认不出来 → None
    （10-28 审查 panel-3：网页以前按这台电脑的时区解析 再按北京时间显示 心情曲线整条往后挪 8 小时）"""
    try:
        d = datetime.strptime(str(k)[:13], "%Y-%m-%d %H")
    except ValueError:
        return None
    tz = getattr(clock, "_tz", None)
    return (d.replace(tzinfo=tz) if tz else d).timestamp()


def stats_view(days=14):
    s = jread(STATS, {}) or {}
    p = prices()
    all_days = s.get("days") or {}
    keys = [clock.strf("%Y-%m-%d", time.time() - i * 86400) for i in range(days - 1, -1, -1)]
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
    mood = [(k, v, _hour_ts(k)) for k, v in sorted((s.get("mood") or {}).items()) if k[:10] >= cut]   # 第三个 = 时间戳（panel-3）
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
            d = days.get(clock.strf("%Y-%m-%d", time.time() - i * 86400)) or {}
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
        k = clock.strf("%Y-%m-%d", time.time() - i * 86400)
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
    today = (st.get("days") or {}).get(clock.strf("%Y-%m-%d")) or {}
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
def her_view(cons=()):
    """总览最上面那条「她现在怎么样」：名字、有没有头像、她这会儿在干嘛（按她的作息 / 时区）、正在兑现的约束"""
    c = cfg()
    out = {"name": str(c.get("bot_name") or "大肥鱼"), "card": str(c.get("group_card") or ""),
           "avatar": AVATAR.exists(), "tz": clock.TZ_LABEL or "电脑时间", "state": "", "how": "", "away": []}
    try:
        out["state"], out["how"] = clock.state()
    except Exception:
        pass
    try:
        cache = jread(CACHE, {}) or {}
        for r in cons or []:
            key = str(r.get("key") or "")
            if key.startswith("group_"):
                nm = ((cache.get("groups") or {}).get(key[6:]) or {}).get("name") or ""
                where = f"群「{nm}」" if nm else "群 " + key[6:]
            else:
                nm = (cache.get("friends") or {}).get(key[8:], "") if isinstance(cache.get("friends"), dict) else ""
                where = f"私聊「{nm}」" if isinstance(nm, str) and nm else "私聊 " + key[8:]
            out["away"].append({"where": where, "name": r.get("name") or "", "say": r.get("say") or "",
                                "left_min": int(r.get("left_min") or 0)})
        out["away"] = out["away"][:6]
    except Exception:
        pass
    return out


def spark_view(s=None, days=7):
    """总览数字卡片底下的小趋势线：最近 7 天（她的时区的天）每天的数 + 最近 24 小时每小时花费（美元）"""
    s = s if s is not None else (jread(STATS, {}) or {})
    p, now = prices(), time.time()
    keys = [clock.strf("%Y-%m-%d", now - i * 86400) for i in range(days - 1, -1, -1)]
    D = s.get("days") or {}

    def ser(*ks):
        return [sum(float((D.get(k) or {}).get(x) or 0) for x in ks) for k in keys]

    hrs = s.get("hours") or {}
    hk = [clock.strf("%Y-%m-%d %H", now - i * 3600) for i in range(23, -1, -1)]
    return {"days": [k[5:] for k in keys], "recv": ser("recv_private", "recv_group"),
            "replies": ser("replies_private", "replies_group"), "voice": ser("voice"), "stickers": ser("stickers"),
            "followups": ser("followups"),
            "cost": [round(float((cost_of(D.get(k) or {}, p) or {}).get("total") or 0), 5) for k in keys],
            "hours": [round(float(hrs.get(k) or 0), 6) for k in hk]}


def overview():
    ping = bot_api("status", timeout=4)
    st = {"bot": "offline", "napcat": napcat_up(), "qq": qq_status()}
    if ping and ping.get("ok"):
        st.update(bot="online", info=ping)
    elif ping is not None:
        st.update(bot="error", error=ping.get("error"))
    elif heartbeat().get("fresh") or bot_procs() or (st["napcat"] and ws_clients()):
        st.update(bot="stuck")
    today = clock.strf("%Y-%m-%d")
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
    st["recent"] = log_tail(14, ["接话", "表情包", "语音", "补回", "控制台", "冒泡", "节奏", "QQ", "启动", "错误", "说到做到"])
    cons = []
    try:
        with _lock:
            commit.reload()                    # 机器人写的 data/commit.json（控制台这边每次现读）
            st["tasks"] = commit.tasks_status()
            cons = commit.status()
    except Exception:
        st["tasks"] = None
    st["her"] = her_view(cons)
    st["spark"] = spark_view(s)
    st["usd_cny"] = float(prices().get("usd_cny") or 7.1)
    st["app_version"] = APP_VERSION
    st["has_token"] = bool(panel_cfg().get("token"))
    try:                                       # config.json 读不出来：总览上说一声（10-28 审查 panel-1）
        cfg_strict()
        st["config_error"] = ""
    except ConfigBroken as e:
        st["config_error"] = str(e)
    hb = heartbeat()
    st["boot"] = (st.get("info") or {}).get("boot") or (hb.get("boot") if hb.get("fresh") else None)
    try:
        st["quick"] = quick_view(hb, st.get("info"))
        st["remembered"] = latest_memory()
    except Exception as e:
        st["quick"], st["remembered"] = None, None
        plog(f"总览：快捷操作 / 刚记住的 读不出来 {e!r}"[:200])
    return st


# ───────────── 遗忘曲线（10-27）：每条记忆清不清楚、快忘掉的、昨晚整理、做的梦 ─────────────
FORGET_FILE = DATA / "forget.json"
DREAMS_FILE = DATA / "dreams.json"
STATE_NAME = {"pinned": "钉住了", "clear": "清楚", "fuzzy": "模糊", "faint": "快想不起来"}


def _fg_on():
    return hasattr(memory, "clarity") and hasattr(memory, "FORGET") and not getattr(memory, "STUB", False)


def _fg_cfg():
    """半衰期 / 门槛按 config 里的 forget 来（机器人开机时也是这么配的）
    控制台是另一个进程 以前从来没配过 一直按默认的 2/5/14/45/120 天画图、给离线「取消钉住」「找回」的半衰期（10-28 审查 panel-6）"""
    if _fg_on() and hasattr(memory, "configure_forget"):
        try:
            memory.configure_forget(cfg().get("forget") or {})
        except Exception:
            pass
    if _fg_on() and hasattr(memory, "configure_reinforce"):      # 记忆强化的半衰期下限也按 config 算（10-28 v1.9.4）
        try:
            memory.configure_reinforce((cfg().get("memory2") or {}).get("reinforce") or {})
        except Exception:
            pass


def _fact_view(f, now):
    """一条记忆给网页看的样子：等级、清晰度、还能记多久"""
    out = {k: f.get(k) for k in ("id", "text", "kind", "key", "imp", "emo", "date", "ts", "hits", "manual", "rv", "src",
                                  "why", "faded_ts", "gist", "status", "who", "uid", "speaker", "supersedes", "by",
                                  "date_uncertain", "superseded_ts")}
    out["src_n"] = len(f["src"]) if isinstance(f.get("src"), list) else 0      # 10-27 记忆卫生：依据几条消息
    if not _fg_on():
        return out
    try:
        memory.ensure_level(f)
        out["lvl"] = memory.lvl(f)
        out["S"] = round(float(memory.half_life(f) if hasattr(memory, "half_life") else f.get("S") or 0), 1)   # 强化撑着的下限也算（10-28 v1.9.4）
        out["clarity"] = round(memory.clarity(f, now), 3)
        if hasattr(memory, "rn"):                   # 记忆强化（10-28 v1.9.4）：强化了几次、上次哪天
            out["rn"] = memory.rn(f)
            out["last_reinforce"] = f.get("last_reinforce") or ""
        out["state"] = memory.state(f, now)
        a, b = memory.days_left(f, now)
        out["to_fuzzy"], out["to_faint"] = (None if a is None else round(a, 1)), (None if b is None else round(b, 1))
        out["impression"] = memory.is_impression(f)
    except Exception:
        pass
    return out


def forget_view(soon_days=7):
    """控制台「遗忘曲线」页：所有对话加起来 清楚 / 模糊 / 快想不起来 / 钉住 各几条、几天内要变的、最近淡忘的、昨晚整理、梦"""
    _fg_cfg()                                  # 图例 / 曲线 / 表格按 config 的半衰期（10-28 审查 panel-6）
    now = time.time()
    cache = jread(CACHE, {}) or {}
    tot = {"pinned": 0, "clear": 0, "fuzzy": 0, "faint": 0, "faded": 0}
    soon, faded, levels = [], [], {}
    on = _fg_on()
    for p in MEM_DIR.glob("*.json"):
        m = jread(p, {}) or {}
        name = conv_name(p.stem, m, cache)
        for f in m.get("facts") or []:
            if not isinstance(f, dict):
                continue
            if f.get("status") == "archived" and "淡忘" in str(f.get("why") or ""):
                tot["faded"] += 1
                if now - float(f.get("faded_ts") or 0) < 14 * 86400:
                    faded.append(dict(_fact_view(f, now), conv=p.stem, conv_name=name))
                continue
            if f.get("status") != "active" or not on:
                continue
            v = _fact_view(f, now)
            tot[v.get("state", "clear")] = tot.get(v.get("state", "clear"), 0) + 1
            levels[v.get("lvl", 3)] = levels.get(v.get("lvl", 3), 0) + 1
            st = v.get("state")
            if (st == "clear" and v.get("to_fuzzy") is not None and v["to_fuzzy"] <= soon_days) or \
                    (st == "fuzzy" and v.get("to_faint") is not None and v["to_faint"] <= soon_days):
                soon.append(dict(v, conv=p.stem, conv_name=name))
    soon.sort(key=lambda v: (v["state"] != "fuzzy", v.get("to_faint") if v["state"] == "fuzzy" else v.get("to_fuzzy") or 0))
    faded.sort(key=lambda v: -(v.get("faded_ts") or 0))
    fl = jread(FORGET_FILE, {}) or {}
    last = fl.get("last") or None
    if last:                                   # 昨晚蒸馏出来的印象：从记忆文件里现读（删了数据的人就看不到了）
        gl = []
        for g in last.get("gists") or []:
            m = jread(MEM_DIR / f"{g.get('key')}.json", {}) or {}
            f = next((x for x in m.get("facts") or [] if isinstance(x, dict) and x.get("id") == g.get("id")), None)
            if f:
                gl.append({"conv": g.get("key"), "conv_name": conv_name(str(g.get("key")), m, cache), "n": g.get("n"),
                           "text": f.get("text"), "who": f.get("who") or "", "active": f.get("status") == "active"})
        last = {k: last.get(k) for k in ("ts", "day", "why", "archived", "calls", "secs")} | {"gists": gl}
    dr = (jread(DREAMS_FILE, {}) or {}).get("entries") or []
    FG = memory.FORGET if on else {}
    c = (cfg().get("forget") or {})
    hb = heartbeat()
    return {"ok": True, "on": on and bool(c.get("enabled", True)), "totals": tot, "levels": levels,
            "soon": soon[:60], "faded": faded[:60], "last": last, "runs": (fl.get("runs") or [])[-14:],
            "dreams": dr[-5:], "half_life": {str(k): v for k, v in (FG.get("half_life") or {}).items()},
            "fuzzy": FG.get("fuzzy", 0.5), "faint": FG.get("faint", 0.2), "night_hours": c.get("night_hours") or [5, 9],
            "consolidating": bool(hb.get("fresh") and hb.get("consolidating"))}


def memory_edit2(action, key, fid):
    """钉住 / 取消钉住 / 找回 / 现在忘：机器人开着让它改（它拿着记忆的锁）；没开就直接改文件"""
    if not re.fullmatch(r"(private|group)_\d+", key or "") or action not in ("pin", "unpin", "restore", "fade"):
        return {"ok": False, "error": "参数不对"}
    if not _fg_on():
        return {"ok": False, "error": "这一版没有遗忘曲线（公开空壳）"}
    r = bot_api("mem_" + action, {"key": key, "id": fid})
    if r is not None:
        return r
    _fg_cfg()                                  # 离线「取消钉住」「找回」按 config 的半衰期给 S（10-28 审查 panel-6）
    p = MEM_DIR / f"{key}.json"
    with _lock:
        m = jread(p)
        if not m:
            return {"ok": False, "error": "没有这个对话"}
        ok = control.mem_edit(m, action, fid)
        if ok:
            backup(p, "mem_" + key)
            jwrite(p, m)
        return {"ok": ok, "offline": True}


def taboo_del(uid, i):
    """控制台删一条禁忌（10-27 记忆卫生）：机器人开着让它删（好感度表它一直在写）；没开就直接改文件"""
    uid = str(uid or "")
    if not re.fullmatch(r"\d{5,12}", uid):
        return {"ok": False, "error": "QQ 号不对"}
    r = bot_api("taboo_del", {"uid": uid, "i": i})
    if r is not None:
        return r
    with _lock:
        d = jread(FAVOR, {}) or {}
        u = d.get(uid) or {}
        lst = [x for x in (u.get("taboo") or []) if isinstance(x, dict)]
        try:
            lst.pop(int(i))
        except (ValueError, IndexError, TypeError):
            return {"ok": False, "error": "没有这一条"}
        u["taboo"] = lst
        d[uid] = u
        backup(FAVOR, "favor")
        jwrite(FAVOR, d)
    return {"ok": True, "offline": True}


def latest_memory():
    """总览「她刚记住的一句」：所有对话里最新记下的一条（不算手动加的、蒸馏的印象另外标）"""
    cache = jread(CACHE, {}) or {}
    best = None
    for p in MEM_DIR.glob("*.json"):
        m = jread(p, {}) or {}
        for f in m.get("facts") or []:
            if isinstance(f, dict) and f.get("status") == "active" and not f.get("manual") and \
                    (best is None or float(f.get("ts") or 0) > best[0]):
                best = (float(f.get("ts") or 0), f, p.stem, m)
    if not best:
        return None
    ts, f, key, m = best
    return {"text": f.get("text"), "ts": ts, "conv": key, "conv_name": conv_name(key, m, cache),
            "kind": f.get("kind"), "impression": bool(str(f.get("key") or "").endswith(".印象"))}


def quick_view(hb=None, info=None):
    """总览「快捷操作」那张卡片要显示的现状（info = 机器人接口刚回的 status：比心跳新）"""
    hb = hb if hb is not None else heartbeat()
    info = info or {}
    now = time.time()
    ctl = control.rules()
    aw = float((ctl.get("awake") or {}).get("until") or 0)
    fl = jread(FORGET_FILE, {}) or {}
    runs = fl.get("runs") or []
    try:
        vm, sm = voice_muted(), sing.muted_convs()
    except Exception:
        vm, sm = [], []
    return {"budget_reset": budget.reset_state(now, max(1, int((cfg().get("budget") or {}).get("window_hours") or 24))),
            "awake_until": aw if aw > now else 0, "voice_muted": len(vm), "sing_muted": len(sm),
            "brakes": int(info.get("brakes") or 0) if "brakes" in info else
            (int(hb.get("brakes") or 0) if hb.get("fresh") else None),
            "consolidating": bool(info.get("consolidating")) if "consolidating" in info else
            bool(hb.get("fresh") and hb.get("consolidating")),
            "last_run": runs[-1] if runs else None, "forget_on": _fg_on()}


def voice_muted():
    """voice.py 要 import 的东西多（语音合成）：控制台这边直接读状态文件"""
    s = jread(DATA / "voice_state.json", {}) or {}
    now = time.time()
    return [c for c, v in (s.get("conv") or {}).items() if isinstance(v, dict) and float(v.get("mute_until") or 0) > now]


# ───────────── QQ 空间（10-27 v1.3.0）─────────────
QZ_SRC = {"diary": "日记那句", "bili": "B站刷到的", "mood": "心情和日常", "group": "群里的趣事"}


def qzone_view():
    c = cfg().get("qzone") or {}
    qzone.configure(c)
    s = jread(qzone.STATE, {}) or {}
    fd = jread(qzone.FEED, {}) or {}
    favor = jread(FAVOR, {}) or {}
    cd = control_data()
    now = time.time()

    def nm(u, fallback=""):
        return str((favor.get(str(u)) or {}).get("name") or fallback or u)
    feed = []
    for x in sorted(fd.get("items") or [], key=lambda x: -float(x.get("read") or 0))[:40]:
        m = x.get("mention") or {}
        feed.append({"uin": x.get("uin"), "name": x.get("name") or nm(x.get("uin")), "text": str(x.get("text") or "")[:160],
                     "take": x.get("take", ""), "score": x.get("score", 0), "sensitive": bool(x.get("sensitive")),
                     "liked": bool(x.get("liked")), "commented": bool(x.get("commented")), "comment": x.get("comment", ""),
                     "ts": x.get("ts") or 0, "read": x.get("read") or 0, "src": x.get("src", "feed"),
                     "img": ((x.get("img_desc") or [""])[0] or "")[:50], "mp": bool(m.get("p")), "mg": len(m.get("g") or {})})
    mine = [{"tid": m.get("tid"), "text": m.get("text", ""), "ts": m.get("ts") or 0, "src": QZ_SRC.get(m.get("src"), m.get("src", "")),
             "deleted": bool(m.get("deleted")), "cmtnum": m.get("cmtnum", 0)} for m in reversed(s.get("mine") or [])][:20]
    replies = sorted([v for v in (s.get("replied") or {}).values() if isinstance(v, dict) and (v.get("text") or v.get("dry"))],
                     key=lambda v: -float(v.get("ts") or 0))[:20]
    replies = [{"name": v.get("name") or nm(v.get("uin")), "to": v.get("to", ""), "text": v.get("text") or v.get("dry"),
                "dry": bool(v.get("dry")), "ts": v.get("ts") or 0} for v in replies]
    visitors = [{"uin": v.get("uin"), "name": v.get("name") or nm(v.get("uin")), "ts": v.get("ts") or 0}
                for v in reversed(s.get("visitors") or [])][:20]

    def lst(k):
        return [{"uin": u, "name": nm(u, (v or {}).get("note") if isinstance(v, dict) else ""),
                 "t": (v or {}).get("t") if isinstance(v, dict) else None} for u, v in (cd.get(k) or {}).items()]
    day = (s.get("days") or {}).get(clock.strf("%Y-%m-%d")) or {}
    plan = s.get("post_plan") or {}
    keys = ("read_every_min", "like_daily", "comment_daily", "comment_min_favor", "post_daily", "post_prob", "reply_daily",
            "reply_rounds", "post_right")
    return {"ok": True, "on": qzone.on(), "cfg": {k: qzone.Q.get(k) for k in keys},
            "dry": {k: bool(v) for k, v in (qzone.Q.get("dry_run") or {}).items()},
            "today": {k: int(day.get(k, 0)) for k in ("read", "like", "comment", "post", "reply")},
            "paused": float(s.get("pause_until") or 0) > now, "pause_until": s.get("pause_until") or 0,
            "pause_why": s.get("pause_why", ""), "selftest": s.get("selftest") or {}, "last_read": s.get("last_read") or 0,
            "like_off": float(s.get("like_off_until") or 0) > now,
            "plan": {"day": plan.get("day"), "go": bool(plan.get("go")), "at": plan.get("at") or 0, "done": bool(plan.get("done"))},
            "events": list(reversed(s.get("events") or []))[:30], "feed": feed, "mine": mine, "replies": replies,
            "visitors": visitors, "off": lst("qzone_off"), "nogroup": lst("qzone_nogroup"),
            "dry_posts": list(reversed(s.get("dry_posts") or []))[:10]}


def prank_targets():
    """「恶趣味」页挑人：每个群最近说过话的人（新的在前）+ 私聊过的人"""
    cache = jread(CACHE, {}) or {}
    cache = cache if isinstance(cache, dict) else {}
    gs = cache.get("groups") if isinstance(cache.get("groups"), dict) else {}
    gnames = {k: (v.get("name") if isinstance(v, dict) else "") or "" for k, v in gs.items()}
    favor = jread(FAVOR, {}) or {}
    favor = favor if isinstance(favor, dict) else {}
    bots = set((control_data().get("bots") or {}).keys())
    groups, privs = [], []
    for p in sorted(MEM_DIR.glob("group_*.json")):
        gid = p.stem.split("_", 1)[1]
        m = jread(p, {}) or {}
        if not isinstance(m, dict):            # 文件里不是一个对象（坏了）：跳过 别让整页 500（复查）
            continue
        who = {}
        for h in reversed([x for x in (m.get("history") or []) if isinstance(x, dict)][-400:]):
            u = str(h.get("uid") or "")
            if h.get("role") == "user" and u and u not in who and len(who) < 40:
                who[u] = {"uid": u, "name": str(h.get("name") or u)[:24], "bot": u in bots}
        groups.append({"id": gid, "name": gnames.get(gid, ""), "last": m.get("last_active") or 0, "people": list(who.values())})
    for p in sorted(MEM_DIR.glob("private_*.json")):
        uid = p.stem.split("_", 1)[1]
        m = jread(p, {}) or {}
        if not isinstance(m, dict):
            continue
        fv = favor.get(uid) if isinstance(favor.get(uid), dict) else {}
        nm = fv.get("name") or next((h.get("name") for h in reversed(m.get("history") or [])
                                     if isinstance(h, dict) and h.get("role") == "user" and h.get("name")), "") or uid
        privs.append({"uid": uid, "name": str(nm)[:24], "last": m.get("last_active") or 0, "bot": uid in bots,
                      "owner": uid == str(cfg().get("owner_qq") or "")})
    groups.sort(key=lambda x: -(x["last"] or 0))
    privs.sort(key=lambda x: -(x["last"] or 0))
    return {"groups": groups, "privates": privs}


def prank_view():
    prank.configure(((cfg().get("humor") or {}).get("prank")) or {})
    r = bot_api("prank", {"op": "status"}, timeout=8)
    online = r is not None
    if not online:                         # 机器人没开：直接读文件（预览 / 发 / 撤回要机器人开着）
        r = prank.status()
    r["online"] = online
    r.update(prank_targets())
    return r


def prank_act(b):
    op = str(b.get("op") or "")
    prank.configure(((cfg().get("humor") or {}).get("prank")) or {})
    r = bot_api("prank", {k: v for k, v in b.items() if k != "token"}, timeout=150 if op in ("preview", "send") else 15)
    if r is not None:
        return r
    hb = jread(HEARTBEAT, {}) or {}
    if isinstance(hb, dict) and time.time() - float(hb.get("ts") or 0) < 60:   # 机器人开着 只是这会儿没回话（模型慢）：别自己改文件 跟它抢（复查）
        return {"ok": False, "error": "机器人开着但这会儿没回话（模型可能慢）—— 等一会儿刷新看看发没发 / 再点一次"}
    with _lock:                            # 机器人没开：改设置 / 玩法卡 / 免整名单 / 取消点单 直接改文件
        if op == "settings":
            return {"ok": prank.set_settings(b.get("auto"), b.get("level"))}
        if op == "cards_save":
            out = prank.save_cards(b.get("cards"))
            return {"ok": True, "cards": out} if out else {"ok": False, "error": "没存上（一张能用的卡都没有 / 文件打不开）"}
        if op == "no_prank":
            return {"ok": prank.no_prank(b.get("uid"), bool(b.get("on")))}
        if op == "cancel":
            return {"ok": prank.cancel_order(str(b.get("id") or ""))}
    return {"ok": False, "error": "机器人没开：预览 / 发 / 撤回 / 点单要机器人开着（先在总览里启动）"}


def qzone_act(b):
    what = str(b.get("what") or "")
    if what in ("unoff", "ungroup"):
        uid = str(b.get("uid") or "")
        k = "qzone_off" if what == "unoff" else "qzone_nogroup"
        try:                               # 走 control._edit：读坏了先另存 .bad、一时打不开就不改（10-28 审查 panel-1：以前当成空的整份写回）
            with _lock:
                control._edit(lambda d: (d.get(k) or {}).pop(uid, None))
        except safeio.Busy:
            return {"ok": False, "error": "control.json 正被别的程序占着 没改上（等一下再点）"}
        return {"ok": True, "msg": "撤销了（马上生效）"}
    if what not in ("read", "post", "reply", "visitors", "delete", "selftest", "resume"):
        return {"ok": False, "error": "不认识的操作"}
    r = bot_api("qzone", {"what": what, "tid": b.get("tid")}, timeout=150)
    if r is not None:
        return r
    if what == "resume":
        with _lock:
            try:                           # 读坏了先另存 .bad；一时打不开就不改（10-28 审查 panel-1：以前当成空的 写回只剩 pause_until）
                s = safeio.read_json(qzone.STATE, {}, plog, busy_raise=True) or {}
            except safeio.Busy:
                return {"ok": False, "error": "空间的状态文件正被别的程序占着 没改上（等一下再点）"}
            s["pause_until"], s["pause_why"] = 0, ""
            jwrite(qzone.STATE, s)
        return {"ok": True, "offline": True, "msg": "不歇了（机器人开起来就接着刷）"}
    return {"ok": False, "error": "机器人没开：空间要通过 QQ（NapCat）才能操作"}


def quick_action(b):
    """快捷操作：机器人开着就让它做（它那边马上生效）；没开 能直接改文件的就改文件"""
    what = str(b.get("what") or "")
    if what not in ("budget_reset", "budget_clear", "mood_reset", "wake", "wake_off", "unmute", "brakes_clear"):
        return {"ok": False, "error": "不认识的快捷操作"}
    r = bot_api("quick", {"what": what, "hours": b.get("hours")})
    if r is not None:
        return r
    if what == "brakes_clear":
        return {"ok": False, "error": "机器人没开：对聊刹车本来就不在了（只在机器人运行时有）"}
    with _lock:
        if what == "budget_reset":
            budget.reset_now("控制台", (jread(STATS, {}) or {}).get("hours") or {})
            return {"ok": True, "offline": True, "msg": "回复意愿从现在重新算了（机器人开了就生效）"}
        if what == "budget_clear":
            budget.reset_clear()
            return {"ok": True, "offline": True, "msg": "恢复按实际花费算了"}
        if what == "mood_reset":
            p = DATA / "mood2.json"
            try:                           # 一时打不开：照实说没改上（10-28 Gemini 第二轮复查：以前当成没有 回「心情回到平常了」）
                m2 = safeio.read_json(p, {}, plog, busy_raise=True) or {}
            except safeio.Busy:
                return {"ok": False, "error": "mood2.json 正被别的程序占着 没改上（等一下再点）"}
            if m2:
                base = float((cfg().get("mood2") or {}).get("baseline", 70))
                m2["mood"] = base
                m2["emos"] = {k: 0 for k in (m2.get("emos") or {})}
                m2["ts"] = time.time()
                m2["timeline"] = ((m2.get("timeline") or []) + [{"t": int(time.time()), "mood": round(base), "d": 0, "emo": "",
                                                                 "why": "控制台：心情回到平常", "conf": 100}])[-40:]
                jwrite(p, m2)
            return {"ok": True, "offline": True, "msg": "心情回到平常了"}
        if what in ("wake", "wake_off"):
            h = max(0.25, min(12.0, float(b.get("hours") or 1)))
            until = time.time() + h * 3600
            control._edit(lambda d: d.__setitem__("awake", {"until": until, "by": "控制台"}) if what == "wake"
                          else d.pop("awake", None))
            return {"ok": True, "offline": True, "msg": f"叫醒了（{h:g} 小时后接着睡）" if what == "wake" else "好 让她接着睡"}
        if what == "unmute":
            n = 0
            for fn in ("voice_state.json", "sing_state.json"):
                p = DATA / fn
                try:                       # 一时打不开：照实说没改上（10-28 Gemini 第二轮复查：以前说「放开了 0 个」）
                    s = safeio.read_json(p, {}, plog, busy_raise=True) or {}
                except safeio.Busy:
                    return {"ok": False, "error": f"{fn} 正被别的程序占着 没改上（等一下再点）" + (f"；已经放开了 {n} 个" if n else "")}
                k = 0
                for v in (s.get("conv") or {}).values():
                    if isinstance(v, dict) and v.pop("mute_until", None):
                        k += 1
                if k:
                    jwrite(p, s)
                n += k
            return {"ok": True, "offline": True, "msg": f"放开了 {n} 个聊天"}
    return {"ok": False, "error": "没做成"}


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
        if isinstance(d, safeio.Unsaved):  # songs.json 一时打不开：别拿空的存回去（10-28 Gemini 复查）
            return {"ok": False, "error": "songs.json 正被别的程序占着 没改上（等一下再改）"}
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
        try:
            e = sing.add_file(name, ext, raw, {"credit": str(b.get("credit") or "").strip()[:120]})
        except RuntimeError as ex:         # 歌单一时打不开（10-28 Gemini 复查）
            return {"ok": False, "error": str(ex)}
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
        line = clock.strf("%m-%d %H:%M:%S ") + f"[{os.getpid()}] " + str(msg).strip() + "\n"
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


def _cfg_tz():
    """config 里的时区（soul.timezone）；config 读不出来 → None"""
    try:
        return str((cfg_strict().get("soul") or {}).get("timezone") or "")
    except (ConfigBroken, AttributeError):
        return None


def _watch(srv, files, every=2.0):
    """代码文件改了（而且写完了、能编译）→ 等手上的请求做完 → 停掉服务 让管家用新代码重起
    config 里的时区改了（功能开关里改 / 手改）→ 一样重起一次：clock 只在 import 时读时区 不重起的话
    「今天收到 / 今天花了」和最近 24 小时的花费还按旧时区取键（10-28 审查 panel-7）
    只认这个控制台起来以后又改了的、而且跟 clock 正在用的不一样（刚起来就对不上的 不会一直重起）"""
    base = {f: _sig(f) for f in files}
    pending = bad = None
    csig, cpend, ctz = _sig(CONFIG), None, _cfg_tz()
    while True:
        time.sleep(every)
        cs = _sig(CONFIG)
        if cs != csig:
            if cs != cpend:            # 刚改：等它稳定一轮（「保存并重启机器人」紧跟着还有一个请求）
                cpend = cs
            else:
                csig, cpend, tz = cs, None, _cfg_tz()
                if tz is not None and tz != ctz:
                    ctz = tz
                    if tz != str(getattr(clock, "_TZ_NAME", "") or ""):
                        plog(clock.strf("[%H:%M:%S]") + f" [控制台] 时区改了（→ {tz or '跟电脑一样'}）→ 重起一次控制台 按新时区算")
                        break
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
        plog(clock.strf("[%H:%M:%S]") + " [控制台] 代码更新了 → 自动换成新版本（网页不用关 刷新一下就是新的）")
        break
    for _ in range(60):                # 最多等 15 秒 让正在处理的请求做完
        if _busy["n"] <= 0:
            break
        time.sleep(0.25)
    _busy["reload"] = True
    srv.shutdown()


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
                try:
                    return self._json({"ok": True, "config": mask(cfg_strict())})
                except ConfigBroken as e:          # 别给一份空的：设置页看着像全没了 一保存反而冲掉（10-28 审查 panel-1）
                    return self._json({"ok": False, "error": str(e), "config": {}})
            if u.path == "/api/people":
                return self._json(people())
            if u.path == "/api/tasks":
                return self._json(tasks_view())
            if u.path == "/api/memory":
                return self._json({"ok": True, "data": memory_list()})
            if u.path == "/api/qzone":
                return self._json(qzone_view())
            if u.path == "/api/prank":
                return self._json(prank_view())
            if u.path == "/api/memory/forget":
                return self._json(forget_view())
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
            if u.path == "/avatar":
                if not AVATAR.exists():
                    return self._json({"ok": False, "error": "没有头像"}, 404)
                return self._file(AVATAR, "image/png")
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
            try:
                done, bad = apply_changes(b.get("changes"))
            except ConfigBroken as e:              # 读不出来就一项都不存 告诉他哪一行坏了（10-28 审查 panel-1）
                return {"ok": False, "done": [], "bad": [], "error": f"{e}（这次一项都没存）"}
            return {"ok": bool(done) and not bad, "done": done, "bad": bad}
        if u == "/api/control":
            # 10-28 审查 panel-1：先查参数 再走 control._edit（读完马上改完写回；读坏了先另存 .bad、一时打不开就不改）
            # 以前 control_data() 读坏了 / 打不开就当成空的 再整份写回：别家机器人、拉黑、群管档位、叫醒、火力全开全丢
            kind = b.get("kind")
            gid, uid = str(b.get("id") or ""), str(b.get("id") or "").strip()
            if kind not in ("group", "self_off", "full", "blocked", "bots"):
                return {"ok": False, "error": "不认识的设置"}
            if kind == "group" and not gid.isdigit():
                return {"ok": False, "error": "群号不对"}
            if kind in ("self_off", "blocked", "bots") and not uid.isdigit():
                return {"ok": False, "error": "QQ 号不对"}
            h = 0.0
            if kind == "full":                 # 火力全开：hours>0 开几小时 / -1 一直开 / 0 关
                try:
                    h = float(b.get("hours") or 0)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "时长不对"}
            mins = float(b.get("mute_min") or 0) if kind == "group" and "mute_min" in b else None

            def edit(d):
                if kind == "group":
                    g = d.setdefault("groups", {}).setdefault(gid, {})
                    if "mode" in b:
                        g["mode"] = b["mode"] if b["mode"] in control.LEVELS else "normal"
                    if mins is not None:
                        g["mute_until"] = time.time() + mins * 60 if mins > 0 else 0
                    if b.get("clear_admin"):           # 撤销群管设的档位
                        g.pop("admin", None)
                    if b.get("clear_ban"):             # QQ 禁言记录（解了但没收到通知时 手动清）
                        old = g.pop("qq_ban", None)
                        if old:
                            g["qq_ban_last"] = {"t": old.get("t"), "lifted": time.time(), "whole": bool(old.get("whole"))}
                elif kind == "self_off":           # 群友「别接我的话」：提前放开
                    (d.get("self_off") or {}).pop(uid, None)
                elif kind == "full":
                    if h > 0:
                        d["full"] = {"until": time.time() + min(h, 72) * 3600, "since": time.time(), "by": "控制台"}
                    elif h < 0:
                        d["full"] = {"until": -1, "since": time.time(), "by": "控制台"}
                    else:
                        d.pop("full", None)
                elif b.get("remove"):              # blocked / bots
                    d.setdefault(kind, {}).pop(uid, None)
                    if kind == "bots":             # 取消了 AI 标记的 = 真人：以后别再自动标（aidetect.py）
                        d.setdefault("humans", {})[uid] = "控制台里取消了 AI 标记 " + clock.strf("%m-%d %H:%M")
                else:
                    d.setdefault(kind, {})[uid] = str(b.get("note") or "")[:40]
                    if kind == "bots":
                        d.setdefault("humans", {}).pop(uid, None)
            try:
                with _lock:
                    control._edit(edit)
            except safeio.Busy:
                return {"ok": False, "error": "control.json 正被别的程序占着 没改上（等一下再点）"}
            if kind == "full":
                return {"ok": True, "full": control.full_state(), "text": control.full_left_text()}
            return {"ok": True}
        if u == "/api/tasks":
            return task_action(b)
        if u == "/api/send":
            r = bot_api("send", {k: b.get(k) for k in ("scene", "target", "text", "sticker", "voice", "emo", "remember")},
                        timeout=60)
            return r if r is not None else {"ok": False, "error": "机器人没开 发不了（先在总览里启动）"}
        if u == "/api/memory/forget":
            return memory_edit("forget", b.get("key"), id=b.get("id"))
        if u == "/api/memory/add":
            return memory_edit("add_fact", b.get("key"), text=b.get("text"), imp=b.get("imp") or 4)
        if u in ("/api/memory/pin", "/api/memory/unpin", "/api/memory/restore", "/api/memory/fade"):
            return memory_edit2(u.rsplit("/", 1)[1], b.get("key"), str(b.get("id") or ""))
        if u == "/api/memory/taboo_del":
            return taboo_del(b.get("uid"), b.get("i"))
        if u == "/api/memory/consolidate":
            r = bot_api("consolidate", {"dream": False}, timeout=10)
            return r if r is not None else {"ok": False, "error": "机器人没开：整理要调模型 得机器人开着"}
        if u == "/api/quick":
            return quick_action(b)
        if u == "/api/qzone":
            return qzone_act(b)
        if u == "/api/prank":
            return prank_act(b)
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
                try:
                    c = cfg_strict()
                except ConfigBroken as e:          # 读不出来就别写回（10-28 审查 panel-1：以前写回后 config 只剩 panel 一段）
                    return {"ok": False, "error": f"{e}（价格没存）"}
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
            if ok is None:                 # 歌单一时打不开（10-28 Gemini 第二轮复查：以前说「没有这首歌」）
                return {"ok": False, "error": "songs.json 正被别的程序占着 没删（等一下再点）"}
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
    """第一次用：在 config 里生成控制台和机器人之间的口令
    config.json 读不出来 → 这次不补（10-28 审查 panel-1：以前当成空的补上口令再写回 整份 config 只剩 panel 一段、口令也换了）"""
    with _lock:
        try:
            c = cfg_strict()
        except ConfigBroken as e:
            plog(f"[控制台] {e}（这次不补口令、不写 config）")
            return False
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
        print(clock.strf("[%H:%M:%S]") + f" [控制台] 新版本开好了（{VERSION}）"
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
        plog(clock.strf("[%H:%M:%S]") + f" [控制台] 网页服务出错退出了（退出码 {rc}）")
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
