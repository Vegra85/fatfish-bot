#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
判断模型 / 调用记录（公开版空壳）
原版是我家这只的私有实验：把「判断」类调用（接话、心情、认 AI、群管）和开思考的回复换个模型试，再把每次调模型记一行做复盘。
空壳 = 全走原来的路（bot.py 的 llm，也就是 DeepSeek），什么都不记。
"""
STUB = True


async def call(kind, system, messages, fallback, temperature=None, max_tokens=400, think=False, effort=None,
               conv="", budget=None, uids=None, log=print, **kw):
    if think:
        kw["think"] = think
    if effort:
        kw["effort"] = effort
    return await fallback(system, messages, temperature=temperature, max_tokens=max_tokens, **kw)


async def probe(log=print):
    return ""


def route(kind):
    """公开版：全走原来的模型"""
    return "deepseek"


def usable(kind, log=print):
    """公开版：不走别家（11-02 v1.17.9 bot 的读懂层看它）"""
    return False, "公开版"


def last_call():
    """公开版：没有别家的调用（11-02 v1.17.9）"""
    return None


def conf():
    """公开版：没有判断模型的设置（11-02 v1.17.8 bot._model_line 看它：没开 = 只有 DeepSeek）"""
    return {"enabled": False}


def has_key():
    return False


def status():
    return {"enabled": False}


def note_window(key, entries, last_text):
    pass


def kind_of(tag="", think=False, think_tag=None):
    return "other"


def trace(kind, system, messages, out, **kw):
    pass


def scope(**kw):
    return None


def reset(token):
    pass


def prune(now=None, keep_days=None):
    return 0
