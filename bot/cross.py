#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
群里群外同一个她（空壳模板）

公开仓库里不带作者自己的这部分 —— 这个仓库给的是思路、工具和集成，「灵魂」要你自己造。
这一版：群里和私聊各聊各的（不互相带），外号按对话记。

bot.py 用到的：configure(cfg) / call_of(uid, name, mem) → 她给这个人起的外号 / set_call(uid, 外号, name) → 记下外号
/ group_side(uid, name, 这次的话) → (群里回他时给她看的说明, 私聊里的事列表, 不带私聊内容的说明)
/ private_side(uid, name) → 私聊时给她看的「他最近在群里说过的」
/ leak(回复, 私聊里的事, 群里看得到的内容, 名字) → (漏出来的那条, [漏的词])，没漏返回 ("", [])
/ save_group_names({群号: 群名}) / group_names()

作者的做法（思路）：外号按 QQ 号记、群里私聊叫的一样；私聊时知道他最近在群里聊了什么；
群里知道「你们私下聊过」但私聊内容默认不说，冒出私聊里才有的词就让她重写；病、感情、钱、家里这类永远不带进群。
"""
import json
from pathlib import Path

STUB = True
ROOT = Path(__file__).resolve().parent
GNAMES = ROOT / "data" / "group_names.json"
C = {}


def configure(cfg=None):
    C.update(cfg or {})


def call_of(uid, name="", mem=None):
    return ((mem or {}).get("alias") or {}).get(name) or ""


def set_call(uid, call, name="", said=""):
    return call


def group_side(uid, name, query=""):
    return "", [], ""


def private_side(uid, name="", now=None):
    return ""


def leak(reply, facts, context, names=()):
    return "", []


def group_names():
    try:
        return json.loads(GNAMES.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_group_names(d):
    try:
        GNAMES.parent.mkdir(parents=True, exist_ok=True)
        old = group_names()
        old.update({str(k): v for k, v in (d or {}).items() if v})
        GNAMES.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
