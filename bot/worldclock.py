#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
别的地方现在几点（10-28）

  她只知道北京时间：在国外的人问「我这边现在几点」「伦敦现在几点」她只能心算、还算不准（「应该是七点多 快八点了
  不过我也没把握」）；对着那边晚上七点半的人说「这个点还不睡」「你那边算算也是半夜了吧」
  → 常见地方的时差和夏令时在这里算好（不用 tzdata：Windows 上 zoneinfo 没有 tzdata 会加载失败）
  → 知道他在哪（config behavior.owner_place / 他自己说过「我在英国」）：每轮告诉她他那边现在几点
  → 有人问「英国现在几点」「伦敦那边天亮了没」：把那边现在的时间算好给她 别让她猜

  now_in(place, ts)    那边的当地时间（datetime 不带时区）/ None
  line(place, ts)      「英国现在 10月27日 星期二 19:38（晚上 · 比北京时间晚 8 小时）」
  asked(text)          这句话在问哪儿的时间 →[地名]（地名旁边要有「几点 / 时间 / 时差 / 天亮…」）
  stated(text)         他在说自己在哪（「我在英国」「我人在伦敦读书」「人在多伦多」）→ 地名 / None
  remember / where / forget   谁在哪（data/places.json；删除我的数据时一起删）
  block(...)           这一轮给她看的那段（群里不报私聊里才知道的地方）
