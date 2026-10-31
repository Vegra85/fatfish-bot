#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大肥鱼菜单（v1.12.0，10-29 小明 定：根目录十几个 .bat 太乱 → 合成一个菜单）

  双击 C:\\QQBot\\大肥鱼.bat → 选数字回车：
    1 启动 QQ 和机器人      2 停止 QQ 和机器人（要管理员）      3 重启机器人（QQ 不掉线）
    4 打开控制台            5 开机自启 / 修复启动（要管理员）
    6 安装…  首次设置、语音听写、本地 AI、视频支持、导入 / 整理表情包
    7 维护…  导出到 GitHub、打包群文件、跑测试、清理旧的 .bat、看状态
    8 肥鱼声音服务…  启动 / 停止 / 看状态（装了 C:\\QQBot\\feiyu_voice 才有这一项；10-30）
  Windows「智能应用控制」把 .bat 拦了 → 双击 C:\\QQBot\\tools\\fish.py，是同一个菜单（.py 由 Python 运行 不拦）
  要管理员的那几项自己弹「是否允许此应用对你的设备进行更改」，点「是」（菜单本身不用右键管理员运行）

  开机自启（计划任务 QQBot_Service）直接运行 pythonw.exe tools\\fish.py service —— 不再经过任何 .bat：
    service = 以前的 run.bat：关掉旧的 QQ / 机器人 → 起守护循环 → 照 NapCat 的 launcher.bat 一样起 QQ（输出接着写 napcat.log）
    loop    = 以前的 bot\\botloop.bat：bot.py 退出码 4 马上再起、3 不再起、有 data\\stop.flag 不再起、别的 15 秒后再起
  换成新的开机自启以后，以前那些 .bat 挪进 bot\\backup_20261024\\old_bats_<时间>\\（不删；里面有 放回去.txt）

命令行（控制台的按钮、首次设置向导也用这些）：
  python tools\\fish.py [start | stop | restart [--hard] | console | autostart [--yes | --no-restart] | status | clean
                         | setup | voice | localai | video | stickers | curate | export | pack | test | service | loop
                         | fyvoice [start | stop | status] | fyd [start | stop | status] [tts | cover | all]]
  --pause：做完停 3 秒再关窗口（出错等回车）—— 控制台点按钮时新开的窗口用
  FATFISH_ROOT=<目录>：换一个「C:\\QQBot」（测试用）
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape as _xesc

IS_WIN = os.name == "nt"
HERE = Path(__file__).resolve().parent
FISH = Path(__file__).resolve()
TASK = "QQBot_Service"
TUNA = "https://pypi.tuna.tsinghua.edu.cn/simple"
OLLAMA_MODEL = "qwen3-vl:8b-instruct-q8_0"
NO_WINDOW, NEW_GROUP, DETACHED, NEW_CONSOLE = 0x08000000, 0x00000200, 0x00000008, 0x00000010
HB_FRESH = 45                     # 心跳 45 秒内写过 = 机器人活着（和控制台一样）
PLACEHOLDER_QQ = "123456789"      # 以前公开版 run.bat 里的占位号码
QQ_REG_KEY = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ"   # NapCat 的 launcher.bat 也读这个
LOOP_DELAY = int(os.environ.get("FATFISH_LOOP_DELAY") or 15)   # 崩了多久再起（测试可以改小）
LEGACY = ["启动.bat", "停止.bat", "重启机器人.bat", "run.bat", "修复启动.bat", "开机自启.bat", "控制台.bat",
          "导出到GitHub.bat", "打包群文件.bat", "首次设置.bat", "安装语音.bat", "安装本地AI.bat", "安装视频支持.bat",
          "导入表情包.bat", "整理表情包.bat", "bot/botloop.bat", "tests/跑测试.bat"]   # v1.12.0 以前的 .bat（都由这个菜单代替）


def set_root(root):
    """「C:\\QQBot」在哪（测试可以换）"""
    global ROOT, BOT, DATA, NAPCAT, RUN_LOG, NAPCAT_LOG, BOT_LOG, STOP_FLAG, RESTART_REQ, HEARTBEAT, CLEAN_MARK, BACKUP
    ROOT = Path(root).resolve()
    BOT = ROOT / "bot"
    DATA = BOT / "data"
    NAPCAT = ROOT / "NapCat"
    RUN_LOG = ROOT / "run.log"
    NAPCAT_LOG = ROOT / "napcat.log"
    BOT_LOG = BOT / "bot.log"
    STOP_FLAG = DATA / "stop.flag"
    RESTART_REQ = DATA / "restart.request"
    HEARTBEAT = DATA / "heartbeat.json"
    CLEAN_MARK = DATA / "old_bats_cleaned.json"
    BACKUP = BOT / "backup_20261024"


set_root(os.environ.get("FATFISH_ROOT") or HERE.parent)


# ───────────── 输出 ─────────────
def say(s=""):
    print(s, flush=True)          # pythonw（开机自启）没有输出窗口：print 什么都不做


def rlog(msg):
    """开机自启那条路的记录：C:\\QQBot\\run.log（以前 run.bat 也写这里）"""
    try:
        with open(RUN_LOG, "a", encoding="utf-8") as f:
            for line in str(msg).rstrip().splitlines() or [""]:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def wait_enter(msg="按回车继续"):
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt):
        pass


def ask_yes(q, default=True):
    try:
        a = input(f"{q} {'[Y/n]' if default else '[y/N]'}：").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if not a:
        return default
    return a in ("y", "yes", "是", "好", "要", "嗯", "1")


def mask(qq):
    qq = str(qq or "")
    return qq[:3] + "…" + qq[-3:] if len(qq) > 6 else (qq or "（没有）")


def _sleep(s):
    time.sleep(s)


# ───────────── 小工具 ─────────────
def _dec(b):
    """Windows 命令的输出：UTF-16（schtasks /xml 有时是）/ UTF-8 / 中文系统的 GBK"""
    if not b:
        return ""
    if b[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(b) > 3 and b[1:2] == b"\x00" and b[3:4] == b"\x00"):
        try:
            return b.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "oem" if IS_WIN else "latin-1", "mbcs" if IS_WIN else "latin-1"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return b.decode("utf-8", "replace")


def run(args, timeout=30, cwd=None):
    """跑一个命令、不开窗口、收输出 → (退出码, 输出)；跑不了 → (-1, 原因)"""
    try:
        r = subprocess.run([str(a) for a in args], capture_output=True, timeout=timeout, cwd=cwd,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW if IS_WIN else 0)
        return r.returncode, _dec(r.stdout) + _dec(r.stderr)
    except subprocess.TimeoutExpired:
        return -1, f"{Path(str(args[0])).name} 超过 {timeout} 秒没结束"
    except OSError as e:
        return -1, f"{Path(str(args[0])).name} 运行不了：{e}"


def call(args, cwd=None):
    """在这个窗口里跑（要跟人对答的：pip、向导、装语音…）→ 退出码"""
    try:
        return subprocess.call([str(a) for a in args], cwd=str(cwd) if cwd else None)
    except OSError as e:
        say(f"  [不行] {Path(str(args[0])).name} 运行不了：{e}")
        return -1
    except KeyboardInterrupt:
        say("\n  停下了")
        return 1


def _exe(name):
    p = Path(sys.executable)
    q = p.with_name(name)
    return str(q) if q.exists() else str(p)


def py_console():
    """有窗口的 python.exe（机器人本体、要对答的都用它）"""
    return _exe("python.exe") if IS_WIN else sys.executable


def py_hidden():
    """没窗口的 pythonw.exe（开机自启、守护循环用）"""
    return _exe("pythonw.exe") if IS_WIN else sys.executable


def is_admin():
    if not IS_WIN:
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def version():
    try:
        m = re.search(r'__version__\s*=\s*"([^"]+)"', (BOT / "version.py").read_text(encoding="utf-8"))
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def jread(p, default=None):
    try:
        return json.loads(Path(p).read_bytes().decode("utf-8-sig"))
    except Exception:
        return default


def cfg():
    return jread(BOT / "config.json", {}) or {}


def port_open(host, port, timeout=0.6):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def napcat_addr():
    m = re.match(r"wss?://([^:/]+):(\d+)", str((cfg().get("onebot") or {}).get("url") or "ws://127.0.0.1:3001"))
    return (m.group(1), int(m.group(2))) if m else ("127.0.0.1", 3001)


def napcat_up():
    return port_open(*napcat_addr())


def heartbeat():
    """机器人的心跳（data\\heartbeat.json 每 15 秒写一次）+ alive"""
    h = jread(HEARTBEAT, {}) or {}
    if not isinstance(h, dict):
        h = {}
    try:
        h["alive"] = time.time() - float(h.get("ts") or 0) < HB_FRESH
    except (TypeError, ValueError):
        h["alive"] = False
    return h


