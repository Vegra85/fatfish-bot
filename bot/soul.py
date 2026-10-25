#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
灵魂：关系、挂念、日记、前情提要、自我认知（空壳模板）

公开仓库里不带作者自己的这部分 —— 这个仓库给的是思路、工具和集成，「灵魂」要你自己造。
这一版能让 bot.py 跑起来：什么都不加（不写日记、不写前情提要、不记她对自己的认识）。

bot.py 用到的函数（照着签名自己写）：
  self_block() / diary_block() / summary_block(mem) / rel_block(mem, is_owner) → 放进提示词的一段文字（没有就空字符串）
  extract_view(mem) / build_extract_prompt(mem, 聊天文字) / apply_extract(mem, 模型返回的 JSON, is_owner) → 记忆提炼用
  build_summary_prompt(mem, 更早的聊天) → 前情提要用的提示词
  open_loops(mem) / due_loops(mem) → 她心里挂着的事（[{text, …}]）；due_loops 里的会让她主动去问
  bump_turn(mem, is_owner) → 每聊一轮记一下（关系慢慢变熟）
  has_diary(那天) / async write_diary(llm, 心情, B站热点, 见闻, 记忆目录, log) → 每天写一篇日记（返回 {"post": 签名} 或 None）
  add_self(一句话, log) → 她发现了自己的新东西（%%STATE%% 里的 self）
  blend_mood(旧心情, 新心情) → 旧版心情系统用

作者的做法（思路）：每个人一张关系卡（第一次聊、熟悉程度、最近在聊什么、她挂念的事）；
滑出上下文的旧聊天压成一段前情提要；她每天夜里写一篇日记；她对自己的认识慢慢攒成几句笔记。
"""
STUB = True          # bot.py 看到这个就不去调模型写前情提要（省钱）


def rel(mem, is_owner=False):
    return mem.setdefault("rel", {})


def open_loops(mem):
    return []


def due_loops(mem):
    return []


def rel_block(mem, is_owner=False):
    return ""


def bump_turn(mem, is_owner=False):
    r = rel(mem, is_owner)
    r["turns"] = int(r.get("turns") or 0) + 1


def extract_view(mem):
    return []


def build_extract_prompt(mem, conv):
    return ("从下面这段聊天里提炼值得长期记住的事。只输出 JSON："
            "{\"memories\": [{\"text\": \"…\", \"kind\": \"fact\", \"imp\": 3}]}\n\n" + str(conv or ""))


def apply_extract(mem, data, is_owner=False):
    return None


def build_summary_prompt(mem, older):
    return "把下面这些更早的聊天压成三五句前情提要。\n\n" + "\n".join(
        f"{h.get('name', '')}: {h.get('text', '')}" for h in older or [])


def summary_block(mem):
    return ""


def blend_mood(old, target):
    try:
        return round(float(old) * 0.7 + float(target) * 0.3)
    except (TypeError, ValueError):
        return old


def has_diary(day):
    return True                       # 空壳：当作已经写过了 不去调模型


async def write_diary(llm_fn, mood, bili_trends, shared_notes, mem_dir, log=print):
    return None


def diary_block():
    return ""


def add_self(text, log=print):
    return None


def self_block(limit=8):
    return ""
