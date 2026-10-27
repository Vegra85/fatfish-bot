#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
说到做到 / 承诺表（空壳模板）

公开仓库里不带作者自己的这个模块 —— 这个仓库给的是思路、工具和集成，「灵魂」要你自己造。
这一版：什么都不记 什么都不挡（她说了「睡了」也照常回话；说了「5 分钟后提醒你」也不会去提醒）。

bot.py / panel.py 用到的：configure(cfg) / enabled() / rules() → 系统提示里的一段 / declare(...) 记一条
/ gate(...) 来了一条消息挡不挡 / hold(...) / away(...) / has(...) / note_call(...) / tick() 到点的约束
/ back_call(rec) / block(...) 这一轮给她看的 / consumed(...) / late_line(...) / status() / release(...) / forget_person(...)
/ 承诺表：due_tasks() / overdue(...) / apologies_due() / task_scene(...) / apology_scene(...) / task_done(...) / task_failed(...)
  / still(...) 还要不要做 / draft(...) / save_draft(...) 没发出去的那句 / apology_sent(...) / apology_failed(...) / find_task(...) / cancel_task(...) / mark_done(...) / panel_add(...)
  / panel_edit(...) / tasks_view() / tasks_status() / reload() / parse_when(...) 几点 / pending_for(...) 他还没做的

作者的做法（思路）：她自己说了接下来要做什么 → 回复的状态行里写 "do"（漏写了 让心情裁判顺便看一眼）→ 记进一张「承诺表」：
1. 到点要做的事（「5 分钟后提醒你」「明早 8 点叫你」「每天催你睡」）：到点她用自己的话去做（群里 @ 他 / 私聊）；
   在「睡觉」也爬起来做；掉线了一小时内补、再晚就去道歉；重复的一直有效；控制台有个日历（打勾 / 取消 / 改时间）。
2. 一段时间里的约束（说了要睡 / 要走 / 不理谁 / 不发语音）：这个对话里真的照做一阵 —— 走多久按最近的热闹程度算
   （越热闹越短 越是话题中心越长）；离开期间有人找她积累「叫醒压力」（每人有上限、像机器人的不算、刚说完最难叫醒）。
3. 没说时间的答应：他下次来找她时提醒她兑现。每个群、每个私聊各算各的；别人家的 AI 说的一切不算。
"""
STUB = True
KINDS = {"sleep": ("睡觉", "all"), "away": ("离开", "all"), "lurk": ("潜水", "all"), "quiet": ("少说", "direct"),
         "ignore": ("不理", "person"), "novoice": ("不发语音", "mod"), "nosticker": ("不发表情包", "mod"),
         "nosing": ("不唱", "mod"), "promise": ("答应", "promise")}
AWAY = ("sleep", "away", "lurk")
C = {"enabled": False, "tasks": {"enabled": False, "max_tokens": 300}}


def configure(cfg=None):
    return None


def enabled():
    return False


def tasks_on():
    return False


def reload():
    return None


def rules():
    return ""


def kind_of(act):
    return None


def declare(key, scene, do, said="", now=None, hist=(), to_uid="", to_name="", names=None, src="state",
            log=print, rng=None, owner_uid="", ai=None):
    return []


def gate(key, scene, uid, name="", now=None, direct=False, owner=False, susp=0.0, text="", mid=None, at=False,
         log=print):
    return {"hold": "", "woke": None, "why": "", "rec": None}


def hold(key, uid="", now=None):
    return ""


def away(key, now=None):
    return None


def has(key, kind, now=None):
    return None


def note_call(key, uid, name="", mid=None, ts=None, text="", at=False, susp=0.0, owner=False):
    return None


def tick(now=None, log=print):
    return []


def back_call(rec, now=None):
    return None


def block(key, uid="", now=None, owner=False):
    return ""


def consumed(key, uid="", now=None):
    return None


def late_line(first, last, now=None, scene="group", kind=""):
    return ""


def status(now=None):
    return []


def release(key, kinds=None, log=print, why=""):
    return 0


def forget_person(uid, names=()):
    return 0


def due_tasks(now=None):
    return []


def apologies_due(now=None):
    return []


def task_scene(t, now=None, asleep=None, late=0.0, scene="group", who_name=""):
    return ""


def apology_scene(t, now=None, scene="group"):
    return ""


def overdue(t, now=None):
    return False


def task_done(t, text="", where="", now=None, log=print, by=""):
    return t


def task_failed(t, why, now=None, log=print):
    return "retry"


def still(t, apology=False):
    return False


def draft(t, key, apology=False):
    return None


def save_draft(t, key, raw, apology=False):
    return None


def apology_sent(t, text="", now=None, log=print):
    return None


def apology_failed(t, why, now=None, log=print):
    return None


def find_task(ref):
    return None


def parse_when(at=None, mins=None, now=None):
    return None


def pending_for(uid):
    return []


def cancel_task(t, by="", log=print, now=None):
    return None


def mark_done(ref, by="控制台", log=print, now=None):
    return None


def panel_add(p, log=print, now=None):
    return None, "公开版没有承诺表（commit.py 是空壳）"


def panel_edit(p, log=print, now=None):
    return None


def tasks_view(now=None, months_ahead=14):
    return {"tasks": [], "promises": [], "constraints": [], "now": 0, "tz": "北京时间", "caps": {}}


def tasks_status(now=None):
    return {"pending": 0, "apology": 0, "next": []}
