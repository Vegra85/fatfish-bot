#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
共享见闻（空壳模板）

公开仓库里不带作者自己的这部分 —— 这个仓库给的是思路、工具和集成，「灵魂」要你自己造。
这一版：没有共享见闻（不额外调模型）。

bot.py 用到的：configure(cfg, log) / load() → {"notes": [...]} / block(条数) → 放进提示词的一段
/ due(间隔秒数) → 该不该重新整理 / async refresh(llm, log)

作者的做法（思路）：隔几小时从各个群聊里提炼几条「最近大家在聊的事」，所有对话都带上 —— 她在 A 群听到的，到 B 群也知道；
默认只从群聊提炼（私聊的事不往外带）。
"""
STUB = True


def configure(cfg=None, log=print):
    return None


def load():
    return {"notes": []}


def block(limit=4):
    return ""


def due(interval=7200):
    return False


async def refresh(llm_fn, log=print):
    return None