def rm(p):
    try:
        Path(p).unlink()
    except OSError:
        pass


# ───────────── 机器人小号的 QQ 号 / 电脑版 QQ ─────────────
def _qq_ok(s):
    s = str(s or "").strip()
    return s if re.fullmatch(r"[1-9]\d{4,11}", s) and s != PLACEHOLDER_QQ else ""


def legacy_qq():
    """以前 run.bat 里写的机器人 QQ 号（launcher.bat 后面那个 / set BOT_QQ=）"""
    p = ROOT / "run.bat"
    try:
        t = p.read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""
    m = re.search(r"(?im)^set BOT_QQ=(\d{5,12})\s*$", t) or re.search(r"launcher(?:-win10)?\.bat\s+(\d{5,12})", t)
    return _qq_ok(m.group(1)) if m else ""


def napcat_qqs():
    """NapCat\\config 里登录过的号（onebot11_<号>.json），新的在前"""
    out = []
    try:
        fs = sorted((NAPCAT / "config").glob("onebot11_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        fs = []
    for f in fs:
        n = _qq_ok(f.stem.split("_", 1)[1])
        if n and n not in out:
            out.append(n)
    return out


def bot_qq():
    """NapCat 登哪个号：config.json 的 bot_qq → 以前 run.bat 里的 → NapCat 登录过的（最近的那个）→ ""（扫码登录）"""
    return _qq_ok(cfg().get("bot_qq")) or legacy_qq() or next(iter(napcat_qqs()), "")


def remember_bot_qq(log=say):
    """run.bat 要收走了：它里面的机器人 QQ 号记进 config.json 的 bot_qq（已经有了 / config 读不出来就不动）"""
    p = BOT / "config.json"
    n = legacy_qq()
    if not n or not p.exists():
        return False
    try:
        raw = p.read_bytes().decode("utf-8-sig")
        c = json.loads(raw)
    except (OSError, ValueError):
        log("  [注意] config.json 读不出来：机器人 QQ 号没记进去（NapCat 登录过的号照样能用）")
        return False
    if not isinstance(c, dict) or _qq_ok(c.get("bot_qq")):
        return False
    c["bot_qq"] = n
    tmp = p.with_name(p.name + ".fish.tmp")
    try:
        tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        rm(tmp)
        log(f"  [注意] config.json 写不进去（{e}）：机器人 QQ 号没记")
        return False
    log(f"  机器人 QQ 号 {mask(n)} 从 run.bat 记进了 config.json（bot_qq）")
    return True


def find_qq():
    """电脑版 QQ.exe 在哪（和 NapCat 的 launcher.bat 一样：注册表里卸载程序的那个文件夹）→ 路径 / ""（没装）"""
    if not IS_WIN:
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, QQ_REG_KEY) as k:
            un = str(winreg.QueryValueEx(k, "UninstallString")[0])
    except OSError:
        return ""
    return qq_from_uninstall(un)


def qq_from_uninstall(un):
    un = str(un or "").strip()
    if un.startswith('"'):
        un = un[1:].split('"', 1)[0]
    exe = Path(un).parent / "QQ.exe" if un else None
    return str(exe) if exe and exe.exists() else ""


# ───────────── NapCat（以前 run.bat 最后一行：call launcher.bat <号>） ─────────────
NAPCAT_FILES = ("NapCatWinBootMain.exe", "NapCatWinBootHook.dll", "qqnt.json", "loadNapCat.js", "napcat.mjs")


def napcat_cmd(uin, qq_exe=None):
    """怎么起 NapCat → (命令, 环境变量, 说明)。
    照 NapCat 自己的 launcher.bat 做（设那 5 个环境变量 → NapCatWinBootMain.exe <QQ.exe> <Hook.dll> <号>），
    不经过 .bat（智能应用控制拦 .bat）；NapCat 换了文件名（新版本）、找不到 QQ → 退回去调它的 launcher.bat"""
    extra = [uin] if uin else []
    qq_exe = find_qq() if qq_exe is None else qq_exe
    if all((NAPCAT / n).exists() for n in NAPCAT_FILES) and qq_exe:
        env = dict(os.environ)
        env.update(NAPCAT_PATCH_PACKAGE=str(NAPCAT / "qqnt.json"), NAPCAT_LOAD_PATH=str(NAPCAT / "loadNapCat.js"),
                   NAPCAT_INJECT_PATH=str(NAPCAT / "NapCatWinBootHook.dll"),
                   NAPCAT_LAUNCHER_PATH=str(NAPCAT / "NapCatWinBootMain.exe"), NAPCAT_MAIN_PATH=str(NAPCAT / "napcat.mjs"))
        return [str(NAPCAT / "NapCatWinBootMain.exe"), qq_exe, str(NAPCAT / "NapCatWinBootHook.dll")] + extra, env, "直接起"
    bat = NAPCAT / "launcher.bat"
    if not shutil.which("wt") and (NAPCAT / "launcher-win10.bat").exists():
        bat = NAPCAT / "launcher-win10.bat"      # 没有 Windows Terminal（多半是 Windows 10）
    why = "NapCat 文件名和以前不一样" if qq_exe else "注册表里没找到电脑版 QQ"
    return ["cmd", "/c", str(bat)] + extra, None, f"用 NapCat 的 {bat.name}（{why}）"


def napcat_run(uin):
    """起 NapCat（QQ）一直等到它退出 → 退出码"""
    args, env, how = napcat_cmd(uin)
    rlog(f"NapCat：{how}，QQ {mask(uin) if uin else '没写（扫码登录）'}")
    try:
        with open(NAPCAT_LOG, "ab") as f:
            p = subprocess.Popen(args, cwd=str(NAPCAT), env=env, stdin=subprocess.DEVNULL, stdout=f,
                                 stderr=subprocess.STDOUT, creationflags=NO_WINDOW if IS_WIN else 0)
            return p.wait()
    except OSError as e:
        rlog(f"NapCat 起不来：{e}")
        return -1


# ───────────── 进程（机器人、守护循环、QQ） ─────────────
def procs():
    """[{pid, ppid, name, cmd}]（不是管理员时 管理员开的进程看不到命令行）；PowerShell 不行 → 退回 wmic"""
    if not IS_WIN:
        return []
    ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine"
          " | ConvertTo-Json -Compress")
    rc, out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=60)
    res = parse_ps_json(out)
    if not res and shutil.which("wmic"):
        rc, out = run(["wmic", "process", "get", "CommandLine,Name,ParentProcessId,ProcessId", "/format:list"], timeout=60)
        res = parse_wmic_list(out)
    return res


def parse_ps_json(out):
    try:
        data = json.loads((out or "").strip() or "[]")
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    res = []
    for d in data if isinstance(data, list) else []:
        if isinstance(d, dict):
            try:
                res.append({"pid": int(d.get("ProcessId") or 0), "ppid": int(d.get("ParentProcessId") or 0),
                            "name": str(d.get("Name") or ""), "cmd": str(d.get("CommandLine") or "")})
            except (TypeError, ValueError):
                pass
    return res


def parse_wmic_list(out):
    """wmic … /format:list：一块一块的「CommandLine=…」「Name=…」「ParentProcessId=…」「ProcessId=…」"""
    res, cur = [], {}
    for line in (out or "").replace("\r", "").split("\n") + [""]:
        if not line.strip():
            if cur.get("ProcessId", "").isdigit():
                res.append({"pid": int(cur["ProcessId"]), "ppid": int(cur.get("ParentProcessId") or 0)
                            if str(cur.get("ParentProcessId") or "").isdigit() else 0,
                            "name": cur.get("Name", ""), "cmd": cur.get("CommandLine", "")})
            cur = {}
            continue
        k, sep, v = line.partition("=")
        if sep and k.strip() in ("CommandLine", "Name", "ParentProcessId", "ProcessId"):
            cur[k.strip()] = v.strip()
    return res


def kind(p):
    """这个进程是我们的哪一个：oldrun（旧 run.bat）/ oldloop（旧 botloop.bat）/ service / loop / bot / qq / napcat / ""（不是）"""
    name = str(p.get("name") or "").lower()
    c = " " + str(p.get("cmd") or "").lower().replace('"', " ").replace("'", " ") + " "
    if name == "cmd.exe":
        if "botloop.bat" in c:
            return "oldloop"
        if str(ROOT / "run.bat").lower() in c:
            return "oldrun"
        return ""
    if name.startswith("py"):
        if re.search(r"fish\.py\s+loop\s", c):
            return "loop"
        if re.search(r"fish\.py\s+service\s", c):
            return "service"
        if re.search(r"[\\/\s]bot\.py\s", c):
            return "bot"
        return ""
    if name == "qq.exe":
        return "qq"
    if name == "napcatwinbootmain.exe":
        return "napcat"
    return ""


ORDER = ["oldrun", "service", "oldloop", "loop", "bot", "napcat", "qq"]   # 先关会把别的再拉起来的


