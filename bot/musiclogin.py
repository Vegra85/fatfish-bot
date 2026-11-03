#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音乐平台扫码登录（11-01 v1.16.0）
  主人在本机控制台「唱歌」页点「登录」→ 这里去平台拿二维码 → 他用手机上的 网易云 / QQ 音乐（或 QQ）/ 酷狗 App 扫一下、点确认
  → 登录态（cookie）存在 data/music_login.json → 点歌时 music.py 拿 cookies(平台) 带上，用的是他自己账号的权限：
    他能听的才放；VIP 歌、只给大陆放的歌他本来就听不了的 照样听不了 —— 不绕过任何限制。机器人从头到尾不碰密码。

  接口（照 CONTRACT_1101 B 块）：
    PLATFORMS = ("netease", "qq", "kugou")
    qr_start(平台)       → {"ok", "key", "img"(data:image/png;base64,...), "why", "url"}
                            key 是这里自己编的（不是平台的 token）；只拿到链接、生成不了图片时 img=""、url=链接、why 写清楚
    qr_poll(平台, key)   → {"state": "wait"|"scanned"|"ok"|"expired"|"error", "nick", "why"}（ok 时已经存好了）
    cookies(平台)        → dict（没登录 / 平台说已经掉线了 = {}）；cookie_header(平台) → "k=v; k2=v2"
    status()             → {平台: {"login", "nick", "since", "last_ok", "vip", "expires", "why", "uin"(只有 qq，打码)}}
    check(平台)          → {"ok", "login", "nick", "vip", "why"}：拿着存的 cookie 问平台「还登着吗、昵称、是不是会员」
                            问不通（网络错）不清 cookie、只记原因；平台明说没登录 → 记成掉线（cookies() 给 {}、文件里先留着）
    logout(平台)         → {"ok": True}：本机删掉这个平台的登录态（不去平台那边注销）

  安全：cookie 的值不进日志、不进 status、不进返回给控制台的任何东西、不进出错原因（网络错只报错误的类名 ——
        httpx 的报错里会带 URL，URL 里可能有 token）。status 里 QQ 号只留后 4 位。文件在 Linux / Mac 上设成只有自己能读。
  网络：全都走 _http(method, url, ...) 这一个函数（测试换成假的）。这台写代码的机器连不上这些网站 ——
        流程是照各平台网页版的做法写的，真机没验证过；哪一步不对 why 里会写是哪一步、平台回了什么码。
  二维码图片：QQ / 酷狗 平台直接给图就用；网易云只给链接 → 装了 qrcode 库用它排点阵；没装 → 用下面自己写的小编码器
              （纯 Python、只做 byte 模式 + L 级纠错 + 版本 1~6，最长 134 个字符，登录链接够用）；PNG 都是自己用 zlib 拼的、不要 Pillow。
              再不行（链接太长）→ img 给空、url 给链接，控制台自己显示链接。
  网易云 weapi 要 AES + RSA：RSA 用 Python 自带的 pow；AES 这里写了一个纯 Python 的（只加密，几百字节的请求慢不了）——
              不用装 pycryptodome / cryptography。
