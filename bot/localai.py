#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地模型（Ollama）
  用原生 /api/chat 接口：可以关掉 thinking、可以让模型常驻显存（keep_alive）
  config.json 里 "local" 段控制；Ollama 没开时调用会抛异常，由调用方回落到云端
"""
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
L = CFG.get("local") or {}

BASE = (L.get("base_url") or "http://localhost:11434").rstrip("/")
MODEL = L.get("model") or "qwen3-vl:8b-instruct"
KEEP = L.get("keep_alive") or "30m"
_down_until = 0          # Ollama 挂了就先别反复撞墙，5 分钟后再试


def enabled(task=None):
    if not L.get("enabled"):
        return False
    if task and task not in (L.get("tasks") or []):
        return False
    return time.time() >= _down_until


async def chat(messages, system=None, images=None, model=None,
               temperature=0.3, max_tokens=500, timeout=120):
    """messages: [{"role","content"}]；images: base64 字符串列表，挂到最后一条 user 消息上"""
    global _down_until
    msgs = ([{"role": "system", "content": system}] if system else []) + \
        [dict(m) for m in messages]
    if images:
        msgs[-1]["images"] = list(images)
    body = {"model": model or MODEL, "messages": msgs, "stream": False,
            "think": False, "keep_alive": KEEP,
            "options": {"temperature": temperature, "num_predict": max_tokens}}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(BASE + "/api/chat", json=body)
            if r.status_code == 400 and "think" in r.text:      # 不支持 think 参数的模型
                body.pop("think")
                r = await c.post(BASE + "/api/chat", json=body)
            r.raise_for_status()
            d = r.json()
            msg = d.get("message") or {}
            out = (msg.get("content") or "").strip()
            if not out and msg.get("thinking"):
                # thinking 版模型会把 token 全花在思考上 —— 请用 xxx-instruct 版
                raise RuntimeError(f"{body['model']} 只输出了思考没有答案"
                                   f"（done_reason={d.get('done_reason')}），请换 instruct 版模型")
            return out
    except httpx.ConnectError:
        _down_until = time.time() + 300
        raise
