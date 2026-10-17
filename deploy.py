#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键部署：查依赖 → 自动认模型和参考音频 → 写出配置 → 启动网页。

设计目标：能双击就跑。
  · 依赖：缺就自动调 install_deps.py 装（已装则跳过）
  · 权重：自动扫 GPT_weights* / SoVITS_weights* / logs/，挑最新的帮你选中
  · 参考音频：自动扫 logs/*/5-wav32k（能连文本一起读）、refs/、训练集目录
  · API Key：隐私内容，必须你亲自填（只会写进本机 chat_config.json）

用法
  runtime\\python.exe deploy.py                       # 交互向导（推荐）
  runtime\\python.exe deploy.py --yes                 # 全自动，不提问
  runtime\\python.exe deploy.py --check-only          # 只体检并告诉我缺什么
  runtime\\python.exe deploy.py --dry-run             # 只展示会写什么配置，不落盘
  runtime\\python.exe deploy.py --api-key sk-xxx --gpt a.ckpt --sovits b.pth --ref refs/a.wav --prompt-text "原话"
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = HERE / "chat_config.json"
EXAMPLE = HERE / "chat_config.example.json"
AUDIO_EXT = (".wav", ".mp3", ".flac", ".m4a")

OK, WARN, INFO, FAIL = "[ OK ]", "[WARN]", "[ .. ]", "[FAIL]"


def say(tag: str, msg: str) -> None:
    print("%s %s" % (tag, msg), flush=True)


# ---------------------------------------------------------------- 配置读写
def default_cfg() -> dict:
    if EXAMPLE.is_file():
        try:
            return json.loads(EXAMPLE.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            pass
    return {"llm": {}, "voice": {}, "ui": {"port": 27865}, "persona": {}, "emotions": {}, "history": {}}


def load_cfg() -> dict:
    base = default_cfg()
    if CFG.is_file():
        try:
            user = json.loads(CFG.read_text(encoding="utf-8-sig"))
        except Exception as e:  # noqa: BLE001
            say(WARN, "chat_config.json 读不了（%s），将按模板重来" % e)
            user = {}
        merge(base, user)
    return base


def merge(base: dict, extra: dict) -> dict:
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge(base[k], v)
        else:
            base[k] = v
    return base


def write_cfg(cfg: dict) -> None:
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 资源发现
def transcript_map(log_dir: Path) -> dict:
    """logs/<实验>/2-name2text.txt：切片名 -> 原文"""
    out = {}
    txt = log_dir / "2-name2text.txt"
    if not txt.is_file():
        return out
    for line in txt.read_text(encoding="utf-8", errors="ignore").splitlines():
        cols = line.split("\t")
        if len(cols) >= 4:
            out[cols[0]] = cols[3]
        elif len(cols) == 2:
            out[cols[0]] = cols[1]
    return out


def find_weights(kind: str) -> list:
    """kind = 'gpt' | 'sovits'。返回 [(相对路径, 修改时间)]，新的在前。"""
    pat = "GPT_weights*" if kind == "gpt" else "SoVITS_weights*"
    ext = ".ckpt" if kind == "gpt" else ".pth"
    items = []
    for d in sorted(HERE.glob(pat)):
        if not d.is_dir():
            continue
        for p in d.rglob("*" + ext):
            items.append((str(p.relative_to(HERE)), p.stat().st_mtime))
    for p in (HERE / "logs").rglob("*" + ext) if (HERE / "logs").is_dir() else []:
        items.append((str(p.relative_to(HERE)), p.stat().st_mtime))
    # v2Pro 优先，其次按修改时间
    items.sort(key=lambda x: (0 if "v2pro" in x[0].lower() else 1, -x[1]))
    return items


def find_refs() -> list:
    """返回 [(相对路径, 原文或空)]，优先带原文的、新的在前。"""
    items, seen = [], set()

    def add(p: Path, text: str = ""):
        try:
            rel = str(p.relative_to(HERE))
        except ValueError:
            return
        if rel in seen:
            return
        seen.add(rel)
        items.append((rel, text))

    for sub in sorted((HERE / "logs").glob("*/5-wav32k")) if (HERE / "logs").is_dir() else []:
        if sub.is_dir():
            mp = transcript_map(sub.parent)
            for w in sorted(sub.glob("*.wav")):
                add(w, mp.get(w.name, ""))
    for cand in ("refs", "_train_da/raw", "data/raw", "raw"):
        d = HERE / cand
        if not d.is_dir():
            continue
        mp = transcript_map(d.parent)
        for ext in AUDIO_EXT:
            for f in sorted(d.rglob("*" + ext)):
                add(f, mp.get(f.name, ""))
    items.sort(key=lambda x: (0 if x[1] else 1))
    return items


