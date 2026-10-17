#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键安装本项目的局部依赖（只装网页层需要的那几个包，不碰 GPT-SoVITS 本体）。

设计原则
  1. 绝不重复安装：已经是目标版本的包直接跳过。
  2. 绝不动 GPT-SoVITS 自带的 torch：整合包里的 torch 是 cu118 版，
     从 PyPI 装 requirements-asr.txt 会把它换成 CPU 版，GPU 就废了 ——
     这里只在「完全没装 torch」时才装，并明确提示。
  3. 自动挑对解释器：优先用同目录下整合包自带的 runtime\\python.exe，
     否则会把依赖装到和应用不一样的环境里（最常见的一类"装完还是报错"）。

用法
  runtime\\python.exe install_deps.py            # 只装核心依赖
  runtime\\python.exe install_deps.py --asr      # 连实时对讲/语音识别一起装（约 2GB）
  runtime\\python.exe install_deps.py --check    # 只体检，不装任何东西
  runtime\\python.exe install_deps.py --mirror tsinghua   # 用国内镜像
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SELF = Path(__file__).resolve()
GRADIO_PIN = "4.44.1"

MIRRORS = {
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple",
    "ustc": "https://pypi.mirrors.ustc.edu.cn/simple",
    "tencent": "https://mirrors.cloud.tencent.com/pypi/simple",
    "huawei": "https://repo.huaweicloud.com/repository/pypi/simple",
}

CORE = [
    ("gradio", GRADIO_PIN),
    ("gradio_client", "1.3.0"),
    ("fastapi", "0.112.1"),
    ("starlette", "0.38.2"),
    ("uvicorn", "0.20.0"),
    ("httpx", "0.27.0"),
    ("numpy", "1.23.4"),
    ("requests", "2.32.3"),
]

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"
INFO = "[ .. ]"


# ---------------------------------------------------------------- 小工具
def say(tag: str, msg: str) -> None:
    print("%s %s" % (tag, msg), flush=True)


def pkg_version(name: str):
    """已安装版本；没装返回 None。"""
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:  # noqa: BLE001
        return None


