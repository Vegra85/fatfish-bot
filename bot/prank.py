#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
恶作剧 / 恶趣味（空壳模板）—— 公开仓库里不带作者的恶作剧模块（跟幽默 humor.py 一样算「灵魂」那部分）

作者的做法（思路）：代码只管「什么时候能整、整谁、多久一次、整完拆穿没」这几道闸（写死 防 bug），整什么、怎么整让模型自己想：
  自发：每轮小概率给她一段「这一轮可以逗他一下（可选）」+ 几张「玩法卡」（吹爆 / 将计就计 / 装傻曲解 / 反套路…）当灵感；
  点单：控制台挑人 → 挑玩法卡 → 补一句要求 → 先预览再发（或者「他下次说话时」「定个时间」）；
  闸：别人家的 AI 不自己去整（会对整停不下来）、看着像小孩 / 正在难过 / 在聊敏感事的不整、群里静音不整、
      钱 / 隐私 / 私聊里的事 / 假装做了事 / 冒充主人 不拿来整、整完必须拆穿（她在回复里留个记号 系统去掉 没拆穿的下一轮提醒她）、
      频率（每人每天 / 每个群几小时 / 点单冷却）、有人说「别整我」以后就不整他；
  整人那轮的话不进「说到做到」、不提炼成记忆。
bot.py / panel.py 用到的函数这里都有（都是什么都不做的版本）：on() 返回 False → 机器人和控制台都当成没这个功能。
"""
STUB = True
P = {"enabled": False, "undo_sec": 110}


def configure(cfg):
    return None


def on():
    return False


def heard(uid, texts, d=None, now=None):
    return None


def pending(key, uid=None, now=None, max_age=900):
    return None


def take_next_order(key, uid):
    return None


def due_orders(now=None):
    return []


def marker(raw):
    return raw, None


def self_revealed(text):
    return False


def forget_person(uid):
    return True


def status():
    return {"ok": True, "enabled": False, "auto": False, "level": "normal", "levels": [], "cards": [], "records": [],
            "orders": [], "no_prank": {}, "today": 0, "cooling": {}, "undo_sec": 110, "order_gap_min": 10, "busy": False}


def set_settings(auto=None, level=None):
    return False


def save_cards(lst):
    return None


def no_prank(uid, on_=True, why=""):
    return False


def cancel_order(oid):
    return False


def load():
    return {"records": []}
