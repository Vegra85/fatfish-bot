#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计：每天收了多少、回了多少、用了多少 token / 语音字数 / 搜索次数，每小时记一次心情
  存 data/stats.json（保留 90 天），控制台的「统计」页读这个算花费
峰谷计价（10-26）：DeepSeek 从 2026-09 起分高峰/空闲两种价（价格表是人民币）
  高峰 = 北京时间 周一到周五 9:00-12:00、14:00-18:00（法定节假日填 config → peak.holidays 算空闲）
  高峰时段用的 token 另记一份（days.<日期>.tok_*_pk、by.<功能>.*_pk）控制台按这个算钱；
  省 token 的曲线还是按美元（人民币 ÷ usd_cny），每次调用按「当时」是不是高峰算
"""
import json
import os
import time
from pathlib import Path

import clock

ROOT = Path(__file__).resolve().parent
FILE = ROOT / "data" / "stats.json"
MOOD = ROOT / "data" / "mood2.json"
KEEP_DAYS = 90
KEEP_HOURS = 72

_d = None
_dirty = False
_last = 0.0
# 价格（config → panel.prices 覆盖）：DeepSeek（deepseek-flash）每百万 token 空闲时段 没命中缓存 / 命中 / 输出（人民币），
# 高峰时段 × peak_mult；DeepSeek 搜索、读网页 每千次成功请求（人民币）；MiniMax 语音每百万字（美元）；usd_cny 美元换人民币
_prices = {"cny_miss": 1.0, "cny_hit": 0.02, "cny_out": 4.0, "peak_mult": 2.0,
           "search_cny_per_k": 5.0, "fetch_cny_per_k": 5.0, "tts_per_m_chars": 60.0, "usd_cny": 7.1}
# 高峰时段（config → peak 覆盖）：北京时间（UTC+8）周一到周五 这几个钟点；holidays = 法定节假日（北京日期 当天全天空闲）
_peak = {"enabled": True, "tz": 8, "weekdays": [0, 1, 2, 3, 4], "hours": [[9, 12], [14, 18]], "holidays": []}


def configure(prices=None):
    for k, v in (prices or {}).items():
        if k in _prices:
            try:
                _prices[k] = float(v)
            except (TypeError, ValueError):
                pass


def configure_peak(cfg=None):
    for k, v in (cfg or {}).items():
        if k in _peak and not str(k).startswith("_"):
            _peak[k] = v


def _bj(now=None):
    return time.gmtime((now or time.time()) + float(_peak.get("tz", 8)) * 3600)


def peak_now(now=None):
    """现在是不是 DeepSeek 的高峰时段（价格翻倍）"""
    if not _peak.get("enabled", True):
        return False
    t = _bj(now)
    if t.tm_wday not in (_peak.get("weekdays") or []) or time.strftime("%Y-%m-%d", t) in (_peak.get("holidays") or []):
        return False
    h = t.tm_hour + t.tm_min / 60
    return any(float(a) <= h < float(b) for a, b in (_peak.get("hours") or []))


def _peak_end(now=None):
    """这段高峰几点结束（北京时间的钟点）；不在高峰 → None"""
    if not peak_now(now):
        return None
    t = _bj(now)
    h = t.tm_hour + t.tm_min / 60 + t.tm_sec / 3600
    return next((float(b) for a, b in (_peak.get("hours") or []) if float(a) <= h < float(b)), None)


def peak_left(now=None):
    """离这段高峰结束还有几秒（不在高峰 = 0）"""
    end = _peak_end(now)
    if end is None:
        return 0
    t = _bj(now)
    return max(0, int((end - (t.tm_hour + t.tm_min / 60 + t.tm_sec / 3600)) * 3600))


def peak_text(now=None):
    """给日志/控制台看：「高峰（价格翻倍 北京时间 18:00 结束）」/「空闲」"""
    end = _peak_end(now)
    if end is None:
        return "空闲"
    return f"高峰（价格翻倍 北京时间 {int(end):02d}:{int(round(end % 1 * 60)):02d} 结束）"


def _usd(cny):
    return cny / max(0.01, _prices["usd_cny"])


def _spend(usd):
    """每小时花了多少钱（省 token 的递减模型按最近 24 小时算）"""
    if usd <= 0:
        return
    h = _load().setdefault("hours", {})
    k = clock.strf("%Y-%m-%d %H")
    h[k] = round(h.get(k, 0.0) + usd, 7)


def spent(hours=24, now=None):
    """最近 hours 小时花了多少（美元）"""
    now = now or time.time()
    keys = {clock.strf("%Y-%m-%d %H", now - i * 3600) for i in range(max(1, int(hours)))}
    return sum(v for k, v in (_load().get("hours") or {}).items() if k in keys)


def _load():
    global _d
    if _d is None:
        try:
            _d = json.loads(FILE.read_text(encoding="utf-8"))
        except Exception:
            _d = {}
        _d.setdefault("days", {})
        _d.setdefault("mood", {})
    return _d


def _day():
    return clock.strf("%Y-%m-%d")             # 北京时间的一天（10-27 起）


def add(key, n=1):
    """计数 +n（key 例如 recv_group / sent_private / voice / stickers / searches）"""
    global _dirty
    if not n:
        return
    d = _load()["days"].setdefault(_day(), {})
    d[key] = d.get(key, 0) + n
    if key == "voice_chars":                      # MiniMax 按美元
        _spend(n * _prices["tts_per_m_chars"] / 1e6)
    elif key == "searches":                       # DeepSeek 搜索 / 读网页：每千次成功请求（人民币 不分峰谷）
        _spend(_usd(n * _prices["search_cny_per_k"] / 1000))
    elif key == "fetches":
        _spend(_usd(n * _prices["fetch_cny_per_k"] / 1000))
    _dirty = True
    flush()


def llm(usage, model="", tag=""):
    """一次大模型调用的 token（DeepSeek 返回的 usage）；tag = 哪个功能用的（聊天回复/心情裁判/学梗…）"""
    u = usage or {}
    try:
        hit = int(u.get("prompt_cache_hit_tokens") or 0)
        miss = u.get("prompt_cache_miss_tokens")
        miss = int(miss) if miss is not None else max(0, int(u.get("prompt_tokens") or 0) - hit)
        out = int(u.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return
    d = _load()["days"].setdefault(_day(), {})
    d["llm_calls"] = d.get("llm_calls", 0) + 1
    d["tok_hit"] = d.get("tok_hit", 0) + hit
    d["tok_miss"] = d.get("tok_miss", 0) + miss
    d["tok_out"] = d.get("tok_out", 0) + out
    b = d.setdefault("by", {}).setdefault(str(tag or "其他")[:16], {})
    for k, v in (("calls", 1), ("miss", miss), ("hit", hit), ("out", out)):
        b[k] = b.get(k, 0) + v
    pk = peak_now()
    if pk:                                        # 高峰时段的那部分 token 另记一份（控制台按这个算翻倍的钱）
        for k, v in (("miss", miss), ("hit", hit), ("out", out)):
            d["tok_" + k + "_pk"] = d.get("tok_" + k + "_pk", 0) + v
            b[k + "_pk"] = b.get(k + "_pk", 0) + v
    cny = (miss * _prices["cny_miss"] + hit * _prices["cny_hit"] + out * _prices["cny_out"]) / 1e6
    _spend(_usd(cny * (_prices["peak_mult"] if pk else 1.0)))
    global _dirty
    _dirty = True
    flush()


def _sample_mood():
    hour = clock.strf("%Y-%m-%d %H")
    m = _load()["mood"]
    if hour in m:
        return
    try:
        v = json.loads(MOOD.read_text(encoding="utf-8")).get("mood")
        if v is not None:
            m[hour] = round(float(v), 1)
    except Exception:
        pass


def flush(force=False):
    """最多 20 秒写一次盘（force=True 马上写）"""
    global _dirty, _last
    if not (_dirty or force) or (not force and time.time() - _last < 20):
        return
    d = _load()
    _sample_mood()
    cut = clock.strf("%Y-%m-%d", time.time() - KEEP_DAYS * 86400)
    for k in [k for k in d["days"] if k < cut]:
        del d["days"][k]
    for k in [k for k in d["mood"] if k[:10] < cut]:
        del d["mood"][k]
    hcut = clock.strf("%Y-%m-%d %H", time.time() - KEEP_HOURS * 3600)
    for k in [k for k in (d.get("hours") or {}) if k < hcut]:
        del d["hours"][k]
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, FILE)
        _dirty, _last = False, time.time()
    except Exception:
        pass