def importable(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:  # noqa: BLE001
        return False


def run(cmd: list) -> int:
    print("   $ " + " ".join(str(c) for c in cmd), flush=True)
    try:
        return subprocess.call([str(c) for c in cmd])
    except FileNotFoundError:
        say(FAIL, "找不到命令：%s" % cmd[0])
        return 127


def pip_install(args: list, mirror: str, extra_index: str = "") -> int:
    cmd = [sys.executable, "-m", "pip", "install"] + args
    if mirror:
        cmd += ["-i", MIRRORS[mirror]]
    if extra_index:
        cmd += ["--extra-index-url", extra_index]
    return run(cmd)


# ---------------------------------------------------------------- 解释器
def ensure_right_python(argv: list) -> None:
    """在 Windows 整合包里，如果当前不是自带的 runtime\\python.exe，自动换过去。"""
    if os.name != "nt":
        return
    cand = HERE / "runtime" / "python.exe"
    if not cand.is_file():
        return
    try:
        same = Path(sys.executable).resolve() == cand.resolve()
    except Exception:  # noqa: BLE001
        same = True
    if same:
        return
    say(WARN, "当前用的是 %s" % sys.executable)
    say(WARN, "但整合包自带的是 %s —— 装到别的环境里会用不上，自动换过去。" % cand)
    rc = subprocess.call([str(cand), str(SELF), "--no-reexec"] + argv)
    sys.exit(rc)


# ---------------------------------------------------------------- 体检
def check_python() -> bool:
    v = sys.version_info
    say(INFO, "Python %d.%d.%d （%s）" % (v.major, v.minor, v.micro, sys.executable))
    if v < (3, 9):
        say(FAIL, "需要 Python 3.9 或更高")
        return False
    if v >= (3, 13):
        say(WARN, "3.13+ 上 gradio 4.44.1 未必能装，建议 3.9~3.12")
    return True


def check_gptsovits() -> bool:
    ok = True
    if not (HERE / "api_v2.py").is_file():
        say(WARN, "当前目录没有 api_v2.py —— 本项目需要放在 GPT-SoVITS 根目录（与 api_v2.py 同级）")
        say(INFO, "当前目录：%s" % HERE)
        ok = False
    if not (HERE / "GPT_SoVITS").is_dir():
        say(WARN, "没找到 GPT_SoVITS/ 目录 —— 语音合成需要它")
        ok = False
    if ok:
        say(OK, "GPT-SoVITS 目录结构正常")
    return ok


def check_pkgs(verbose: bool = True) -> list:
    """返回缺的/版本不对的包名列表。"""
    missing = []
    for name, want in CORE:
        got = pkg_version(name)
        if got is None:
            if verbose:
                say(WARN, "%-14s 未安装（需要 %s）" % (name, want))
            missing.append(name)
        elif got != want:
            if verbose:
                say(WARN, "%-14s 版本 %s ≠ 需要的 %s" % (name, got, want))
            missing.append(name)
        elif verbose:
            say(OK, "%-14s %s" % (name, got))
    return missing


def check_ffmpeg(verbose: bool = True) -> bool:
    cand = [HERE / "runtime" / "ffmpeg.exe", HERE / "ffmpeg.exe"]
    for c in cand:
        if c.is_file():
            if verbose:
                say(OK, "ffmpeg：%s" % c)
            return True
    w = shutil.which("ffmpeg")
    if w:
        if verbose:
            say(OK, "ffmpeg：%s（来自 PATH）" % w)
        return True
    if verbose:
        say(WARN, "没找到 ffmpeg（对讲录音转码会失败）。放到 runtime\\ffmpeg.exe，或装进 PATH")
    return False


def check_assets(verbose: bool = True) -> None:
    gpt = sorted(HERE.glob("GPT_weights*/*.ckpt"))
    sov = sorted(HERE.glob("SoVITS_weights*/*.pth"))
    say(OK if gpt else WARN, "GPT 权重 %d 个%s" % (len(gpt), "" if gpt else "（还没训练？设置页可直接填绝对路径）"))
    say(OK if sov else WARN, "SoVITS 权重 %d 个%s" % (len(sov), "" if sov else "（同上）"))
    pre = HERE / "GPT_SoVITS" / "pretrained_models"
    if pre.is_dir():
        need = [pre / "s1v3.ckpt", pre / "v2Pro" / "s2Gv2ProPlus.pth"]
        for n in need:
            say(OK if n.is_file() else WARN,
                "预训练模型 %s %s" % (n.relative_to(HERE), "在" if n.is_file() else "缺（README 第三章有下载地址）"))
    refs = HERE / "refs"
    refs.mkdir(exist_ok=True)
    n_ref = len([p for p in refs.rglob("*") if p.suffix.lower() in (".wav", ".mp3", ".flac")])
    say(OK if n_ref else WARN, "参考音频 %d 个（放 refs/ 里，刷新页面后会出现在下拉中）" % n_ref)


def ensure_config() -> None:
    cfg = HERE / "chat_config.json"
    if cfg.is_file():
        say(OK, "chat_config.json 已存在（不会覆盖）")
        return
    ex = HERE / "chat_config.example.json"
    if not ex.is_file():
        say(WARN, "既没有 chat_config.json 也没有模板，启动后会自动生成一份")
        return
    shutil.copyfile(ex, cfg)
    say(OK, "已从模板生成 chat_config.json —— 记得填 llm.api_key 与 voice.*")


# ---------------------------------------------------------------- 安装
def install_core(mirror: str, force: bool) -> bool:
    missing = check_pkgs(verbose=not force)
    need = CORE if force else [c for c in CORE if c[0] in missing]
    if not need:
        say(OK, "核心依赖都已经装好，跳过")
        return True
    say(INFO, "开始安装 %d 个核心包（gradio %s 等）..." % (len(need), GRADIO_PIN))
    req = HERE / "requirements.txt"
    if req.is_file() and not force:
        rc = pip_install(["-r", str(req)], mirror)
    else:
        rc = pip_install([("%s==%s" % (n, v)) for n, v in need], mirror)
    if rc != 0:
        say(FAIL, "核心依赖安装失败（退出码 %d）。常见原因：网络/Python 版本不匹配/没有写权限" % rc)
        say(INFO, "试试换镜像：--mirror tsinghua 或 --mirror aliyun")
        return False
    say(OK, "核心依赖安装完成")
    return True


def install_asr(mirror: str) -> bool:
    say(INFO, "安装「实时对讲 / 语音输入」依赖（约 2GB，首次识别还会下载约 1GB 模型）")
    torch_v = pkg_version("torch")
    if torch_v:
        say(OK, "已装 torch %s —— 不动它（整合包的 cu118 版换掉会让 GPU 失效）" % torch_v)
    # funasr 一定要装
    if pkg_version("funasr"):
        say(OK, "funasr 已装，跳过")
    else:
        if pip_install(["funasr==1.0.27"], mirror) != 0:
            say(FAIL, "funasr 安装失败")
            return False
    if not torch_v:
        say(WARN, "没检测到 torch：将安装 PyPI 的 CPU 版。")
        say(WARN, "如果你要用 GPU 推理，请改用官方 CUDA 轮子：")
        say(INFO, "  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118")
        if pip_install(["torch==2.0.0", "torchaudio==2.0.0"], mirror) != 0:
            say(FAIL, "torch 安装失败")
            return False
    say(OK, "对讲 / 语音输入依赖就绪")
    return True


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="一键安装 AI 语音聊天 WebUI 的局部依赖")
    ap.add_argument("--asr", action="store_true", help="附带安装实时对讲/语音识别依赖（约 2GB）")
    ap.add_argument("--mirror", default="", choices=[""] + list(MIRRORS),
                    help="PyPI 镜像：tsinghua / aliyun / ustc / tencent / huawei")
    ap.add_argument("--check", action="store_true", help="只体检不安装")
    ap.add_argument("--force", action="store_true", help="强制重装核心依赖（版本被别的东西改坏时用）")
    ap.add_argument("--no-reexec", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if not args.no_reexec:
        ensure_right_python([a for a in sys.argv[1:] if a != "--no-reexec"])

    print("=" * 62)
    print(" AI 语音聊天 WebUI —— 局部依赖安装")
    print("=" * 62)

    py_ok = check_python()
    gs_ok = check_gptsovits()
    print("-" * 62)
    say(INFO, "依赖体检：")
    missing = check_pkgs()
    check_ffmpeg()
    check_assets()
    print("-" * 62)

    if args.check:
        say(INFO, "--check 模式：只看不装。缺 %d 个核心包" % len(missing))
        return 0 if not missing else 0
    if not py_ok:
        return 1

    ok = install_core(args.mirror, args.force)
    if ok and args.asr:
        ok = install_asr(args.mirror)
    if not ok:
        return 1

    print("-" * 62)
    say(INFO, "安装后复检：")
    left = check_pkgs()
    gv = pkg_version("gradio")
    if gv and gv != GRADIO_PIN:
        say(FAIL, "gradio 是 %s，不是 %s —— 新版 API 变了会启动失败。跑：--force" % (gv, GRADIO_PIN))
        ok = False
    check_ffmpeg()
    ensure_config()
    check_assets(verbose=False)

    print("=" * 62)
    if ok and not left:
        say(OK, "全部就绪！下一步：")
        print("   1) 填配置：打开 chat_config.json 写 llm.api_key 与 voice.*（或在网页设置页里填）")
        print("   2) 启动：  %s voice_chat.py" % (sys.executable if os.name == "nt" else "python"))
        print("   3) 打开：  http://127.0.0.1:%s/"
              % (read_port() or 7865))
        if not gs_ok:
            print("   ⚠ 但当前目录不是 GPT-SoVITS 根目录，语音合成会找不到模型")
    else:
        say(WARN, "还有 %d 个核心包没到位，请按上面的提示处理" % len(left))
    print("=" * 62)
    return 0 if (ok and not left) else 1


def read_port():
    try:
        import json
        with open(HERE / "chat_config.json", "r", encoding="utf-8-sig") as f:
            return (json.load(f).get("ui") or {}).get("port")
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say(WARN, "已取消")
        sys.exit(130)
