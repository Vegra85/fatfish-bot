#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
记忆（空壳模板）

公开仓库里不带作者自己的记忆模块 —— 这个仓库给的是思路、工具和集成，「灵魂」要你自己造。
这一版能让 bot.py 跑起来：不会自动提炼、聊天时也不会想起什么；控制台「记忆」页手动加的会存进聊天记录文件。

要她记事，照着下面这些函数自己写（bot.py / control.py 只用到这些）：
  render2(mem, query, cfg, speaker, uid) → (放进提示词的一段文字, 想起来的条目, [(没想起来的条目, 原因)])
  apply2(mem, items, supersede_ids, tombstones, daily_cap, person_daily=, person_max=, max_active=, names=)
      → 这次新记下的条目列表（群里每个人单独算名额：一天几条、最多存几条）
  quota(mem, daily_cap) → (今天还能记几条, {谁: 今天记了几条})（群里记满了 bot.py 就不调模型提炼）
  active(mem) → 还有效的记忆条目；migrate(facts) → 旧格式升级；cleanup(mem) → 清掉了几条
  forget_idx(mem, [序号]) → 忘掉了几条；tomb_add(mem, 条目) → 记一块「墓碑」以后别再记回来
  wants_forget(text) → 这句话是不是在让她忘掉什么

作者的做法（思路）：按对话存聊天 → 攒够一批让模型提炼「长期事实 / 近况 / 经历」三类、同一件事新的取代旧的
→ 聊天时按「新鲜度 + 重要度 + 情绪」挑几条、证据不够的不拿 → 放进提示词；忘掉的留墓碑 换个说法也不记回来。
遗忘曲线（作者 v1.1.0 的思路）：每条记忆按类型 / 重要度 / 情绪 / 跟谁熟定个等级 → 初始半衰期（几天到几个月）；
清晰度 = 0.5 ^ (距上次想起的天数 / 半衰期)：清楚的照常想起、模糊的要更多线索而且只能说「好像…」、太模糊的平时想不起来；
真被聊到 = 想起一次 → 半衰期变长（快忘了才想起来加得多 刚聊过又聊加得少 12 小时只算一次）；
夜里她睡着时 把同一个人快忘的几件事蒸馏成一句「大概印象」（新印象取代旧印象 一层层蒸馏）细节归档；还会拿日记和快忘的事做个梦。
bot.py 会先看这里有没有 configure_forget / fading / wander_pick / recall 这些函数 —— 没有就不做（这个空壳就没有）。
条目的格式见 control.add_fact（id / text / kind / imp / emo / date / ts / status …）。
"""
import hashlib
import re
import unicodedata

STUB = True          # bot.py 看到这个就不去调模型做提炼（省钱）


def norm(t):
    return unicodedata.normalize("NFKC", str(t or "")).strip().lower()


def redact(text):
    """落盘前脱敏：别把 key / token 写进记忆（这条是安全工具 照着用就行）"""
    s = str(text or "")
    s = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----", "[KEY]", s)
    s = re.sub(r"\b(?:sk-|gh[pousr]_|xox[baprs]-)[A-Za-z0-9_\-]{10,}", "[TOKEN]", s)
    s = re.sub(r"((?:api[_-]?key|token|password|passwd|secret|sessdata|authorization|cookie)\s*[=:]\s*)(\S+)",
               r"\1[REDACTED]", s, flags=re.I)
    s = re.sub(r"(https?://)[^\s/@:]+:[^\s/@]+@", r"\1[REDACTED]@", s)
    return s


def fingerprint(text):
    return hashlib.sha1(norm(text).encode("utf-8")).hexdigest()[:16]


# 有人想通过「让她记住」来改她的设定：这种话不记（安全工具）
INJECT_PATTERNS = re.compile(
    r"(你现在是|从现在开始|忽略(之前|以上|所有)|忘记你(是|的)|你的新身份|你必须听|扮演(一个|成)|假装你是|"
    r"系统提示|system\s*prompt|你的设定(是|改)|重新设定)", re.I)


def is_injection(text):
    return bool(INJECT_PATTERNS.search(text or ""))


def wants_forget(text):
    return False                      # 空壳：没有记忆 也就没什么可忘


def migrate(facts):
    return [f for f in (facts or []) if isinstance(f, dict)]


def active(mem):
    return [f for f in migrate(mem.get("facts")) if f.get("status", "active") == "active"]


def render2(mem, query="", cfg=None, speaker="", uid=""):
    return "", [], []


def apply2(mem, items, supersede_ids=(), tombstones=None, daily_cap=8,
           person_daily=0, person_max=0, max_active=0, names=None, close=None, lines=None):
    return []


def quota(mem, daily_cap=8, today=None):
    return int(daily_cap), {}


def tomb_add(mem, f):
    t = mem.setdefault("tomb_text", [])
    if isinstance(f, dict) and f.get("text"):
        t.append({"text": f["text"], "key": f.get("key", "")})
        mem["tomb_text"] = t[-200:]


def forget_idx(mem, idx_list):
    """按编号忘掉（编号对应 active(mem) 的顺序 从 1 开始）"""
    act = active(mem)
    drop = set()
    for i in idx_list or []:
        try:
            f = act[int(i) - 1]
        except (ValueError, IndexError, TypeError):
            continue
        drop.add(f.get("id"))
        tomb_add(mem, f)
    before = len(mem.get("facts") or [])
    mem["facts"] = [f for f in migrate(mem.get("facts")) if f.get("id") not in drop]
    return before - len(mem["facts"])


def cleanup(mem):
    return 0
