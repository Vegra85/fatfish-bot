#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
首次设置向导（v1.2.0）：双击 C:\\QQBot\\首次设置.bat —— 它先用国内镜像装好库，再运行这个

  1. 检查：文件夹在 C:\\QQBot、Python 3.12 以上、装了电脑版 QQ、NapCat 在 C:\\QQBot\\NapCat
     （没有 NapCat 就去 C:\\QQBot、「下载」、桌面找 NapCat.Shell*.zip，自动解压）
  2. 问 4 件事：机器人小号的 QQ 号、你自己的 QQ 号、DeepSeek API key（顺便查能不能用、还剩多少钱）、机器人叫什么
  3. 写好：
       bot\\config.json         从 config.example.json 来；已经有了就只改这几项（别的设置不动）
       控制台口令               config → panel.token（没有就生成：机器人和控制台第一次就能对上，不用再重启）
       bot\\persona.md          第一次从 persona.example.md 复制，「<她的名字>」换成你起的名字（已经有了就不碰）
       run.bat                  机器人的 QQ 号；这台电脑没有 Windows Terminal（多半是 Windows 10）就改用 launcher-win10.bat
       NapCat 的正向 WebSocket  NapCat\\config\\onebot11_<机器人QQ>.json 打开 127.0.0.1:3001（不用再进 NapCat 网页设置）
  4. 问要不要现在启动：弹一个管理员确认 → 建开机自启任务、启动 QQ 和机器人 → 打开控制台，用机器人小号的手机 QQ 扫码

再运行一次 = 改设置：每一项直接回车就保留原来的。
只用 Python 自带的库（装库失败也能先把设置做完）。

试一试又不想动正在跑的那套（群主用）：解压到别的文件夹，命令行里
  set FATFISH_SETUP_TEST=1 && 首次设置.bat
→ 不查位置、不启动，只在那个文件夹里写设置（启动脚本写死了 C:\\QQBot，测试模式绝不去跑它们）
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "bot"
NAPCAT = ROOT / "NapCat"
EXPECTED_ROOT = r"C:\QQBot"
WS_HOST, WS_PORT = "127.0.0.1", 3001
NAME_SLOT = "<她的名字>"                  # persona.example.md 里留给名字的空
DEFAULT_ALIASES = ("大肥鱼", "肥鱼")
PLACEHOLDER_QQ = "123456789"             # 公开版 run.bat 里的占位号码
QQ_REG_KEY = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ"   # NapCat 的 launcher.bat 也读这个
IS_WIN = os.name == "nt"
TEST = os.environ.get("FATFISH_SETUP_TEST") == "1"


# ───────────── 输出 ─────────────
def say(s=""):
    print(s, flush=True)


def good(s):
    say("  [好] " + s)


def note(s):
    say("  [注意] " + s)


def bad(s):
    say("  [不行] " + s)


def mask(v):
    v = str(v or "")
    return v[:5] + "…" + v[-4:] if len(v) > 12 else "已填"


def write_json(p, obj, indent=1):
    """先写临时文件再替换（写一半出错也不会把原文件弄坏）；不带 BOM"""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")
    os.replace(tmp, p)


# ───────────── 1. 检查 ─────────────
def check_location():
    if TEST:
        note(f"测试模式：不查位置、最后不启动（只在 {ROOT} 里写设置）")
        return True
    if not IS_WIN or os.path.normcase(os.path.normpath(str(ROOT))) == os.path.normcase(EXPECTED_ROOT):
        return True
    bad(f"这个文件夹现在在 {ROOT}，要放在 {EXPECTED_ROOT}（启动脚本里写死了这个位置）")
    say(r"    把整个文件夹移动 / 改名成 C:\QQBot（打开后直接能看到 bot 文件夹），再双击 首次设置.bat")
    return False


def check_python():
    ver = sys.version.split()[0]
    if sys.version_info >= (3, 12):
        good(f"Python {ver}")
        return True
    bad(f"Python 是 {ver}，要 3.12 或更新的（群文件里有安装包；装的时候勾上 Add python.exe to PATH）")
    return False


def find_qq():
    """电脑版 QQ 装在哪：返回 QQ.exe 路径；没装 → ""；不是 Windows（没法查）→ None"""
    if not IS_WIN:
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, QQ_REG_KEY) as k:
            un = str(winreg.QueryValueEx(k, "UninstallString")[0])
    except OSError:
        return ""
    exe = Path(un.strip().strip('"')).parent / "QQ.exe"
    return str(exe) if exe.exists() else ""


