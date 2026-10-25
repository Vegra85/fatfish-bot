#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
心情（空壳模板）

公开仓库里不带作者自己的心情模块 —— 这个仓库给的是思路、工具和集成，「灵魂」要你自己造。
这一版：心情一直是「平静」，不会因为聊天变化（不额外调模型）。

bot.py 用到的：configure(cfg) / block() → 放进提示词的一段 / async judge(llm, 对方说的, 她回的, 场合, log)
/ bump(心情变化, **情绪变化)（比如刷到好玩的 B站视频 bump(2, happy=4)）

作者的做法（思路）：每轮回复后另起一次便宜的模型调用当「裁判」，给心情和几种情绪打分（开心、委屈、吃醋…）；
心情有基准线、会慢慢回落；越高越难涨、同一个方向连着变会打折（不然很快就到顶了）；理由只告诉她一次。
"""
STUB = True
_cfg = {}


def configure(cfg):
    _cfg.update(cfg or {})


def current():
    return 70


def label():
    return "平静"


def block():
    return "\n# 你现在的心情\n平静。\n"


async def judge(llm_fn, user_text, reply_text, who="别人找她聊天", log=print):
    return None


def bump(md=0, **emos):
    return None