"""
import base64
import hashlib
import json
import os
import random
import re
import secrets
import struct
import threading
import time
import uuid
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import safeio

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data" / "music_login.json"
PLATFORMS = ("netease", "qq", "kugou")
NAMES = {"netease": "网易云音乐", "qq": "QQ 音乐", "kugou": "酷狗音乐"}
MAIN_KEY = {"netease": ("MUSIC_U",), "qq": ("qqmusic_key", "qm_keyst"), "kugou": ("token",)}   # 有这个才算登着
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PENDING_SEC = 300             # 二维码拿到以后 5 分钟还没扫完 = 过期（平台那边一般 2~3 分钟就过期了 这里只是兜底 别一直挂着）
TIMEOUT = 12

_LOCK = threading.RLock()
_PENDING = {}                 # 我们自己编的 key → {"p": 平台, "t": 开始时间, ...平台那边的二维码 token}（只在内存里）


class _Fail(Exception):
    """流程里认出来的错（原因是这里自己写的话 不带 cookie / token）"""


# ───────────── 网络：就这一个函数 测试换成假的 ─────────────
class _Resp:
    def __init__(self, status, content=b"", headers=None, set_cookies=None, url=""):
        self.status = int(status)
        self.content = content if isinstance(content, bytes) else str(content).encode("utf-8")
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        self.set_cookies = dict(set_cookies or {})       # 这次响应 Set-Cookie 里有值的那些（name → value；空值 = 平台在删 cookie 不要）
        self.url = url

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        try:
            return json.loads(self.text)
        except ValueError:
            raise _Fail(f"平台回的不是 JSON（HTTP {self.status}）") from None


def _parse_set_cookie(lines):
    out = {}
    for line in lines or []:
        first = str(line).split(";", 1)[0]
        if "=" not in first:
            continue
        k, v = first.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and v:
            out[k] = v
    return out


def _http(method, url, *, params=None, data=None, js=None, headers=None, cookies=None, timeout=TIMEOUT):
    """发一个请求、不跟跳转（QQ 那几步要从跳转地址里拿东西）→ _Resp。网络错直接抛（外面统一变成不带细节的原因）"""
    import httpx
    h = {"User-Agent": UA}
    h.update(headers or {})
    if cookies:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    with httpx.Client(timeout=timeout, follow_redirects=False) as c:
        r = c.request(method, url, params=params, data=data, json=js, headers=h)
        return _Resp(r.status_code, r.content, dict(r.headers), _parse_set_cookie(r.headers.get_list("set-cookie")), str(r.url))


def _ewhy(e):
    """出错原因：自己认出来的照说；别的只报类名（httpx 的报错文字里有 URL，URL 里可能有 token / cookie）"""
    if isinstance(e, _Fail):
        return str(e)
    name = type(e).__name__
    if any(w in name for w in ("Timeout", "Connect", "Network", "Proxy", "Transport", "Protocol", "Read", "Write", "SSL", "OSError")):
        return f"连不上平台（{name}）"
    return f"出错了（{name}）"


def _ok_http(r, what):
    if r.status >= 400:
        raise _Fail(f"{what}：平台回 HTTP {r.status}")
    return r


# ───────────── 存登录态 ─────────────
def _load():
    """读 data/music_login.json。没有 / 坏了（不是合法 JSON、结构不对）→ 当没登录（坏文件 safeio 会另存一份 .bad）"""
    try:
        d = safeio.read_json(STORE, {})
    except Exception:
        d = {}
    if not isinstance(d, dict):
        return {}
    out = {}
    for p in PLATFORMS:
        e = d.get(p)
        if isinstance(e, dict) and isinstance(e.get("cookies"), dict):
            e["cookies"] = {str(k): str(v) for k, v in e["cookies"].items() if v not in (None, "")}
            out[p] = e
    return out


def _save(d):
    safeio.write_json(STORE, d)
    try:
        os.chmod(STORE, 0o600)          # 只有自己能读（Windows 上这句基本不起作用 不要紧）
    except OSError:
        pass


def _has_main(p, e):
    ck = (e or {}).get("cookies") or {}
    return any(ck.get(k) for k in MAIN_KEY[p])


def _store_login(p, ck, nick="", uin="", expires=None, extra=None):
    now = int(time.time())
    with _LOCK:
        d = _load()
        d[p] = {"cookies": {k: str(v) for k, v in ck.items() if v not in (None, "")}, "nick": nick or "", "since": now,
                "last_ok": now, "vip": None, "expires": expires, "why": "", "dead": 0}
        if uin:
            d[p]["uin"] = str(uin)
        if extra:
            d[p]["extra"] = extra          # 以后续期用的东西（QQ 的 refresh_key 之类）—— 也是秘密 不进 status
        _save(d)


def _mask(uin):
    s = re.sub(r"\D", "", str(uin or "")).lstrip("0")
    return ("*" * max(len(s) - 4, 2) + s[-4:]) if s else ""


def cookies(platform):
    """点歌带的 cookie（dict 副本）。没登录、或者上次问平台它明说已经掉线了 → {}"""
    if platform not in PLATFORMS:
        return {}
    with _LOCK:
        e = _load().get(platform)
    if not e or e.get("dead") or not _has_main(platform, e):
        return {}
    return dict(e["cookies"])


def cookie_header(platform):
    return "; ".join(f"{k}={v}" for k, v in cookies(platform).items())


def status():
    """给控制台看：只有 登没登着 / 昵称 / 什么时候登的 / 上次确认还登着是什么时候 / 会员 / 什么时候过期（知道的话）/ 原因。
    没有任何 cookie 值；QQ 号只留后 4 位"""
    with _LOCK:
        d = _load()
    out = {}
    for p in PLATFORMS:
        e = d.get(p) or {}
        has = bool(e) and _has_main(p, e)
        why = e.get("why") or ""
        if has and e.get("dead"):
            why = why or "平台说登录已经失效了，重新扫码"
        s = {"login": bool(has and not e.get("dead")), "nick": e.get("nick") or "", "since": e.get("since") if has else None,
             "last_ok": e.get("last_ok") if has else None, "vip": e.get("vip") if has else None,
             "expires": e.get("expires") if has else None, "why": why if has else ""}
        if p == "qq":
            s["uin"] = _mask(e.get("uin")) if has else ""
        out[p] = s
    return out


def logout(platform, log=None):
    """本机删掉这个平台的登录态（和还没扫完的二维码）"""
    if platform not in PLATFORMS:
        return {"ok": False, "why": "不认识的平台"}
    with _LOCK:
        for k in [k for k, v in _PENDING.items() if v.get("p") == platform]:
            _PENDING.pop(k, None)
        d = _load()
        had = d.pop(platform, None)
        _save(d)
    if log and had:
        log(f"[音乐登录] {NAMES[platform]} 退出了（本机的登录态删掉了）")
    return {"ok": True, "why": ""}


# ───────────── 二维码图片（PNG 自己拼 不要 Pillow） ─────────────
def _png(matrix, scale=8, border=4):
    n = len(matrix)
    size = (n + border * 2) * scale
    raw = bytearray()
    blank = b"\x00" + b"\xff" * size
    for _ in range(border * scale):
        raw += blank
    for row in matrix:
        line = bytearray(b"\xff" * (border * scale))
        for v in row:
            line += (b"\x00" if v else b"\xff") * scale
        line += b"\xff" * (border * scale)
        for _ in range(scale):
            raw += b"\x00" + line
    for _ in range(border * scale):
        raw += blank

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _data_uri(png):
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


# 自己写的小 QR 编码器（主人电脑上没装 qrcode 库时用）：byte 模式、L 级纠错、版本 1~6
# 版本 → (数据码字数, 每块纠错码字数, 块数, 对齐图案中心)
_QR_L = {1: (19, 7, 1, []), 2: (34, 10, 1, [6, 18]), 3: (55, 15, 1, [6, 22]), 4: (80, 20, 1, [6, 26]),
         5: (108, 26, 1, [6, 30]), 6: (136, 18, 2, [6, 34])}
_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _rs_ec(data, n):
    gen = [1]
    for i in range(n):
        nxt = [0] * (len(gen) + 1)
        for j, g in enumerate(gen):
            nxt[j] ^= g
            if g:
                nxt[j + 1] ^= _GF_EXP[_GF_LOG[g] + i]
        gen = nxt
    rem = list(data) + [0] * n
    for i in range(len(data)):
        c = rem[i]
        if c:
            lc = _GF_LOG[c]
            for j in range(1, len(gen)):
                if gen[j]:
                    rem[i + j] ^= _GF_EXP[lc + _GF_LOG[gen[j]]]
    return rem[len(data):]


_MASKS = [lambda i, j: (i + j) % 2 == 0, lambda i, j: i % 2 == 0, lambda i, j: j % 3 == 0,
          lambda i, j: (i + j) % 3 == 0, lambda i, j: (i // 2 + j // 3) % 2 == 0,
          lambda i, j: (i * j) % 2 + (i * j) % 3 == 0, lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
          lambda i, j: ((i * j) % 3 + (i + j) % 2) % 2 == 0]


def _qr_codewords(data, ver):
    ndata, nec, nblk, _ = _QR_L[ver]
    bits = []

    def put(v, n):
        bits.extend((v >> (n - 1 - k)) & 1 for k in range(n))
    put(0b0100, 4)
    put(len(data), 8)
    for b in data:
        put(b, 8)
    limit = ndata * 8
    bits.extend([0] * min(4, limit - len(bits)))
    if len(bits) % 8:
        bits.extend([0] * (8 - len(bits) % 8))
    cw = [int("".join(map(str, bits[k:k + 8])), 2) for k in range(0, len(bits), 8)]
    pad = 0
    while len(cw) < ndata:
        cw.append(0xEC if pad % 2 == 0 else 0x11)
        pad += 1
    per = ndata // nblk
    blocks = [cw[k * per:(k + 1) * per] for k in range(nblk)]
    ecs = [_rs_ec(b, nec) for b in blocks]
    out = [b[k] for k in range(per) for b in blocks]
    out += [e[k] for k in range(nec) for e in ecs]
    return out


def _qr_matrix(data, ver, mask):
    n = ver * 4 + 17
    m = [[None] * n for _ in range(n)]
    for row, col in ((0, 0), (n - 7, 0), (0, n - 7)):        # 三个角的定位图案（带一圈白边）
        for r in range(-1, 8):
            if not 0 <= row + r < n:
                continue
            for c in range(-1, 8):
                if not 0 <= col + c < n:
                    continue
                m[row + r][col + c] = ((0 <= r <= 6 and c in (0, 6)) or (0 <= c <= 6 and r in (0, 6)) or
                                       (2 <= r <= 4 and 2 <= c <= 4))
    pos = _QR_L[ver][3]
    for row in pos:                                           # 对齐图案
        for col in pos:
            if m[row][col] is not None:
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    m[row + r][col + c] = r in (-2, 2) or c in (-2, 2) or (r == 0 and c == 0)
    for k in range(8, n - 8):                                 # 时序线
        if m[k][6] is None:
            m[k][6] = k % 2 == 0
        if m[6][k] is None:
            m[6][k] = k % 2 == 0
    fmt = (1 << 3) | mask                                     # L 级 = 01
    d = fmt << 10
    while d.bit_length() >= 11:
        d ^= 0x537 << (d.bit_length() - 11)
    fbits = ((fmt << 10) | d) ^ 0x5412
    for i in range(15):                                       # 格式信息（两份）
        v = bool((fbits >> i) & 1)
        if i < 6:
            m[i][8] = v
        elif i < 8:
            m[i + 1][8] = v
        else:
            m[n - 15 + i][8] = v
        if i < 8:
            m[8][n - i - 1] = v
        elif i < 9:
            m[8][15 - i] = v
        else:
            m[8][15 - i - 1] = v
    m[n - 8][8] = True                                        # 固定的那个黑点
    cw = _qr_codewords(data, ver)
    fn = _MASKS[mask]
    inc, row, bit, idx = -1, n - 1, 7, 0
    for col in range(n - 1, 0, -2):                           # 之字形填数据
        if col <= 6:
            col -= 1
        while True:
            for c in (col, col - 1):
                if m[row][c] is None:
                    dark = idx < len(cw) and bool((cw[idx] >> bit) & 1)
                    if fn(row, c):
                        dark = not dark
                    m[row][c] = dark
                    bit -= 1
                    if bit < 0:
                        idx += 1
                        bit = 7
            row += inc
            if row < 0 or row >= n:
                row -= inc
                inc = -inc
                break
    return m


def _penalty(m):
    n = len(m)
    pts = 0
    for lines in (m, [list(x) for x in zip(*m)]):             # 规则 1：一行 / 一列连着 5 个以上同色
        for line in lines:
            run = 1
            for k in range(1, n):
                if line[k] == line[k - 1]:
                    run += 1
                else:
                    pts += run - 2 if run >= 5 else 0
                    run = 1
            pts += run - 2 if run >= 5 else 0
    for r in range(n - 1):                                    # 规则 2：2×2 同色块
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                pts += 3
    dark = sum(map(sum, m))                                   # 规则 4：黑白比例（规则 3 省了 扫码不受影响）
    pts += abs(dark * 100 // (n * n) - 50) // 5 * 10
    return pts


def _qr_own(text):
    data = text.encode("utf-8")
    for ver in sorted(_QR_L):
        if len(data) + 2 <= _QR_L[ver][0]:                    # 2 = 模式 4 位 + 长度 8 位 + 结束位（向上取整）
            break
    else:
        return None
    best = min(range(8), key=lambda k: _penalty(_qr_matrix(data, ver, k)))
    return _qr_matrix(data, ver, best)


def _qr_png(text):
    """链接 → (PNG 字节, 用的哪个)；都不行 → (None, 原因)"""
    try:
        import qrcode
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
        qr.add_data(text)
        qr.make(fit=True)
        return _png([[bool(v) for v in row] for row in qr.get_matrix()]), "qrcode"
    except ImportError:
        pass
    except Exception:
        pass                              # qrcode 库自己出错了：退回自己的
    try:
        mat = _qr_own(text)
    except Exception:
        mat = None
    if mat is None:
        return None, "没装 qrcode 库、链接又太长 自带的小编码器排不下 —— 控制台直接显示链接"
    return _png(mat), "自带编码器"


# ───────────── 网易云：weapi 加密（AES 纯 Python + RSA 用 pow） ─────────────
def _aes_tables():
    sbox = [0] * 256
    p = q = 1
    while True:
        p = (p ^ (p << 1) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q
        for s in (1, 2, 3, 4):
            x ^= ((q << s) | (q >> (8 - s))) & 0xFF
        sbox[p] = x ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


_SBOX = _aes_tables()


def _xt(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _aes_keys(key):
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= rcon
            rcon = _xt(rcon)
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [sum(w[r * 4:r * 4 + 4], []) for r in range(11)]


def _aes_block(s, rk):
    s = [a ^ b for a, b in zip(s, rk[0])]
    for rnd in range(1, 11):
        s = [_SBOX[b] for b in s]
        s = [s[(r + 4 * ((c + r) % 4))] for c in range(4) for r in range(4)]     # ShiftRows（按列存）
        if rnd != 10:
            t = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                t += [_xt(a0) ^ _xt(a1) ^ a1 ^ a2 ^ a3, a0 ^ _xt(a1) ^ _xt(a2) ^ a2 ^ a3,
                      a0 ^ a1 ^ _xt(a2) ^ _xt(a3) ^ a3, _xt(a0) ^ a0 ^ a1 ^ a2 ^ _xt(a3)]
            s = t
        s = [a ^ b for a, b in zip(s, rk[rnd])]
    return s


def _aes_cbc(data, key, iv):
    """AES-128-CBC 加密（data 已经补齐到 16 的倍数）"""
    rk = _aes_keys(key)
    prev, out = list(iv), bytearray()
    for i in range(0, len(data), 16):
        prev = _aes_block([a ^ b for a, b in zip(data[i:i + 16], prev)], rk)
        out += bytes(prev)
    return bytes(out)


def _aes_b64(text, key):
    n = 16 - len(text) % 16
    return base64.b64encode(_aes_cbc(text + bytes([n]) * n, key, NE_IV)).decode("ascii")


# 网易云网页版 core.js 里公开写着的参数（谁都一样 不是谁的秘密）
NE_PRESET = b"0CoJUm6Qyw8W8jud"
NE_IV = b"0102030405060708"
NE_RSA_E = 0x010001
NE_RSA_N = int("00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e41"
               "7629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b4"
               "24d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7", 16)
_B62 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _weapi(obj):
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sec = "".join(random.SystemRandom().choice(_B62) for _ in range(16)).encode("ascii")
    params = _aes_b64(_aes_b64(text, NE_PRESET).encode("ascii"), sec)
    enc = pow(int(sec[::-1].hex(), 16), NE_RSA_E, NE_RSA_N)
    return {"params": params, "encSecKey": format(enc, "x").zfill(256)}


NE_BASE = "https://music.163.com"
NE_HEAD = {"Referer": "https://music.163.com/", "Origin": "https://music.163.com",
           "Content-Type": "application/x-www-form-urlencoded"}


def _ne_post(path, obj, ck):
    csrf = ck.get("__csrf", "")
    body = dict(obj, csrf_token=csrf)
    send = dict({"os": "pc", "appver": "2.9.11"}, **ck)
    r = _http("POST", f"{NE_BASE}/weapi/{path}?csrf_token={csrf}", data=_weapi(body), headers=NE_HEAD, cookies=send)
    return _ok_http(r, "网易云"), r.json()


def _ne_start():
    r, j = _ne_post("login/qrcode/unikey", {"type": 1}, {})
    key = j.get("unikey") if isinstance(j, dict) else None
    if not key or j.get("code") != 200:
        msg = str((j.get("message") or j.get("msg") or "") if isinstance(j, dict) else "")[:40]
        raise _Fail(f"网易云没给二维码（code={j.get('code') if isinstance(j, dict) else '?'}{'：' + msg if msg else ''}）")
    return {"unikey": key, "ck": dict(r.set_cookies)}, None, f"https://music.163.com/login?codekey={key}"


def _ne_poll(s):
    r, j = _ne_post("login/qrcode/client/login", {"key": s["unikey"], "type": 1}, s["ck"])
    s["ck"].update(r.set_cookies)
    code = j.get("code")
    if code == 801:
        return "wait", "", ""
    if code == 802:
        return "scanned", str(j.get("nickname") or ""), ""
    if code == 800:
        return "expired", "", "二维码过期了"
    if code == 803:
        if not s["ck"].get("MUSIC_U"):
            raise _Fail("网易云说登好了 但没给 MUSIC_U（登录态没拿到）")
        _store_login("netease", s["ck"], s.get("nick", ""))
        return "ok", s.get("nick", ""), ""
    msg = j.get("message") or j.get("msg") or ""
    raise _Fail(f"网易云回 code={code}" + (f"：{str(msg)[:40]}" if msg else ""))


def _ne_check(ck):
    _, j = _ne_post("w/nuser/account/get", {}, ck)
    prof, acct = j.get("profile"), j.get("account") or {}
    if j.get("code") == 200 and isinstance(prof, dict):
        vt = max(int(acct.get("vipType") or 0), int(prof.get("vipType") or 0))
        return True, str(prof.get("nickname") or ""), vt > 0
    if j.get("code") in (200, 301) and not prof:
        return False, "", None
    raise _Fail(f"网易云回 code={j.get('code')}")


# ───────────── QQ 音乐：QQ 的 ptlogin 二维码 → QQ 互联授权 → 换 QQ 音乐登录态（照 y.qq.com 网页版的流程） ─────────────
QQ_APPID = "716027609"        # QQ 互联登录框的 appid（网页版公开参数）
QQ_DAID = "383"
QQ_3RD = "100497308"          # QQ 音乐在 QQ 互联的 client_id
QQ_U1 = "https://graph.qq.com/oauth2.0/login_jump"
QQ_REDIRECT = "https://y.qq.com/portal/wx_redirect.html?login_type=1&surl=https://y.qq.com/"
QQ_MUSICU = "https://u.y.qq.com/cgi-bin/musicu.fcg"


def _hash33(s):
    """ptqrtoken = qrsig 算出来的（ptlogin 的 js 里的 hash33）"""
    e = 0
    for ch in s:
        e += (e << 5) + ord(ch)
    return e & 0x7FFFFFFF


def _gtk(s):
    """g_tk（QQ 各种接口的那个 5381 哈希）"""
    h = 5381
    for ch in s:
        h += (h << 5) + ord(ch)
    return h & 0x7FFFFFFF


def _qq_start():
    q = {"appid": QQ_APPID, "e": "2", "l": "M", "s": "3", "d": "72", "v": "4", "t": str(random.random()),
         "daid": QQ_DAID, "pt_3rd_aid": QQ_3RD, "u1": QQ_U1}
    r = _ok_http(_http("GET", "https://ssl.ptlogin2.qq.com/ptqrshow", params=q,
                       headers={"Referer": "https://xui.ptlogin2.qq.com/"}), "QQ 二维码")
    qrsig = r.set_cookies.get("qrsig")
    if not qrsig or not r.content.startswith(b"\x89PNG"):
        raise _Fail("QQ 没给二维码（没有 qrsig 或者不是图片）")
    return {"qrsig": qrsig}, r.content, ""


def _qq_poll(s):
    q = {"u1": QQ_U1, "ptqrtoken": str(_hash33(s["qrsig"])), "ptredirect": "0", "h": "1", "t": "1", "g": "1",
         "from_ui": "1", "ptlang": "2052", "action": f"0-0-{int(time.time() * 1000)}", "js_ver": "24112817",
         "js_type": "1", "login_sig": "", "pt_uistyle": "40", "aid": QQ_APPID, "daid": QQ_DAID,
         "pt_3rd_aid": QQ_3RD, "has_onekey": "1"}
    r = _ok_http(_http("GET", "https://ssl.ptlogin2.qq.com/ptqrlogin", params=q, cookies={"qrsig": s["qrsig"]},
                       headers={"Referer": "https://xui.ptlogin2.qq.com/"}), "QQ 扫码状态")
    args = re.findall(r"'([^']*)'", r.text)
    if not r.text.lstrip().startswith("ptuiCB") or len(args) < 5:
        raise _Fail("QQ 回的扫码状态看不懂")
    code, jump, msg = args[0], args[2], args[4]
    nick = args[5] if len(args) > 5 else ""
    if code == "66":
        return "wait", "", ""
    if code == "67":
        return "scanned", nick, ""
    if code == "65":
        return "expired", "", "二维码过期了"
    if code == "68":
        return "expired", "", "手机上点了拒绝"
    if code != "0":
        raise _Fail(f"QQ 回 {code}：{msg[:40]}")
    return _qq_finish(jump, nick or s.get("nick", ""))


def _qq_finish(jump, nick):
    host = urlparse(jump).hostname or ""
    if not jump.startswith("https://") or not (host == "qq.com" or host.endswith(".qq.com")):
        raise _Fail("QQ 给的跳转地址不对（不是 qq.com）")
    # 1 跟着跳一下：拿到 QQ 的登录 cookie（p_skey 等 —— 只在这几步里用 不存：那是整个 QQ 号的登录态 管的不止听歌）
    r = _ok_http(_http("GET", jump, headers={"Referer": "https://xui.ptlogin2.qq.com/"}), "QQ 登录跳转")
    pt = dict(r.set_cookies)
    if not pt.get("p_skey"):
        raise _Fail("QQ 跳转后没拿到 p_skey（换 QQ 音乐登录态要用）")
    uin = re.sub(r"\D", "", pt.get("uin") or pt.get("p_uin") or "").lstrip("0")
    # 2 QQ 互联授权给 QQ 音乐 → 跳转地址里有 code
    form = {"response_type": "code", "client_id": QQ_3RD, "redirect_uri": QQ_REDIRECT,
            "scope": "get_user_info,get_app_friends", "state": "state", "switch": "", "from_ptlogin": "1", "src": "1",
            "update_auth": "1", "openapi": "1010_1030", "g_tk": str(_gtk(pt["p_skey"])),
            "auth_time": str(int(time.time() * 1000)), "ui": str(uuid.uuid4())}
    r = _http("POST", "https://graph.qq.com/oauth2.0/authorize", data=form, cookies=pt,
              headers={"Referer": "https://graph.qq.com/", "Content-Type": "application/x-www-form-urlencoded"})
    loc = r.headers.get("location") or ""
    code = (parse_qs(urlparse(loc).query).get("code") or [""])[0]
    if not code:
        raise _Fail(f"QQ 互联没给授权码（HTTP {r.status}）")
    # 3 授权码换 QQ 音乐的登录态
    body = {"comm": {"g_tk": 5381, "platform": "yqq", "ct": 24, "cv": 0},
            "req": {"module": "QQConnectLogin.LoginServer", "method": "QQLogin", "param": {"code": code}}}
    r = _ok_http(_http("POST", QQ_MUSICU, js=body, headers={"Referer": "https://y.qq.com/"}), "QQ 音乐换登录态")
    j = r.json()
    req = j.get("req") if isinstance(j, dict) else None
    if not isinstance(req, dict) or req.get("code") != 0:
        raise _Fail(f"QQ 音乐换登录态没成（code={(req or {}).get('code')}）")
    data = req.get("data") or {}
    ck = dict(r.set_cookies)
    key = data.get("musickey") or ck.get("qqmusic_key") or ck.get("qm_keyst")
    mid = str(data.get("musicid") or ck.get("uin") or uin or "")
    if not key or not mid:
        raise _Fail("QQ 音乐换登录态：没拿到 musickey / uin")
    ck.setdefault("qqmusic_key", key)
    ck.setdefault("qm_keyst", key)
    ck["uin"] = mid
    ck.setdefault("tmeLoginType", "2")                      # 2 = QQ 号登录（网页版这么写）
    if data.get("encryptUin"):
        ck.setdefault("euin", str(data["encryptUin"]))
    exp = None
    try:
        sec = int(data.get("keyExpiresIn") or 0)
        exp = int(time.time()) + sec if sec > 0 else None
    except (TypeError, ValueError):
        pass
    extra = {k: str(data[k]) for k in ("refresh_key", "refresh_token") if data.get(k)}
    _store_login("qq", ck, nick, uin=mid, expires=exp, extra=extra or None)
    return "ok", nick, ""


def _qq_comm(ck):
    key = ck.get("qqmusic_key") or ck.get("qm_keyst") or ""
    return {"g_tk": _gtk(key), "uin": ck.get("uin", ""), "format": "json", "ct": 24, "cv": 0,
            "platform": "yqq.json", "authst": key}


def _qq_check(ck):
    q = {"format": "json", "cid": "205360838", "reqfrom": "1", "reqtype": "0", "userid": ck.get("uin", "")}
    r = _ok_http(_http("GET", "https://c.y.qq.com/rsc/fcgi-bin/fcg_get_profile_homepage.fcg", params=q, cookies=ck,
                       headers={"Referer": "https://y.qq.com/"}), "QQ 音乐")
    j = r.json()
    if j.get("code") == 1000:
        return False, "", None
    if j.get("code") != 0:
        raise _Fail(f"QQ 音乐回 code={j.get('code')}")
    nick = str(((j.get("data") or {}).get("creator") or {}).get("nick") or "")
    vip = None
    try:                                                    # 会员：问不出来就是「不知道」（不影响登没登着）
        body = {"comm": _qq_comm(ck), "req": {"module": "userInfo.VipQueryServer", "method": "SRFVipQuery_V2",
                                              "param": {"uin_list": [ck.get("uin", "")]}}}
        v = _http("POST", QQ_MUSICU, js=body, cookies=ck, headers={"Referer": "https://y.qq.com/"}).json()
        info = (((v.get("req") or {}).get("data") or {}).get("infoMap") or {}).get(ck.get("uin", "")) or {}
        if "iVipFlag" in info:
            vip = bool(int(info.get("iVipFlag") or 0) or int(info.get("iSuperVip") or 0))
    except Exception:
        pass
    return True, nick, vip


# ───────────── 酷狗：网页版的二维码登录（login-user.kugou.com） ─────────────
KG_APPID = "1014"             # 酷狗网页版的 appid / srcappid（公开参数）
KG_SRC = "2919"
KG_VER = "20000"
KG_SIGN = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"      # 酷狗网页版 js 里给请求签名用的公开串（谁都一样）
KG_QR_PAGE = "https://h5.kugou.com/apps/loginQRCode/html/index.html"


def _kg_sign(q):
    s = "".join(sorted(f"{k}={v}" for k, v in q.items()))
    return hashlib.md5((KG_SIGN + s + KG_SIGN).encode("utf-8")).hexdigest()


# 11-01 v1.16.2：小明 扫码时酷狗回 error_code=20006（v1 接口 + appid 1014 + 毫秒时间戳不认）。
#   这边连不上酷狗、没法一个个试 → 按「最像现在网页版」的顺序几种写法挨个试，哪种给了二维码就用哪种（轮询跟它一样），都不行把每种的码都报出来
KG_WAYS = (("v2", "1001", "s"), ("v2", "1014", "s"), ("v2", "1001", "ms"), ("v1", "1014", "ms"), ("v1", "1001", "s"))


def _kg_params(mid, appid=KG_APPID, ts="ms", **more):
    now = time.time()
    q = {"appid": str(appid), "srcappid": KG_SRC, "clientver": KG_VER,
         "clienttime": str(int(now * 1000) if ts == "ms" else int(now)), "mid": mid, "uuid": mid, "dfid": "-"}
    q.update({k: str(v) for k, v in more.items()})
    q["signature"] = _kg_sign(q)
    return q


def _kg_get(url, q, ck=None):
    r = _ok_http(_http("GET", url, params=q, cookies=ck, headers={"Referer": "https://www.kugou.com/"}), "酷狗")
    return r, r.json()


def _kg_start():
    mid = hashlib.md5(secrets.token_bytes(16)).hexdigest()
    tried, got = [], None
    for ver, appid, ts in KG_WAYS:
        r, j = _kg_get(f"https://login-user.kugou.com/{ver}/qrcode",
                       _kg_params(mid, appid, ts, type=1, plat=4, qrcode_txt=f"{KG_QR_PAGE}?appid={appid}&"))
        d = j.get("data") if isinstance(j, dict) else None
        if isinstance(j, dict) and j.get("status") == 1 and isinstance(d, dict) and d.get("qrcode"):
            got = (ver, appid, ts)
            break
        tried.append(f"{ver}/{appid}/{ts}:{j.get('error_code') if isinstance(j, dict) else '?'}")
    if not got:
        raise _Fail("酷狗没给二维码（试了几种写法 都不认：" + "、".join(tried) + "）")
    key = str(d["qrcode"])
    img = d.get("qrcode_img") or ""
    png = None
    if img:
        try:
            png = base64.b64decode(img.split(",", 1)[1] if img.startswith("data:") else img)
            if not png.startswith(b"\x89PNG"):
                png = None
        except (ValueError, TypeError):
            png = None
    return {"qr": key, "mid": mid, "ck": dict(r.set_cookies), "way": list(got)}, png, f"{KG_QR_PAGE}?qrcode={key}"


def _kg_poll(s):
    ver, appid, ts = s.get("way") or ("v1", KG_APPID, "ms")        # 跟给二维码的那种写法一样（11-01 v1.16.2）
    r, j = _kg_get(f"https://login-user.kugou.com/{ver}/get_userinfo_qrcode",
                   _kg_params(s["mid"], appid, ts, plat=4, qrcode=s["qr"]), s["ck"] or None)
    s["ck"].update(r.set_cookies)
    d = j.get("data") if isinstance(j, dict) else None
    if j.get("status") != 1 or not isinstance(d, dict):
        raise _Fail(f"酷狗回 error_code={j.get('error_code')}")
    st = d.get("status")
    if st == 1:
        return "wait", "", ""
    if st == 2:
        return "scanned", str(d.get("nickname") or ""), ""
    if st == 0:
        return "expired", "", "二维码过期了"
    if st == 4:
        if not d.get("token") or not d.get("userid"):
            raise _Fail("酷狗说登好了 但没给 token / userid")
        nick = str(d.get("nickname") or s.get("nick") or "")
        ck = dict(s["ck"])
        ck.update({"token": str(d["token"]), "userid": str(d["userid"]), "kg_mid": s["mid"], "kg_dfid": "-"})
        _store_login("kugou", ck, nick)
        return "ok", nick, ""
    raise _Fail(f"酷狗回的扫码状态看不懂（status={st}）")


def _kg_check(ck):
    q = _kg_params(ck.get("kg_mid") or "-", busi_type="concept", opt_product_types="dvip,qvip",
                   userid=ck.get("userid", ""), token=ck.get("token", ""))
    _, j = _kg_get("https://kugouvip.kugou.com/v1/get_union_vip", q, ck)
    if j.get("status") == 1:
        d = j.get("data") or {}
        vip = None
        if "is_vip" in d:
            vip = bool(int(d.get("is_vip") or 0))
        return True, "", vip
    raise _Fail(f"酷狗回 error_code={j.get('error_code')}")      # 酷狗哪个码是「掉线了」没验证过：一律当问不通 不记成掉线


_FLOW = {"netease": (_ne_start, _ne_poll, _ne_check), "qq": (_qq_start, _qq_poll, _qq_check),
         "kugou": (_kg_start, _kg_poll, _kg_check)}


# ───────────── 对外 ─────────────
def qr_start(platform, log=None):
    """去平台拿一张登录二维码。key 是这里编的（不是平台的 token），qr_poll 时带回来"""
    if platform not in PLATFORMS:
        return {"ok": False, "key": "", "img": "", "url": "", "why": "不认识的平台"}
    try:
        sess, png, url = _FLOW[platform][0]()
    except Exception as e:
        why = _ewhy(e)
        if log:
            log(f"[音乐登录] {NAMES[platform]} 拿二维码没成：{why}")
        return {"ok": False, "key": "", "img": "", "url": "", "why": why}
    why = ""
    if png is None and url:
        png, how = _qr_png(url)
        if png is None:
            why = how
    key = secrets.token_urlsafe(9)
    sess.update(p=platform, t=time.time(), nick="")
    with _LOCK:
        now = time.time()
        for k in [k for k, v in _PENDING.items() if now - v["t"] > PENDING_SEC * 2]:
            _PENDING.pop(k, None)
        _PENDING[key] = sess
    if log:
        log(f"[音乐登录] {NAMES[platform]} 二维码拿到了 等手机扫")
    return {"ok": True, "key": key, "img": _data_uri(png) if png else "", "url": url if not png else "", "why": why}


def qr_poll(platform, key, log=None):
    """问一次扫没扫：wait 等扫 / scanned 扫了等手机上点确认 / ok 登好了（已经存好）/ expired 过期了 / error 出错了"""
    with _LOCK:
        s = _PENDING.get(key)
    if platform not in PLATFORMS:
        return {"state": "error", "nick": "", "why": "不认识的平台"}
    if not s or s.get("p") != platform:
        return {"state": "expired", "nick": "", "why": "这个二维码不认识或者已经用完了，重新拿一个"}
    if time.time() - s["t"] > PENDING_SEC:
        with _LOCK:
            _PENDING.pop(key, None)
        return {"state": "expired", "nick": "", "why": "二维码过期了"}
    try:
        state, nick, why = _FLOW[platform][1](s)
    except Exception as e:
        why = _ewhy(e)
        if log:
            log(f"[音乐登录] {NAMES[platform]} 问扫码状态没成：{why}")
        if isinstance(e, _Fail):                          # 平台明确回了错（不是网络一时不通）→ 这个二维码不要了
            with _LOCK:
                _PENDING.pop(key, None)
        return {"state": "error", "nick": "", "why": why}
    if nick:
        s["nick"] = nick
    if state in ("ok", "expired"):
        with _LOCK:
            _PENDING.pop(key, None)
    if state == "ok":
        if log:
            log(f"[音乐登录] {NAMES[platform]} 登好了（{nick or '没给昵称'}）")
        try:                                              # 顺手问一下昵称 / 会员（问不通不要紧）
            c = check(platform, log=log)
            nick = c.get("nick") or nick
        except Exception:
            pass
    elif state == "expired" and log:
        log(f"[音乐登录] {NAMES[platform]} 二维码{why or '过期了'}")
    return {"state": state, "nick": nick or "", "why": why or ""}


def check(platform, log=None):
    """拿存的 cookie 问平台：还登着吗、昵称、是不是会员。
    问通了 → 记 last_ok / 昵称 / 会员；平台明说没登录 → 记成掉线（cookie 先留着，cookies() 给 {}）；问不通 → 只记原因"""
    if platform not in PLATFORMS:
        return {"ok": False, "login": None, "nick": "", "vip": None, "why": "不认识的平台"}
    with _LOCK:
        e = _load().get(platform)
    if not e or not _has_main(platform, e):
        return {"ok": True, "login": False, "nick": "", "vip": None, "why": "没登录"}
    try:
        login, nick, vip = _FLOW[platform][2](dict(e["cookies"]))
    except Exception as ex:
        why = _ewhy(ex)
        _patch(platform, why=f"{time.strftime('%m-%d %H:%M')} 问平台没问通：{why}")
        if log:
            log(f"[音乐登录] {NAMES[platform]} 查登录状态没问通：{why}（登录态先留着）")
        return {"ok": False, "login": None, "nick": e.get("nick") or "", "vip": e.get("vip"), "why": why}
    if not login:
        _patch(platform, dead=int(time.time()), why="平台说登录已经失效了，重新扫码")
        if log:
            log(f"[音乐登录] {NAMES[platform]} 平台说登录已经失效了 要重新扫码")
        return {"ok": True, "login": False, "nick": e.get("nick") or "", "vip": None, "why": "平台说登录已经失效了，重新扫码"}
    upd = {"last_ok": int(time.time()), "why": "", "dead": 0}
    if nick:
        upd["nick"] = nick
    if vip is not None:
        upd["vip"] = vip
    _patch(platform, **upd)
    return {"ok": True, "login": True, "nick": nick or e.get("nick") or "", "vip": vip if vip is not None else e.get("vip"), "why": ""}


def _patch(platform, **kw):
    with _LOCK:
        d = _load()
        if platform in d:
            d[platform].update(kw)
            _save(d)


if __name__ == "__main__":
    # 本机看一眼（不打印任何 cookie）：python musiclogin.py
    for p, s in status().items():
        print(NAMES[p], json.dumps(s, ensure_ascii=False))