def kill_kinds(kinds, log=say):
    """关掉这几类进程（连子进程一起）→ [(pid, 类)]；自己和叫自己的那个不关"""
    mine = {os.getpid(), os.getppid()}
    ps = procs()
    if IS_WIN and not ps:
        log("  [注意] 看不到这台电脑的进程列表（PowerShell 和 wmic 都不行）：机器人 / 守护循环可能没关掉")
    hit = [(p["pid"], kind(p)) for p in ps if p["pid"] not in mine and kind(p) in kinds]
    hit.sort(key=lambda x: ORDER.index(x[1]) if x[1] in ORDER else 99)
    done = []
    for pid, k in hit:
        rc, out = run(["taskkill", "/f", "/t", "/pid", pid], timeout=20)
        done.append((pid, k))
    if done:
        log("  关掉了：" + "、".join(f"{NAMES.get(k, k)}（{pid}）" for pid, k in done))
    return done


NAMES = {"oldrun": "旧的 run.bat", "oldloop": "旧的守护循环 botloop.bat", "service": "开机自启那个进程", "loop": "守护循环",
         "bot": "机器人", "napcat": "NapCat", "qq": "QQ"}


def old_loop_state():
    """旧的 bot\\botloop.bat 还在不在跑：True / False / None（看不出来）"""
    if IS_WIN and is_admin():
        ps = procs()
        return any(kind(p) == "oldloop" for p in ps) if ps else None      # 看不到进程列表 → 说不准
    hb = heartbeat()
    if hb["alive"] and hb.get("loop_kind") == "fish":
        return False                     # 机器人是新的守护循环起的
    if hb["alive"] and hb.get("loop") and hb.get("loop_kind") in (None, "1"):
        return True                      # 旧循环起的（v1.12.0 以前的机器人不写 loop_kind）
    if not IS_WIN:
        return False
    ps = procs()
    if not ps:
        return None
    if any(kind(p) == "oldloop" for p in ps):
        return True
    if any(str(p.get("name")).lower() == "cmd.exe" and not p.get("cmd") for p in ps):
        return None                      # 有看不到命令行的 cmd（管理员开的）：说不准
    return False


