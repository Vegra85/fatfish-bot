#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出到 GitHub（公开仓库用）
  把 C:\\QQBot 里能公开的部分拷到 C:\\QQBot\\github\\fatfish-bot
  然后打开 GitHub Desktop：左下角写一句改了什么 → Commit to main → Push origin

这个仓库给的是思路、工具和集成 —— 不是拿来就能用的成品 AI（10-26 小明 定的）：
  人设 persona.md 和「灵魂」类模块（PRIVATE 里那些）不导出，换成 tools/public_stubs/ 里的空壳（同样的函数 什么都不记）
  —— 下载的人照样能跑起来 灵魂要自己造。空壳缺了哪个就整个不导出（免得仓库跑不起来）

宁可不导出 也不把隐私传上去：
  1. 只拷白名单里的文件（代码、bat、控制台网页）；聊天记录、记忆、日志、表情包图片、备份、config.json 都不拷
  2. config.json → bot/config.example.json：key / token / SESSDATA 清空，老板 QQ 换成占位符，群号列表清空
  3. run.bat 里的机器人 QQ 号换成 %BOT_QQ%（文件开头 set BOT_QQ=…）
  4. tools/export_rules.json（它自己不会被导出）里的替换规则：提示词例子里的真名、电脑上的个人路径…
  5. 最后把导出结果整个再查一遍：config 里任何一个 key/token 的值、聊过天的人的 QQ 号和群号、名字、长得像 key 的字符串
     有一处对上 就整个不导出（导出目录一个字都不改）并告诉你是哪个文件哪一行

用法：双击 C:\\QQBot\\导出到GitHub.bat
     或 python tools/export_github.py [QQBot 目录] [导出目录] [规则文件]
