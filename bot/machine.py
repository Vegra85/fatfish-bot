#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取老板电脑的真实配置，让她能骄傲地介绍自己住的地方"""
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "machine.json"

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
    info = read()
    if not info:
        return None
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def load():
    if CACHE.exists():
        try:
            d = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - d.get("ts", 0) < 86400:
                return d
        except Exception:
            pass
    return refresh()


def block():
    d = load()
    if not d:
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
    print(json.dumps(load(), ensure_ascii=False, indent=1))
    print(block())