def start_loop():
    """起守护循环（不开窗口；关掉菜单 / 控制台它也接着跑）"""
    args = [py_hidden(), str(FISH), "loop"]
    kw = dict(cwd=str(BOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if IS_WIN:
        kw["creationflags"] = DETACHED | NEW_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    env = dict(os.environ)
    env.pop("FATFISH_LOOP", None)
    return subprocess.Popen(args, env=env, **kw)


# ───────────── 计划任务 QQBot_Service（开机自启） ─────────────
def task_info():
    """{"exists": True/False/None, command, args, limit, delay}；None = 不是 Windows / 看不到"""
    if not IS_WIN:
        return {"exists": None}
    rc, out = run(["schtasks", "/query", "/tn", TASK, "/xml"], timeout=20)
    if rc != 0:
        low = out.lower()
        if "access" in low or "拒绝" in out:
            return {"exists": None, "err": out.strip()[:200]}
        return {"exists": False, "err": out.strip()[:200]}

    def tag(t):
        m = re.search(rf"<{t}>(.*?)</{t}>", out, re.S)
        return _unxml(m.group(1).strip()) if m else ""
    return {"exists": True, "command": tag("Command"), "args": tag("Arguments"), "limit": tag("ExecutionTimeLimit"),
            "delay": tag("Delay"), "workdir": tag("WorkingDirectory")}


def _unxml(s):
    return s.replace("&quot;", '"').replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def task_state(t=None):
    """"new"（tools\\fish.py）/ "old"（还是 run.bat）/ "other" / "none"（没建）/ "unknown"（看不到）"""
    t = task_info() if t is None else t
    if t.get("exists") is None:
        return "unknown"
    if not t.get("exists"):
        return "none"
    s = (t.get("command", "") + " " + t.get("args", "")).lower()
    if "fish.py" in s and "service" in s:
        return "new"
    if "run.bat" in s:
        return "old"
    return "other"


def task_user():
    d, u = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{d}\\{u}" if d and u else u


def task_xml(user, pyw, fish, root):
    """和以前 开机自启.bat 建的一样（登录 1 分钟后、最高权限、不限时长、用电池也跑、已经在跑就不再起一个）
    只是启动的东西从 run.bat 换成 pythonw.exe tools\\fish.py service"""
    e = _xesc
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>FatFish: start QQ (NapCat) and the bot 1 minute after logon (tools\\fish.py service)</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{e(user)}</UserId>
      <Delay>PT1M</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{e(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{e(str(pyw))}</Command>
      <Arguments>{e(subprocess.list2cmdline([str(fish), "service"]))}</Arguments>
      <WorkingDirectory>{e(str(root))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def register_task(log=say):
    """建 / 重建 QQBot_Service（要管理员）→ (成没成, 怎么建的 / 为什么不成)"""
    user, pyw = task_user(), py_hidden()
    fails = []
    tmp = Path(tempfile.gettempdir()) / f"fatfish_task_{os.getpid()}.xml"
    try:
        tmp.write_text(task_xml(user, pyw, FISH, ROOT), encoding="utf-16")
        rc, out = run(["schtasks", "/create", "/tn", TASK, "/xml", tmp, "/f"], timeout=60)
    except OSError as e:
        rc, out = -1, str(e)
    finally:
        rm(tmp)
    if rc == 0 and task_state() == "new":
        return True, "计划任务"
    fails.append("schtasks /xml：" + out.strip()[:160])
    # 退路 1：PowerShell（和以前 开机自启.bat 一样的写法；智能应用控制下 PowerShell 可能是受限模式 不一定行）
    ps = (f"$u = {_ps_quote(user)}; "
          f"$a = New-ScheduledTaskAction -Execute {_ps_quote(pyw)} "
          f"-Argument {_ps_quote(subprocess.list2cmdline([str(FISH), 'service']))} -WorkingDirectory {_ps_quote(ROOT)}; "
          "$t = New-ScheduledTaskTrigger -AtLogOn -User $u; $t.Delay = 'PT1M'; "
          "$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries "
          "-DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew; "
          "$p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Highest; "
          f"Register-ScheduledTask -TaskName {_ps_quote(TASK)} -Action $a -Trigger $t -Settings $s -Principal $p "
          "-Force -ErrorAction Stop | Out-Null")
    rc, out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], timeout=90)
    if rc == 0 and task_state() == "new":
        return True, "PowerShell"
    fails.append("PowerShell：" + out.strip()[:160])
    # 退路 2：schtasks 简单版（Windows 默认跑满 3 天会停掉）
    tr = subprocess.list2cmdline([pyw, str(FISH), "service"])
    rc, out = run(["schtasks", "/create", "/tn", TASK, "/tr", tr, "/sc", "onlogon", "/delay", "0001:00",
                   "/rl", "highest", "/f"], timeout=60)
    if rc == 0 and task_state() == "new":
        return True, "简单版（注意：Windows 默认跑满 3 天会停掉它 —— 到时候再选一次「开机自启 / 修复启动」）"
    fails.append("schtasks：" + out.strip()[:160])
    return False, "；".join(fails)


# ───────────── 以前的 .bat 收走 ─────────────
def legacy_left():
    return [r for r in LEGACY if (ROOT / r).exists()]


def clean_legacy(log=say):
    """以前的 .bat 挪进 bot\\backup_20261024\\old_bats_<时间>\\（不删）→ (挪了的, {留着的: 为什么})
    开机自启还指着 run.bat → run.bat 先留着；旧的守护循环还在跑（它边跑边读 botloop.bat）→ botloop.bat 先留着"""
    have = legacy_left()
    keep = {}
    if "run.bat" in have:
        st = task_state()
        if st in ("old", "unknown"):
            keep["run.bat"] = "开机自启还在用它（先选「开机自启 / 修复启动」）" if st == "old" else "看不到开机自启任务指着谁"
    if "bot/botloop.bat" in have:
        s = old_loop_state()
        if s is not False:
            keep["bot/botloop.bat"] = "旧的守护循环还在用它（重启一次 QQ 和机器人以后再收）" if s else \
                "看不出旧的守护循环还在不在跑（要管理员）"
    if "run.bat" in have and "run.bat" not in keep:
        remember_bot_qq(log)               # 它里面的机器人 QQ 号先记进 config
    moved = []
    dest = BACKUP / ("old_bats_" + time.strftime("%Y%m%d_%H%M%S"))
    n = 2
    while dest.exists():
        dest = dest.with_name(dest.name.split("~")[0] + f"~{n}")
        n += 1
    for r in have:
        if r in keep:
            continue
        dst = dest / r
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(ROOT / r, dst)
            moved.append(r)
        except OSError as e:
            keep[r] = f"挪不动（{e.strerror or e}）"
    if moved:
        lines = ["v1.12.0 以前的 .bat（10-29 合成了一个菜单：C:\\QQBot\\大肥鱼.bat，被拦了就双击 C:\\QQBot\\tools\\fish.py）",
                 "",
                 "想放回去：把这个文件夹里的文件按原来的位置拷回 C:\\QQBot（bot\\botloop.bat 回 C:\\QQBot\\bot\\，tests\\跑测试.bat 回 C:\\QQBot\\tests\\）。",
                 "注意：开机自启现在走的是 tools\\fish.py；要换回 run.bat，放回去以后右键 开机自启.bat →「以管理员身份运行」。",
                 "",
                 "挪过来的："] + ["  " + r.replace("/", "\\") for r in moved]
        try:
            (dest / "放回去.txt").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8-sig")
        except OSError:
            pass
        log(f"  以前的 .bat 收走了 {len(moved)} 个 → {dest}")
    for r, why in keep.items():
        log(f"  {r.replace('/', chr(92))} 先留着：{why}")
    if not legacy_left():
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            CLEAN_MARK.write_text(json.dumps({"t": time.time(), "dest": str(dest) if moved else ""},
                                             ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return moved, keep


# ───────────── 管理员 ─────────────
def elevate(args, wait=True):
    """用管理员权限再开一个窗口跑「fish.py <args> --elevated」（弹「是否允许」）→ (弹出来了没, 退出码 / None)"""
    if not IS_WIN:
        return False, None
    import ctypes
    from ctypes import wintypes
    exe, params, cwd = py_console(), subprocess.list2cmdline([str(FISH)] + list(args) + ["--elevated"]), str(ROOT)

    class SEI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SEI)]
        shell32.ShellExecuteExW.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        sei = SEI()
        sei.cbSize = ctypes.sizeof(SEI)
        sei.fMask = 0x00000040                  # SEE_MASK_NOCLOSEPROCESS：拿到进程 等它做完
        sei.lpVerb, sei.lpFile, sei.lpParameters, sei.lpDirectory, sei.nShow = "runas", exe, params, cwd, 1
        if not shell32.ShellExecuteExW(ctypes.byref(sei)):
            return False, None                  # 1223 = 点了「否」
        if not sei.hProcess:
            return True, None
        try:
            if not wait:
                return True, None
            kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
            return True, int(code.value)
        finally:
            kernel32.CloseHandle(sei.hProcess)
    except Exception:
        try:                                    # 退路：老办法（不等它）
            return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, cwd, 1) > 32, None
        except Exception:
            return False, None


def need_admin(args, what):
    """这一步要管理员：弹「是否允许」→ 在管理员窗口里做完 → 退出码"""
    if "--elevated" in sys.argv:
        say(f"  [不行] {what}要管理员权限，但还是拿不到（右键 大肥鱼.bat →「以管理员身份运行」再试）")
        return 1
    say(f"  {what}要管理员权限：电脑上会弹一个「是否允许此应用对你的设备进行更改」→ 点「是」")
    ok, rc = elevate(args)
    if not ok:
        say("  没拿到管理员权限（是不是点了「否」？）这次什么都没做")
        return 1
    if rc is None:
        say("  在新开的管理员窗口里做（看那个窗口）")
        return 0
    say("  做完了" if rc == 0 else f"  没做成（退出码 {rc}）—— 看刚才那个管理员窗口里写的")
    return rc


# ───────────── 各个命令 ─────────────
def wait_for(cond, secs, step=2, dots=True):
    end = time.time() + secs
    n = 0
    while time.time() < end:
        if cond():
            if n and dots:
                say("")
            return True
        _sleep(step)
        n += 1
        if dots and n % 3 == 0:
            print(".", end="", flush=True)
    if n and dots:
        say("")
    return cond()


STOP_GRACE = 20                   # 秒：菜单 2 / 控制台停过以后 这么久心跳还没更新 = 机器人真的没了（活着的机器人每 15 秒写一次）


def bot_gone(hb):
    """心跳是新的（45 秒内写过）可写心跳的那个机器人已经没了？（10-29：菜单 2 停了以后一分钟内按 1 会说「已经在跑了」——刚停完那 45 秒心跳文件还算新的）
    True = 没了；False = 还在；None = 看不出来。先看心跳里的 pid 在不在进程列表里（管理员开的进程看不到命令行 但看得到 pid）；
    进程列表看不到时 退一步：stop.flag 比最后一次心跳新、而且已经过了 STOP_GRACE 秒（活着的机器人早该写新的了）= 停过了"""
    try:
        pid = int(hb.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    ps = procs() if pid else []
    if pid and ps:
        return not any(q.get("pid") == pid for q in ps)
    try:
        ft = STOP_FLAG.stat().st_mtime
        if float(hb.get("ts") or 0) < ft and time.time() - ft >= STOP_GRACE:
            return True
    except (OSError, TypeError, ValueError):
        pass
    return None


def napcat_proc():
    """NapCat（NapCatWinBootMain.exe）在运行吗：True / False / None（看不到进程列表）"""
    ps = procs()
    if not ps:
        return None
    return any(kind(q) == "napcat" for q in ps)


def qq_stack_down(hb):
    """机器人活着（心跳新）但 QQ 那边没起来：心跳说 connected=false、NapCat 端口不通、NapCat 进程不在
    （10-29 06:14 NapCat 窗口被关：机器人一直在重连 菜单 1 却只说「机器人已经在跑了」不拉 QQ）
    看不到进程列表时（None）：要连不上已经超过 1 分钟（down_since）才算，免得 QQ 刚在启动就又被拉一次"""
    if hb.get("connected") is not False or napcat_up():
        return False
    proc = napcat_proc()
    if proc is True:
        return False
    if proc is False:
        return True
    try:
        since = float(hb.get("down_since") or 0)
    except (TypeError, ValueError):
        since = 0
    return since > 0 and time.time() - since >= 60


def cmd_start(args):
    """1 启动：机器人在跑、QQ 也在 → 不动；QQ 在线、机器人没开 → 只起机器人；都没开 → 运行开机自启任务（QQ + 机器人）
    10-29 补：① 机器人在跑但 QQ 没起来（connected=false 且 NapCat 不在）→ 照样运行开机自启任务把 QQ 拉起来；
    ② 刚停完（心跳还是新的 机器人进程已经没了）→ 当成没开 不再说「已经在跑了」"""
    if IS_WIN:                                    # 常驻说话 / 唱歌换声（10-31 v1.15.14 一键开机）：没在跑就顺手开
        try:
            fyd_autostart(say)
        except Exception as e:
            say(f"  常驻说话 / 唱歌换声没开成：{e!r}"[:160])
    hb = heartbeat()
    if hb["alive"] and bot_gone(hb):
        say("  （心跳文件还是新的 但机器人进程已经不在了：刚停过 → 当成没开）")
        hb["alive"] = False
    if hb["alive"]:
        if not qq_stack_down(hb):
            say("  机器人已经在跑了")
            return 0
        say("  机器人在跑 但没连上 QQ（NapCat 也没在运行）→ 把 QQ 拉起来")
    elif napcat_up():
        start_loop()
        say("  QQ 本来就在线 → 机器人启动中 …")
        if wait_for(lambda: heartbeat()["alive"], 60):
            say("  机器人起来了")
            return 0
        say(r"  一分钟了心跳还没来：看 C:\QQBot\bot\bot.log 最后几行（或者打开控制台看）")
        return 1
    if not IS_WIN:
        say("  只能在 Windows 上启动")
        return 1
    st = task_state()
    if st == "none":
        say("  还没设开机自启任务（QQBot_Service）")
        if ask_yes("  现在设吗？（要管理员；设好就顺便启动）"):
            return cmd_autostart(["--yes"])
        return 1
    rc, out = run(["schtasks", "/run", "/tn", TASK], timeout=30)
    if rc != 0:
        say("  启动失败：" + out.strip()[:200])
        return 1
    say("  QQ 和机器人启动中（大概 30 秒）…")
    qr = NAPCAT / "cache" / "qrcode.png"
    t0 = time.time()

    def ready():
        try:
            if qr.stat().st_mtime >= t0 - 1:
                return True
        except OSError:
            pass
        return napcat_up()
    if wait_for(ready, 75):
        if napcat_up():
            say("  QQ 在线了；机器人十几秒内连上（控制台总览能看到）")
        else:
            say("  QQ 要扫码登录：打开控制台（菜单 4）用机器人小号的手机 QQ 扫码")
        return 0
    if st == "old":
        say("  一分多钟了 QQ 还没起来：开机自启还在用旧的 run.bat，多半被 Windows 拦了 → 选「开机自启 / 修复启动」换成新的")
    else:
        say(r"  一分多钟了 QQ 还没起来：看 C:\QQBot\run.log 和 napcat.log 最后几行；或者选「开机自启 / 修复启动」")
    return 1


def stop_everything(log=say):
    """关掉 QQ、NapCat、开机自启那个进程、守护循环、机器人（要管理员）"""
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        STOP_FLAG.write_text("fish stop", encoding="utf-8")     # 万一守护循环没关掉 它也不会再拉起机器人
    except OSError:
        pass
    run(["schtasks", "/end", "/tn", TASK], timeout=30)
    run(["taskkill", "/f", "/t", "/im", "QQ.exe"], timeout=30)
    done = kill_kinds({"oldrun", "service", "oldloop", "loop", "bot", "napcat", "qq"}, log)
    _sleep(2)
    return done


def cmd_stop(args):
    """2 停止 QQ 和机器人（要管理员：机器人和 QQ 是用管理员权限跑的）"""
    if not IS_WIN:
        say("  只能在 Windows 上用")
        return 1
    if not is_admin():
        return need_admin(["stop"], "停止")
    stop_everything()
    say("  FatFish stopped：QQ 和机器人都停了（要再开：菜单 1 启动）")
    return 0


def restart_hard(log=say):
    kill_kinds({"oldloop", "loop", "bot"}, log)
    _sleep(2)
    rm(STOP_FLAG)
    start_loop()


def cmd_restart(args):
    """3 重启机器人（QQ 不动）：机器人活着 → 让它自己重启（先试加载新代码、做东西做完再重启）；没响应 / --hard → 管理员强制重启"""
    hard = "--hard" in args
    hb = heartbeat()
    if not hard and hb["alive"]:
        pid, started = hb.get("pid"), hb.get("started")
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            RESTART_REQ.write_text("大肥鱼菜单：重启机器人", encoding="utf-8")
        except OSError as e:
            say(f"  写不了重启请求（{e}）")
            return 1
        say("  让机器人自己重启（QQ 不动；15 秒内收到，先试加载新代码）…")

        def back():
            h = heartbeat()
            return h["alive"] and (h.get("pid") != pid or h.get("started") != started)
        if wait_for(back, 120):
            say("  重启好了")
            return 0
        say("  两分钟了还没重启：可能正在做网页 / 代码（做完会自己重启，最多等 10 分钟），"
            "或者新代码试加载没过（看 bot.log 里 [控制台]）。卡住了就再选一次、回答要强制重启")
        if "--elevated" not in sys.argv and IS_WIN and ask_yes("  现在强制重启吗？（要管理员）", default=False):
            return cmd_restart(["--hard"])
        return 1
    if not IS_WIN:
        say("  只能在 Windows 上强制重启")
        return 1
    if not is_admin():
        if not hard:
            say("  机器人没在跑 / 没响应 → 强制重启")
        return need_admin(["restart", "--hard"], "强制重启机器人")
    old = heartbeat()
    restart_hard()
    say("  机器人重新起了（QQ 没动）…")
    if wait_for(lambda: heartbeat()["alive"] and heartbeat().get("pid") != old.get("pid"), 60):
        say("  起来了")
        return 0
    say(r"  一分钟了心跳还没来：看 C:\QQBot\bot\bot.log 最后几行")
    return 1


def cmd_console(args):
    """4 控制台：在这个窗口里跑 bot\\panel.py（关掉窗口 = 关掉控制台；机器人不受影响）"""
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW("FatFish control panel")
        except Exception:
            pass
    rc = call([py_console(), str(BOT / "panel.py")] + [a for a in args if a != "--no-wait"], cwd=BOT)
    say("")
    say("Control panel stopped. 按回车关掉这个窗口")
    if "--no-wait" not in args:
        wait_enter("")
    return rc


def open_console_window():
    """菜单里选 4：控制台开在新窗口（这个菜单还能接着用）"""
    if IS_WIN:
        subprocess.Popen([py_console(), str(FISH), "console"], cwd=str(BOT), creationflags=NEW_CONSOLE)
        say("  控制台在新窗口里开着（浏览器会自己打开 http://127.0.0.1:8765）；关掉那个窗口 = 关掉控制台")
        return 0
    return cmd_console([])


def cmd_autostart(args):
    """5 开机自启 / 修复启动（要管理员）：重建 QQBot_Service（直接跑 tools\\fish.py，不经过 .bat）→ 要的话马上重启生效 → 收走以前的 .bat"""
    if not IS_WIN:
        say("  只能在 Windows 上用")
        return 1
    if not is_admin():
        flags = [a for a in args if a in ("--yes", "--no-restart")]
        if not flags:
            say("  开机自启 / 修复启动：重建计划任务 QQBot_Service —— 登录 Windows 1 分钟后自己起 QQ 和机器人，不经过 .bat")
            flags = ["--yes"] if ask_yes("  建好以后马上重启 QQ 和机器人、让它生效吗？（QQ 会重新登录一次，大概半分钟；"
                                         "这台电脑上开着的 QQ 都会先关掉）") else ["--no-restart"]
        return need_admin(["autostart"] + flags, "开机自启 / 修复启动")
    say("[1/3] 建计划任务 QQBot_Service …")
    remember_bot_qq()
    ok, how = register_task()
    if not ok:
        say("  [不行] 建不了：" + how)
        return 1
    say(f"  好了（{how}）：登录 Windows 1 分钟后自己起 QQ 和机器人；不限时长（不会跑 3 天被 Windows 停掉）")
    if "--yes" in args:
        go = True
    elif "--no-restart" in args:
        go = False
    else:
        go = ask_yes("  现在重启 QQ 和机器人、让它生效吗？（QQ 会重新登录一次；这台电脑上开着的 QQ 都会先关掉）")
    if not go:
        say("[2/3] 先不重启：下次登录 Windows 时生效")
        say("[3/3] 收走以前的 .bat …")
        clean_legacy()
        return 0
    say("[2/3] 关掉正在跑的 QQ 和机器人 …")
    stop_everything()
    say("[3/3] 收走以前的 .bat、用新的开机自启启动 …")
    clean_legacy()
    rc, out = run(["schtasks", "/run", "/tn", TASK], timeout=30)
    if rc != 0:
        say("  启动失败：" + out.strip()[:200])
        return 1
    say("  QQ 和机器人启动中 …")
    if wait_for(napcat_up, 75):
        say("  QQ 在线了；机器人十几秒内连上")
    else:
        say("  QQ 还没在线：多半在等扫码 —— 打开控制台（菜单 4）看二维码；一直不行看 C:\\QQBot\\run.log")
    return 0


def cmd_service(args):
    """开机自启任务跑的就是这个（以前的 run.bat）：关掉旧的 → 起守护循环 → 起 NapCat（QQ），一直等它退出"""
    if IS_WIN and not is_admin():
        return need_admin(["service"], "启动 QQ")
    rlog("===== START（tools\\fish.py service）=====")
    if IS_WIN:
        rc, out = run(["taskkill", "/f", "/t", "/im", "QQ.exe"], timeout=30)
        if out.strip():
            rlog(out.strip())
        kill_kinds({"oldrun", "oldloop", "loop", "bot", "napcat", "service"}, rlog)
        _sleep(6)                                 # 等 QQ 退干净（以前 run.bat 也等 6 秒）
    rm(STOP_FLAG)
    if IS_WIN:                                    # 常驻说话 / 唱歌换声（10-31 v1.15.14 一键开机）：config 里开着的开窗口
        try:
            fyd_autostart(rlog)
        except Exception as e:
            rlog(f"常驻说话 / 唱歌换声没开成：{e!r}"[:160])
    uin = bot_qq()                                # 先拿号（run.bat 下面可能被收走）
    if not CLEAN_MARK.exists() and legacy_left():
        clean_legacy(rlog)                        # 换成新开机自启以后的第一次：以前的 .bat 收走（只收一次）
    start_loop()
    rlog("守护循环起了")
    rc = napcat_run(uin)
    rlog(f"NapCat 退出了（退出码 {rc}）")
    return 0


def cmd_loop(args):
    """守护循环（以前的 bot\\botloop.bat）：bot.py 退出码 4 = 要重启 马上再起；3 = 已经有一个在跑 不起了；
    data\\stop.flag = 控制台点了停 不起了；别的（崩了）15 秒后再起"""
    rm(STOP_FLAG)
    env = dict(os.environ, FATFISH_LOOP="fish")
    flags = NO_WINDOW if IS_WIN else 0
    try:
        while True:
            try:
                with open(BOT_LOG, "ab") as f:
                    rc = subprocess.call([py_console(), "bot.py"], cwd=str(BOT), env=env, stdin=subprocess.DEVNULL,
                                         stdout=f, stderr=subprocess.STDOUT, creationflags=flags)
            except OSError as e:
                rc = -1
                _botlog(f"[loop] bot.py could not start: {e}")
            if STOP_FLAG.exists() or rc == 3:
                break
            if rc == 4:
                continue
            _botlog(f"[loop] bot.py stopped (exit code {rc}) - starting it again in {LOOP_DELAY} seconds")
            for _ in range(max(1, LOOP_DELAY)):
                _sleep(1)
                if STOP_FLAG.exists():
                    break
            if STOP_FLAG.exists():
                break
    finally:
        rm(STOP_FLAG)
    return 0


def _botlog(line):
    try:
        with open(BOT_LOG, "ab") as f:
            f.write((line + "\n").encode("utf-8"))
    except OSError:
        pass


def cmd_status(args):
    """看状态（菜单 7 里；也给测试用）"""
    t = task_info()
    st = task_state(t)
    hb = heartbeat()
    say(f"  大肥鱼 v{version()}   文件夹 {ROOT}")
    say(f"  Python：{sys.executable}（{sys.version.split()[0]}）")
    say(f"  config.json：{'有' if (BOT / 'config.json').exists() else '还没有（先「首次设置」）'}")
    say(f"  机器人小号：{mask(bot_qq())}")
    if IS_WIN:
        args_, env, how = napcat_cmd(bot_qq())
        say(f"  NapCat：{'在' if NAPCAT.exists() else '不在'} {NAPCAT}（起法：{how}）")
        say(f"  电脑版 QQ：{find_qq() or '注册表里没找到'}")
    say("  开机自启：" + {"new": "好了（tools\\fish.py service）", "old": "还是旧的 run.bat（选「开机自启 / 修复启动」换成新的）",
                      "none": "没设", "other": f"指着别的：{t.get('command')} {t.get('args')}",
                      "unknown": "看不到（不是 Windows / 没权限）"}[st]
        + (f"；时长上限 {t.get('limit')}" if t.get("limit") and t.get("limit") != "PT0S" else ""))
    say(f"  QQ（NapCat {':'.join(map(str, napcat_addr()))}）：{'在线' if napcat_up() else '没连上'}")
    loop = {"fish": "新的守护循环", "1": "旧的 botloop.bat"}.get(str(hb.get("loop_kind") or ("1" if hb.get("loop") else "")),
                                                              "没有守护循环")
    say(f"  机器人：{'在跑（pid ' + str(hb.get('pid')) + '，' + loop + '）' if hb['alive'] else ('停了' if hb.get('stopped') else '没在跑')}")
    left = legacy_left()
    say(f"  以前的 .bat：{'还有 ' + str(len(left)) + ' 个（' + '、'.join(left[:4]) + ('…' if len(left) > 4 else '') + '）' if left else '都收走了'}")
    return 0


def cmd_clean(args):
    """以前的 .bat 收进备份（不删）"""
    if not legacy_left():
        say("  以前的 .bat 都收走了")
        return 0
    moved, keep = clean_legacy()
    if not moved and not keep:
        say("  没有要收的")
    return 0


# ───── 安装 ─────
def offer_restart(full=False):
    if "--elevated" in sys.argv:
        return
    if full:
        say("  要整体重启一次（停止 → 启动）机器人才找得到新装的东西（QQ 会重新登录一次）")
        if ask_yes("  现在整体重启吗？", default=False):
            if cmd_stop([]) == 0:
                cmd_start([])
        return
    if heartbeat()["alive"] and ask_yes("  重启一下机器人才用得上（QQ 不动）。现在重启吗？"):
        cmd_restart([])


def cmd_setup(args):
    """首次设置：用国内镜像装好库 → 运行向导（问 QQ 号、key、名字）"""
    if sys.version_info < (3, 12):
        say(f"  [不行] Python 是 {sys.version.split()[0]}，要 3.12 或更新的（装的时候第一页勾上 Add python.exe to PATH）")
        return 1
    req = ROOT / "requirements.txt"
    if req.exists():
        say("  装机器人要用的库（国内镜像，一分钟左右）…")
        if call([py_console(), "-m", "pip", "install", "--disable-pip-version-check", "-r", req, "-i", TUNA]) != 0:
            say("  [不行] 装库失败：看看网络，再选一次「首次设置」")
            return 1
    return call([py_console(), HERE / "setup_wizard.py"], cwd=ROOT)


def cmd_voice(args):
    """语音听写（sherpa-onnx + SenseVoice，约 250 MB；结果写 C:\\QQBot\\voice_test.txt）"""
    say("  装本地听写（sherpa-onnx + SenseVoice 模型，大概 250 MB），只用装一次")
    rc = call([py_console(), "setup_voice.py"], cwd=BOT)
    if rc == 0:
        offer_restart()
    return rc


def find_ollama():
    p = shutil.which("ollama")
    if p:
        return p
    q = Path(os.environ.get("LOCALAPPDATA") or "") / "Programs" / "Ollama" / "ollama.exe"
    return str(q) if os.environ.get("LOCALAPPDATA") and q.exists() else ""


def cmd_localai(args):
    """本地 AI（Ollama + qwen3-vl 识图，模型约 10 GB）"""
    say("===== [1/4] Ollama =====")
    exe = find_ollama()
    if not exe:
        say("  没装 Ollama → 用 winget 装（官方包 Ollama.Ollama；问你同不同意条款就输入 Y 回车）")
        call(["winget", "install", "--id", "Ollama.Ollama", "-e"])
        exe = find_ollama()
    if not exe:
        say("  [不行] 自动安装没成：打开官网下载页 → 装好 OllamaSetup.exe 再选一次")
        try:
            import webbrowser
            webbrowser.open("https://ollama.com/download/windows")
        except Exception:
            pass
        return 1
    say(f"  找到了：{exe}")
    call([exe, "--version"])
    say("===== [2/4] 启动 Ollama 服务 =====")
    if run([exe, "list"], timeout=20)[0] != 0:
        app = Path(os.environ.get("LOCALAPPDATA") or "") / "Programs" / "Ollama" / "ollama app.exe"
        try:
            if IS_WIN and os.environ.get("LOCALAPPDATA") and app.exists():
                os.startfile(str(app))
            else:
                subprocess.Popen([exe, "serve"], creationflags=NEW_CONSOLE if IS_WIN else 0)
        except OSError as e:
            say(f"  起不来：{e}")
        _sleep(8)
    say("  服务好了")
    say(f"===== [3/4] 下载 {OLLAMA_MODEL}（大概 10 GB）=====")
    if call([exe, "pull", OLLAMA_MODEL]) != 0:
        say("  [不行] 下载失败：看看网络，再选一次")
        return 1
    say("===== [4/4] 试一下（看图 + 记忆）=====")
    if (BOT / "test_local_ai.py").exists():
        call([py_console(), "test_local_ai.py"], cwd=BOT)
        say(r"  结果在 C:\QQBot\local_ai_test.txt")
    say("===== 好了 =====")
    offer_restart()
    return 0


def cmd_video(args):
    """视频支持（ffmpeg：唱歌剪歌、视频截帧要用）"""
    p = shutil.which("ffmpeg")
    if p:
        say(f"  ffmpeg 已经装好了：{p}")
        return 0
    say("  用 winget 装 ffmpeg（官方包 Gyan.FFmpeg；问你同不同意条款就输入 Y 回车）")
    rc = call(["winget", "install", "--id", "Gyan.FFmpeg", "-e"])
    if rc == 0:
        say("  装好以后 bot.log 里会有一行 ffmpeg OK")
        offer_restart(full=True)
    return rc


def _pillow():
    say("  装图片库 Pillow（装过就跳过）…")
    call([py_console(), "-m", "pip", "install", "pillow", "-q", "-i", TUNA])


def cmd_stickers(args):
    """导入表情包（kiriyuki.com 的动图 + 桌面上 DeepSeek 表情包里挑的；能重复跑 只补缺的）"""
    _pillow()
    say("  导入表情包要几分钟；重复跑也没事（只拿缺的）")
    rc = call([py_console(), "import_nekowhale.py"], cwd=BOT)
    say(r"  报告在 C:\QQBot\sticker_import.txt")
    if rc == 0:
        offer_restart()
    return rc


def cmd_curate(args):
    """整理表情包（本地 AI 把整个表情包过一遍，1~2 小时；中途关掉下次接着来）"""
    _pillow()
    say("  用本地 AI 过一遍表情包，要 1~2 小时；随时能关，下次接着来")
    rc = call([py_console(), "sticker_curate.py"], cwd=BOT)
    if (ROOT / "sticker_review" / "running.lock").exists():
        say("  [!] 没做完（中途停了）：再选一次「整理表情包」接着来")
    else:
        say(r"  全做完了：结果在 C:\QQBot\sticker_review")
    return rc


# ───── 维护 ─────
def cmd_export(args):
    return call([py_console(), HERE / "export_github.py"], cwd=ROOT)


def cmd_pack(args):
    if not (HERE / "pack_qqbot.py").exists():
        say("  这个版本没有打包工具（只有作者那台有）")
        return 1
    return call([py_console(), HERE / "pack_qqbot.py"], cwd=ROOT)


def cmd_test(args):
    t = ROOT / "tests" / "test_public_package.py"
    if not t.exists():
        say("  这个版本没有测试")
        return 1
    say("  公开包测试：在临时文件夹里搭一个假的 C:\\QQBot（真的只读），大概半分钟 …")
    return call([py_console(), t] + list(args), cwd=ROOT)


# ───────────── 肥鱼声音服务（10-30）─────────────
# 本地「肥鱼」说话（CosyVoice3）+ 换声（RVC）：API 在 Windows 的 feiyu_voice\server\.venv，推理在 WSL（Ubuntu-24.04）。
# 只管开关：127.0.0.1:9880。key 在 feiyu_voice\server\.runtime\voice-service\api_key.txt —— 这里只说位置、不读不打印。
FYV_PORT = 9880


def fyv_dir():
    return ROOT / "feiyu_voice" / "server"


def fyv_log():
    return ROOT / "feiyu_voice" / "voice_service.log"


def fyv_installed():
    d = fyv_dir()
    return (d / ".venv" / "Scripts" / "python.exe").exists() and (d / "configs" / "voice_service.json").exists()


def fyv_up():
    return port_open("127.0.0.1", FYV_PORT)


def fyv_parse(out):
    """PowerShell ConvertTo-Json 的输出（一个 / 一串 / 空）→ [(pid, 命令行)]"""
    out = (out or "").strip()
    if not out:
        return []
    try:
        j = json.loads(out[out.find("{") if out.find("[") < 0 or 0 <= out.find("{") < out.find("[") else out.find("["):])
    except (ValueError, TypeError):
        return []
    rows = j if isinstance(j, list) else [j]
    res = []
    for r in rows:
        try:
            res.append((int(r.get("pid")), str(r.get("cmd") or "")))
        except (TypeError, ValueError, AttributeError):
            pass
    return res


def fyv_listeners():
    """在 9880 上听的进程 [(pid, 命令行)]（不是 Windows / 查不了 → []）"""
    if not IS_WIN:
        return []
    ps = (f"Get-NetTCPConnection -LocalPort {FYV_PORT} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ "
          "$p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.OwningProcess); "
          "[pscustomobject]@{pid=$_.OwningProcess; cmd=$p.CommandLine} } | ConvertTo-Json -Compress")
    rc, out = run(["powershell", "-NoProfile", "-Command", ps], timeout=20)
    return fyv_parse(out) if rc == 0 else []


def fyv_start(log=say):
    if fyv_up():
        log(f"  肥鱼声音服务已经开着（127.0.0.1:{FYV_PORT}）")
        return 0
    d = fyv_dir()
    # Windows PowerShell 的 Tee-Object 写的是 UTF-16：看状态时用 _dec 认
    cmd = ("$host.UI.RawUI.WindowTitle = 'feiyu-voice 9880'; "
           f"& {_ps_quote(d / 'scripts' / 'Start-VoiceService.ps1')} -Config {_ps_quote(d / 'configs' / 'voice_service.json')} 2>&1 "
           f"| Tee-Object -FilePath {_ps_quote(fyv_log())} -Append")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd], cwd=str(d),
                         creationflags=NEW_CONSOLE if IS_WIN else 0)
    except OSError as e:
        log(f"  [不行] 起不来：{e}")
        return 1
    log("  开了一个新窗口「feiyu-voice 9880」：那个窗口开着 = 服务开着（关窗口 = 停）")
    if wait_for(fyv_up, 60):
        log(f"  好了：127.0.0.1:{FYV_PORT} 在听")
        return 0
    log(f"  60 秒还没起来 —— 看那个窗口里的报错，或者 {fyv_log()}")
    return 1


def fyv_stop(log=say):
    rows = fyv_listeners()
    ours = [pid for pid, c in rows if "feiyu_voice_service" in c]
    if not ours:
        if fyv_up():
            log(f"  {FYV_PORT} 上在听的不是肥鱼声音服务（{'、'.join(str(p) for p, _ in rows) or '看不到是谁'}）—— 没动它")
            return 1
        log("  肥鱼声音服务本来就没开")
        return 0
    for pid in ours:
        rc, out = run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=20)   # /T：它起的 WSL 推理进程一起关
        log(f"  关 pid {pid}：{'好了' if rc == 0 else out.strip()[:120]}")
    if wait_for(lambda: not fyv_up(), 15):
        log("  停了")
        return 0
    log(f"  15 秒后 {FYV_PORT} 还在听 —— 直接关掉「feiyu-voice 9880」那个窗口")
    return 1


