#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安装「听语音」—— 双击 安装语音.bat 运行
  1. pip 装 sherpa-onnx（本地语音识别引擎）和 numpy
  2. 下载 SenseVoice 听写模型（int8 版 约 230MB）到 C:\\QQBot\\models\\sensevoice
  3. 拿模型自带的测试录音听写一遍 结果写到 C:\\QQBot\\voice_test.txt
"""
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT.parent / "models" / "sensevoice"
REPORT = ROOT.parent / "voice_test.txt"
PKG = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
TAR_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{PKG}.tar.bz2"
HF = "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/"
lines = []


def say(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    lines.append(msg)


def write_report():
    try:
        REPORT.write_text(f"== 语音安装 {time.strftime('%Y-%m-%d %H:%M:%S')}\n" + "\n".join(lines) + "\n",
                          encoding="utf-8")
    except Exception:
        pass


def step_pip():
    say("[1/3] 安装 sherpa-onnx + numpy ……")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "sherpa-onnx", "sherpa-onnx-core", "numpy"])
    if r.returncode != 0:
        say("   pip 装 sherpa-onnx-core 失败 改成只装 sherpa-onnx numpy 再试一次")
        subprocess.run([sys.executable, "-m", "pip", "install", "-U", "sherpa-onnx", "numpy"])
    try:
        import numpy
        import sherpa_onnx
        say(f"   OK  sherpa-onnx {getattr(sherpa_onnx, '__version__', '?')}  numpy {numpy.__version__}  "
            f"Python {sys.version.split()[0]}")
        return True
    except Exception as e:
        say("   [!] 装完了但导入失败：", repr(e)[:300])
        return False


def download(url, dst):
    import httpx
    tmp = dst.with_suffix(dst.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got, last = 0, 0
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if time.time() - last > 2:
                    last = time.time()
                    pct = f"{got * 100 // total}%" if total else ""
                    print(f"\r   已下载 {got >> 20} MB {pct}   ", end="", flush=True)
    print()
    tmp.replace(dst)


def step_model():
    say(f"[2/3] 听写模型 → {DEST}")
    DEST.mkdir(parents=True, exist_ok=True)
    need = ["model.int8.onnx", "tokens.txt"]
    if all((DEST / n).exists() and (DEST / n).stat().st_size > 1000 for n in need):
        say("   已经下载过了 跳过")
        return True
    tar = DEST.parent / f"{PKG}.tar.bz2"
    try:
        say("   从 GitHub 下载（约 160MB 压缩包）……")
        download(TAR_URL, tar)
        say("   解压中……")
        with tarfile.open(tar, "r:bz2") as t:
            for m in t.getmembers():
                name = m.name.replace("\\", "/")
                base = name.rsplit("/", 1)[-1]
                if m.isfile() and (base in need or "/test_wavs/" in name):
                    sub = DEST / ("test_wavs" if "/test_wavs/" in name else "")
                    sub.mkdir(parents=True, exist_ok=True)
                    with t.extractfile(m) as src, (sub / base).open("wb") as out:
                        out.write(src.read())
        tar.unlink()
    except Exception as e:
        say("   GitHub 下载失败：", repr(e)[:200])
        say("   改从 HuggingFace 下载……")
        try:
            for n in need + ["test_wavs/zh.wav"]:
                (DEST / n).parent.mkdir(parents=True, exist_ok=True)
                download(HF + n, DEST / n)
        except Exception as e2:
            say("   [!] HuggingFace 也失败：", repr(e2)[:200])
            return False
    ok = all((DEST / n).exists() for n in need)
    say("   OK" if ok else "   [!] 模型文件不全")
    return ok


def step_test():
    say("[3/3] 听写测试 ……")
    sys.path.insert(0, str(ROOT))
    import voice
    wavs = sorted((DEST / "test_wavs").glob("*.wav"))
    zh = [w for w in wavs if w.name.startswith("zh")] or wavs[:1]
    if not zh:
        say("   没有测试录音 跳过（模型已经在了 bot 重启后就能听）")
        return True
    t0 = time.time()
    res = voice.recognize(zh[0])
    if res is None:
        say("   [!] 听写失败：", voice._rec_err or "读不出音频")
        return False
    say(f"   {zh[0].name} → 「{res['text']}」 语气 {res['emo'] or '-'} 声音事件 {res['event'] or '-'} "
        f"（{res['secs']:.1f} 秒的录音 用时 {time.time() - t0:.1f}s 含加载模型）")
    try:
        st = voice._rec.create_stream()
        import numpy as np
        a, sr = voice._read_audio(zh[0])
        st.accept_waveform(sr, a)
        voice._rec.decode_stream(st)
        say("   原始结果：", str(st.result)[:400].replace("\n", " "))
    except Exception as e:
        say("   （原始结果没打出来：", repr(e)[:120], "）")
    return True


def main():
    ok = step_pip() and step_model() and step_test()
    say("")
    say("全部搞定 ✓  重启 bot（停止.bat → 启动.bat）她就能听语音了" if ok else
        "没装完 —— 把这个窗口截图发给 Claude（结果也写在 C:\\QQBot\\voice_test.txt）")
    write_report()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        say("出错了：", repr(e)[:300])
        write_report()