def pick_interactive(title: str, items: list, allow_skip: bool = True):
    print("\n--- %s ---" % title)
    for i, it in enumerate(items[:12], 1):
        label = it[0] if isinstance(it, tuple) else it
        tail = ""
        if isinstance(it, tuple) and len(it) > 1 and it[1]:
            tail = "   （原文：%s…）" % it[1][:16]
        print("  %2d) %s%s" % (i, label, tail))
    if len(items) > 12:
        print("      …… 共 %d 个，只列前 12 个" % len(items))
    hint = "回车=用第 1 个" + ("，0=跳过" if allow_skip else "")
    while True:
        s = input("%s [%s]: " % ("选择", hint)).strip()
        if not s:
            return items[0] if items else None
        if s == "0" and allow_skip:
            return None
        if s.isdigit() and 1 <= int(s) <= min(12, len(items)):
            return items[int(s) - 1]
        print("  输入 1~%d 之间的数字" % min(12, len(items)))


# ---------------------------------------------------------------- 依赖
def deps_ok() -> bool:
    need = ["gradio", "gradio_client", "fastapi", "starlette", "uvicorn", "httpx", "numpy", "requests"]
    miss = []
    import importlib.util
    for m in need:
        try:
            if importlib.util.find_spec(m) is None:
                miss.append(m)
        except Exception:  # noqa: BLE001
            miss.append(m)
    if miss:
        say(WARN, "缺依赖：%s" % ", ".join(miss))
        return False
    try:
        from importlib.metadata import version
        gv = version("gradio")
        if gv != "4.44.1":
            say(WARN, "gradio 是 %s，本项目需要 4.44.1（跑 install_deps.py --force）" % gv)
            return False
        say(OK, "依赖齐全（gradio %s）" % gv)
    except Exception:  # noqa: BLE001
        pass
    return True


def ensure_deps(auto: bool) -> bool:
    if deps_ok():
        return True
    inst = HERE / "install_deps.py"
    if not inst.is_file():
        say(FAIL, "缺依赖，且找不到 install_deps.py，请手动 pip install -r requirements.txt")
        return False
    if not auto:
        s = input("\n是否现在自动安装依赖？[Y/n]: ").strip().lower()
        if s not in ("", "y", "yes"):
            return False
    say(INFO, "开始安装依赖……")
    rc = subprocess.call([sys.executable, str(inst), "--no-reexec"])
    return rc == 0 and deps_ok()


# ---------------------------------------------------------------- 向导
def wizard(cfg: dict, args) -> dict:
    llm = cfg.setdefault("llm", {})
    voice = cfg.setdefault("voice", {})

    # ① 权重
    gpt_list = find_weights("gpt")
    sov_list = find_weights("sovits")
    say(INFO, "扫到 GPT 权重 %d 个、SoVITS 权重 %d 个" % (len(gpt_list), len(sov_list)))
    if args.gpt:
        voice["gpt_weights"] = args.gpt
    elif not voice.get("gpt_weights") and gpt_list:
        chosen = (gpt_list[0] if args.yes else pick_interactive("选 GPT 权重（.ckpt，决定「怎么说」）", gpt_list))
        if chosen:
            voice["gpt_weights"] = chosen[0]
    if args.sovits:
        voice["sovits_weights"] = args.sovits
    elif not voice.get("sovits_weights") and sov_list:
        chosen = (sov_list[0] if args.yes else pick_interactive("选 SoVITS 权重（.pth，决定「音色像不像」）", sov_list))
        if chosen:
            voice["sovits_weights"] = chosen[0]

    # ② 参考音频 + 原文
    refs = find_refs()
    say(INFO, "扫到可用参考音频 %d 个" % len(refs))
    if args.ref:
        voice["ref_audio_path"] = args.ref
        if args.prompt_text:
            voice["prompt_text"] = args.prompt_text
    elif not voice.get("ref_audio_path") and refs:
        chosen = (refs[0] if args.yes else pick_interactive("选默认参考音频（3~10 秒、一整句、无背景音）", refs))
        if chosen:
            voice["ref_audio_path"] = chosen[0]
            if chosen[1]:
                voice["prompt_text"] = chosen[1]
    if not str(voice.get("prompt_text") or "").strip() and str(voice.get("ref_audio_path") or "").strip():
        if args.yes:
            say(WARN, "参考音频没有配套原文 —— 记得手动填 voice.prompt_text，否则音色会飘")
        else:
            say(WARN, "这段音频在大模型那边没有现成原文，请手动输入它说的那句话（必须一致）")
            voice["prompt_text"] = input("参考音频的原文: ").strip()

    # ③ API Key
    if args.api_key:
        llm["api_key"] = args.api_key
    if not str(llm.get("api_key") or "").strip():
        if args.yes:
            say(WARN, "没有 API Key —— 网页能开，但发消息会报错。可以稍后在「设置 → ✏️ 修改 API 接口」里填")
        else:
            print("\n--- 大模型接口 ---")
            print("  任何 OpenAI 兼容接口都行；不知道填什么就留空，稍后在网页设置里填。")
            base = input("接口地址 [%s]: " % (llm.get("base_url") or "https://api.deepseek.com")).strip()
            if base:
                llm["base_url"] = base
            model = input("模型名 [%s]: " % (llm.get("model") or "deepseek-v4-flash")).strip()
            if model:
                llm["model"] = model
            key = input("API Key（直接回车=稍后在网页里填）: ").strip()
            if key:
                llm["api_key"] = key

    # ④ 端口
    ui = cfg.setdefault("ui", {})
    if not ui.get("port"):
        ui["port"] = 27865
    return cfg