def napcat_zips():
    home = Path.home()
    found = []
    for d in (ROOT, home / "Downloads", home / "Desktop"):
        try:
            zs = sorted(d.glob("NapCat.Shell*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            continue
        found += [z for z in zs if z not in found]
    return found


def extract_napcat(zp):
    """把压缩包里 launcher.bat 所在的那一层解压到 C:\\QQBot\\NapCat（不管外面套了几层文件夹）"""
    with zipfile.ZipFile(zp) as z:
        names = [i.filename.replace("\\", "/") for i in z.infolist()]
        hits = [n for n in names if n.rsplit("/", 1)[-1].lower() == "launcher.bat"]
        if not hits:
            return False
        prefix = min(hits, key=len)[: -len("launcher.bat")]
        for info in z.infolist():
            n = info.filename.replace("\\", "/")
            if not n.startswith(prefix) or n.endswith("/"):
                continue
            rel = n[len(prefix):]
            parts = Path(rel).parts
            if not rel or ".." in parts or Path(rel).is_absolute():
                continue
            dst = NAPCAT.joinpath(*parts)
            dst.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dst, "wb") as f:
                shutil.copyfileobj(src, f)
    return (NAPCAT / "launcher.bat").exists()


def ensure_napcat():
    if (NAPCAT / "launcher.bat").exists():
        good(f"NapCat 在 {NAPCAT}")
        return True
    for zp in napcat_zips():
        say(f"  找到 {zp}，解压到 {NAPCAT} …")
        try:
            if extract_napcat(zp):
                good("NapCat 解压好了")
                return True
            note(f"{zp.name} 里没有 launcher.bat，不像 NapCat 的 Shell 版")
        except (zipfile.BadZipFile, OSError) as e:
            note(f"{zp.name} 解压失败（{e}）—— 多半是没下载完，重新下载一次")
    bad(r"没找到 NapCat：把群文件里的 NapCat.Shell.zip 放进 C:\QQBot（或者「下载」文件夹），再运行一次")
    return False


# ───────────── 2. 问 4 件事 ─────────────
def ask(prompt, default="", check=None, secret=False):
    while True:
        shown = (mask(default) if secret else default) if default else ""
        try:
            s = input(f"  {prompt}" + (f"（直接回车 = {shown}）" if shown else "") + "：")
        except EOFError:
            s = ""
        s = s.strip().strip('"').strip("'").strip()
        if not s:
            if default:
                return default
            say("    这一项要填")
            continue
        err = check(s) if check else None
        if err:
            say("    " + err)
            continue
        return s


def check_qq(s):
    return None if re.fullmatch(r"[1-9]\d{4,11}", s) else "QQ 号是 5~12 位数字"


def check_name(s):
    if len(s) > 20:
        return "名字太长了（最多 20 个字）"
    return "名字里别带引号和反斜杠" if re.search(r"[\"'\\]", s) else None


def check_key_format(s):
    if re.fullmatch(r"sk-[A-Za-z0-9_\-]{16,}", s):
        return None
    return "key 是 sk- 开头的一长串字母数字：platform.deepseek.com →「API keys」→ 创建，复制整串粘贴进来（右键 = 粘贴）"


def check_key_online(key):
    """问 DeepSeek 这个 key 能不能用、还剩多少钱。返回 (True / False / None 查不了, 说明)"""
    req = urllib.request.Request("https://api.deepseek.com/user/balance",
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "DeepSeek 说这个 key 不对（复制全了吗？是不是删掉重建过？）"
        return None, f"查余额时 DeepSeek 回了 {e.code}，先跳过"
    except Exception as e:      # 没网、证书、代理…… 不挡着设置
        return None, f"连不上 DeepSeek（{type(e).__name__}），先跳过：网络好了机器人会自己用"
    infos = [i for i in (d.get("balance_infos") or []) if isinstance(i, dict)]
    bal = "、".join(f"{i.get('total_balance')} {i.get('currency', '')}".strip() for i in infos
                   if i.get("total_balance") is not None)
    if not d.get("is_available", True):
        return True, f"key 能用，但余额不够（{bal or '0'}）：去 platform.deepseek.com 充值，不然她一句也回不了"
    return True, ("key 能用，余额 " + bal) if bal else "key 能用"


def ask_key(default=""):
    while True:
        key = ask("DeepSeek 的 API key", default, check_key_format, secret=True)
        say("    正在问 DeepSeek 这个 key 能不能用 …")
        res, msg = check_key_online(key)
        if res is False:
            bad(msg)
            again = input("    重新输入 key 吗？直接回车 = 重新输入，输入 n = 还是用这个：").strip().lower()
            if again in ("n", "no", "否", "不"):
                return key
            default = ""
            continue
        (good if res else note)(msg)
        return key


def run_bat_qq():
    """run.bat 里现在写的机器人 QQ 号（占位的不算）"""
    p = ROOT / "run.bat"
    if not p.exists():
        return ""
    t = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"(?im)^set BOT_QQ=(\d{5,12})\s*$", t) or re.search(r"launcher(?:-win10)?\.bat\s+(\d{5,12})", t)
    return m.group(1) if m and m.group(1) != PLACEHOLDER_QQ else ""


# ───────────── 3. 写设置 ─────────────
def load_cfg():
    """(config, 是不是原来就有)；config.json 坏了 → 另存 .bad，从模板重来"""
    p = BOT / "config.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8-sig")), True
        except ValueError as e:
            shutil.copyfile(p, p.with_name("config.json.bad"))
            note(f"config.json 格式坏了（{e}）：旧的另存成 config.json.bad，这次从 config.example.json 重新来")
    return json.loads((BOT / "config.example.json").read_text(encoding="utf-8-sig")), False


