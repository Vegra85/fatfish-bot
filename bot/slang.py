#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
她自己的梗库（空壳模板）

公开仓库里不带作者自己的梗库模块 —— 这个仓库给的是思路、工具和集成，「灵魂」要你自己造。
这一版：她自己不学梗、不攒梗库；但「梗百科」（memewiki.py，cnmeme.wiki 的词库）照样能用：
  有人问「X 是什么梗」→ 先查梗百科给她看；聊天里出现词库里的梗 → 提示她一两条。

bot.py 用到的：
  load() → {"terms": {名字: {meaning, usage, example, …}}, "last_scan": …} / known(词) / variants(文字, n) → 改了一个字的老梗
  wants(这句话, 是不是老板) → 这句话在问的梗（「X 是什么梗」）/ async learn(词, llm, log, 群里的用法, hint, src) → 学会了的条目
  block(最近的聊天, 这次的话) → 放进提示词的「你会的梗」/ note_usage(话, 谁) → 记群友的真实用法
  enqueue / pop_queue / requeue_unclear / retry_unknown / async scan(llm, 群聊, 热搜, log) → 后台慢慢学
  fix_1026 / fix_1026b → 作者自己梗库的一次性修复（空壳里什么都不做）；CMD → 机器人指令（#xx /xx）不是梗

作者的做法（思路）：不认识的梗先查梗百科，没有再一起查 搜索引擎（带知乎/小红书/抖音）+ 小鸡词典 + 萌娘百科 + B站，
让模型只根据资料总结「意思 / 怎么用 / 例句 / 分寸」、给出有多大把握，普通词和没把握的不记；
认得出「改了一个字」的老梗；群友用到的记成真实例句；每隔几小时扫一眼群聊和 B站热搜挑新梗后台学。
"""
import re

STUB = True
ASK = re.compile(r"[「“\"]?([^\s，,。？?！!「」“”\"]{1,10}?)[」”\"]?\s*(?:是什么梗|是啥梗|什么梗|啥梗|是什么意思|是啥意思|啥意思|什么意思)")
_NOT_TERM = re.compile(r"^(你|我|他|她|它)(们)?(在|刚才|刚)?(说|讲|发)|^(梗|意思|啥|什么|这|那|这个|那个|这是|那是|这句|那句)$")
CMD = re.compile(r"^[#/!！@＠]")      # 「#sl」「/help」是机器人指令、「@某人」是在叫人 都不是梗
SELF_NAMES = set()
OWNER_LIST_1025 = ""


def load():
    return {"terms": {}, "unknown": {}, "ignore": {}, "queue": [], "batches": [], "last_scan": 0}


def save(d):
    return None


def known(term):
    return False


def variants(text, limit=2):
    return []


def wants(text, is_owner=False):
    """这句话是不是在问一个梗 → 那个词（梗百科会去查）"""
    m = ASK.search(text or "")
    if m:
        t = re.sub(r"^(这个|那个|这|那|所以|请问|话说|就是)", "", m.group(1)).strip("的")
        if 2 <= len(t) <= 10 and not _NOT_TERM.search(t) and not CMD.match(t):
            return t
    return None


async def learn(term, llm_fn, log=print, chat_lines=None, hint="", src="learned"):
    return None


def note_usage(text, who=""):
    return None


def block(context="", fresh=""):
    """只有梗百科的提示（她自己的梗库是空的）"""
    if not fresh:
        return ""
    try:
        import memewiki
        return memewiki.hint_block(memewiki.find_in(fresh, 2))
    except Exception:
        return ""


def _parse_owner_list(txt):
    return []


def enqueue(items, batch_id=None):
    return 0


def requeue_unclear(batch_id):
    return 0


def fix_1026(log=print):
    return 0


def fix_1026b(log=print):
    return 0


def pop_queue():
    return None


def retry_unknown():
    return None


async def scan(llm_fn, chat_lines, hot_words, log=print):
    return None