"""
import fnmatch
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "github" / "fatfish-bot"
RULES = Path(sys.argv[3]) if len(sys.argv) > 3 else HERE / "export_rules.json"
BOT = ROOT / "bot"
DATA = BOT / "data"

ROOT_FILES = ["启动.bat", "停止.bat", "run.bat", "修复启动.bat", "开机自启.bat", "控制台.bat", "重启机器人.bat", "导出到GitHub.bat",
              "安装语音.bat", "安装本地AI.bat", "安装视频支持.bat", "导入表情包.bat", "整理表情包.bat"]
BOT_FILES = ["bot.py", "budget.py", "catchup.py", "clock.py", "control.py", "followup.py", "galgame.py", "imgprep.py",
             "localai.py", "machine.py", "media.py", "memory.py", "mood2.py", "rhythm.py", "shared.py", "slang.py",
             "soul.py", "speakup.py", "stats.py", "stickers.py", "style.py", "vision.py", "voice.py", "websearch.py",
             "bili.py", "bili2.py", "panel.py", "panel.html", "botloop.bat", "persona.md", "setup_voice.py",
             "apply_stickers.py", "import_nekowhale.py", "sticker_curate.py", "anim.py", "cross.py", "wipe.py", "sing.py",
             "music.py", "aidetect.py", "memewiki.py", "groupadmin.py", "safeio.py"]
# 不公开的「灵魂」：导出的是 tools/public_stubs/ 里同名的空壳（10-26）
PRIVATE = {"persona.md", "soul.py", "memory.py", "mood2.py", "galgame.py", "cross.py", "slang.py", "style.py", "shared.py"}
STUBS = HERE / "public_stubs"
TOOL_FILES = ["export_github.py"]
HANDWRITTEN = {"README.md", "CHANGELOG.md", ".gitignore", ".gitattributes", "LICENSE",
               "requirements.txt"}   # 导出目录里手写的 不覆盖也不删
SECRET_KEY = re.compile(r"key|token|sessdata|secret|password|cookie", re.I)
PATTERNS = [(r"sk-[A-Za-z0-9]{16,}", "像 API key"),
            (r"eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}", "像 JWT（MiniMax 的 key 长这样）"),
            (r"SESSDATA=[^;\s'\"]{8,}", "像 B 站 SESSDATA"),
            (r"(?i)bearer\s+[A-Za-z0-9._-]{24,}", "像 token")]
COMMON = {"大肥鱼", "肥鱼", "蓝色大肥鱼", "老板", "系统", "某人", "群友", "全体成员", "网友", "管理员", "群主", "大家"}


def read(p):
    return Path(p).read_bytes().decode("utf-8")       # 不转换换行（bat 要保持 CRLF）


def jload(p, default=None):
    try:
        return json.loads(read(p))
    except Exception:
        return default


# ───────────── 哪些东西算隐私（都从本机的私有数据里现找 不写死在这个文件里） ─────────────
def private_numbers(cfg):
    nums = {str(cfg.get("owner_qq") or "")}
    m = re.search(r"launcher\.bat\s+(\d{5,})", read(ROOT / "run.bat")) if (ROOT / "run.bat").exists() else None
    if m:
        nums.add(m.group(1))
    for p in list((DATA / "memory").glob("*.json")) + list((DATA / "archive").glob("*/*.jsonl")):
        nums.update(re.findall(r"\d{5,}", p.name))
    nums.update((jload(DATA / "favor.json", {}) or {}).keys())
    ctl = jload(DATA / "control.json", {}) or {}
    for k in ("groups", "blocked", "bots"):
        nums.update((ctl.get(k) or {}).keys())
    return {n for n in nums if n and n.isdigit() and len(n) >= 5}


def private_names():
    names = set()
    for u in (jload(DATA / "favor.json", {}) or {}).values():
        if isinstance(u, dict) and u.get("name"):
            names.add(str(u["name"]))
    for p in (DATA / "memory").glob("*.json"):
        m = jload(p, {}) or {}
        for h in m.get("history") or []:
            if h.get("role") == "user" and h.get("name"):
                names.add(str(h["name"]))
        for k, v in (m.get("alias") or {}).items():
            names.add(str(k))
    return {n.strip() for n in names if len(n.strip()) >= 2 and n.strip() not in COMMON
            and not re.fullmatch(r"[\d\W_]+", n.strip())}


def secret_values(o, out=None):
    out = set() if out is None else out
    if isinstance(o, dict):
        for k, v in o.items():
            if SECRET_KEY.search(str(k)) and isinstance(v, str) and len(v) >= 6:
                out.add(v)
            secret_values(v, out)
    elif isinstance(o, list):
        for x in o:
            secret_values(x, out)
    return out


def example_config(cfg, nums):
    def clean(o, key=""):
        if isinstance(o, dict):
            r = {}
            for k, v in o.items():
                if SECRET_KEY.search(str(k)) and isinstance(v, str):
                    r[k] = ""
                elif k == "owner_qq":
                    r[k] = "你的QQ号"
                else:
                    r[k] = clean(v, k)
            return r
        if isinstance(o, list):
            return [clean(x, key) for x in o if str(x) not in nums]
        if str(o) in nums:
            return ""
        return o
    return json.dumps(clean(cfg), ensure_ascii=False, indent=1) + "\n"


# ───────────── 导出 ─────────────
def build(cfg, nums, rules):
    files = {}
    for name in ROOT_FILES:
        if (ROOT / name).exists():
            files[name] = read(ROOT / name)
    for name in BOT_FILES:
        if name in PRIVATE:
            if not (STUBS / name).exists():
                raise SystemExit(f"tools/public_stubs/{name} 不在：「{name}」不公开 要有空壳才能导出（不然仓库跑不起来）")
            files["bot/" + name] = read(STUBS / name)
        elif (BOT / name).exists():
            files["bot/" + name] = read(BOT / name)
    for name in TOOL_FILES:
        if (HERE / name).exists():
            files["tools/" + name] = read(HERE / name)
    files["bot/config.example.json"] = example_config(cfg, nums)
    if "run.bat" in files:                 # 机器人 QQ 号 → 变量（用的人改第一行就行）
        t = files["run.bat"]
        m = re.search(r"launcher\.bat\s+(\d{5,})", t)
        if m:
            t = t.replace(m.group(0), "launcher.bat %BOT_QQ%")
            nl = "\r\n" if "\r\n" in t else "\n"
            t = t.replace("@echo off" + nl, "@echo off" + nl + "rem your bot's QQ number (the account NapCat logs in with)" + nl
                          + "set BOT_QQ=123456789" + nl, 1)
        files["run.bat"] = t
    for r in rules.get("replace") or []:
        pat, old, new = r.get("files", "*"), r.get("old", ""), r.get("new", "")
        if not old:
            continue
        for rel in files:
            if fnmatch.fnmatch(rel, pat):
                files[rel] = files[rel].replace(old, new)
    return files


def scan(files, nums, names, secrets):
    probs = []
    for rel, t in files.items():
        if rel.endswith(".bat"):
            try:
                t.encode("ascii")
            except UnicodeEncodeError:
                probs.append(f"{rel}: bat 里有中文（Windows 命令行会乱码）")
        for i, line in enumerate(t.splitlines(), 1):
            for v in secrets:
                if v in line:
                    probs.append(f"{rel}:{i} 有 config.json 里的密钥")
            for n in nums:
                if re.search(rf"(?<!\d){n}(?!\d)", line):
                    probs.append(f"{rel}:{i} 有 QQ号/群号 {n[:3]}…")
            for nm in names:
                if nm in line:
                    probs.append(f"{rel}:{i} 有认识的人的名字「{nm}」")
            for pat, what in PATTERNS:
                if re.search(pat, line):
                    probs.append(f"{rel}:{i} {what}")
    return probs


def sync(files):
    OUT.mkdir(parents=True, exist_ok=True)
    removed, changed = [], []
    for p in sorted(OUT.rglob("*")):
        rel = p.relative_to(OUT).as_posix()
        if not p.is_file() or rel.split("/")[0] == ".git" or rel in HANDWRITTEN or rel in files:
            continue
        if ("/" not in rel and rel.endswith(".bat")) or rel.startswith(("bot/", "tools/")):
            p.unlink()                     # 以前导出过、现在不在白名单里的
            removed.append(rel)
    for rel, t in sorted(files.items()):
        dst = OUT / rel
        data = t.encode("utf-8")
        if dst.exists() and dst.read_bytes() == data:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        changed.append(rel)
    return changed, removed


def main():
    cfg = jload(BOT / "config.json", {}) or {}
    rules = jload(RULES, {}) or {}
    nums, names, secrets = private_numbers(cfg), private_names(), secret_values(cfg)
    names = {n for n in names if n not in set(rules.get("allow_names") or [])}
    files = build(cfg, nums, rules)
    probs = scan(files, nums, names, secrets)
    print(f"要导出 {len(files)} 个文件（查了 {len(secrets)} 个密钥、{len(nums)} 个 QQ号/群号、{len(names)} 个名字）"
          f"；不公开的 {len(PRIVATE)} 个换成了空壳：{'、'.join(sorted(PRIVATE))}")
    if probs:
        print("\n发现可能是隐私的东西 这次不导出（导出目录没动）：")
        for x in probs[:40]:
            print("  -", x)
        print("\n把这些加进 tools/export_rules.json 的 replace（换掉）或者 allow_names（确认不是隐私）再导出")
        return 1
    changed, removed = sync(files)
    print(f"导出到 {OUT}")
    print(f"  有变化 {len(changed)} 个" + (("：" + "、".join(changed[:12]) + ("…" if len(changed) > 12 else "")) if changed else ""))
    if removed:
        print(f"  删掉 {len(removed)} 个：" + "、".join(removed[:12]))
    print("\n下一步：打开 GitHub Desktop → 左下角写一句改了什么 → Commit to main → Push origin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