def update_cfg(cfg, owner, key, name):
    cfg["bot_name"] = name
    al = [a for a in (cfg.get("aliases") or []) if isinstance(a, str) and a.strip()]
    if name in DEFAULT_ALIASES:
        al = al if name in al else [name] + al
    else:           # 改了名字：默认的「大肥鱼 / 肥鱼」不再算叫她，自己加过的留着
        al = [name] + [a for a in al if a != name and a not in DEFAULT_ALIASES]
    cfg["aliases"] = al
    cfg["owner_qq"] = owner
    llm = cfg.setdefault("llm", {})
    llm.setdefault("provider", "deepseek")
    llm.setdefault("deepseek", {})["api_key"] = key
    ob = cfg.setdefault("onebot", {})
    if not ob.get("url"):
        ob["url"] = f"ws://{WS_HOST}:{WS_PORT}"
    panel = cfg.setdefault("panel", {})
    if not panel.get("token"):
        panel["token"] = secrets.token_hex(16)
    return cfg


def ensure_persona(cfg, name):
    pf = BOT / (cfg.get("persona_file") or "persona.md")
    if pf.exists():
        good(f"人设 bot\\{pf.name} 已经有了，没动")
        return
    ex = BOT / "persona.example.md"
    if not ex.exists():
        note("没找到人设模板 persona.example.md（压缩包不完整？），机器人启动时会提示")
        return
    pf.write_text(ex.read_text(encoding="utf-8").replace(NAME_SLOT, name), encoding="utf-8")
    good(f"人设：从模板复制了 bot\\{pf.name}（名字填成了「{name}」，以后用记事本照着改）")


def patch_run_bat(bot_qq):
    p = ROOT / "run.bat"
    if not p.exists():
        note("没找到 run.bat（压缩包不完整？）")
        return
    t = p.read_bytes().decode("utf-8")
    new = t
    if re.search(r"(?im)^set BOT_QQ=", new):
        new = re.sub(r"(?im)^set BOT_QQ=[^\r\n]*", "set BOT_QQ=" + bot_qq, new)
    else:
        new = re.sub(r"(launcher(?:-win10)?\.bat\s+)\d{5,12}", lambda m: m.group(1) + bot_qq, new)
    extra = ""
    # launcher.bat 没有管理员权限时用 Windows Terminal（wt.exe）重开自己；Windows 10 多半没有 → 用 launcher-win10.bat
    if IS_WIN and not shutil.which("wt") and (NAPCAT / "launcher-win10.bat").exists() \
            and re.search(r"(?i)call launcher\.bat", new):
        new = re.sub(r"(?i)call launcher\.bat", "call launcher-win10.bat", new)
        extra = "；这台电脑没有 Windows Terminal（多半是 Windows 10），改用 launcher-win10.bat"
    if new != t:
        p.write_bytes(new.encode("utf-8"))      # 只改那一行，换行符（CRLF）原样留着
    good(f"run.bat：机器人 QQ 号 {bot_qq}{extra}")


