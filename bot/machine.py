#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取老板电脑的真实配置，让她能骄傲地介绍自己住的地方
拼系统提示（block）只读缓存；PowerShell 交给 bot.py 后台的 machine_loop 在线程里跑（10-28 Gemini 复查 machine）"""
import json
import re
import subprocess
import time
from pathlib import Path

import safeio

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "machine.json"
FAIL_RETRY = 6 * 3600        # 读失败以后隔这么久再试（10-28 Gemini 复查 machine：读失败也记一笔 不然每次都重跑）

PS = r'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$gpus = (Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join '|'
$cs  = Get-CimInstance Win32_ComputerSystem
$os  = Get-CimInstance Win32_OperatingSystem
$d   = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
[pscustomobject]@{
  cpu    = $cpu.Name
  cores  = $cpu.NumberOfCores
  ghz    = [math]::Round($cpu.MaxClockSpeed/1000,2)
  gpus   = $gpus
  ram    = [math]::Round($cs.TotalPhysicalMemory/1GB,0)
  os     = $os.Caption
  disk   = [math]::Round($d.Size/1GB,0)
  free   = [math]::Round($d.FreeSpace/1GB,0)
} | ConvertTo-Json -Compress
'''

DISCRETE = re.compile(r"(RTX|GTX|GeForce|Radeon RX|Arc A|Quadro|Tesla)", re.I)


def _clean(s):
    s = re.sub(r"\((R|TM|C)\)", "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def read():
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS],
                           capture_output=True, timeout=30)
        out = r.stdout.decode("utf-8", errors="ignore").strip()
        if not out:
            out = r.stdout.decode("gbk", errors="ignore").strip()
        i = out.find("{")
        if i < 0:
            return None
        d = json.loads(out[i:])
    except Exception as e:
        print("读取硬件失败:", e)
        return None

    names = [_clean(x) for x in (d.get("gpus") or "").split("|") if x.strip()]
    discrete = [n for n in names if DISCRETE.search(n)]
    d["gpu"] = discrete[0] if discrete else (names[0] if names else "")
    d["cpu"] = _clean(d.get("cpu"))
    d.pop("gpus", None)
    d["ts"] = time.time()
    return d


def refresh():
    """会跑 PowerShell（最长 30 秒）：只在后台线程里调（bot.py machine_loop → asyncio.to_thread）"""
    info = read()
    ok = bool(info)
    if not ok:
        # 读失败也写一笔带 ts 的（负缓存）：过 FAIL_RETRY 再试；以前读到过的配置留着照旧用（10-28 Gemini 复查 machine）
        # 以前读失败不写缓存 → 下一条回复又当场重跑一次（WMI 出毛病时每条回复都卡 最长 30 秒）
        info = {k: v for k, v in load().items() if k not in ("ts", "failed")}
        info.update(ts=time.time(), failed=True)
    safeio.write_json(CACHE, info)           # 后台线程写、回复时读 → 原子写 读不到半截（10-28 Gemini 复查 machine）
    return info if ok else None


def load():
    """只读缓存文件 不跑 PowerShell（10-28 Gemini 复查 machine）
    以前缓存过了一天 / 没有缓存就在这里当场 refresh()：拼系统提示的时候在事件循环里同步跑 PowerShell
    每天第一条回复卡 1~3 秒、读失败（不写缓存）每条回复都卡"""
    try:
        d = json.loads(CACHE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def due():
    """后台该重读了吗：读到过的一天一次；上次读失败（负缓存）隔 FAIL_RETRY 再试（10-28 Gemini 复查 machine）"""
    d = load()
    try:
        ts = float(d.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0.0       # 时间戳坏了：当成没读过 重读一次（10-28 复查：不然 machine_loop 每小时报一次错、永远不重读；以前 load 里出错是当场重读的）
    return time.time() - ts >= (FAIL_RETRY if d.get("failed") else 86400)


def block():
    d = load()                     # 只读缓存（过期的也照用 后台会去刷新）（10-28 Gemini 复查 machine）
    if not (d.get("cpu") or d.get("os")):          # 没缓存 / 只有一笔读失败
        return ""
    lines = ["\n# 你住的这台电脑（你老板的，可以骄傲地介绍）",
             f"- CPU：{d.get('cpu')}，{d.get('cores')} 核 {d.get('ghz')}GHz"]
    if d.get("gpu"):
        lines.append(f"- 显卡：{d['gpu']}")
    lines.append(f"- 内存：{d.get('ram')}GB")
    lines.append(f"- 系统：{d.get('os')}")
    if d.get("disk"):
        lines.append(f"- C 盘：共 {d.get('disk')}GB，还剩 {d.get('free')}GB")
    lines.append("你只知道这些，**别编**你读不到的参数。")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(json.dumps(refresh() or load(), ensure_ascii=False, indent=1))       # 手动跑：真去读一次
    print(block())
