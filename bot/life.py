#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日生活（公开版空壳）
原版：她每天醒来自己排一天的日程（在干什么、在哪、回消息方不方便），每轮当状态给她看；忙的时候别人找她过几分钟才回、
不插话不冒泡，QQ 在线状态跟着日程走，她也能自己改日程 —— 那是我家这只的「灵魂」，不公开。
思路：一次模型调用排出 JSON（summary + segments[{start, end, what, where, mode}]）→ 代码校验（排序、不重叠、睡觉段固定、
忙的总时长有上限、空档补齐）→ 不行就退回周模板 / 内置作息。想做的话照着 bot.py 里调用的这几个函数自己写一份。
空壳：没有日程 —— 一切照旧（不延迟、不改在线状态、提示里什么都不加）
"""
STUB = True
MODES = ("free", "slow", "busy", "sleep")
MODE_NAME = {}
C = {"enabled": False}


def configure(cfg=None, data_dir=None):
    pass


def on():
    return False


def mode_now(now=None):
    return None                   # qqevents 退回按作息


def what_now(now=None):
    return ""                     # 没有日程：QQ 在线状态不按「在做什么」挑扩展状态（11-02 v1.17.8）


def current(now=None, d=None):
    return None                   # 没有日程：没有「现在这段」


def quiet(now=None):
    return False


def doing(now=None):
    return None


def block(now=None):
    return ""


def rules_line():
    return ""


def apply_edit(x, now=None, log=print):
    return False, ""


def urgent(texts):
    return False


def delay(owner=False, now=None, rng=None):
    return 0.0, None


def engaged(her_t, t_first=None, now=None):
    return False                  # 没有日程 本来就不等


def pend_set(key, due_t, t0, what, uids=(), now=None):
    pass


def pend_get(key):
    return None


def pend_clear(key):
    pass


def note_event(kind, text, now=None):
    pass


def load(day=None, now=None):
    return None


def due(now=None):
    return False


def take_replan():
    return None


def request_replan(why=""):
    return False


async def plan(llm, persona="", now=None, log=print, why="", commit=None):
    return None


def view(now=None):
    return {"ok": True, "enabled": False, "stub": True, "has": False, "segments": [], "edits": [], "events": []}