def enable_ws(bot_qq, cfg):
    """NapCat 给这个号打开正向 WebSocket（和在 NapCat 网页「网络配置」里新建一个是一回事）"""
    m = re.match(r"wss?://([^:/]+):(\d+)", (cfg.get("onebot") or {}).get("url") or "")
    host, port = (m.group(1), int(m.group(2))) if m else (WS_HOST, WS_PORT)
    if host == "localhost":
        host = WS_HOST
    f = NAPCAT / "config" / f"onebot11_{bot_qq}.json"
    o = {}
    if f.exists():
        try:
            o = json.loads(f.read_text(encoding="utf-8-sig"))
        except ValueError:
            shutil.copyfile(f, f.with_name(f.name + ".bad"))
            note(f"{f.name} 格式坏了：另存成 .bad，重新写一份")
            o = {}
    net = o.setdefault("network", {})
    for k in ("httpServers", "httpSseServers", "httpClients", "websocketServers", "websocketClients", "plugins"):
        if not isinstance(net.get(k), list):
            net[k] = []
    srv = next((s for s in net["websocketServers"] if isinstance(s, dict) and str(s.get("port")) == str(port)), None)
    if srv is None:
        srv = {"name": "fatfish-ws", "enable": True, "host": host, "port": port, "messagePostFormat": "array",
               "reportSelfMessage": False, "token": "", "enableForcePushEvent": True, "debug": False,
               "heartInterval": 30000}
        net["websocketServers"].append(srv)
    srv.update(enable=True, host=host, messagePostFormat="array")
    if srv.get("token") and not (cfg.get("onebot") or {}).get("token"):
        cfg.setdefault("onebot", {})["token"] = srv["token"]      # NapCat 那边设过口令：机器人这边也带上
    for k, v in (("musicSignUrl", ""), ("enableLocalFile2Url", False), ("parseMultMsg", False)):
        o.setdefault(k, v)
    write_json(f, o, indent=2)
    good(f"NapCat：正向 WebSocket {host}:{port} 打开了（NapCat\\config\\{f.name}）")


# ───────────── 4. 启动 ─────────────
def start_now():
    if not IS_WIN or TEST:
        return False
    try:
        a = input("  现在启动吗？会弹一个「是否允许此应用对你的设备进行更改」，点「是」[Y/n]：").strip().lower()
    except EOFError:
        a = "n"
    if a in ("n", "no", "否", "不"):
        say(r"    好。以后要启动：右键 C:\QQBot\修复启动.bat →「以管理员身份运行」")
        return False
    bat = ROOT / "修复启动.bat"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", f"Start-Process -FilePath '{bat}' -Verb RunAs"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        note(r"没能以管理员身份运行（是不是点了「否」？）：右键 C:\QQBot\修复启动.bat →「以管理员身份运行」")
        return False
    good("开始建开机自启任务、启动 QQ 和机器人（另一个黑窗口出现 DONE 就好了）")
    say("    过几秒自动打开控制台 …")
    time.sleep(8)
    try:
        os.startfile(str(ROOT / "控制台.bat"))
    except OSError:
        note(r"控制台没打开：双击 C:\QQBot\控制台.bat")
    return True


def main():
    say("=" * 56)
    say("  大肥鱼 首次设置")
    say("=" * 56)
    say()
    say("[1/4] 检查")
    if not (check_location() and check_python()):
        return 1
    qq = find_qq()
    if qq == "":
        bad("没找到电脑版 QQ：先去 im.qq.com 下载安装（装好不用登录），再运行一次")
        return 1
    if qq:
        good(f"电脑版 QQ：{qq}")
    if not (BOT / "config.example.json").exists() and not (BOT / "config.json").exists():
        bad("bot 文件夹不完整（没有 config.example.json）：重新解压一次压缩包")
        return 1
    if not ensure_napcat():
        return 1
    cfg, existed = load_cfg()

    say()
    say("[2/4] 填 4 件事" + ("（已经设置过：直接回车 = 保留原来的）" if existed else ""))
    bot_qq = ask("机器人小号的 QQ 号", run_bat_qq(), check_qq)
    old_owner = str(cfg.get("owner_qq") or "")
    owner = ask("你自己大号的 QQ 号（机器人认你当主人）", old_owner if old_owner.isdigit() else "",
                lambda s: check_qq(s) or ("这是机器人小号的号，这里填你自己的大号" if s == bot_qq else None))
    old_key = str(((cfg.get("llm") or {}).get("deepseek") or {}).get("api_key") or "")
    key = ask_key(old_key if existed else "")
    name = ask("机器人叫什么名字", str(cfg.get("bot_name") or "大肥鱼"), check_name)

    say()
    say("[3/4] 写设置")
    update_cfg(cfg, owner, key, name)
    enable_ws(bot_qq, cfg)
    write_json(BOT / "config.json", cfg)
    good("bot\\config.json 写好了（主人、key、名字、控制台口令）")
    ensure_persona(cfg, name)
    patch_run_bat(bot_qq)

    say()
    say("[4/4] 启动")
    started = start_now()
    say()
    say("接下来：")
    if not started:
        say(r"  0. 右键 C:\QQBot\修复启动.bat →「以管理员身份运行」，再双击 控制台.bat")
    say("  1. 控制台（浏览器里那个页面）上出现二维码后，用机器人小号的手机 QQ 扫码登录")
    say("  2. 用你的大号私聊机器人小号说「你好」，几秒内回你就成功了")
    say(r"  3. 人设：用记事本打开 C:\QQBot\bot\persona.md 照着改（控制台「人设」页也能改），改完下一句就生效")
    say("  想改设置：再双击一次 首次设置.bat")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say()
        say("  取消了，什么都没改完的话再运行一次就行")
        sys.exit(1)