def fyv_status(log=say):
    d = fyv_dir()
    up = fyv_up()
    log(f"  肥鱼声音服务：{'开着' if up else '没开'}（127.0.0.1:{FYV_PORT}）")
    log(f"  装在：{d}")
    for name in ("FeiyuVoice-CosyVoice3", "FeiyuVoice-RVC"):
        log(f"  模型 {name}：{'有' if (d / 'models' / name).is_dir() else '没有'}")
    key = d / ".runtime" / "voice-service" / "api_key.txt"
    log(f"  API key 文件：{key}（{'有' if key.exists() else '还没有：第一次启动时生成'}；别发给别人、别放进仓库）")
    lg = fyv_log()
    if lg.exists():
        try:
            tail = [ln for ln in _dec(lg.read_bytes()).splitlines() if ln.strip()][-4:]
        except OSError:
            tail = []
        log(f"  日志 {lg} 最后几行：")
        for ln in tail:
            log("    " + ln[:160])
    return 0


def cmd_fyvoice(args):
    sub = (args[0] if args else "status").lower()
    if sub not in ("start", "stop", "status"):
        say("  用法：python tools\\fish.py fyvoice [start | stop | status]")
        return 2
    if not fyv_installed():
        say(f"  肥鱼声音服务没装（{fyv_dir()} 里没有 .venv 或 configs\\voice_service.json）")
        return 1
    return {"start": fyv_start, "stop": fyv_stop, "status": fyv_status}[sub](say)     # say 现找（测试会换掉它）


