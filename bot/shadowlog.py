#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影子日志（10-30）：「阶段 0」的新东西只记、不改她的行为 —— 一类一个文件夹 data/<类>/YYYY-MM-DD.jsonl
  court     法庭：她给过答案、有人说不对（bot 私有模块 court.py 写）
  ambiguity 歧义度：点歌时第一第二拉不开（music.py 写）
  cooling   按人冷却：人冷了 会提炼他这一场的哪几句（attention.py 写）
  每类留 keep_days 天（默认 30）；删数据（wipe.traces）按 QQ 号 / 名字删行；tools/shadow_report.py 按天汇总
"""
import json
import threading
import time
from pathlib import Path

KINDS = ("court", "ambiguity", "cooling", "social")
_dir = Path(__file__).resolve().parent / "data"
_lock = threading.Lock()
_cleaned = {}


def configure(data_dir=None):
    global _dir
    if data_dir:
        _dir = Path(data_dir)


def folder(kind):
    return _dir / kind


def write(kind, rec, now=None, keep_days=30):
    """记一行；一天第一次写时顺手删掉超过 keep_days 天的文件。写不进去不报错（影子日志不能影响她）"""
    now = float(now or time.time())
    try:
        d = folder(kind)
        d.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        line = json.dumps(dict(rec, t=round(now, 1)), ensure_ascii=False, separators=(",", ":"))
        with _lock:
            with open(d / f"{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        if _cleaned.get(kind) != day:
            _cleaned[kind] = day
            for p in d.glob("*.jsonl"):
                try:
                    if now - p.stat().st_mtime > float(keep_days) * 86400:
                        p.unlink()
                except OSError:
                    pass
        return True
    except (OSError, TypeError, ValueError):
        return False


def read(kind, day=None):
    day = day or time.strftime("%Y-%m-%d")
    p = folder(kind) / f"{day}.jsonl"
    out = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def days(kind):
    try:
        return sorted(p.stem for p in folder(kind).glob("*.jsonl"))
    except OSError:
        return []
