#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
好感度（空壳模板）

公开仓库里不带作者自己的好感度模块 —— 这个仓库给的是思路、工具和集成，「灵魂」要你自己造。
这一版：好感度不会因为聊天变化；控制台「记忆和好感」页能手动改（存在 data/favor.json）。

bot.py 用到的：_load() / _save(d)（data/favor.json：{QQ: {"favor": 数, "name": 名字, …}}）
/ get(uid, name, is_owner) → 这个人的记录 / add(uid, 变化, name, is_owner, log) / block(uid, name, is_owner) → 提示词
/ group_block([(uid, name)]) → 群里最近说话的几个人跟她多熟 / event_done(uid) / face(对话, 立绘名) → 换立绘的表情包标记
/ taboo_from_text / taboo_add / taboo_del / taboo_block（禁忌：空壳不记）

作者的做法（思路）：按 QQ 号记好感、每天最多涨一点；分几档（陌生 → 熟人 → …），跨档触发一次小事件；
熟到一定程度才给做动画、才开某些玩笑。
"""
import json
from pathlib import Path

STUB = True
ROOT = Path(__file__).resolve().parent
FAVOR = ROOT / "data" / "favor.json"


def _load():
    try:
        return json.loads(FAVOR.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d):
    FAVOR.parent.mkdir(parents=True, exist_ok=True)
    tmp = FAVOR.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FAVOR)


def get(uid, name="", is_owner=False):
    u = _load().get(str(uid)) or {}
    return {"favor": int(u.get("favor") or (100 if is_owner else 0)), "name": u.get("name") or name,
            "pending_event": None}


def add(uid, delta, name="", is_owner=False, log=print):
    return None


def block(uid, name="", is_owner=False):
    return ""


def group_block(uids_names):
    return ""


def event_done(uid):
    return None


def face(conv, name):
    return None


# 禁忌（作者 10-27 加的思路）：他明确说「别提 X / 别叫我 X」→ 按 QQ 号记下 对他常驻；空壳不记
def taboo_from_text(text):
    return None


def taboo_add(uid, text, name="", kind="topic", verb="提", src=None, where="", date="", log=print):
    return None


def taboo_del(uid, i):
    return None


def taboo_block(people, group=False):
    return ""

