#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
写文件防坏 + data 每天备份（10-26）

  write_json / write_text：先写到旁边的临时文件 再一下子换过去（os.replace）
    以前是直接覆盖着写：写到一半程序崩了、电脑断电了 文件就只剩半截 —— 记忆、好感、梗库读不出来就等于丢了
    现在写到一半出事 原来的文件还是好好的
    Windows 上控制台正好在读这个文件时换不过去 → 等一下再换；实在换不过去才直接写（跟以前一样）
  read_json：读坏了（不是合法的 JSON）→ 先把坏文件另存一份「xxx.json.bad-月日-时分秒」再用默认值
    （以前是悄悄当成空的 下次一保存 坏文件连同里面还能救的东西就被覆盖没了）
    一时打不开（正被别的程序占着）→ 等一下再读 别当成没有
  daily_backup：data 文件夹每天打一个包 → backup_20261024/daily/data_年月日.zip 留 14 天
    不打包：archive（原始聊天存档 只往后加不改 本来就安全）、语音/动画/梗百科词库（大 而且能重新下）、缓存和临时文件
    哪天记忆坏了 从前一天的包里把那个文件拿出来放回去就行
"""
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DAILY = ROOT / "backup_20261024" / "daily"
KEEP_DAYS = 14
SKIP_DIRS = {"archive", "voice", "anim", "memewiki"}
SKIP_FILES = {"heartbeat.json", "vision_cache.json", "panel_cache.json", "machine.json", "bot.lock", "restart.request"}
SKIP_EXT = {".tmp", ".lock", ".mp3", ".wav", ".silk", ".amr", ".mp4", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".db"}


def write_text(path, text, encoding="utf-8"):
    """原子写：要么是旧的 要么是新的 不会只剩半截"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding=encoding) as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())           # 真写到盘上再换（断电也不会换成一个空文件）
        except OSError:
            pass
    for i in range(6):
        try:
            os.replace(tmp, p)
            return True
        except PermissionError:            # Windows：别的程序正开着这个文件 等一下
            time.sleep(0.05 * (i + 1))
    try:                                   # 实在换不过去：直接写（跟以前一样） 临时文件删掉
        p.write_text(text, encoding=encoding)
        return True
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def write_json(path, obj, indent=1):
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent))


class Busy(OSError):
    """文件一直被别的程序占着 读不了（不是坏了 别当成空的再存回去）"""


def read_json(path, default=None, log=None, busy_raise=False):
    """读 JSON。没有这个文件 → default；读坏了 → 留一份 .bad 再 default；
    一时打不开（被占着）→ 等一下再读；一直打不开 → busy_raise=True 就抛 Busy（记忆文件用：免得当成空的存回去把记忆冲掉）"""
    p = Path(path)
    raw = None
    for i in range(6):
        try:
            raw = p.read_bytes()
            break
        except FileNotFoundError:
            return default
        except OSError as e:
            if i == 5:
                if log:
                    log(f"[存档] {p.name} 一直打不开（{e!r:.60}）")
                if busy_raise:
                    raise Busy(f"{p.name} 打不开") from e
                return default
            time.sleep(0.05 * (i + 1))
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        keep_bad(p, raw, log, repr(e)[:80])
        return default


def keep_bad(p, raw=None, log=None, why=""):
    """坏文件另存一份（同一个文件最多留 5 份坏的）"""
    p = Path(p)
    try:
        bad = p.with_name(f"{p.name}.bad-{time.strftime('%m%d-%H%M%S')}")
        if raw is None:
            shutil.copy2(p, bad)
        else:
            bad.write_bytes(raw)
        old = sorted(p.parent.glob(p.name + ".bad-*"))
        for x in old[:-5]:
            x.unlink()
        if log:
            log(f"[存档] {p.name} 读坏了（{why}）原文件另存成 {bad.name} 先用空的")
    except OSError:
        pass


# ───────────── data 每天备份 ─────────────
def _want(rel):
    parts = rel.parts
    if parts and parts[0] in SKIP_DIRS:
        return False
    if rel.name in SKIP_FILES or rel.suffix.lower() in SKIP_EXT or ".bad-" in rel.name or rel.name.endswith(".tmp"):
        return False
    return True


def daily_backup(log=print, now=None):
    """今天还没打包就打一个 → 打了返回 zip 路径；今天打过了返回 None"""
    day = time.strftime("%Y%m%d", time.localtime(now or time.time()))
    out = DAILY / f"data_{day}.zip"
    if out.exists() or not DATA.exists():
        return None
    DAILY.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    n = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(DATA.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(DATA)
            if not _want(rel):
                continue
            try:
                z.writestr(str(Path("data") / rel).replace("\\", "/"), f.read_bytes())
                n += 1
            except OSError:
                continue
    os.replace(tmp, out)
    keep = sorted(x for x in DAILY.glob("data_*.zip") if re.fullmatch(r"data_\d{8}\.zip", x.name))
    for x in keep[:-KEEP_DAYS]:
        try:
            x.unlink()
        except OSError:
            pass
    log(f"[备份] data 打包好了 {out.name}（{n} 个文件 {out.stat().st_size // 1024} KB）留最近 {KEEP_DAYS} 天")
    return out
