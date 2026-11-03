#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
余额（11-02 v1.17.0）—— 工具类，公开
  小明：「加入claude api和ds api的余额查询 如果有人问你还剩多少token类的问题她可以用碗里还有多少多少饭类似的回复 不要写死回复句式」
  有人问她余额 / token / 额度 / 还剩多少（钱 / token / 饭）/ 还能聊多久 / 是不是快没钱了（want()：小正则 + 上下文 不太宽）
    → 这一轮的情况里加一段状态（block()）：数字 + 哪来的（刚查的 / 自己记账估的）
  E1：只给数字和事实 —— 不给比喻、不给例句、不给语气；怎么说她自己定（人设里本来就有 token = 她吃的东西）
  DeepSeek：官方查余额接口 GET {base_url}/user/balance（请求头 Authorization: Bearer <key>；
            回 is_available、balance_infos[{currency CNY/USD, total_balance, granted_balance, topped_up_balance}]，见 api-docs.deepseek.com）
            结果缓存 cache_sec（默认 10 分钟）；查不到 → 照实说查不到（失败的结果只缓存 fail_cache_sec 别一直白试）
            key 只放在请求头里：不进日志、不进状态、不进返回值
  Claude：Anthropic 没有给普通 API key 查余额的接口（用量 / 费用报告是 Admin API 要管理员凭证）→ 用判断模型 judge.budget_state() 自己记的账
          （这个账单周期送的额度、已经花了多少、还剩多少、还有几天、今天能花多少），状态里写明「自己记账估的 不是官方查的」
          公开版的 judge 是空壳（没有 budget_state）/ 没填 Anthropic key → 不提 Claude
  config → balance（enabled / cache_sec / fail_cache_sec / timeout_sec / sticky_sec / follow_sec）
  11-02 v1.17.3（C74 人格测试 问题 2）：资料只给一轮 → 下一轮她看不到了 把自己说对的「107 刀」认成编的 —— 现在
    同一个对话里粘一段时间（sticky：问过的人 sticky_sec 内再说话还给；follow_sec 内谁又提到「刀 / 余额 / 刚才说的 / 到底哪个」也给）、
    状态头写明「你 N 分钟前看过这份」；record() 给聊天记录留一条「这一轮系统给你看过余额」的系统记录（bot.py 记在她那句旁边）