def summarize(cfg: dict) -> list:
    """返回还缺什么（空列表=齐了）。"""
    llm, voice = cfg.get("llm") or {}, cfg.get("voice") or {}
    todo = []
    if not str(llm.get("api_key") or "").strip():
        todo.append("API Key（设置页 → ✏️ 修改 API 接口）")
    if not str(voice.get("gpt_weights") or "").strip():
        todo.append("GPT 权重（设置页 → 高级设置 → GPT 模型）")
    if not str(voice.get("sovits_weights") or "").strip():
        todo.append("SoVITS 权重（同上，可留空用底模）")
    if not str(voice.get("ref_audio_path") or "").strip():
        todo.append("参考音频（设置页 → 高级设置 → 参考音频路径）")
    if not str(voice.get("prompt_text") or "").strip():
        todo.append("参考音频的原文 prompt_text（必须与音频一致）")
    return todo


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="一键部署 AI 语音聊天 WebUI")
    ap.add_argument("--yes", action="store_true", help="全自动，不提问（挑最新权重 / 第一条参考音频）")
    ap.add_argument("--check-only", action="store_true", help="只体检，不装不写不启动")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要写入的配置，不落盘")
    ap.add_argument("--no-run", action="store_true", help="配置弄好但不启动服务")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--gpt", default="")
    ap.add_argument("--sovits", default="")
    ap.add_argument("--ref", default="")
    ap.add_argument("--prompt-text", default="")
    ap.add_argument("--no-reexec", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    # 在整合包里自动换成自带的 python
    if os.name == "nt" and not args.no_reexec:
        cand = HERE / "runtime" / "python.exe"
        if cand.is_file() and Path(sys.executable).resolve() != cand.resolve():
            say(INFO, "切换到整合包自带解释器：%s" % cand)
            sys.exit(subprocess.call([str(cand), str(Path(__file__).resolve()), "--no-reexec"]
                                     + sys.argv[1:]))

    print("=" * 64)
    print(" AI 语音聊天 WebUI —— 一键部署")
    print("=" * 64)
    say(INFO, "目录：%s" % HERE)
    in_root = (HERE / "api_v2.py").is_file() and (HERE / "GPT_SoVITS").is_dir()
    say(OK if in_root else FAIL,
        "GPT-SoVITS 根目录检测：%s" % ("正常" if in_root else "不在这里！本项目必须与 api_v2.py 同级"))
    if not in_root:
        say(WARN, "继续跑网页能打开，但语音合成会找不到模型")

    # ffmpeg 提示（不阻断）
    ff = HERE / "runtime" / "ffmpeg.exe"
    say(OK if ff.is_file() or shutil.which("ffmpeg") else WARN,
        "ffmpeg：%s" % (ff if ff.is_file() else (shutil.which("ffmpeg") or "没找到（对讲转码会失败）")))

    cfg = load_cfg()
    if args.check_only:
        todo = summarize(cfg)
        say(INFO, "配置体检：%s" % ("已完整" if not todo else "还缺 %d 项" % len(todo)))
        for t in todo:
            print("   - " + t)
        return 0

    if not ensure_deps(auto=args.yes):
        say(WARN, "依赖没齐，先解决依赖再来（或直接跑 install_deps.py）")

    cfg = wizard(cfg, args)
    todo = summarize(cfg)

    print("\n" + "-" * 64)
    print("将要写入 chat_config.json 的关键配置：")
    print("  GPT 权重    : %s" % ((cfg["voice"].get("gpt_weights") or "（空，用 api_v2 底模）")))
    print("  SoVITS 权重 : %s" % ((cfg["voice"].get("sovits_weights") or "（空）")))
    print("  参考音频    : %s" % ((cfg["voice"].get("ref_audio_path") or "（空）")))
    print("  参考原文    : %s" % ((cfg["voice"].get("prompt_text") or "（空）")[:40]))
    print("  接口        : %s / %s" % (cfg["llm"].get("base_url"), cfg["llm"].get("model")))
    print("  API Key     : %s" % (("已填 %d 位" % len(cfg["llm"].get("api_key") or "")) if cfg["llm"].get("api_key") else "（空）"))
    print("  端口        : %s" % cfg["ui"].get("port"))
    print("-" * 64)

    if args.dry_run:
        say(INFO, "--dry-run：不写文件、不启动")
        return 0

    write_cfg(cfg)
    say(OK, "配置已写入 chat_config.json")
    if todo:
        say(WARN, "还有 %d 项要你自己补（网页里也能补）：" % len(todo))
        for t in todo:
            print("   - " + t)
    else:
        say(OK, "配置完整，可以用了")

    if args.no_run:
        say(INFO, "--no-run：不启动服务。手动启动：%s voice_chat.py" % sys.executable)
        return 0

    print("\n启动网页服务（Ctrl+C 停止）…")
    try:
        return subprocess.call([sys.executable, str(HERE / "voice_chat.py")])
    except KeyboardInterrupt:
        say(WARN, "已停止")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say(WARN, "已取消")
        sys.exit(130)