# ───────────── 常驻说话 / 常驻唱歌换声（10-31 v1.15.14 小明 点的「一键开机」）─────────────
# feiyu_voice 里我们加的两个常驻（tts_daemon.py 9881 / cover_daemon.py 9882）：以前要手动双击两个 .bat
# 现在：开机自启（service）和菜单 1 启动时 —— config 里开着的（voice.local / sing.cover 的 enabled，且 autostart 不是 false）没在跑 → 照那两个 .bat 开窗口
# 停：WSL 里 pkill 那个脚本（窗口会停在「按任意键」）
FYD = {"tts": {"name": "常驻说话", "port": 9881, "bat": "启动肥鱼说话-常驻.bat", "script": "tts_daemon.py", "cfg": ("voice", "local")},
       "cover": {"name": "常驻唱歌换声", "port": 9882, "bat": "启动肥鱼唱歌-常驻.bat", "script": "cover_daemon.py", "cfg": ("sing", "cover")}}
WSL_DISTRO = "Ubuntu-24.04"


def fyd_bat(k):
    return ROOT / "feiyu_voice" / FYD[k]["bat"]


def fyd_installed(k):
    return fyd_bat(k).exists()


def fyd_any():
    return any(fyd_installed(k) for k in FYD)


def fyd_up(k):
    return port_open("127.0.0.1", FYD[k]["port"])


