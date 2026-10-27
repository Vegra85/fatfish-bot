#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
幽默（公开版空壳）
原版会在「回梗 / 回玩笑」那一轮告诉她怎么接、开思考、复读跟风、冒充老板时将计就计 —— 那是我家这只的性格，不公开。
思路：认出这一轮有梗 / 有图 / 有人挖坑 / 在逗她 → 往这一轮的提示里加几句「别复述图、看穿坑反挖回去、玩笑别委屈」，
再给那一轮开模型的思考。想做的话照着 bot.py 里调用的这几个函数自己写一份。
"""


def configure(cfg):
    pass


def on(part=None):
    return False


def cfg():
    return {}


def analyze(texts, has_img=False, meme_hint=False):
    return {"any": False}


def tags(a):
    return []


def want_think(a, direct):
    return False


def block(a, owner=False, close=False):
    return ""


def owner_claim(text, names=()):
    return False


def prank_plan(key, uid, now=None):
    return None


def prank_mark(key, uid, stage, now=None):
    pass


def prank_pending(key, uid):
    return False


def flip_delay():
    return 45.0


def prank_block(stage, name):
    return ""


def repeat_seen(gid, uid, text, now=None, rnd=None):
    return ""


def repeat_mine(gid, text, now=None):
    pass