"""
import re
import time

import httpx

DEFAULTS = {"enabled": True, "cache_sec": 600, "fail_cache_sec": 60, "timeout_sec": 6.0,
            "sticky_sec": 600,             # 问过余额的人 这么多秒内再说话 还给她看（11-02 v1.17.3）
            "follow_sec": 1800}            # 这么多秒内这个对话里有人又提到「刀 / 余额 / 刚才说的 / 到底哪个」也给
C = dict(DEFAULTS)
_DS = {"t": 0.0, "v": None}


def configure(cfg=None):
    C.clear()
    C.update(DEFAULTS)
    for k, v in (cfg or {}).items():
        if not str(k).startswith("_") and k in DEFAULTS:
            C[k] = v
    _DS.update(t=0.0, v=None)


def on():
    return bool(C.get("enabled", True))


def _f(k):
    try:
        return float(C.get(k, DEFAULTS[k]))
    except (TypeError, ValueError):
        return float(DEFAULTS[k])


# ───────────── 什么时候给她看 ─────────────
_W = r"[^。！？!?\n]"
ASK = re.compile(
    r"(?:余额|额度|tokens?|托肯|饭钱|口粮)" + _W + r"{0,10}(?:还[有剩]|剩(?:下|了)?|多少|几个|够不够|够吗|够用|用完|用光|花完|吃完|见底|查一?下|看一?下)"
    r"|(?:还[有剩]|剩(?:下|了)?)" + _W + r"{0,6}(?:多少|几)" + _W + r"{0,4}(?:钱|余额|额度|tokens?|饭|口粮)"
    r"|(?:钱|余额|额度|tokens?|饭)" + _W + r"{0,4}还[有剩]" + _W + r"{0,4}(?:多少|几)"
    r"|(?:查一?下|看一?下|查查)" + _W + r"{0,4}(?:余额|额度|tokens?)"
    r"|(?:碗里|锅里|饭碗)" + _W + r"{0,6}(?:还[有剩]|多少|几)"
    r"|你[的那]?(?:饭|米|口粮|粮食)" + _W + r"{0,4}(?:还[有剩]|剩|够)"
    r"|还能(?:聊|说|撑|用|活|陪我?聊)多久"
    r"|(?:是不是|快|要|会不会)" + _W + r"{0,3}(?:没钱|破产|欠费|饿死|断粮|没饭吃)"
    r"|(?:烧|花|用|吃)(?:了|掉)多少(?:钱|tokens?|额度)", re.I)
# 明摆着说的是别的账户（支付宝 / 饭卡 / 话费…）
OTHER = re.compile(r"支付宝|微信钱包|零钱|银行卡|饭卡|校园卡|公交卡|话费|电费|水费|燃气|花呗|信用卡|游戏币|点券|会员", re.I)
MINE = re.compile(r"我(?:的|这)?(?:余额|额度|tokens?|饭钱|口粮)", re.I)
# 问的是价钱 / 计费（「token 多少钱一百万」「额度怎么收费」）不是她还剩多少（11-02 v1.17.1：以前一样当成问余额）
PRICE = re.compile(r"多少钱|一百万|百万|每千|每万|千个?tokens?|万个?tokens?|\d+\s*[kKmM]\s*tokens?|单价|价格|价钱|定价|收费|计费|怎么算钱|贵不贵|便宜", re.I)
# 说的是第三个人 / 别的东西快没钱（「群主要破产了」「他快饿死了」）
OTHER_WHO = re.compile(r"[他她它]|群主|管理员?")
HER = re.compile(r"你|她的名字|肥鱼|她的小名|西米|本鱼|deepseek|claude|\bds\b|api", re.I)


def want(texts, to_her=False):
    """他这几句（最近 3 句）是不是在问她的余额 / token / 额度 / 还能聊多久 → True / False
    to_her：私聊 / @ 她 / 叫她（不然要句子里有「你」或者她的名字、DeepSeek / Claude）"""
    if not on():
        return False
    for t in list(texts or [])[-3:]:
        t = str(t or "")[:300]
        m = ASK.search(t)
        if not m or OTHER.search(t):
            continue
        if PRICE.search(t) and not re.search(r"还[有剩]|剩(?:下|了)?|余额|够不够|够吗|够用|用完|用光|见底|(?:烧|花|用|吃)(?:了|掉)", t):
            continue                       # 「token 多少钱一百万」「额度怎么收费」：问价钱 不是问她还剩多少（11-02 v1.17.1）
        bare = re.sub(r"@\S+\s?", "", t)        # 「@她的名字 群主要破产了」：@ 她只是叫她 说的还是群主
        if "你" not in bare and not HER.search(bare) and OTHER_WHO.search(t[max(0, m.start() - 6):m.start()]):
            continue                       # 「群主要破产了」「他快没饭吃了」：说的是别人（11-02 v1.17.1）
        if "你" not in t and (MINE.search(t) or "我" in t[max(0, m.start() - 6):m.start()]):
            continue                       # 「我的 token 用完了」「我还剩多少钱」说的是他自己
        if to_her or HER.search(t):
            return True
    return False


# 她刚报过数 对方接着说的（「那一百多刀够你吃几顿饭」「现在又说看不到余额 到底哪个是真的」）—— want() 抓不到的追问（11-02 v1.17.3）
FOLLOW = re.compile(r"[一二两三四五六七八九十百千万几多\d]\s*刀|刀(?:乐|了|呢|啊|吗)?(?:\s|$|[，,。！!？?])|美元|美金|余额|额度|tokens?|托肯|账本|账单|重置|"
                    r"刚才(?:说|讲|报)的|刚刚(?:说|讲|报)的|你(?:刚才|刚刚)?说(?:还剩|剩)|到底哪个|哪个(?:才)?是真的|看不到|看得到|查不到|"
                    r"几顿|够你|够不够|还剩|剩多少|饭钱|口粮", re.I)


def followup(texts):
    """他这几句（最近 3 句）是不是还在说余额这件事（只在她刚看过余额资料的对话里用）"""
    return any(FOLLOW.search(str(t or "")[:300]) for t in list(texts or [])[-3:])


def sticky(last, texts, uid="", now=None):
    """这个对话刚给她看过余额（last = {"t", "uid"}）→ 这一轮还给不给：问过的那个人 sticky_sec 内 / follow_sec 内又提到余额这件事"""
    if not on() or not isinstance(last, dict) or not last.get("t"):
        return False
    age = float(now or time.time()) - float(last["t"])
    if age < 0:
        return False
    if age <= _f("sticky_sec") and (not uid or str(uid) == str(last.get("uid") or uid)):
        return True
    return age <= _f("follow_sec") and followup(texts)


# ───────────── DeepSeek：官方余额接口 ─────────────
def _base(spec):
    return re.sub(r"/v1/?$", "", str((spec or {}).get("base_url") or "https://api.deepseek.com").rstrip("/"))


async def deepseek(spec, force=False, now=None):
    """spec = config → llm.deepseek → {"ok", "available", "infos": [{currency, total, granted, topped}], "t", "fresh"} /
    {"ok": False, "err": "查不到（原因）", "t"}；缓存 cache_sec（失败 fail_cache_sec）。返回值和报错里都没有 key"""
    now = float(now or time.time())
    v = _DS["v"]
    if v is not None and not force:
        age = now - float(_DS["t"])
        if age < (_f("cache_sec") if v.get("ok") else _f("fail_cache_sec")):
            return dict(v, fresh=False)
    key = str((spec or {}).get("api_key") or "").strip()
    if not key:
        v = {"ok": False, "err": "config 里没填 DeepSeek key"}
    else:
        try:
            async with httpx.AsyncClient(timeout=_f("timeout_sec"), trust_env=True) as c:
                r = await c.get(_base(spec) + "/user/balance",
                                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
            if r.status_code >= 400:
                v = {"ok": False, "err": f"查不到（DeepSeek 回 HTTP {r.status_code}" + ("：key 不对" if r.status_code == 401 else "") + "）"}
            else:
                j = r.json()
                infos = []
                for x in (j.get("balance_infos") or []):
                    if isinstance(x, dict):
                        infos.append({"currency": str(x.get("currency") or ""), "total": str(x.get("total_balance") or "0"),
                                      "granted": str(x.get("granted_balance") or "0"),
                                      "topped": str(x.get("topped_up_balance") or "0")})
                v = {"ok": True, "available": bool(j.get("is_available")), "infos": infos}
        except Exception as e:
            v = {"ok": False, "err": f"查不到（{_why(e)}）"}
    v["t"] = now
    _DS.update(t=now, v=v)
    return dict(v, fresh=True)


def _why(e):
    """查余额出错 → 人话（11-02 v1.17.1：以前直接写异常的类名「ConnectTimeout」进状态 她会照着念）"""
    n = type(e).__name__
    if "Timeout" in n:
        return "等太久没回"
    if "Connect" in n or "Network" in n or "Proxy" in n:
        return "连不上 DeepSeek"
    if isinstance(e, ValueError):
        return "DeepSeek 回的东西看不懂"
    return "出了点岔子"


def _money(cur, amt):
    sign = {"CNY": "¥", "USD": "$"}.get(str(cur).upper())
    try:
        a = f"{float(amt):.2f}"
    except (TypeError, ValueError):
        a = str(amt)
    return f"{sign}{a}" if sign else f"{a} {cur}".strip()


# ───────────── Claude：自己记的账 ─────────────
def claude(judge_mod):
    """judge.budget_state() → {"monthly", "total", "spent", "spent_today", "left", "days_left", "allow", "start", "end", "dynamic"}；
    公开版空壳 / 没填 key / 没开 Claude 路由 → None"""
    try:
        if judge_mod is None or getattr(judge_mod, "STUB", False) or not hasattr(judge_mod, "budget_state"):
            return None
        if hasattr(judge_mod, "has_key") and not judge_mod.has_key():
            return None
        c = judge_mod.conf() if hasattr(judge_mod, "conf") else {}
        if not c.get("enabled", True):
            return None
        if not any(str(v).strip().lower() in ("anthropic", "auto") for k, v in (c.get("routes") or {}).items()
                   if not str(k).startswith("_")):
            return None                    # 哪儿都没走 Claude：不提
        b = judge_mod.budget_state() or {}
        out = {"dynamic": bool(b.get("on"))}
        if b.get("on") and "left" in b:
            out.update({k: b.get(k) for k in ("monthly", "total", "spent", "spent_today", "left", "days_left", "allow", "start", "end")})
        else:                              # 没开动态用量：只有「每天上限」那套
            sp = float(judge_mod.spent_today()) if hasattr(judge_mod, "spent_today") else 0.0
            out.update(cap=float(c.get("daily_cap_usd") or 0), spent_today=round(sp, 3))
        return out
    except Exception:
        return None


# ───────────── 给她看的（E1：只给数字和事实）─────────────
def _round_money(x):
    try:
        a = round(float(x.get("total") or 0))
    except (TypeError, ValueError):
        a = "?"
    return {"CNY": "¥", "USD": "$"}.get(str(x.get("currency")).upper(), "") + str(a)


def _md(d):
    s = str(d or "")
    return s[5:] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else s


def block(ds=None, cl=None, now=None, again=None):
    """「这一轮的情况」里那段：DeepSeek 的余额（刚查的 / 几分钟前查的 / 查不到）+ Claude 这期的额度（自己记账估的）；都没有 → \"\"
    again：这个对话上次给她看余额的时间（粘着给的那几轮 11-02 v1.17.3）→ 状态头换成「你 N 分钟前看过这份」"""
    now = float(now or time.time())
    lines = []
    if ds is not None:
        if ds.get("ok"):
            mins = int((now - float(ds.get("t") or now)) // 60)
            when = "刚查的" if ds.get("fresh") or mins < 1 else f"{mins} 分钟前查的"
            parts = [f"{_money(x['currency'], x['total'])}（赠送 {_money(x['currency'], x['granted'])}、充值 {_money(x['currency'], x['topped'])}）"
                     for x in ds.get("infos") or []]
            lines.append("- DeepSeek（平时说话、干杂活用的那家）：账户余额 " + ("；".join(parts) if parts else "没有返回金额")
                         + ("；官方说余额够调用" if ds.get("available") else "；官方说余额已经不够调用了")
                         + f"（{when}，DeepSeek 官方余额接口）")
        else:
            lines.append(f"- DeepSeek（平时说话、干杂活用的那家）：这会儿{ds.get('err') or '查不到'}")
    if cl:
        if cl.get("dynamic") and cl.get("left") is not None:
            lines.append(f"- Claude（Anthropic 家的；有人找你时多半是它）：这个账单周期（{_md(cl.get('start'))} 起，{_md(cl.get('end'))} 重置）"
                         f"能用约 ${cl.get('total')}，已经用了约 ${cl.get('spent')}，还剩约 ${cl.get('left')}；"
                         f"离重置还有 {cl.get('days_left')} 天，今天能用约 ${cl.get('allow')}（今天已经用了 ${cl.get('spent_today')}）。"
                         "这是自己调用时记的账、估出来的，不是 Anthropic 官方查的（Anthropic 没有给普通 key 查余额的接口）")
        elif cl.get("cap"):
            lines.append(f"- Claude（Anthropic 家的）：每天最多用 ${cl.get('cap'):g}，今天已经用了约 ${cl.get('spent_today')}"
                         "（自己记账估的，不是 Anthropic 官方查的）")
    if not lines:
        return ""
    if again:
        mins = max(0, int((now - float(again)) // 60))
        head = (f"你{'刚才' if mins < 1 else f' {mins} 分钟前'}在这个对话里看过这份资料（之前要是报过数 就是照这份报的）；"
                "这一轮系统接着给你看（他可能还在说这件事；用不上就不用提）。数字和来源：\n")
    else:
        head = "他这几句提到了余额 / token / 额度这类事（多半是在问你的；不是在问你的就用不上）。数字和来源：\n"
    return "\n# 余额和额度（状态 不是台词）\n" + head + "\n".join(lines) + "\n"


def record(ds=None, cl=None):
    """聊天记录里那条系统记录（11-02 v1.17.3）：这一轮给她看过的余额 —— 只要数字和出处 短；都没有 → \"\""""
    bits = []
    if ds is not None:
        if ds.get("ok"):
            bits.append("DeepSeek 账户余额 " + ("、".join(_money(x["currency"], x["total"]) for x in ds.get("infos") or []) or "没有返回金额")
                        + "（官方余额接口）")
        else:
            bits.append(f"DeepSeek 这会儿{ds.get('err') or '查不到'}")
    if cl:
        if cl.get("dynamic") and cl.get("left") is not None:
            bits.append(f"Claude 这期（{_md(cl.get('end'))} 重置）还剩约 ${cl.get('left')}（自己记账估的）")
        elif cl.get("cap"):
            bits.append(f"Claude 今天已经用了约 ${cl.get('spent_today')}、每天最多 ${cl.get('cap'):g}（自己记账估的）")
    return ("余额资料：" + "；".join(bits)) if bits else ""


def describe(ds=None, cl=None):
    """日志里一行（金额只到整数、不带 key）"""
    bits = []
    if ds is not None:
        if ds.get("ok"):
            bits.append("DeepSeek 约 " + "、".join(_round_money(x) for x in ds.get("infos") or [])
                        + ("（刚查的）" if ds.get("fresh") else "（缓存）"))
        else:
            bits.append("DeepSeek " + str(ds.get("err") or "查不到"))
    if cl and cl.get("left") is not None:
        bits.append(f"Claude 估还剩 ${round(float(cl.get('left') or 0))}（自己记账）")
    return " · ".join(bits) or "没有可给的"