def fyd_wanted(k):
    a, b = FYD[k]["cfg"]
    sec = ((cfg() or {}).get(a) or {}).get(b) or {}
    return bool(sec.get("enabled")) and sec.get("autostart", True) is not False


def fyd_start(k, log=say, wait=0):
    d = FYD[k]
    if fyd_up(k):
        log(f"  {d['name']}已经开着（127.0.0.1:{d['port']}）")
        return 0
    if not fyd_installed(k):
        log(f"  {d['name']}没装（{fyd_bat(k)} 不在）")
        return 1
    if not IS_WIN:
        log("  只能在 Windows 上开")
        return 1
    try:
        subprocess.Popen(["cmd", "/c", str(fyd_bat(k))], cwd=str(fyd_bat(k).parent), creationflags=NEW_CONSOLE)
    except OSError as e:
        log(f"  [不行] {d['name']}起不来：{e}")
        return 1
    log(f"  开了「{d['name']}」窗口（端口 {d['port']}；载模型一两分钟；那个窗口开着 = 开着）")
    if wait:
        if wait_for(lambda: fyd_up(k), wait):
            log(f"  好了：127.0.0.1:{d['port']} 在听")
            return 0
        log(f"  {wait} 秒还没好 —— 看那个窗口里有没有报错（第一次载模型可能要更久）")
        return 1
    return 0