"""
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data" / "places.json"

# 时区：(标准时差 小时, 夏令时规则, 这个时区里的地名)
#   eu：3 月最后一个周日 ~ 10 月最后一个周日（UTC 01:00 切）  us：3 月第二个周日 ~ 11 月第一个周日（当地 02:00）
#   au：10 月第一个周日 ~ 4 月第一个周日（南半球）            nz：9 月最后一个周日 ~ 4 月第一个周日
#   bc：加拿大 BC 省 2026-11-01 起一直用夏令时（UTC-7 不再拨回来；IANA tzdata 2026e）  None：没有夏令时
ZONES = [
    (0, "eu", "英国 伦敦 曼彻斯特 伯明翰 利物浦 爱丁堡 格拉斯哥 牛津 剑桥 布里斯托 利兹 谢菲尔德 诺丁汉 纽卡斯尔 卡迪夫 "
              "贝尔法斯特 苏格兰 英格兰 威尔士 爱尔兰 都柏林 葡萄牙 里斯本"),
    (1, "eu", "法国 巴黎 德国 柏林 慕尼黑 法兰克福 汉堡 意大利 罗马 米兰 威尼斯 西班牙 马德里 巴塞罗那 荷兰 阿姆斯特丹 "
              "比利时 布鲁塞尔 瑞士 苏黎世 日内瓦 奥地利 维也纳 瑞典 斯德哥尔摩 挪威 奥斯陆 丹麦 哥本哈根 波兰 华沙 捷克 布拉格 "
              "匈牙利 布达佩斯 欧洲"),
    (2, "eu", "希腊 雅典 芬兰 赫尔辛基 乌克兰 基辅 罗马尼亚 保加利亚"),
    (3, None, "俄罗斯 莫斯科 圣彼得堡 土耳其 伊斯坦布尔"),
    (4, None, "迪拜 阿联酋 阿布扎比"),
    (5.5, None, "印度 新德里 孟买 班加罗尔"),
    (7, None, "泰国 曼谷 清迈 普吉岛 越南 河内 胡志明 印尼 雅加达 柬埔寨"),
    (8, None, "新加坡 马来西亚 吉隆坡 菲律宾 马尼拉 珀斯"),
    (9, None, "日本 东京 大阪 京都 名古屋 札幌 福冈 冲绳 韩国 首尔 釜山"),
    (10, "au", "澳洲 澳大利亚 悉尼 墨尔本 堪培拉"),
    (10, None, "布里斯班 昆士兰"),
    (12, "nz", "新西兰 奥克兰 惠灵顿"),
    (-5, "us", "美东 纽约 波士顿 华盛顿 费城 迈阿密 亚特兰大 多伦多 蒙特利尔 渥太华"),
    (-6, "us", "芝加哥 休斯顿 达拉斯"),
    (-7, "us", "丹佛 盐湖城"),
    (-7, None, "凤凰城 亚利桑那"),
    (-8, "us", "美西 洛杉矶 旧金山 西雅图 圣地亚哥 硅谷 加州 拉斯维加斯 波特兰"),
    (-8, "bc", "温哥华 维多利亚"),
    (-10, None, "夏威夷 檀香山"),
    (-3, None, "巴西 圣保罗 里约 阿根廷 布宜诺斯艾利斯"),
    (-6, None, "墨西哥"),
]
# 一个国家好几个时区：两头都报
MULTI = {"美国": ("美东", "美西"), "加拿大": ("多伦多", "温哥华")}

_ZONE = {}
for _std, _rule, _names in ZONES:
    for _n in _names.split():
        _ZONE[_n] = (_std, _rule)
_ALL = sorted(list(_ZONE) + list(MULTI), key=len, reverse=True)      # 长的先认：「罗马尼亚」别认成「罗马」
PLACE = re.compile("(?<!北)(" + "|".join(map(re.escape, _ALL)) + ")")  # 「北京都几点了」别认成「京都」
TIME_WORDS = re.compile(r"几点|时间|时差|几号|天亮|天黑|白天|晚上|早上|上午|下午|中午|半夜|凌晨|深夜|睡了没|起床|作息|早晚")
# 他说自己在哪（「我在英国」「我现在人在伦敦」「我在多伦多读书」「人在纽约 刚下飞机」）；「我不在英国」「我想去英国」不算
_SELF = re.compile(r"(?:(?<![不没])我(?:现在|目前|这会儿|这阵子|最近|一直)?(?:人)?(?:就)?(?:住在|住|待在|呆在|定居在?|生活在|在)"
                   r"|(?:^|[\s，,。！!？?~～])人在)(?:" + "|".join(map(re.escape, _ALL)) + ")")
WEEK = "一二三四五六日"
BC_ALWAYS = datetime(2026, 11, 1, 9)        # BC 省最后一次拨表：2026-11-01 02:00 PDT（= UTC 09:00）之后一直 UTC-7


def _sunday(y, m, n):
    """y 年 m 月第 n 个周日（n = -1：最后一个）→ datetime（0 点）"""
    if n > 0:
        d = datetime(y, m, 1)
        return d + timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))
    d = datetime(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - 6) % 7)


def _dst(rule, std, utc):
    """utc：不带时区的 UTC 时间 → 这时候那边在不在夏令时"""
    if not rule:
        return False
    y = utc.year
    if rule == "bc":
        if utc >= BC_ALWAYS:
            return True
        rule = "us"
    if rule == "eu":
        return _sunday(y, 3, -1) + timedelta(hours=1) <= utc < _sunday(y, 10, -1) + timedelta(hours=1)
    if rule == "us":
        return (_sunday(y, 3, 2) + timedelta(hours=2 - std) <= utc
                < _sunday(y, 11, 1) + timedelta(hours=2 - std - 1))
    end = _sunday(y, 4, 1) + timedelta(hours=3 - std - 1)            # 南半球：年初到 4 月是夏令时 年底又开始
    start = (_sunday(y, 10, 1) if rule == "au" else _sunday(y, 9, -1)) + timedelta(hours=2 - std)
    return utc < end or utc >= start


def offset(place, ts=None):
    """那边比 UTC 快几小时（含夏令时）/ None"""
    z = _ZONE.get(place)
    if not z:
        return None
    utc = datetime.fromtimestamp(float(ts if ts is not None else time.time()), timezone.utc).replace(tzinfo=None)
    return z[0] + (1 if _dst(z[1], z[0], utc) else 0)


def now_in(place, ts=None):
    off = offset(place, ts)
    if off is None:
        return None
    utc = datetime.fromtimestamp(float(ts if ts is not None else time.time()), timezone.utc).replace(tzinfo=None)
    return utc + timedelta(hours=off)


def _part(h):
    return ("凌晨" if h < 5 else "早上" if h < 8 else "上午" if h < 11 else "中午" if h < 13 else "下午" if h < 17
            else "傍晚" if h < 19 else "晚上" if h < 23 else "深夜")


def _diff(off):
    d = off - 8
    if d == 0:
        return "跟北京时间一样"
    n = abs(d)
    n = int(n) if n == int(n) else n
    return f"比北京时间{'晚' if d < 0 else '早'} {n} 小时"


def line(place, ts=None):
    """「英国现在 10月27日 星期二 19:38（晚上 · 比北京时间晚 8 小时）」；一国好几个时区就都报"""
    if place in MULTI:
        return "；".join(line(p, ts) for p in MULTI[place])
    t, off = now_in(place, ts), offset(place, ts)
    if t is None:
        return ""
    summer = _ZONE[place][1] and off != _ZONE[place][0]
    return (f"{place}现在 {t.month}月{t.day}日 星期{WEEK[t.weekday()]} {t:%H:%M}"
            f"（{_part(t.hour)} · {_diff(off)}" + ("、夏令时" if summer else "") + "）")


def asked(text):
    """这句话在问哪儿现在几点（地名前后 8 个字里有「几点 / 时间 / 天亮…」）→ [地名] 最多 3 个"""
    t = str(text or "")
    out = []
    for m in PLACE.finditer(t):
        win = t[max(0, m.start() - 8):m.end() + 8]
        if TIME_WORDS.search(win) and m.group(1) not in out:
            out.append(m.group(1))
    return out[:3]


def stated(text):
    """他在说自己在哪 → 地名 / None（问句「你在英国吗」不算）"""
    t = str(text or "")
    m = _SELF.search(t)
    if not m:
        return None
    p = PLACE.search(t, m.start())
    head, tail = t[max(0, m.start() - 6):m.start()], t[m.end():m.end() + 10]
    if not p or re.match(r"[吗么嘛?？的]|那种|那边的?人", tail):     # 问句；「我在英国的朋友 / 的时候」不一定是他现在在哪
        return None
    if re.search(r"以前|之前|去年|前年|当年|小时候|那时|那会|如果|要是|假如|万一|梦见|梦到", head) or \
            re.match(r"[^，,。！!？?]{0,8}(?:时候|那会儿?|那年|那阵子)", tail):
        return None                                   # 说的是以前 / 假如
    return p.group(1)


# ───────────── 谁在哪（他自己说的）─────────────
def _load():
    try:
        import safeio
        d = safeio.read_json(STORE, {})
    except Exception:
        d = {}
    return d if isinstance(d, dict) else {}


def remember(uid, place, scene="private", said="", now=None):
    """他自己说了在哪 → 记下（换地方了就改）；返回 True = 新记的 / 改了"""
    uid = str(uid or "")
    if not uid or place not in _ZONE and place not in MULTI:
        return False
    d = _load()
    old = d.get(uid) or {}
    if old.get("place") == place and (old.get("scene") == "group" or scene == "private"):
        return False
    d[uid] = {"place": place, "scene": scene if old.get("place") != place else min(old.get("scene", scene), scene),
              "ts": float(now or time.time()), "said": str(said or "")[:60]}
    try:
        import safeio
        safeio.write_json(STORE, d)
    except Exception:
        return False
    return True


def where(uid):
    return _load().get(str(uid or "")) or None


def forget(uid):
    d = _load()
    if d.pop(str(uid or ""), None) is None:
        return False
    try:
        import safeio
        safeio.write_json(STORE, d)
    except Exception:
        pass
    return True


def block(uid="", is_owner=False, text="", group=False, owner_place="", ts=None):
    """这一轮给她看的：跟她说话的人那边现在几点 + 这句话问到的地方现在几点"""
    s = ""
    rec = where(uid) if uid else None
    place, private_only = "", False
    if rec and (rec.get("place") in _ZONE or rec.get("place") in MULTI):
        place, private_only = rec["place"], rec.get("scene") != "group"
    elif is_owner and owner_place and (owner_place in _ZONE or owner_place in MULTI):
        place, private_only = owner_place, True
    if place:
        s += (f"\n# 他那边的时间\n他在{place}：{line(place, ts)}。"
              "跟他说早晚、睡没睡、吃没吃饭 按他那边的钟点来（他那边晚上七八点就别说「这个点还不睡」）；"
              "他问他那边几点 直接说这个 别猜。")
        if group and private_only:
            s += "（他在哪是私下知道的：群里别说出来、别报他那边几点 —— 只是别说跟他那边钟点对不上的话）"
        s += "\n"
    want = [p for p in asked(text) if p != place or not s]
    if want:
        s += "\n# 问到的地方现在几点（算好的 直接报 别说「应该」「大概」）\n" + "\n".join(line(p, ts) for p in want) + "\n"
    return s