def fyd_stop(k, log=say):
    d = FYD[k]
    if not fyd_up(k):
        log(f"  {d['name']}本来就没开")
        return 0
    rc, out = run(["wsl.exe", "-d", WSL_DISTRO, "-e", "pkill", "-f", d["script"]], timeout=30)
    if wait_for(lambda: not fyd_up(k), 15):
        log(f"  {d['name']}停了（那个窗口停在「按任意键」 关掉就行）")
        return 0
    log(f"  15 秒后 {d['port']} 还在听 —— 直接关掉「{d['name']}」那个窗口")
    return 1


def fyd_autostart(log=say):
    """开机自启 / 菜单 1 启动时调：config 里开着、装了、没在跑的常驻 → 开窗口（不等它载完）→ 开了几个"""
    n = 0
    for k in FYD:
        try:
            if fyd_installed(k) and fyd_wanted(k) and not fyd_up(k):
                if fyd_start(k, log) == 0:
                    n += 1
        except Exception as e:
            log(f"  {FYD[k]['name']}没开成：{e!r}"[:160])
    return n


def fyd_status(log=say):
    for k, d in FYD.items():
        if not fyd_installed(k):
            continue
        log(f"  {d['name']}：{'开着' if fyd_up(k) else '没开'}（127.0.0.1:{d['port']}；config 里{'开着 开机会自己开' if fyd_wanted(k) else '关着 / autostart=false 开机不开'}）")
    return 0


def cmd_fyd(args):
    sub = (args[0] if args else "status").lower()
    which = (args[1] if len(args) > 1 else "all").lower()
    if sub not in ("start", "stop", "status") or which not in ("tts", "cover", "all"):
        say("  用法：python tools\\fish.py fyd [start | stop | status] [tts | cover | all]")
        return 2
    if sub == "status":
        return fyd_status(say)
    ks = [k for k in FYD if which in (k, "all") and fyd_installed(k)]
    if not ks:
        say("  常驻说话 / 唱歌换声没装（C:\\QQBot\\feiyu_voice 里没有那两个「常驻」.bat）")
        return 1
    rc = 0
    for k in ks:
        rc |= (fyd_start(k, say, wait=180) if sub == "start" else fyd_stop(k, say))
    return rc


COMMANDS = {"start": cmd_start, "stop": cmd_stop, "restart": cmd_restart, "console": cmd_console,
            "autostart": cmd_autostart, "service": cmd_service, "loop": cmd_loop, "status": cmd_status,
            "clean": cmd_clean, "setup": cmd_setup, "voice": cmd_voice, "localai": cmd_localai, "video": cmd_video,
            "stickers": cmd_stickers, "curate": cmd_curate, "export": cmd_export, "pack": cmd_pack, "test": cmd_test,
            "fyvoice": cmd_fyvoice, "fyd": cmd_fyd}


# ───────────── 菜单 ─────────────
def header():
    hb = heartbeat()
    st = task_state()
    qq = "在线" if napcat_up() else "没连上"
    bot = "在跑" if hb["alive"] else ("停了" if hb.get("stopped") else "没在跑")
    auto = {"new": "好了", "old": "旧的 run.bat", "none": "没设", "other": "指着别的", "unknown": "看不到"}[st]
    out = ["=" * 60, f"  大肥鱼 v{version()}    {ROOT}", f"  QQ：{qq}    机器人：{bot}    开机自启：{auto}"]
    tips = []
    if not (BOT / "config.json").exists():
        tips.append("第一次用：先选 6 →「首次设置」")
    if st == "old":
        tips.append("开机自启还走旧的 run.bat：选 5 换成新的（一次就好，以后不经过 .bat）")
    elif st == "none" and (BOT / "config.json").exists():
        tips.append("还没设开机自启：选 5")
    elif st == "new" and legacy_left():
        tips.append("以前的 .bat 还在：选 7 →「清理旧的 .bat」收走")
    out += ["  ※ " + t for t in tips]
    out.append("=" * 60)
    return out


def _items(kind_):
    if kind_ == "main":
        short = {cmd_export: "导出到 GitHub", cmd_pack: "打包", cmd_test: "测试", cmd_clean: "清理旧的 .bat",
                 cmd_status: "看状态"}
        maint = "、".join(short[fn] for _, fn, _ in _items("maint") if fn in short)
        return [("启动 QQ 和机器人", cmd_start, []), ("停止 QQ 和机器人（要管理员）", cmd_stop, []),
                ("重启机器人（QQ 不掉线）", cmd_restart, []), ("打开控制台（网页）", None, "console"),
                ("开机自启 / 修复启动（要管理员）", cmd_autostart, []),
                ("安装…  首次设置、语音听写、本地 AI、视频支持、表情包", None, "install"),
                ("维护…  " + maint, None, "maint")] + (
            [("肥鱼声音服务…  启动 / 停止 / 看状态（本地说话、换声）", None, "fyvoice")] if (fyv_installed() or fyd_any()) else [])
    if kind_ == "fyvoice":
        return [("启动（开一个新窗口：窗口开着 = 服务开着）", cmd_fyvoice, ["start"]),
                ("停止", cmd_fyvoice, ["stop"]), ("看状态（API key 文件在哪也在这）", cmd_fyvoice, ["status"])] + (
            [("常驻说话 + 唱歌换声：都开（config 里关着的也开）", cmd_fyd, ["start", "all"]),
             ("常驻说话 + 唱歌换声：都停", cmd_fyd, ["stop", "all"]),
             ("常驻说话 + 唱歌换声：看状态", cmd_fyd, ["status"])] if fyd_any() else [])
    if kind_ == "install":
        return [("首次设置（装库 + 填机器人 QQ、你的 QQ、DeepSeek key、名字）", cmd_setup, []),
                ("语音听写（SenseVoice，约 250 MB）", cmd_voice, []),
                ("本地 AI（Ollama 识图，约 10 GB）", cmd_localai, []),
                ("视频支持（ffmpeg：唱歌、视频截帧）", cmd_video, []),
                ("导入表情包", cmd_stickers, []),
                ("整理表情包（本地 AI 过一遍，1~2 小时）", cmd_curate, [])]
    items = [("导出到 GitHub", cmd_export, [])]
    if (HERE / "pack_qqbot.py").exists():
        items.append(("打包群文件", cmd_pack, []))
    if (ROOT / "tests" / "test_public_package.py").exists():
        items.append(("跑测试（公开包测试）", cmd_test, []))
    if legacy_left():
        items.append(("清理旧的 .bat（挪进备份，不删）", cmd_clean, []))
    items.append(("看状态", cmd_status, []))
    return items


def menu(kind_="main"):
    if IS_WIN and kind_ == "main":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW("FatFish")
        except Exception:
            pass
    while True:
        say("")
        if kind_ == "main":
            for line in header():
                say(line)
        else:
            say("-" * 60)
            say("  " + {"install": "安装", "maint": "维护", "fyvoice": "肥鱼声音服务"}[kind_])
            say("-" * 60)
        items = _items(kind_)
        for i, (label, _, _) in enumerate(items, 1):
            say(f"  {i}  {label}")
        say("  0  " + ("退出" if kind_ == "main" else "返回"))
        try:
            ch = input("选一个数字，回车：").strip()
        except (EOFError, KeyboardInterrupt):
            say("")
            return 0
        if ch in ("0", "q", "Q", "退出"):
            return 0
        if not ch.isdigit() or not 1 <= int(ch) <= len(items):
            say(f"  没有 {ch or '这一项'}，输入 0~{len(items)} 的数字")
            continue
        label, fn, extra = items[int(ch) - 1]
        say(f"\n>>> {label}")
        if fn is None and extra == "console":
            open_console_window()
            continue
        if fn is None:
            menu(extra)
            continue
        try:
            fn(list(extra))
        except KeyboardInterrupt:
            say("\n  停下了")
        wait_enter("\n按回车回到菜单")


USAGE = __doc__.split("命令行", 1)[1].split("FATFISH_ROOT", 1)[0] if "命令行" in __doc__ else ""
QUICK = {"start", "stop", "restart", "autostart"}     # 新开的窗口：这几个做好了停 3 秒自己关；别的（装东西、导出…）等回车


def main(argv):
    args = [a for a in argv if a not in ("--elevated", "--pause")]
    if not args:
        return menu()
    fn = COMMANDS.get(args[0])
    if not fn:
        say(f"不认识的命令：{args[0]}\n命令行" + USAGE)
        return 2
    rc = fn(args[1:])
    if args[0] not in ("service", "loop", "console") and ("--elevated" in argv or "--pause" in argv):
        if rc or args[0] not in QUICK:
            wait_enter("\n按回车关掉这个窗口")
        else:
            _sleep(3)
    return rc


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except KeyboardInterrupt:
        code = 1
    except Exception:
        import traceback
        tb = traceback.format_exc()
        say(tb)
        rlog("[fish.py] 出错了\n" + tb)
        if sys.stdin and sys.stdin.isatty():
            wait_enter("出错了（上面是原因）。按回车关掉这个窗口")
        code = 1
    sys.exit(code)
