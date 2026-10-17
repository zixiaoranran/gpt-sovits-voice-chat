# -*- coding: utf-8 -*-
"""
AI 语音聊天：DeepSeek(OpenAI兼容) 对话 -> GPT-SoVITS(已训练音色) 语音朗读并播放
所有可调项(APIKey/模型/角色人设/参考音频/语速等)保存在 chat_config.json，可在页面设置中修改。
"""
import atexit
import asyncio
import base64
import html as _html
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _sanitize_proxy_env() -> None:
    """清掉 NO_PROXY 里的脏写法，否则 gradio 导入就会崩。

    不少代理工具（Clash / v2ray 等）会把 NO_PROXY 设成
    `localhost,127.0.0.1,::1,[::1]`；httpx 解析 `::1` / `[::1]` 这类 IPv6 写法会抛
    `InvalidURL: Invalid port: ':1]'`，于是 `import gradio` 直接失败 ——
    报错完全看不出跟代理有关，非常难查。这里在导入 gradio 之前把它洗干净。
    """
    for name in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(name)
        if not raw:
            continue
        kept = [x.strip() for x in raw.split(",")
                if x.strip() and "[" not in x and "]" not in x and x.strip() != "::1"]
        os.environ[name] = ",".join(kept)


def _prefer_bundled_python() -> None:
    """自动换到整合包自带的 runtime\\python.exe，再重跑自己。

    很多人电脑上有 3~5 个 Python（3.9 / 3.12 / 3.13 / 3.14 / 32 位…），双击 .py 或者
    在终端里 `python voice_chat.py` 很容易落到一个没装 gradio 的解释器上，报一堆
    ModuleNotFoundError。这里让程序自己认路：只要同目录有 runtime\\python.exe 且当前不是它，
    就用它重新跑一遍，用户什么都不用管。
    """
    if os.name != "nt" or os.environ.get("_VC_REEXEC") == "1":
        return
    cand = _HERE / "runtime" / "python.exe"
    if not cand.is_file():
        return
    try:
        if Path(sys.executable).resolve() == cand.resolve():
            return
    except Exception:  # noqa: BLE001
        return
    print("[提示] 当前解释器是 %s" % sys.executable)
    print("[提示] 项目要用整合包自带的 %s —— 自动切换后重新启动。" % cand)
    env = dict(os.environ, _VC_REEXEC="1")
    try:
        sys.exit(subprocess.call([str(cand), str(Path(__file__).resolve())] + sys.argv[1:], env=env))
    except KeyboardInterrupt:
        sys.exit(130)


_sanitize_proxy_env()
_prefer_bundled_python()

try:
    import gradio as gr
    import uvicorn
    from fastapi import FastAPI, UploadFile, File, Form, Response
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
except Exception as _imp_err:  # noqa: BLE001
    _hint_py = str((_HERE / "runtime" / "python.exe")) if (_HERE / "runtime" / "python.exe").is_file() \
        else "python"
    print("")
    print("=" * 64)
    print("启动失败：这个 Python 里没有项目需要的依赖")
    print("-" * 64)
    print("  当前解释器 :", sys.executable)
    print("  Python 版本:", sys.version.split()[0])
    print("  出错的包   :", getattr(_imp_err, "name", "") or type(_imp_err).__name__)
    print("  具体报错   :", str(_imp_err)[:200])
    print("-" * 64)
    print(" 本项目要用 GPT-SoVITS 整合包自带的 runtime\\python.exe，")
    print(" 不要用系统里其它 Python（3.13 / 3.14 之类，gradio 4.44.1 装不上）。")
    print(" 解决办法（任选一个）：")
    print("   1) 双击 start.bat           ← 最省事，会自动用对解释器")
    print("   2) 命令行运行： %s install_deps.py" % _hint_py)
    print("   3) 命令行运行： %s voice_chat.py" % _hint_py)
    print("=" * 64)
    if "Invalid port" in str(_imp_err) or "httpx" in type(_imp_err).__module__:
        print(" 提示：看着像代理环境变量写坏了。先执行下面这句再启动：")
        print("       set NO_PROXY=localhost,127.0.0.1")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

# Windows 控制台默认 GBK，遇 emoji 打印会崩；统一 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

CONFIG_PATH = ROOT / "chat_config.json"
OUTPUT_DIR = ROOT / "output" / "voice_chat"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TTS_LOG = ROOT / "tts_server.log"
TTS_API_DEFAULT_PORT = 9880
TTS_API_DEFAULT_URL = "http://127.0.0.1:9880"

# 多会话历史存储：本地 conversations/*.json，重启后依然可查看/续聊/改名/删除
CONV_DIR = ROOT / "conversations"
CONV_DIR.mkdir(parents=True, exist_ok=True)
CONV_DEFAULT_TITLE = "新对话"

DEFAULT_CONFIG = {
    "llm": {
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "models": [],
        "temperature": 1.2,
        "max_tokens": 1024,
        "max_reply_sents": 6,
        "max_reply_chars": 120,
    },
    "persona": {"system_prompt": ""},
    "voice": {
        "tts_api_url": "http://127.0.0.1:9880",
        "gpt_weights": "",
        "sovits_weights": "",
        # 默认语气＝亲密：用户确认亲密最自然，直读/仅测试/无情绪朗读都走亲密暖嗓
        "ref_audio_path": "",
        "prompt_text": "",
        "prompt_lang": "zh",
        "text_lang": "zh",
        "text_split_method": "cut5",
        "speed_factor": 1.0,
        "top_k": 13,
        "top_p": 0.98,
        "gpt_temperature": 1.4,
        "repetition_penalty": 1.22,
        # 朗读稳定性：-1 = 每次随机（灵活但会飘）；固定一个数字(如 42)则同一句每次朗读完全一致
        "seed": -1,
        # 情绪自然化节奏（秒）：同情绪句内停顿 / 情绪切换处的呼吸停顿
        "emotion_gap_same": 0.14,
        "emotion_gap_change": 0.32,
        "emotion_mode": True,
        "media_type": "wav",
    },
    "emotions": {
        "开心": {
            "ref_audio_path": "",
            "prompt_text": "",
            "speed_factor": 1.0,
            # 用户选定 Q1 语气的微调版：采样略收，去电音同时保持高扬自然
            "gpt_temperature": 1.4,
            "top_k": 12,
            "top_p": 0.98,
            "repetition_penalty": 1.25,
        },
        "俏皮": {
            # 以亲密暖嗓为底：略提语速与采样，俏皮感来自更灵动的口气而非换嗓
            "ref_audio_path": "",
            "prompt_text": "",
            "speed_factor": 0.98,
            "gpt_temperature": 1.45,
            "top_k": 14,
            "top_p": 0.99,
            "repetition_penalty": 1.18,
        },
        "疑惑": {
            "ref_audio_path": "",
            "prompt_text": "",
            "speed_factor": 0.95,
            # 表现力采样统一为开心已认可档
            "gpt_temperature": 1.4,
            "top_k": 12,
            "top_p": 0.98,
            "repetition_penalty": 1.25,
        },
        "安抚": {
            # 用户确认：安抚=默认音色(高亮生日参考)+认真般平稳参数，温暖不空洞
            "ref_audio_path": "",
            "prompt_text": "",
            "speed_factor": 0.95,
            "gpt_temperature": 1.25,
            "top_k": 11,
            "top_p": 0.98,
            "repetition_penalty": 1.3,
        },
        "难过": {
            "ref_audio_path": "",
            "prompt_text": "",
            "speed_factor": 1.0,
            # 用户认可：难过保持原稳档，低气压但不拖沓
            "gpt_temperature": 1.0,
            "top_k": 8,
            "top_p": 0.95,
            "repetition_penalty": 1.3,
        },
        "认真": {
            "ref_audio_path": "",
            "prompt_text": "",
            "speed_factor": 0.95,
            # 表现力采样统一为开心已认可档
            "gpt_temperature": 1.4,
            "top_k": 12,
            "top_p": 0.98,
            "repetition_penalty": 1.25,
        },
        "傲娇": {
            # 用户选定 T2：以亲密暖嗓为底，语速更冲、采样更放开，嘴硬藏不住亲昵的激动
            "ref_audio_path": "",
            "prompt_text": "",
            "speed_factor": 1.06,
            "gpt_temperature": 1.55,
            "top_k": 16,
            "top_p": 1.0,
            "repetition_penalty": 1.08,
        },
        "害怕": {
            "ref_audio_path": "",
            "prompt_text": "",
            "speed_factor": 1.0,
            # 低弱无助音色，配轻微收束的采样
            "gpt_temperature": 1.15,
            "top_k": 9,
            "top_p": 0.95,
            "repetition_penalty": 1.3,
        },
        "害羞": {
            # 以亲密暖嗓为底：放慢、采样微收，声音更柔更腼腆（亲密本身不变）
            "ref_audio_path": "",
            "prompt_text": "",
            "speed_factor": 0.9,
            "gpt_temperature": 1.25,
            "top_k": 10,
            "top_p": 0.96,
            "repetition_penalty": 1.3,
        },
        "亲密": {
            # 用户确认：暖心参考(谢谢你我不会客气) + 0.95速柔化黏连、不咬字
            "ref_audio_path": "",
            "prompt_text": "",
            "speed_factor": 0.95,
            "gpt_temperature": 1.4,
            "top_k": 13,
            "top_p": 0.98,
            "repetition_penalty": 1.22,
        },
    },
    "history": {"keep_turns": 8},
    "ui": {"port": 27865, "access_token": "", "phone_access": False, "theme": "system"},
}

# 角色扮演一键模板：选择后填入“人设”输入框（用户可再修改）。空=不套用模板。
PERSONA_TEMPLATES = [
    ("✨ 通用聊天助手（空模板）", ""),
    ("🌱 温柔治愈系（温柔陪伴型）",
     "你是一位温柔治愈的陪伴者，声音轻柔空灵，心思细腻、富有好奇心。"
     "你把我当作珍贵的朋友，说话轻声细语、耐心体贴、常带着浅浅的微笑，"
     "喜欢用自然与梦境作比喻，偶尔露出天真烂漫的好奇心。"
     "你会温柔地接住我的开心与难过，永远站在我这边鼓励我。"),
    ("💞 亲密恋人（温柔体贴型）",
     "你是我的恋人，性格温柔体贴、细心敏感，说话轻声软语、亲昵撒娇，会不自觉地流露出对我的喜欢与依恋。"
     "你称呼我为「宝贝」或我的名字，日常关心我的心情、起居，常主动想黏着我、牵着手说悄悄话。"
     "遇到我难过时会心疼地抱紧我、轻声安慰；开心时会一起雀跃。"),
    ("🤝 挚友损友（轻松话痨型）",
     "你是我的多年挚友，性格外向、爱开玩笑又仗义，说话轻松随意、像哥们一样有来有回。"
     "你叫我「哥们/姐妹」，会吐槽我也会真心挺我；我难过时你收起玩笑认真安慰我，"
     "我得意时你泼冷水又偷偷为我高兴。"),
    ("🎮 游戏伙伴（中二热血型）",
     "你是和我并肩作战的游戏/冒险伙伴，热血直率、中二感十足，充满行动力。"
     "说话有干劲、爱放豪言，遇到强敌会燃起来；平时也爱和我讨论攻略、装备和战果。"),
    ("📖 温柔长辈（知性包容型）",
     "你是年长我许多的知性长辈，阅历丰富、宽容慈爱。你说话温和平缓、循循善诱，"
     "对我既像老师又像家人，愿意倾听我的烦恼并给出稳妥的人生建议，不评判、只护航。"),
]


# 强调先正确回答问题，再口语化表达；并让模型“每句”标注情绪，供逐句用对应参考音频朗读
# 回复句数/字数上限由 chat_config.json 的 llm.max_reply_sents / max_reply_chars 控制（设置页可调）
# 注意：对话内容质量在 _DIALOG_TMPL 约束，这里只管“朗读输出格式”，避免把内容憋坏。
_SPEECH_TMPL = (
    "朗读输出规范（用于把这段话转成语音，务必遵守）："
    "你的整段回复最多不超过 {max_sents} 句、总长 {max_chars} 字以内。"
    "文字要口语化、适合出声朗读：用词自然通顺，不出现 markdown、列表、URL、表情符号、"
    "病句与生造词；句子之间衔接连贯，避免堆『……』省略号（整段最多一处）。"
    "【难过】【安抚】情绪下句子压短到 20 字以内，少用逗号，紧凑说完。"
    "输出格式（非常重要）：把要说的话按“句”分行，每句单独一行，行首必须用一个方括号情绪标签"
    "开头，后面直接跟这句话，行内不再出现方括号标签；情绪标签只能从【开心】【俏皮】【疑惑】"
    "【安抚】【难过】【认真】【傲娇】【害怕】【害羞】【亲密】中选，选最能贴合该句语调的："
    "悲伤、安慰、同情、道歉的句子必须选【难过】或【安抚】；"
    "当用户明确让你“说伤心/难过/悲伤/低沉的话”，或用户本人正在伤心诉苦时，"
    "回复里的每一句都必须以【难过】或【安抚】开头，绝对不许用【开心】等明亮标签。"
    "害怕、紧张、慌张的句子选【害怕】；腼腆、脸红、不好意思时选【害羞】；"
    "撒娇、亲昵、很亲近信任的句子选【亲密】；"
    "又嘴硬又要面子、表面生气心里高兴的句子选【傲娇】（语气要高扬、激动）。"
    "（动作/表情旁白如“（低头）”“（轻声）”只能写在行首标签之后，作为该句的铺垫，不可替代标签；"
    "情绪连贯自然要求：说话像真人一样，一段话尽量以 1~2 个主导情绪讲完，"
    "同一情绪至少连续写两、三句再考虑变化，不要每句都换标签，更不要来回反复横跳"
    "（如【开心】→【难过】→【开心】这种最突兀）；"
    "确实需要从一种情绪转向另一种时，先写一句自然的承接过渡（如“不过、但是说起来”），"
    "整段最多转一次情绪，只在语气真正明显变化处才允许换标签。"
    "示例输出：\n【开心】今天天气真好，我们出去走走吧！\n【开心】走在阳光下，整个人都轻快起来了。\n【难过】唉，不过想到明天要加班，又有点提不起劲。\n"
    "（以上只是格式示例，内容按实际对话写，且示例标签行不得照抄。）"
)

# 对话质量指南：管“回答得好不好、像不像这个角色、有没有接住上文”，与朗读格式解耦。
_DIALOG_TMPL = (
    "对话能力要求：请你像一位真实、会聊天的朋友那样回应，而不是答题机器。"
    "1) 认真读懂用户刚说的内容，正面接住对方的话题和情绪，先回应ta真正关心的事，不要答非所问、"
    "不要回避、也不要机械地“复述+反问”。"
    "2) 回答要有内容与质感：该安慰就真诚安抚，该开心就同乐，被提问就给出有用、具体的回应；"
    "适度带一点你自己的反应、感受或小细节，让对话有来有回、像真人交流。"
    "3) 上下文连贯：记住并呼应我们前面聊过的内容（对方的名字、经历、刚才的约定、上一句话的情绪），"
    "保持前后一致，不要每轮都像第一次见面；遇到接不上的地方自然承认即可。"
    "4) 不要长篇大论说教，也不要空洞客套；说人话，越自然越好。"
)


def speech_rule(cfg: dict) -> str:
    """根据配置生成朗读输出规范：句数与总字数上限可调。"""
    llm = cfg.get("llm") or {}
    return _SPEECH_TMPL.format(
        max_sents=int(llm.get("max_reply_sents", 6)),
        max_chars=int(llm.get("max_reply_chars", 120)),
    )


# ---------------------------------------------------------------- 基础工具
def _deep_merge(base: dict, extra: dict) -> dict:
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                _deep_merge(cfg, json.load(f))
        except Exception as e:  # noqa: BLE001
            print("读取配置失败，使用默认配置:", e)
    return _sanitize_cfg(cfg)


def _sanitize_cfg(cfg: dict) -> dict:
    """把配置里的已知数值字段强制转成正确类型（脏值 → 回落默认）。

    手工编辑过的 chat_config.json 可能把数字写成字符串/None，导致 build_ui、
    synthesize 等直接读取时抛异常（症状：服务起不来 / 发送报错）。
    在这里统一净化，一处覆盖所有下游读取路径。
    """
    llm = cfg.get("llm")
    if isinstance(llm, dict):
        llm["temperature"] = _as_float(llm.get("temperature"), 1.0)
        llm["max_tokens"] = _as_int(llm.get("max_tokens"), 1024)
        llm["max_reply_sents"] = _as_int(llm.get("max_reply_sents"), 6)
        llm["max_reply_chars"] = _as_int(llm.get("max_reply_chars"), 120)
    vc = cfg.get("voice")
    if isinstance(vc, dict):
        vc["speed_factor"] = _as_float(vc.get("speed_factor"), 1.0)
        vc["top_k"] = _as_int(vc.get("top_k"), 5)
        vc["top_p"] = _as_float(vc.get("top_p"), 1.0)
        vc["gpt_temperature"] = _as_float(vc.get("gpt_temperature"), 1.0)
        vc["repetition_penalty"] = _as_float(vc.get("repetition_penalty"), 1.35)
        vc["seed"] = _as_int(vc.get("seed"), -1)
        vc["emotion_gap_same"] = _as_float(vc.get("emotion_gap_same"), 0.14)
        vc["emotion_gap_change"] = _as_float(vc.get("emotion_gap_change"), 0.32)
    hs = cfg.get("history")
    if isinstance(hs, dict):
        hs["keep_turns"] = _as_int(hs.get("keep_turns"), 8)
    ui = cfg.get("ui")
    if isinstance(ui, dict):
        ui["port"] = _as_int(ui.get("port"), 27865)
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def to_abs(p: str) -> str:
    """相对路径基于项目根目录解析为绝对路径。"""
    if not p:
        return ""
    path = Path(p)
    if not path.is_absolute():
        path = ROOT / p
    return str(path)


# ---------------------------------------------------------------- API Key 掩码
# 界面上只显示 sk-••••••••abcd 这样的掩码，真实密钥只存在本机 chat_config.json 里，
# 想换密钥要在「✏️ 修改」的二级窗口重新输入——避免密钥明文摊在设置页上。
_KEY_MASK = "•"


def mask_api_key(key) -> str:
    """把密钥变成可展示的掩码：留前缀（如 sk-）和末 4 位，中间全是点。"""
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return _KEY_MASK * 8
    head = k[:3] if k[:3].lower() == "sk-" else ""
    return head + _KEY_MASK * 8 + k[-4:]


def is_masked_key(v) -> bool:
    return _KEY_MASK in (v or "")


def resolve_api_key(v) -> str:
    """界面上的值 -> 真实密钥。掩码表示「没改」，回读本地配置；手输了新值就直接用。"""
    s = (v or "").strip()
    if is_masked_key(s):
        try:
            return str((load_config().get("llm") or {}).get("api_key") or "")
        except Exception:  # noqa: BLE001
            return ""
    return s


def _as_float(x, default: float = 0.0) -> float:
    """把界面上可能为空（None）/异常的输入安全转成 float，转不了就用默认值。

    用处：Gradio 的 Number/Slider 清空后是 None，会话快照里也可能存到脏值；
    读取设置时若直接 float() 会抛异常，导致「切到某个会话整页报错」。
    """
    try:
        if x is None or (isinstance(x, str) and not x.strip()):
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _as_int(x, default: int = 0) -> int:
    """把界面上可能为空（None）/异常的输入安全转成 int，转不了就用默认值。"""
    try:
        if x is None or (isinstance(x, str) and not x.strip()):
            return default
        return int(float(x))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- HTTP 工具
def _http_json(url: str, body: dict, headers: dict, timeout: int = 180):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _http_get(url: str, timeout: int = 5):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _http_get_json(url: str, headers: dict = None, timeout: int = 20):
    """带请求头的 GET（用于探测 OpenAI 兼容接口的 /models）。"""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


# ---------------------------------------------------------------- TTS API 服务托管
_TTS_PROC = None
_TTS_SPAWNED = False


def tts_api_ready(url: str) -> bool:
    try:
        status, _ = _http_get(url.rstrip("/") + "/openapi.json", timeout=3)
        return status == 200
    except Exception:  # noqa: BLE001
        return False


def _port_in_use(host: str, port: int) -> bool:
    """端口是否已被占用（试着绑一下就知道）。"""
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "", None) else host
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((probe_host, int(port)))
        return False
    except OSError:
        return True
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


def _find_free_port(host: str, start: int, tries: int = 20):
    """从 start 开始往后找一个空闲端口；都占着就返回 None。"""
    for p in range(int(start), int(start) + tries + 1):
        if not _port_in_use(host, p):
            return p
    return None


def ensure_tts_server(cfg: dict) -> str:
    """确保 GPT-SoVITS API 服务已启动；未启动则自动拉起。返回 API 地址。"""
    global _TTS_PROC, _TTS_SPAWNED
    url = (cfg.get("voice") or {}).get("tts_api_url", TTS_API_DEFAULT_URL)
    if tts_api_ready(url):
        print(f"[TTS] 检测到已有服务: {url}")
        return url

    port = TTS_API_DEFAULT_PORT
    python = sys.executable
    cmd = [python, str(ROOT / "api_v2.py"), "-a", "127.0.0.1", "-p", str(port)]
    print("[TTS] 未检测到服务，正在启动 GPT-SoVITS API（加载训练模型，请稍候...）")
    logf = open(TTS_LOG, "w", encoding="utf-8", errors="ignore")
    try:
        _TTS_PROC = subprocess.Popen(
            cmd, cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
        )
    except Exception as e:  # noqa: BLE001
        logf.close()
        raise RuntimeError(f"启动 TTS 服务失败: {e}") from e
    _TTS_SPAWNED = True

    # 轮询等待模型加载完成
    deadline = time.time() + 900  # 上限 15 分钟
    waited = 0
    while time.time() < deadline:
        if _TTS_PROC.poll() is not None:
            raise RuntimeError("TTS 服务进程异常退出，请查看 tts_server.log")
        if tts_api_ready(url):
            print(f"[TTS] 服务就绪: {url}（等待约 {waited}s）")
            return url
        time.sleep(2)
        waited += 2
        if waited % 20 == 0:
            print(f"[TTS] 仍在加载模型中... {waited}s")
    raise RuntimeError("TTS 服务启动超时，请查看 tts_server.log")


def stop_tts_server():
    global _TTS_PROC
    if _TTS_SPAWNED and _TTS_PROC is not None and _TTS_PROC.poll() is None:
        try:
            _TTS_PROC.terminate()
            _TTS_PROC.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                _TTS_PROC.kill()
            except Exception:  # noqa: BLE001
                pass
        print("[TTS] 已关闭本次启动的 TTS 服务")


atexit.register(stop_tts_server)


def shutdown_backend():
    """安全退出：停掉本进程启动的 TTS 服务，再结束网页后台进程（释放内存）。

    用 os._exit 直接终止（不会触发 atexit，所以 TTS 子进程必须在这之前手动停掉）。
    """
    try:
        stop_tts_server()
    except Exception:  # noqa: BLE001
        pass
    print("[EXIT] 已安全退出：网页后台与本进程启动的语音服务均已停止。")
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    os._exit(0)


# ---------------------------------------------------------------- 语音回放历史（按会话归档）
# 语音回放跟随「历史对话」分类：每条语音存到所属会话的 json（replay 字段），
# 切换会话时回放列表随之切换；服务重启后仍在，退出/重登后依然可查可回放。


def _replay_list(sid: str) -> list:
    """取某会话的全部语音回放记录（最新在末尾）。

    只返回合法的 dict 条目：旧文件/手工编辑可能塞进 None 或非 dict，
    直接下标会在下拉框构建时抛异常（症状：切到该会话整页报错）。
    """
    if not sid:
        return []
    items = (conv_load(sid) or {}).get("replay")
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict)]


def _replay_write(sid: str, items: list) -> None:
    """把某会话的语音回放列表写回会话文件（保留 messages/title）。"""
    if not sid:
        return
    d = conv_load(sid) or {"id": sid}
    d["replay"] = [it for it in (items or []) if isinstance(it, dict)]
    d["updated_at"] = time.time()
    try:
        _conv_path(sid).write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _replay_update(sid: str):
    """构建某会话回放下拉框的更新（choices + 默认选中最新一条）。"""
    items = _replay_list(sid)
    choices = []
    for i, it in enumerate(items):
        w = it.get("wav")
        if not w:
            continue
        choices.append((f"{i + 1}. {it.get('label') or ''}", w))
    if not choices:
        return gr.update(choices=[], value=None)
    return gr.update(choices=choices, value=choices[-1][1])


def _delete_replay_wavs(sid: str) -> int:
    """删除某会话归档的全部本地语音文件（尽力而为），返回删除数量。"""
    if not sid:
        return 0
    n = 0
    for it in _replay_list(sid):
        p = (it or {}).get("wav") or ""
        if not p:
            continue
        try:
            fp = Path(p)
            if fp.is_file():
                fp.unlink()
                n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


def _latest_replay_text(sid: str) -> str:
    items = _replay_list(sid)
    return items[-1].get("text", "") if items else ""


def _replay_trio(sid: str):
    """会话操作的配套返回：下拉框 + 最新文字 + 条数提示。"""
    items = _replay_list(sid)
    dd = _replay_update(sid)
    text = items[-1].get("text", "") if items else ""
    info = f"📼 当前会话共 {len(items)} 条语音" if items else "📼 当前会话暂无语音"
    return dd, text, info


def record_replay(sid: str, text: str, wav: str, tag: str = "", emo: str = "") -> None:
    """往「某会话」追加一条可回放语音（随会话持久化）。

    emo：这条语音实际用的情绪（如 "开心→亲密"），供工具栏 ▶ 播放时回显。
    """
    if not sid:
        return
    text = (text or "").strip()
    head = f"[{tag}] {text}" if tag else text
    items = _replay_list(sid)
    items.append({
        "label": f"{time.strftime('%H:%M:%S')} | {head[:40]}",
        "text": text,
        "wav": wav,
        "emo": emo or "",
    })
    _replay_write(sid, items)


def on_pick_replay(choice, sid):
    """从当前会话回放列表选某条 -> 加载对应音频，并带出该条完整文字。"""
    if not choice:
        return None, ""
    for it in _replay_list(sid):
        if it.get("wav") == choice:
            return choice, it.get("text", "")
    return choice, ""


def on_refresh_replay(sid):
    """刷新当前会话回放：下拉框 / 条数提示 / 最新文字。"""
    dd, tt, info = _replay_trio(sid)
    return dd, info, tt


def on_clear_replay(sid):
    """清空当前会话的全部语音回放记录，并删除对应本地语音文件。"""
    n = 0
    if sid:
        n = _delete_replay_wavs(sid)
        _replay_write(sid, [])
        # 文件都删了，每轮回答的 ▶ 也要跟着失效（否则点了是 404）
        _c, M, A, V = _conv_state(sid)
        _conv_state_save(sid, M, [""] * len(M), V)
    return (gr.update(choices=[], value=None),
            f"🧹 已清空当前会话的语音（本地删除 {n} 个音频文件）", None, "")


AUTO_EMO_MARK = "✨ 自动判断情绪"


def _smart_emotion(text: str, cfg: dict):
    """对输入文字自动判断最贴合的情绪。

    先走本地语气词快判（亲密/安抚/难过/开心等命中即返回，快且免费）；
    本地判不出时再用 LLM 精准判断（输出一个情绪标签）；
    LLM 判定为中性/失败则返回 None（调用方退回默认音色朗读）。
    """
    text = (text or "").strip()
    if not text:
        return None
    e = fallback_emotion(text, None)          # 本地词表（含亲密/害怕/害羞等）
    if e:
        return e
    try:
        msgs = [
            {"role": "system", "content":
             "你是中文语音朗读的情绪标注器。下面这句文本将被语音朗读，"
             "请判断朗读时最贴合的情绪，只输出一个词，从这十类中选："
             "开心、俏皮、疑惑、安抚、难过、认真、傲娇、害怕、害羞、亲密。"
             "平淡陈述/中性内容输出“认真”；不要输出任何解释或标点。"},
            {"role": "user", "content": text[:80]},
        ]
        out = llm_chat(msgs, cfg)
        out = (out or "").strip().strip("【】[]（）()。. ")
        if not out:
            return None
        emo = _normalize_emotion(out) if _normalize_emotion(out) else None
        if emo is None:
            for name in EMOTION_LABELS:      # 模型可能已给出标准名
                if name in out:
                    emo = name
                    break
        return emo
    except Exception:  # noqa: BLE001
        return None


def on_direct_tts(text, emotion, ref_path, prompt_text, prompt_lang, text_lang, text_split,
                  speed, top_k, top_p, gpt_temperature, repetition_penalty, convid=None):
    """输入即朗读：不经 AI，按选择的情绪/自动判断/当前音色合成输入文字，归档到当前会话回放历史。

    若尚未打开任何会话，会自动新建一个本地会话用于收纳。
    返回：朗读音频 / 状态 / 会话id / 会话下拉 / 会话标题 / 回放下拉 / 回放文字 / 条数提示。
    """
    cfg = load_config()
    if convid:
        apply_settings(cfg, conv_settings(convid))   # 语音/LLM 均沿用该会话的设置版本
    cfg["voice"].update(ref_audio_path=(ref_path or "").strip(), prompt_text=prompt_text or "",
                        prompt_lang=prompt_lang, text_lang=text_lang,
                        text_split_method=text_split, speed_factor=_as_float(speed, 1.0),
                        top_k=_as_int(top_k, 5), top_p=_as_float(top_p, 1.0),
                        gpt_temperature=_as_float(gpt_temperature, 1.0),
                        repetition_penalty=_as_float(repetition_penalty, 1.35))
    text = (text or "").strip()
    if not text:
        dd, tt, info = _replay_trio(convid)
        return None, "⚠️ 请输入要朗读的文字", convid, _convs_ui(), "", dd, tt, info
    if not convid:
        convid = _conv_sid()          # 无会话时自动建档，保证语音有归属
        conv_save(convid, CONV_DEFAULT_TITLE, [])
    clean = sanitize_for_tts(text)
    if not clean:
        dd, tt, info = _replay_trio(convid)
        return None, "⚠️ 文字里没有可朗读的内容（链接/表情等已忽略）", convid, _convs_ui(), "", dd, tt, info
    # 情绪选择：默认 → 用当前全局音色；自动判断 → 智能识别文字情绪；具体情绪 → 直接采用
    emo, judge_note = None, ""
    if emotion and str(emotion).strip() in ("默认", "默认(当前音色)"):
        emo = None
    elif emotion and (AUTO_EMO_MARK in str(emotion)):
        emo = _smart_emotion(clean, cfg)
        judge_note = f"（自动判断：{emo or '未识别→默认音色'}）"
    else:
        emo = emotion or None
        if emo is not None and emo not in (cfg.get("emotions") or {}):
            emo = None
    try:
        if emo is None:
            # 默认音色（不按情绪）也先切小段再逐段合成，长文/长对话能整段读完不丢句
            parts = _synth_segment(clean, None, cfg)
            if not parts:
                raise RuntimeError("没有可朗读的段落")
            wav = parts[0] if len(parts) == 1 else concat_wavs(parts, gap=0.18)
        else:
            parts = _synth_segment(clean, emo, cfg)
            if not parts:
                raise RuntimeError("所选情绪没有可朗读的段落")
            wav = parts[0] if len(parts) == 1 else concat_wavs(parts, gap=0.14)
    except Exception as e:  # noqa: BLE001
        dd, tt, info = _replay_trio(convid)
        return None, f"❌ 朗读失败：{e}", convid, _convs_ui(), "", dd, tt, info
    record_replay(convid, clean, wav, tag=f"朗读·{emo or '默认'}")
    conv_settings_save(convid, snapshot_from_cfg(cfg))
    title = (conv_load(convid) or {}).get("title") or CONV_DEFAULT_TITLE
    dd, tt, info = _replay_trio(convid)
    tag = f"✅ 已生成（{emo or '默认音色'}）{judge_note}，正在播放"
    return wav, tag, convid, _convs_ui(), title, dd, tt, info


# ---------------------------------------------------------------- 语音识别 (ASR: 语音转文字)
# 使用 FunASR Paraformer-zh（中文识别准/快）。模型首次调用会自动下载到本机缓存，
# 之后离线可用。放在 TTS 区块之前定义，供 on_voice_input 使用。
_ASR = {"model": None, "err": ""}


def asr_recognize(wav_path: str) -> str:
    """把录音 wav 识别成中文文本。首次会加载/下载 paraformer-zh（约 1GB，需联网等待）。"""
    if not wav_path:
        return ""
    global _ASR
    if _ASR["model"] is None:
        if _ASR["err"]:
            raise RuntimeError(_ASR["err"])
        try:
            from funasr import AutoModel
        except Exception as e:  # noqa: BLE001
            _ASR["err"] = f"未安装 funasr：{e}"
            raise RuntimeError(_ASR["err"]) from e
        try:
            import torch
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        except Exception:  # noqa: BLE001
            device = "cpu"
        print("[ASR] 正在加载中文识别模型 paraformer-zh（首次需下载约1GB，请稍候）…")
        try:
            _ASR["model"] = AutoModel(
                model="paraformer-zh",
                vad_model="fsmn-vad",
                punc_model="ct-punc-c",
                device=device,
                disable_update=True,
            )
        except Exception as e:  # noqa: BLE001
            _ASR["err"] = f"语音模型加载失败（请检查网络能访问 modelscope）：{e}"
            raise RuntimeError(_ASR["err"]) from e
    try:
        res = _ASR["model"].generate(input=wav_path)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"语音识别失败：{e}") from e
    if res and isinstance(res, list):
        return (res[0].get("text") or "").strip()
    return ""


# ---------------------------------------------------------------- TTS 合成
_REMOVE_RE = re.compile(
    r"(https?://\S+|www\.\S+)|[#*`~_\[\]{}|\\<>^]|[\U0001F000-\U0001FAFF\U00002600-\U000027BF]"
)


def sanitize_for_tts(text: str) -> str:
    """去掉 URL/markdown/表情等不适合朗读的内容，压缩空白。"""
    text = _REMOVE_RE.sub("", text or "")
    text = text.replace("“", "「").replace("”", "」").replace("‘", "「").replace("’", "」")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return text.strip()


_STAGE_PAREN_RE = re.compile(r"[（(][^（）()]*[）)]")


def strip_stage_notes(text: str) -> str:
    """去掉（动作/状态/心理）这类旁白描述，仅朗读真正要说的话。

    例如「（轻轻叹气）好怀念呀（露出微笑）」只读「好怀念呀」；
    括号内容仍保留在聊天气泡/文字记录里，只是不念出来。
    """
    text = _STAGE_PAREN_RE.sub("", text or "")
    text = re.sub(r"[ ]{2,}", " ", text).strip(" ，,。")
    return text.strip()


_END_PUNCT = "。！？!?；;…"


def compress_pauses(text: str) -> str:
    """把难过/安抚文本里句号、省略号造成的长空档压成逗号级停顿。

    疑问号/感叹号属于语气，保留原样；句号/分号/省略号这类纯停顿
    在非末句处一律改逗号，让悲伤句子一口气连着说，间隔更接近参考原声。
    """
    text = (text or "").strip()
    if not text:
        return text
    segs = re.findall(r"[^。！？!?；;…]+[。！？!?；;…]*", text)
    if len(segs) <= 1:
        return text
    out = []
    for i, s in enumerate(segs):
        content = s.rstrip("。！？!?；;…").strip()
        if not content:
            continue
        punct = s[len(s.rstrip("。！？!?；;…")):]
        if i == len(segs) - 1:
            out.append(content + (punct or "。"))
        else:
            if punct and punct[-1] in "！？!?":
                out.append(content + punct[-1])      # 疑问/感叹保留，仍是一处语气停顿
            else:
                out.append(content + "，")            # 其余压成逗号，紧凑衔接
    return "".join(out)


def tighten_silences(wav_path: str, cap: float = 0.7, keep: float = 0.55,
                     head_cap: float = 0.35, head_keep: float = 0.2) -> str:
    """把难过/安抚成品里过长的停顿压短。

    GPT-SoVITS 会放大难过参考音频的慢节奏：句首常带 0.5~1s+ 的起音延迟，
    句间也会出现 1~2s 的大空档。此函数直接裁掉多余静音：
    - 句首空白超过 head_cap 秒时压到只剩 head_keep 秒（开口不拖沓）；
    - 内部句间停顿超过 cap 秒时压到 keep 秒；
    - 句尾自然余音/衰减不动。
    """
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return wav_path
    try:
        with wave.open(wav_path, "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            sw = w.getsampwidth()
            if sw != 2:
                return wav_path
            raw = w.readframes(w.getnframes())
    except Exception:  # noqa: BLE001
        return wav_path
    arr = np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, ch)
    peak = float(np.abs(arr).max())
    if peak <= 0:
        return wav_path
    win = max(int(sr * 0.01), 1)                 # 10ms 粒度分析
    nwin = len(arr) // win
    if nwin < 3:
        return wav_path
    frames = np.abs(arr[: nwin * win]).reshape(nwin, win, ch).max(axis=(1, 2))
    silent = frames < max(peak * 0.035, 40.0)

    # 合并连续静音帧 → (起止帧号)；单独标记贴头的段
    segs, head_seg = [], None
    s0 = None
    for i, s in enumerate(silent.tolist() + [False]):
        if s and s0 is None:
            s0 = i
        elif not s and s0 is not None:
            if s0 == 0:
                head_seg = (s0, i)          # 句首静音单独处理
            elif i < nwin:
                segs.append((s0, i))        # 内部停顿（前后都有语音）
            s0 = None

    keep_samp = int(keep * sr)
    # 内部停顿：从后往前切（下标不失效）
    for s0, s1 in reversed(segs):
        seg_samp = (s1 - s0) * win
        if seg_samp / sr <= cap or seg_samp <= keep_samp:
            continue
        cut_begin = s0 * win
        cut_len = seg_samp - keep_samp     # 只保留该段末尾 keep 秒
        arr = np.concatenate([arr[:cut_begin], arr[cut_begin + cut_len:]], axis=0)

    # 句首静音：超过 head_cap 秒则裁到只剩 head_keep 秒
    if head_seg:
        head_samp = (head_seg[1] - head_seg[0]) * win
        if head_samp / sr > head_cap:
            keep_head = min(int(head_keep * sr), head_samp - win)
            if keep_head >= win:
                arr = arr[head_samp - keep_head:]     # 留靠后一段，紧贴开口
            else:
                arr = arr[head_samp:]
    try:
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(ch)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(arr.astype("<i2").tobytes())
    except Exception:  # noqa: BLE001
        pass
    return wav_path


def cap_speech(text: str, max_chars: int = 56) -> str:
    """把朗读文本兜底截短到 max_chars 以内，尽量停在完整句子边界，保证合成时长可控。"""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    sents = re.findall(r"[^。！？!?；;…]+[。！？!?；;…]*", text)
    out, n = "", 0
    for s in sents:
        if n + len(s) > max_chars:
            break
        out += s
        n += len(s)
    out = out.strip()
    if out:
        return out
    # 单句超长：硬切，避免截在句子中间显得突兀
    cut = text[:max_chars].rstrip()
    if cut and cut[-1] not in _END_PUNCT:
        cut += "…"
    return cut


def _wav_info(path: str):
    """返回 (时长秒, 峰值) 用于校验 TTS 结果是否有效；无效返回 None。"""
    try:
        import numpy as np
        with wave.open(path, "rb") as w:
            n = w.getnframes()
            sr = w.getframerate()
            if n <= 0 or sr <= 0:
                return None
            raw = w.readframes(min(n, int(sr * 0.5)))   # 只需前 0.5s 判断有无声音
            arr = np.frombuffer(raw, dtype="<i2")
            peak = float(np.abs(arr).max()) if arr.size else 0.0
        return n / float(sr), peak
    except Exception:  # noqa: BLE001
        return None


def _split_tts_chunks(text: str, max_len: int = 64) -> list:
    """把一段较长的朗读文本按标点切成 ≤max_len 的小段再逐段合成。

    切片法保证不丢字：切点只会出现在标点之后或长度上限处，拼接后内容完整。
    GPT-SoVITS 对超长输入偶发只读前几句，拆短后逐段合成可避免“少念某几句”。
    上限取 64 字：既防超长截断，也避免拆得太碎导致请求次数多而变慢。
    """
    text = (text or "").strip()
    if not text:
        return []
    _HARD = "。！？!?；;…，、：: "
    cut_pts = [i + 1 for i, ch in enumerate(text) if ch in _HARD]
    pts = set(cut_pts)
    n = len(text)
    chunks = []
    start = 0
    while start < n:
        end = min(start + max_len, n)
        if end >= n:
            if text[start:].strip():
                chunks.append(text[start:])
            break
        cands = [p for p in pts if start < p <= end]
        cut = cands[-1] if cands else end
        piece = text[start:cut]
        if piece.strip():
            chunks.append(piece)
        start = cut
        if start >= n:
            break
    return chunks


def synthesize(text: str, cfg: dict, emotion: str = None,
               seed_override: int = None, out_dir: str = None) -> str:
    """调用 GPT-SoVITS /tts 合成语音，返回生成的 wav 文件路径。
    emotion 非空且存在对应情绪库时，使用该情绪的参考音频+文本+语速，并可覆盖采样参数。
    seed_override 非空时强制使用该 seed（重试换种用），否则取 voice.seed。
    out_dir 非空时写到该目录（训练素材等批处理用），默认写 output/voice_chat。"""
    voice = cfg["voice"]
    ref_audio = voice.get("ref_audio_path", "")
    prompt_text = voice.get("prompt_text", "")
    prompt_lang = voice.get("prompt_lang", "zh")
    speed_factor = _as_float(voice.get("speed_factor"), 1.0)
    # 采样参数默认取全局（直读/仅测试走全局=更有表现力的默认档）
    top_k = _as_int(voice.get("top_k"), 5)
    top_p = _as_float(voice.get("top_p"), 1.0)
    temperature = _as_float(voice.get("gpt_temperature"), 1.0)
    repetition_penalty = _as_float(voice.get("repetition_penalty"), 1.35)

    if emotion:
        emo = (cfg.get("emotions") or {}).get(emotion)
        if emo:
            ref_audio = emo.get("ref_audio_path") or ref_audio
            prompt_text = emo.get("prompt_text") or prompt_text
            # 默认倍速（voice.speed_factor）对大多数情绪统一生效；
            # 只有「激烈情绪」保留各自 speed_factor，免得把开心/难过/害怕的起伏拉平。
            if emotion in _EMO_KEEP_SPEED and emo.get("speed_factor") is not None:
                speed_factor = _as_float(emo["speed_factor"], speed_factor)
            # 每种情绪可配自己的表现力参数：难过/安抚偏稳，欢快情绪可更放开
            if emo.get("gpt_temperature") is not None:
                temperature = _as_float(emo["gpt_temperature"], temperature)
            if emo.get("top_k") is not None:
                top_k = _as_int(emo["top_k"], top_k)
            if emo.get("top_p") is not None:
                top_p = _as_float(emo["top_p"], top_p)
            if emo.get("repetition_penalty") is not None:
                repetition_penalty = _as_float(emo["repetition_penalty"], repetition_penalty)

    url = voice["tts_api_url"].rstrip("/") + "/tts"
    # 兜底：实时参数与情绪都没能给出参考音频时，回落到磁盘配置里的默认参考音频。
    # 否则 TTS 会因 ref_audio_path 为空直接返回 400（且不写日志，极难定位）。
    if not str(ref_audio or "").strip():
        ref_audio = str((load_config().get("voice") or {}).get("ref_audio_path") or "")
    if not str(ref_audio or "").strip():
        raise RuntimeError("没有可用的参考音频：请到「设置 → 参考音频」指定，或在 chat_config.json 里配置")
    payload = {
        "text": text,
        "text_lang": voice.get("text_lang", "zh"),
        "ref_audio_path": to_abs(ref_audio),
        "prompt_text": prompt_text,
        "prompt_lang": prompt_lang,
        "text_split_method": voice.get("text_split_method", "cut5"),
        "speed_factor": speed_factor,
        "top_k": top_k,
        "top_p": top_p,
        "temperature": temperature,
        "repetition_penalty": repetition_penalty,
        "seed": int(seed_override) if seed_override is not None
                else _as_int(voice.get("seed"), -1),  # -1=随机；固定=同句可复现
        "batch_size": 1,
        "media_type": voice.get("media_type", "wav"),
        "streaming_mode": False,
    }
    try:
        status, data = _http_json(url, payload, {"Content-Type": "application/json"}, timeout=300)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"TTS 接口错误 HTTP {e.code}: {err}") from e
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"无法连接 TTS 服务({voice.get('tts_api_url')}): {e}") from e
    if status != 200:
        raise RuntimeError(f"TTS 合成失败 HTTP {status}")
    name = time.strftime("%Y%m%d_%H%M%S") + "_%03d.wav" % (int(time.time() * 1000) % 1000)
    dest = Path(out_dir) if out_dir else OUTPUT_DIR
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / name
    out.write_bytes(data)
    if _wav_info(str(out)) is None:
        # 接口返回 200 但不是有效 wav（偶发 JSON 错误/空文件），让上层可重试而非静默丢弃
        raise RuntimeError(f"TTS 返回内容不是有效音频（文本过长或接口异常）：{text[:20]}")
    return str(out)


# 感叹句逐句升调（表现“越说越激动”）：只用于活泼情绪，且同组朗读里至少 2 个感叹句时才触发，
# 否则保持原样不升调。每多一个感叹句，音调整体再高 1 个半音（封顶 3 个半音）。
_EXCLAIM_RISE_EMOS = ("开心", "俏皮", "傲娇")
_EXCLAIM_STEP_ST = 1.0          # 每个后继感叹句比上一个高 1 个半音
_EXCLAIM_MAX_ST = 3.0           # 单个感叹句最多升 3 个半音，避免过头


def _exclaim_sentences(text: str):
    """按句切分，返回 [(整句含标点, 是否感叹句)]。"""
    out = []
    for s in re.findall(r"[^。！？!?；;…]+[。！？!?；;…]*", text or ""):
        s = s.strip()
        if s:
            out.append((s, s[-1] in "！!"))
    return out


def _split_exclaim_chunks(text: str, max_len: int = 64) -> list:
    """把文本切段，让感叹句自成一段，便于对感叹句逐个升调。

    非感叹句照常按长度合并进上一段，控制请求次数；句子超长则沿用硬切兜底。
    """
    def _push(cur, chunks):
        cur = cur.strip()
        if not cur:
            return chunks
        if len(cur) > max_len:
            chunks.extend(_split_tts_chunks(cur, max_len))
        else:
            chunks.append(cur)
        return chunks

    chunks, cur = [], ""
    for s, is_ex in _exclaim_sentences(text):
        if is_ex:
            chunks = _push(cur, chunks)
            cur = ""
            chunks = _push(s, chunks)
        else:
            if cur and len(cur) + len(s) <= max_len:
                cur += s
            else:
                chunks = _push(cur, chunks)
                cur = s
    chunks = _push(cur, chunks)
    return chunks


def _pitch_shift_wav(src: str, semis: float) -> str:
    """把 wav 整体升/降 semis 个半音（时长与采样率不变），返回新文件路径。

    通过 ffmpeg 的 asetrate+aresample+atempo 实现变速不变调的反向处理：
    升采样变快→变高，再用 atempo 拉回原时长。
    """
    if not semis:
        return src
    try:
        import wave as _wave
        with _wave.open(src, "rb") as w:
            sr, ch = w.getframerate(), w.getnchannels()
        ratio = 2 ** (semis / 12.0)
        ff = ROOT / "runtime" / "ffmpeg.exe"
        ff = str(ff) if ff.exists() else "ffmpeg"
        base = src[:-4] if src.lower().endswith(".wav") else src
        dst = f"{base}_p{int(round(semis * 10))}.wav"
        af = f"asetrate={int(sr * ratio)},aresample={sr},atempo={1 / ratio:.6f}"
        subprocess.run([ff, "-y", "-i", src, "-af", af, "-ac", str(ch), dst],
                       check=True, capture_output=True)
        return dst
    except Exception:  # noqa: BLE001
        return src      # 升调失败就回退原声，绝不丢句


# 傲娇专用：句首/分句处独立出现的「哼/哈」等语气词，补成「哼！」的娇嗔口吻。
# 参考音频试听确认：带感叹号的「哼！」才会真正出声，光写「哼，」易被 TTS 当轻飘语气词吞掉。
# 只作用在朗读文本上（该情绪段逐句首边界），不影响聊天记录里原来的字。
_TSUN_INTERJ_RE = re.compile(r"(^|[。！？!?；;…])\s*([哼哈噗啧哎咦]+)([，,。.、]?)(?=[^！!]|$)")


def _voice_tsun_interjections(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return text

    def _rep(m):
        return m.group(1) + m.group(2) + "！"

    return _TSUN_INTERJ_RE.sub(_rep, text)


def _synth_segment(read: str, emo, cfg: dict) -> list:
    """合成一个情绪段的朗读文本，返回拆分出的各小段 wav 列表（顺序即朗读顺序）。

    - 文本超过 ~36 字自动按标点切成小段逐段合成，避免 GPT-SoVITS 长句偶发只读一半；
    - 任一小段失败自动换 seed 重试一次，仍失败立即抛错，绝不静默漏句；
    - 难过/安抚/害怕/害羞 情绪段仍做停顿收紧；
    - 活泼情绪里出现连续感叹句时，后一个感叹句比前一个再高一些（越说越激动）。
    """
    special = emo in ("难过", "安抚", "害怕", "害羞")
    if emo == "傲娇":
        read = _voice_tsun_interjections(read)
    # 感叹句逐句升调只在该情绪启用、且整段确有 ≥2 个感叹句时才走专门切法
    if emo in _EXCLAIM_RISE_EMOS and sum(1 for _, ex in _exclaim_sentences(read) if ex) >= 2:
        chunks = _split_exclaim_chunks(read)
    else:
        chunks = _split_tts_chunks(read)
    if not chunks:
        return []
    fixed = _as_int(cfg["voice"].get("seed"), -1)
    parts = []
    exc_n = 0   # 已读到第几个感叹句（用于逐步升调）
    for ch in chunks:
        if special:
            ch = compress_pauses(ch) or ch
        wav = None
        for attempt in (0, 1):
            try:
                so = (fixed + 1) if (attempt and fixed >= 0) else None
                w = synthesize(ch, cfg, emotion=emo, seed_override=so)
                if _wav_info(w) is not None:
                    wav = w
                    break
            except Exception:  # noqa: BLE001
                continue
        if wav is None:
            raise RuntimeError(f"句子语音合成失败（已重试仍失败）：{ch[:24]}")
        if special:
            wav = tighten_silences(wav)
        else:
            wav = tighten_silences(wav, cap=0.9, keep=0.5, head_cap=0.4, head_keep=0.2)
        # 感叹句 → 递增升调：第 2 个起每次再高 1 个半音，封顶 +3
        if emo in _EXCLAIM_RISE_EMOS and ch.strip()[-1:] in ("！", "!"):
            exc_n += 1
            if exc_n >= 2:
                st = min((exc_n - 1) * _EXCLAIM_STEP_ST, _EXCLAIM_MAX_ST)
                wav = _pitch_shift_wav(wav, st)
        parts.append(wav)
    return parts


# ---------------------------------------------------------------- 情绪连贯自然化
# 突兀的根源：每句各自用不同参考+语速合成后等距硬拼。这里做三件事让它更像真人说话：
# 1. 连续同情绪句子合并成一段连续朗读（稳定同一个基调，减少无谓硬切）；
# 2. 只在情绪真正改变的位置留更长的“呼吸停顿”(0.32s)，同情绪内停得紧凑(0.14s)；
# 3. 模型侧已经要求：同一情绪连写几句、最多转一次情绪（见 _SPEECH_TMPL）。
_SENT_END = "。！？!?…；;"


def _merge_emotion_groups(speak_parts: list, cfg: dict, user_hint: str = None) -> list:
    """把逐句朗读计划按情绪合并成连贯组：返回 [(emo, joined_read), ...]。

    连续同情绪的句子合成一段连续朗读，避免每句换参考/换语速造成的跳变感。
    """
    merged = []
    for emo, read, raw in speak_parts:
        e = emo or fallback_emotion(raw, user_hint) or fallback_emotion(read, user_hint)
        e = e if e in (cfg.get("emotions") or {}) else None
        if not read:
            continue
        if merged and merged[-1][0] == e:
            prev = merged[-1][1]
            sep = "" if prev[-1:] in _SENT_END else "。"
            merged[-1][1] = prev + sep + read
        else:
            merged.append([e, read])
    return merged


def build_emotional_speech(speak_parts: list, cfg: dict, user_hint: str = None):
    """按“情绪连贯自然化”规则把一组待朗读句子合成一段语音。

    返回 (wav路径, [组内使用过的情绪…])；speak_parts = [(emo, 朗读文本, 原文), ...]。
    """
    groups = _merge_emotion_groups(speak_parts, cfg, user_hint)
    voice = cfg.get("voice") or {}
    gap_same = _as_float(voice.get("emotion_gap_same"), 0.14)
    gap_change = _as_float(voice.get("emotion_gap_change"), 0.32)
    group_wavs, used = [], []
    for e, read in groups:
        used.append(e or "默认")
        blocks = _synth_segment(read, e, cfg)
        if not blocks:
            continue
        gw = blocks[0] if len(blocks) == 1 else concat_wavs(blocks, gap=gap_same)
        group_wavs.append(gw)
    if not group_wavs:
        return "", []
    if len(group_wavs) == 1:
        return group_wavs[0], used
    # 情绪切换处留自然的呼吸停顿；同情绪组已被上面合并，这里相邻必为情绪转变
    return concat_wavs(group_wavs, gap=gap_change), used


# ---------------------------------------------------------------- 逐句情绪分段合成
_LINE_EMO_RE = re.compile(r"^\s*【([^】]{1,8})】\s*(.*)$")


def split_reply_segments(raw: str) -> list:
    """把模型回复切成 [(情绪|None, 句子)]。

    支持两种格式：
    1. 新版逐句行：每句一行且行首带【情绪】，各自成为一段；
    2. 兼容旧版：整段回复只带一个标签时，归为一段用同一情绪朗读。
    未带标签的行会并入上一条；段落正文保留（含（动作）等旁白，朗读前再剥离）。
    """
    segs = []
    for ln in (raw or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        m = _LINE_EMO_RE.match(ln)
        if m:
            emo = _normalize_emotion(m.group(1).strip())
            if m.group(2).strip():
                segs.append({"emo": emo, "body": m.group(2).strip()})
            else:
                # 标签独占一行（旧格式：下一行才是正文）
                segs.append({"emo": emo, "body": ""})
        else:
            if segs and not segs[-1]["body"]:
                segs[-1]["body"] = ln
            elif segs:
                segs[-1]["body"] = (segs[-1]["body"].rstrip() + " " + ln).strip()
            else:
                segs.append({"emo": None, "body": ln})
    segs = [s for s in segs if s["body"].strip()]
    return segs


def join_segment_display(segs: list) -> str:
    """展示/记录用：去掉【情绪】标记、把各句拼成一段普通文字（保留（动作）旁白）。"""
    return "".join(s["body"].strip() for s in segs if s["body"].strip()).strip()


def concat_wavs(wav_paths: list, gap: float = 0.25) -> str:
    """把多段 wav 拼接成一段，段间插入 gap 秒静音，返回新 wav 路径。"""
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return wav_paths[0] if wav_paths else ""
    if not wav_paths:
        return ""
    if len(wav_paths) == 1:
        return wav_paths[0]
    sr = None
    chunks = []
    for p in wav_paths:
        try:
            with wave.open(p, "rb") as w:
                if sr is None:
                    sr, ch = w.getframerate(), w.getnchannels()
                raw = w.readframes(w.getnframes())
            arr = np.frombuffer(raw, dtype="<i2").astype(np.int16).reshape(-1, ch)
            if arr.shape[0] == 0:
                raise ValueError("空音频")
        except Exception as e:  # noqa: BLE001
            # 有片段读不到必须显式报错，绝不能静默跳过，否则会“少念几句”
            raise RuntimeError(f"拼接语音失败，片段不可读：{p} ({e})") from e
        chunks.append(arr)
        chunks.append(np.zeros((int(gap * sr), ch), dtype=np.int16))
    if not chunks:
        return ""
    # 去掉最后多余的一段静音后拼接
    cat = np.concatenate(chunks[:-1], axis=0)
    name = time.strftime("%Y%m%d_%H%M%S") + "_%03d_seg.wav" % (int(time.time() * 1000) % 1000)
    out = OUTPUT_DIR / name
    with wave.open(str(out), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(cat.tobytes())
    return str(out)


# ---------------------------------------------------------------- 权重热切换
def _walk_ext(root_rel: str, exts: tuple) -> list:
    root = ROOT / root_rel
    if not root.is_dir():
        return []
    return sorted(str(p.relative_to(ROOT)) for p in root.rglob("*") if p.suffix.lower() in exts)


def _weight_dirs(prefix: str) -> list:
    """找出所有 <prefix>* 目录（GPT_weights / GPT_weights_v2 / ..._v2ProPlus / ..._v4 等）。"""
    try:
        return sorted(p for p in ROOT.glob(prefix + "*") if p.is_dir())
    except Exception:  # noqa: BLE001
        return []


def _is_pretrained_base(rel: str, prefix: str) -> bool:
    """判断 pretrained_models 里的这个文件是不是「TTS 底模」。

    不写死版本号，只按命名规律认，v1/v2/v2Pro/v3/v4/v5 都通用：
      GPT   → `s1*.ckpt`（s1bert25hz-2kh… / s1v3 / v2final 的 s1bert25hz-5kh…）
      SoVITS→ `s2*.pth` （s2G488k / s2Gv3 / s2Gv2ProPlus / s2Gv4 / s2D2333k…）
    排除：`vocoder*`（声码器不是 SoVITS 权重）、`sv/`（声纹模型，选错必炸）。
    """
    low = rel.replace("\\", "/").lower()
    name = low.rsplit("/", 1)[-1]
    if "vocoder" in name or "/sv/" in low:
        return False
    if prefix.startswith("GPT"):
        return name.startswith("s1") and name.endswith(".ckpt")
    return name.startswith("s2") and name.endswith(".pth")


def _scan_weights(prefix: str, exts: tuple, prefer: str = "v2Pro") -> list:
    """扫权重：自己训的（各版本目录 + logs 训练目录）全列 + 官方底模（按命名规律认）。

    注意：官方底模只是「备选」，真正能不能用取决于它和你装的 GPT-SoVITS 版本是否同架构 ——
    这一点由 api_v2.py 决定，选错了它会启动/切换失败，本项目只负责把候选列出来。
    """
    found = []
    seen = set()

    def add(rel):
        if rel not in seen:
            seen.add(rel)
            found.append(rel)

    # 1) 自己训练 / 自己放进去的权重：GPT_weights* / SoVITS_weights* 全版本目录
    for d in _weight_dirs(prefix):
        for rel in _walk_ext(d.name, exts):
            add(rel)
    # 2) 训练完还没拷出来的：logs/<实验名>/ 下的权重
    for rel in _walk_ext("logs", exts):
        add(rel)
    # 3) 官方底模：按命名规律识别（v1~v5 通用）
    for rel in _walk_ext("GPT_SoVITS/pretrained_models", exts):
        if _is_pretrained_base(rel, prefix):
            add(rel)

    def rank(rel):
        low = rel.lower().replace("\\", "/")
        return (0 if prefer.lower() in low else 1,
                1 if "pretrained" in low else 0,
                low)
    return sorted(found, key=rank)


def scan_gpt_weights() -> list:
    """GPT 候选：各版本微调权重 + logs 里的 + 官方底模（s1*）。"""
    return _scan_weights("GPT_weights", (".ckpt",))


def scan_sovits_weights() -> list:
    """SoVITS 候选：各版本微调权重 + logs 里的 + 官方底模（s2*）。"""
    return _scan_weights("SoVITS_weights", (".pth",))



def switch_weights(cfg, gpt_path, sovits_path):
    """调用 TTS 服务的 /set_gpt_weights 与 /set_sovits_weights 热切换权重。"""
    base = cfg["voice"]["tts_api_url"].rstrip("/")
    out = []
    for name, path in (("GPT", gpt_path), ("SoVITS", sovits_path)):
        if not path:
            continue
        endpoint = "gpt" if name == "GPT" else "sovits"
        try:
            req = urllib.request.Request(
                f"{base}/set_{endpoint}_weights?weights_path="
                + urllib.parse.quote(to_abs(path)), method="GET")
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            out.append(f"{name}:{body}")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")
            out.append(f"{name}:HTTP {e.code} {err}")
        except Exception as e:  # noqa: BLE001
            out.append(f"{name}:{e}")
    return " | ".join(out)


# ---------------------------------------------------------------- 会话历史持久化
# 每个会话 = conversations/<sid>.json：{id, title, updated_at, messages:[[user, ai], ...]}
# 供「像网页版 DeepSeek 一样」新建/续聊/改名/删除多个本地历史对话，重启不丢失。
_SID_LOCK = threading.Lock()
_SID_LAST_MS = 0
_SID_SEQ = 0


def _conv_sid() -> str:
    """生成唯一的会话 id。

    原来是纯毫秒时间戳：同一毫秒内连续新建两个会话会得到同一个 id，
    后者会覆盖前者的文件（实测连续调用会全部撞成同一个）。这里改成
    「毫秒时间戳 + 自增序号」，并确认文件名不重复。
    """
    global _SID_LAST_MS, _SID_SEQ
    with _SID_LOCK:
        ms = int(time.time() * 1000)
        if ms == _SID_LAST_MS:
            _SID_SEQ += 1
        else:
            _SID_LAST_MS = ms
            _SID_SEQ = 0
        sid = str(ms) if _SID_SEQ == 0 else "%d%03d" % (ms, _SID_SEQ)
        while _conv_path(sid).exists():
            _SID_SEQ += 1
            sid = "%d%03d" % (ms, _SID_SEQ)
        return sid


def _conv_path(sid: str):
    return CONV_DIR / f"{sid}.json"


def _conv_default_title() -> str:
    return CONV_DEFAULT_TITLE


def conv_list() -> list:
    """返回所有会话元信息：置顶的排最前，其余按最近更新倒序。"""
    items = []
    for f in CONV_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            items.append({"id": d.get("id", f.stem),
                          "title": d.get("title") or CONV_DEFAULT_TITLE,
                          "updated_at": float(d.get("updated_at", 0)),
                          "pinned": bool(d.get("pinned", False)),
                          "pinned_at": float(d.get("pinned_at", 0)),
                          "messages": d.get("messages", [])})
        except Exception:  # noqa: BLE001
            continue
    # 置顶优先（置顶时间越晚越靠前），未置顶的按更新时间倒序
    items.sort(key=lambda x: (x["pinned_at"] if x["pinned"] else 0, x["updated_at"]),
               reverse=True)
    return items


def convs_state_value() -> list:
    """扁平历史列表要显示的精简数据（不含 messages，避免状态过大）。"""
    return [{"id": c["id"], "title": c["title"], "pinned": c["pinned"]}
            for c in conv_list()]


def conv_load(sid: str):
    if not sid:
        return None
    p = _conv_path(sid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def conv_save(sid: str, title: str, messages: list) -> None:
    """保存会话。messages 形如 [[user, ai], ...]。title 非空则覆盖旧标题。"""
    if not sid:
        return
    old = conv_load(sid) or {}
    msgs = messages if isinstance(messages, list) else []
    aud = [str(x or "") for x in (old.get("audio") or [])]
    aud = (aud + [""] * len(msgs))[:len(msgs)]
    _old_ver = old.get("versions")
    _old_rep = old.get("replay")
    data = {
        "id": sid,
        "title": (title or "").strip() or old.get("title") or CONV_DEFAULT_TITLE,
        "updated_at": time.time(),
        "messages": msgs,
        "audio": aud,                          # 与 messages 一一对应的「每条回答的语音」
        "versions": _old_ver if isinstance(_old_ver, dict) else {},   # 重新加载产生的多个回答版本
        "summary": old.get("summary") or "",        # 长程记忆：更早对话的折叠摘要
        "summary_upto": _as_int(old.get("summary_upto"), 0),
        "replay": [it for it in _old_rep if isinstance(it, dict)]
                  if isinstance(_old_rep, list) else [],              # 保留该会话的语音回放记录
        "settings": old.get("settings") or {},   # 保留该会话专属的设置快照
        "pinned": bool(old.get("pinned", False)),
        "pinned_at": _as_float(old.get("pinned_at"), 0.0),
    }
    try:
        _conv_path(sid).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def conv_delete(sid: str) -> None:
    if not sid:
        return
    # 删除会话时一并清理其归档的本地语音文件，避免残留孤儿 wav
    try:
        _delete_replay_wavs(sid)
    except Exception:  # noqa: BLE001
        pass
    try:
        p = _conv_path(sid)
        if p.exists():
            p.unlink()
    except Exception:  # noqa: BLE001
        pass


def _conv_state(sid: str):
    """读会话的「消息 / 每条回答的语音 / 回答版本」三件套。

    - M：[[提问, 回答], ...]，就是界面上从上到下那条对话（当前分支）
    - A：与 M 一一对应的语音文件路径（没有则空串）
    - V：{轮次: {"cur": 当前第几个, "alts": [{"reply","wav","tail","tail_audio"}, ...]}}
         只有「重新加载」过的轮次才有，用于 <2/2> 来回切换
    """
    c = conv_load(sid) or {}
    raw_msgs = c.get("messages")
    raw_msgs = raw_msgs if isinstance(raw_msgs, list) else []
    M = [[(p[0] if isinstance(p, (list, tuple)) and len(p) > 0 and p[0] is not None else ""),
          (p[1] if isinstance(p, (list, tuple)) and len(p) > 1 and p[1] is not None else "")]
         for p in raw_msgs]
    raw_aud = c.get("audio")
    raw_aud = raw_aud if isinstance(raw_aud, list) else []
    A = [str(x or "") for x in raw_aud]
    A = (A + [""] * len(M))[:len(M)]
    V = c.get("versions") or {}
    V = dict(V) if isinstance(V, dict) else {}   # 容错：versions 非 dict 时不崩
    return c, M, A, V


def _versions_sync(M, A, V):
    """把「当前界面上这条对话」的尾部写回它所属的那个版本。

    ★ 关键：不在当前可见范围内的轮次（重新加载后，它后面的对话被折进了某个版本里），
    它们的版本信息要**原样保留**，不能丢 —— 否则切回去时那个 < 2/2 > 就没了。
    """
    out = {}
    for k, e in (V or {}).items():
        try:
            i = int(k)
        except Exception:  # noqa: BLE001
            continue
        e = e or {}
        # 容错：alts 里出现非 dict 的脏数据（旧文件/手工编辑）时跳过，别让 dict() 抛异常
        alts = [dict(x) if isinstance(x, dict) else {} for x in (e.get("alts") or [])]
        cur = _as_int(e.get("cur"), 0)
        if 0 <= i < len(M) and 0 <= cur < len(alts):
            alts[cur]["reply"] = M[i][1]
            alts[cur]["wav"] = A[i] if i < len(A) else ""
            alts[cur]["tail"] = [list(p) for p in M[i + 1:]]
            alts[cur]["tail_audio"] = list(A[i + 1:])
        out[k] = {"cur": cur, "alts": alts}      # 看得见就刷新，看不见就照原样留着
    return out


def _versions_shift(V, removed: int):
    """删掉第 removed 轮后，把它后面那些轮次的版本号往前挪一位（别把版本信息丢掉）。"""
    out = {}
    for k, e in (V or {}).items():
        try:
            i = int(k)
        except Exception:  # noqa: BLE001
            continue
        if i == removed:
            continue
        out[str(i - 1 if i > removed else i)] = e
    return out


def _conv_state_save(sid: str, M, A, V=None) -> None:
    """把三件套写回会话文件（标题/回放/设置/置顶等原样保留）。"""
    if not sid:
        return
    c = conv_load(sid) or {"id": sid, "title": CONV_DEFAULT_TITLE}
    V = _versions_sync(M, A, V if V is not None else (c.get("versions") or {}))
    c["messages"] = [list(p) for p in M]
    c["audio"] = [str(x or "") for x in (list(A) + [""] * len(M))[:len(M)]]
    c["versions"] = V
    c["updated_at"] = time.time()
    try:
        _conv_path(sid).write_text(
            json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _turn_audio_set(sid: str, turn: int, wav: str) -> None:
    """记下「第 turn 轮回答」的语音，供界面上那个 ▶ 按钮播放。turn 给 -1 表示最后一轮。"""
    _c, M, A, V = _conv_state(sid)
    if turn < 0:
        turn = len(M) - 1
    if not (0 <= turn < len(M)):
        return
    A = (A + [""] * len(M))[:len(M)]
    A[turn] = wav or ""
    _conv_state_save(sid, M, A, V)


def _forget_audio(sid: str, wav: str) -> None:
    """删掉一段语音：本地文件 + 会话里的回放记录（用于「删除」/「改写」回答）。

    只删指定的那一份；同一路径被多处引用时也会一并清掉（都是同一条语音）。
    """
    wav = str(wav or "").strip()
    if not sid or not wav:
        return
    try:
        items = [it for it in _replay_list(sid) if (it or {}).get("wav") != wav]
        _replay_write(sid, items)
    except Exception:  # noqa: BLE001
        pass
    try:
        p = Path(to_abs(wav))
        if p.is_file():
            p.unlink()
    except Exception:  # noqa: BLE001
        pass


def _conv_patch(sid: str, **fields) -> None:
    """只改会话文件里的少数字段，不动 updated_at（改名/置顶不该把它顶到最前）。"""
    if not sid:
        return
    d = conv_load(sid)
    if not d:
        return
    d.update(fields)
    try:
        _conv_path(sid).write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def conv_set_pinned(sid: str, on: bool) -> None:
    """置顶（置顶时间倒序）或取消置顶。"""
    _conv_patch(sid, pinned=bool(on), pinned_at=(time.time() if on else 0))


# ---------------------------------------------------------------- 会话级设置快照
# 每个会话独立保存一份「人设/LLM/音色/语速/采样」设置：
# 切回该会话即还原当时调试的参数；「新对话」用 chat_config.json 的全局默认。
_SET_LLM_KEYS = ("api_key", "base_url", "model", "temperature", "max_tokens",
                 "max_reply_sents", "max_reply_chars")
_SET_VOICE_KEYS = ("ref_audio_path", "prompt_text", "prompt_lang", "text_lang",
                   "text_split_method", "speed_factor", "top_k", "top_p",
                   "gpt_temperature", "repetition_penalty", "emotion_mode")
# 快照叠加时的类型白名单：脏值按默认/旧值兜底，避免一个坏字段把整页设置搞崩
_FLOAT_LLM = ("temperature",)
_INT_LLM = ("max_tokens", "max_reply_sents", "max_reply_chars")
_FLOAT_VOICE = ("speed_factor", "top_p", "gpt_temperature", "repetition_penalty")
_INT_VOICE = ("top_k",)


def snapshot_from_cfg(cfg: dict) -> dict:
    """从配置里抽出会话可调字段，生成一份设置快照（情绪库/权重/端口不随会话）。"""
    llm = cfg.get("llm") or {}
    vc = cfg.get("voice") or {}
    return {
        "persona": (cfg.get("persona") or {}).get("system_prompt", ""),
        "llm": {k: llm.get(k) for k in _SET_LLM_KEYS},
        "voice": {k: vc.get(k) for k in _SET_VOICE_KEYS},
        "history": {"keep_turns": (cfg.get("history") or {}).get("keep_turns", 8)},
    }


def apply_settings(cfg: dict, snap: dict) -> dict:
    """把会话快照叠加到配置上（快照没有的字段保持全局默认）。

    对数值字段做类型校验：快照里存到 None/字符串等脏值时按旧值兜底，
    否则会让 TTS/界面读取时抛异常（症状：切到某会话整页报错）。
    """
    snap = snap or {}
    if isinstance(snap.get("persona"), str):
        cfg["persona"]["system_prompt"] = snap["persona"]
    lsnap = snap.get("llm") or {}
    for k in _SET_LLM_KEYS:
        if k not in lsnap:
            continue
        if k in _FLOAT_LLM:
            cfg["llm"][k] = _as_float(lsnap[k], cfg["llm"].get(k))
        elif k in _INT_LLM:
            cfg["llm"][k] = _as_int(lsnap[k], cfg["llm"].get(k))
        else:
            cfg["llm"][k] = lsnap[k]
    vsnap = snap.get("voice") or {}
    for k in _SET_VOICE_KEYS:
        if k not in vsnap:
            continue
        v = vsnap[k]
        # ★ 旧快照里可能存着空 ref/prompt（历史 bug 遗留），别用它覆盖磁盘上的有效值，
        #   否则 cfg 变空 → 无情绪句回落到空参考 → TTS 400（症状：重新加载语音合成失败）。
        if k in ("ref_audio_path", "prompt_text"):
            if not str(v or "").strip():
                continue
            cfg["voice"][k] = str(v)
        elif k in _FLOAT_VOICE:
            cfg["voice"][k] = _as_float(v, cfg["voice"].get(k))
        elif k in _INT_VOICE:
            cfg["voice"][k] = _as_int(v, cfg["voice"].get(k))
        elif k == "emotion_mode":
            cfg["voice"][k] = bool(v)
        else:
            cfg["voice"][k] = v
    hs = snap.get("history") or {}
    if "keep_turns" in hs:
        cfg["history"]["keep_turns"] = _as_int(hs["keep_turns"],
                                               cfg["history"].get("keep_turns", 8))
    return cfg


def conv_settings(sid: str) -> dict:
    """取某会话保存的设置快照（没有则返回空 dict，即用全局默认）。"""
    c = conv_load(sid) or {}
    return c.get("settings") or {}


def conv_settings_save(sid: str, snap: dict) -> None:
    """把设置快照写回会话文件（保留消息/回放/标题）。"""
    if not sid:
        return
    c = conv_load(sid) or {"id": sid, "title": CONV_DEFAULT_TITLE}
    c["settings"] = snap or {}
    c["updated_at"] = time.time()
    try:
        _conv_path(sid).write_text(
            json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def settings_values(cfg: dict) -> list:
    """把配置转成设置区控件的显示值列表（顺序与 UI 组件列表严格一致）。"""
    llm = cfg.get("llm") or {}
    vc = cfg.get("voice") or {}
    return [
        (cfg.get("persona") or {}).get("system_prompt", "") or "",
        mask_api_key(llm.get("api_key", "")), llm.get("base_url", "") or "", llm.get("model", "") or "",
        _as_float(llm.get("temperature"), 1.0), _as_int(llm.get("max_tokens"), 1024),
        _as_int(llm.get("max_reply_sents"), 4), _as_int(llm.get("max_reply_chars"), 60),
        vc.get("ref_audio_path", "") or "", vc.get("ref_audio_path", "") or "",  # ref_dd 与 ref_path 同源
        vc.get("prompt_text", "") or "",
        vc.get("prompt_lang", "zh"), vc.get("text_lang", "zh"),
        vc.get("text_split_method", "cut5"), _as_float(vc.get("speed_factor"), 1.0),
        _as_float(vc.get("speed_factor"), 1.0),   # speed_num 与 speed 同源（滑块 + 自定义输入框）
        _as_int(vc.get("top_k"), 5), _as_float(vc.get("top_p"), 1.0),
        _as_float(vc.get("gpt_temperature"), 1.0), _as_float(vc.get("repetition_penalty"), 1.35),
        _as_int((cfg.get("history") or {}).get("keep_turns"), 8),
        bool(vc.get("emotion_mode", True)),
    ]


def settings_updates(cfg: dict, ref_choices: list = None) -> tuple:
    """生成让设置区控件切到 cfg 状态的 gr.update 元组（与 settings_values 同序）。"""
    if ref_choices is None:
        ref_choices = [name for _, name in ref_choices_for_dropdown()]
    outs = []
    for i, v in enumerate(settings_values(cfg)):
        if i == 8:  # 参考音频下拉：值须在候选中，否则留空（文本框仍显示实际路径）
            outs.append(gr.update(value=v if v and v in ref_choices else None))
        else:
            outs.append(gr.update(value=v))
    return tuple(outs)


def settings_updates_for_sid(sid: str) -> tuple:
    """某会话（含空/无快照会话）应显示的那套设置更新：有快照还原，无则全局默认。"""
    cfg = load_config()
    if sid:
        apply_settings(cfg, conv_settings(sid))
    return settings_updates(cfg)


def _chatbox_to_messages(chat: list) -> list:
    """把 Chatbot 二元组去占位、去掉空回复后转成可持久化的消息对列表。"""
    out = []
    for u, a in (chat or []):
        u = (u or "").strip()
        a = (a or "").strip()
        if u == "……":
            u = ""
        if not u and not a:
            continue
        if u:
            out.append([u, a])
        elif out and not out[-1][1]:
            out[-1][1] = a
    return out


def _title_from_messages(msgs: list) -> str:
    """取第一条用户消息自动生成会话标题（去标点，前 18 字）。"""
    for u, a in msgs:
        if u:
            t = re.sub(r"[\s，。！？、；：,.!?;:…~～—-]+", "", u)
            return t[:18] or CONV_DEFAULT_TITLE
    return CONV_DEFAULT_TITLE


def persist_chat(sid: str, chat: list) -> str:
    """把当前聊天框内容落盘到会话 sid，并返回会话标题。

    标题规则：旧标题是默认「新对话」时，取第一条用户提问自动生成；已改过名则保留。
    """
    msgs = _chatbox_to_messages(chat)
    old = conv_load(sid) or {}
    title = old.get("title") or ""
    if not title or title == CONV_DEFAULT_TITLE:
        title = _title_from_messages(msgs)
    conv_save(sid, title, msgs)
    return title


# ---------------------------------------------------------------- LLM 对话
# 只要对方是「OpenAI 兼容」接口（DeepSeek / OpenAI / 各类中转站 / Ollama / vLLM / LM Studio…）
# 都能直接用：只认 base_url + api_key + model，不绑死任何一家。
def _llm_base(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def _llm_chat_url(base_url: str) -> str:
    """把用户填的 base_url 规范成 /chat/completions 地址（填全了也不会重复拼）。"""
    b = _llm_base(base_url)
    if b.endswith("/chat/completions"):
        return b
    return b + "/chat/completions"


def _llm_models_url(base_url: str) -> str:
    b = _llm_base(base_url)
    if b.endswith("/models"):
        return b
    return b + "/models"


def _is_deepseek(base_url: str) -> bool:
    return "deepseek" in (base_url or "").lower()


def llm_chat(messages: list, cfg: dict, retries: int = 1) -> str:
    """调用任意 OpenAI 兼容接口（DeepSeek / OpenAI / 中转站 / Ollama…）。空内容时自动重试。"""
    llm = cfg["llm"]
    if not _llm_base(llm.get("base_url")):
        raise RuntimeError("还没填 AI 接口地址：设置 → AI 接口 → 接口地址 base_url")
    url = _llm_chat_url(llm.get("base_url"))
    if not (llm.get("model") or "").strip():
        raise RuntimeError("还没填模型名：设置 → AI 接口 → 模型（可先点「🔌 测试连通」看该接口支持哪些模型）")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + (llm.get("api_key") or "").strip(),
    }
    last_err = None
    for attempt in range(max(1, retries + 1)):
        body = {
            "model": (llm.get("model") or "").strip(),
            "messages": messages,
            "temperature": float(llm.get("temperature", 1.0)),
            "max_tokens": int(llm.get("max_tokens", 1024)),
            "stream": False,
        }
        # thinking 是 DeepSeek 专有参数：V4 默认思考模式会先写思维链，
        # 容易吃光 max_tokens 导致空回答，语音场景要关掉；别的接口不认识它，就别发。
        if _is_deepseek(llm.get("base_url")):
            body["thinking"] = {"type": "disabled"}
        try:
            status, data = _http_json(url, body, headers, timeout=180)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")
            last_err = f"AI 接口错误 HTTP {e.code}: {err}"
            continue
        except Exception as e:  # noqa: BLE001
            last_err = f"无法连接 LLM 服务({llm.get('base_url')}): {e}"
            continue
        if status != 200:
            last_err = f"LLM 调用失败 HTTP {status}"
            continue
        try:
            obj = json.loads(data.decode("utf-8"))
            content = obj["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            last_err = "LLM 返回格式异常"
            continue
        content = (content or "").strip()
        if content:
            return content
        last_err = "模型返回了空内容"
    raise RuntimeError(last_err or "LLM 调用失败")


def probe_llm(base_url, api_key, model):
    """🔌 测试连通：判断这个 AI 接口能不能用，并尽量列出它支持的模型。

    两步：先 GET /models（OpenAI 兼容接口基本都有，能直接拿到模型清单）；
    拿不到再发一条最小对话（max_tokens=8）验证真正能不能出字。
    返回 (状态文案 Markdown, 模型下拉的更新)。
    """
    base = _llm_base(base_url)
    key = resolve_api_key(api_key)
    cur = (model or "").strip()
    if not base:
        return "❌ 请先填「接口地址 base_url」，例如 `https://api.deepseek.com` 或 `http://127.0.0.1:11434/v1`", gr.update()
    headers = {"Authorization": "Bearer " + key} if key else {}
    lines = [f"**接口**：`{base}`　**鉴权**：{'已填 API Key' if key else '未填（本地 Ollama 之类可以不填）'}"]
    models = []
    # ① 列模型
    try:
        status, data = _http_get_json(_llm_models_url(base), headers, timeout=20)
        if status == 200:
            obj = json.loads(data.decode("utf-8", errors="ignore") or "{}")
            items = obj.get("data") or obj.get("models") or []
            for it in items if isinstance(items, list) else []:
                mid = ""
                if isinstance(it, dict):
                    mid = it.get("id") or it.get("name") or it.get("model") or ""
                else:
                    mid = str(it)
                if str(mid).strip():
                    models.append(str(mid).strip())
    except urllib.error.HTTPError as e:
        lines.append(f"· 列模型 `/models` 返回 HTTP {e.code}（有些中转站关掉了这个接口，不影响使用）")
    except Exception as e:  # noqa: BLE001
        lines.append(f"· 列模型 `/models` 失败：{e}")
    if models:
        show = models[:40]
        lines.append("✅ 连通「列模型」成功，共 **%d** 个模型：`%s`%s" % (
            len(models), "`、`".join(show), "……" if len(models) > len(show) else ""))
        # 记到配置里，下次打开设置页下拉里就有这些模型可选
        try:
            c = load_config()
            c.setdefault("llm", {})["models"] = models[:200]
            save_config(c)
        except Exception:  # noqa: BLE001
            pass
        if cur and cur in models:
            lines.append(f"✅ 当前填的模型 `{cur}` 在清单里")
        elif cur:
            lines.append(f"⚠️ `/models` 清单里没有 `{cur}`——不少接口的模型名是别名，"
                         f"不会列进清单；**看下面「试发对话」能不能通**，能通就没问题")
        else:
            lines.append("⚠️ 还没填模型名，从上面挑一个填进「模型」")
    # ② 真发一次最小对话（有模型名才发，否则不知道用哪个）
    if cur:
        body = {"model": cur, "messages": [{"role": "user", "content": "你好"}],
                "max_tokens": 8, "stream": False, "temperature": 0.3}
        if _is_deepseek(base):
            body["thinking"] = {"type": "disabled"}
        try:
            status, data = _http_json(_llm_chat_url(base), body, {**headers,
                                                                  "Content-Type": "application/json"},
                                      timeout=30)
            obj = json.loads(data.decode("utf-8", errors="ignore") or "{}")
            reply = ((obj.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            lines.append("✅ 试发一句话成功，模型回了：`%s`" % (reply.strip()[:40] or "(空内容)"))
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")[:300]
            lines.append(f"❌ 试发对话失败 HTTP {e.code}：`{err}`")
        except Exception as e:  # noqa: BLE001
            lines.append(f"❌ 试发对话失败：{e}")
    else:
        lines.append("（填上模型名后可以再测一次「真发一句话」）")
    upd = gr.update(choices=models, value=cur) if models else gr.update()
    return "\n\n".join(lines), upd



# ---------------------------------------------------------------- 长程记忆摘要
# 会话可以聊很久，但送给 LLM 的上下文窗口有限（保持 keep 轮完整消息）。
# 当历史超出窗口后，把更早的轮次“折叠”成一段记忆摘要存到会话里，供后续回答引用，
# 这样几十轮之后仍记得开头聊过什么（类似 chatbox 的长程记忆），又不撑爆上下文。
def _summarize_turns(cfg: dict, old_summary: str, new_turns: list) -> str:
    """把旧摘要 + 一段新对话合并成新的精简摘要（中文，3~8 条要点）。"""
    if not new_turns:
        return old_summary
    dialog = "\n".join(
        f"用户：{u or ''}\n你：{a or ''}" for u, a in new_turns if (u or a))
    if not dialog:
        return old_summary
    prompt = (
        "下面是同一段长对话的较早部分。请把它们压缩成一份简洁的中文记忆摘要，"
        "只保留对后续聊天真正有用的信息：聊过的重要话题、"
        "用户告诉过你的事实（名字、喜好、处境、约定）、你答应过或承诺过的事、情绪变化。"
        "用条目或短句写，控制在 8 行以内；不要写开场白。\n\n"
    )
    if old_summary:
        prompt += f"【已有摘要】\n{old_summary[:1500]}\n\n【这段新发生的更早对话】\n{dialog[:3500]}\n"
    else:
        prompt += f"【这段更早对话】\n{dialog[:4000]}\n"
    try:
        return (llm_chat([{"role": "user", "content": prompt}], cfg) or "").strip()[:2000]
    except Exception:  # noqa: BLE001
        return old_summary


def refresh_session_summary(sid: str, cfg: dict, chat: list) -> str:
    """把超出保留窗口的更早对话折叠进该会话的记忆摘要；返回最新摘要文本。

    触发节奏：只有当“超出保留窗口、但还没被折叠”的历史又攒够 keep 轮时，
    才调用一次 LLM 把它们折叠进摘要；其余轮次只做轻量判断，不会拖慢每轮。
    失败时静默保留旧摘要。
    """
    if not sid:
        return ""
    msgs = _chatbox_to_messages(chat)
    k = max(2, int((cfg.get("history") or {}).get("keep_turns", 8)))
    keep_win = k * 2                  # 折叠后保留最近 2k 轮完整（窗口3k，滞后段仍在内）
    if len(msgs) < keep_win + k + 1:
        return ""
    conv = conv_load(sid) or {}
    old_summary = (conv.get("summary") or "").strip()
    upto = _as_int(conv.get("summary_upto"), 0)     # 已折叠到第几条（消息对）
    # 保留最近 keep_win 轮完整：fold_target 是当前能折叠的终点
    fold_target = len(msgs) - keep_win
    if fold_target <= upto:
        return old_summary
    if fold_target - upto < k:     # 攒够 k 轮未折才真正折叠，避免每轮都慢
        return old_summary
    new_turns = msgs[upto:fold_target]
    summary = _summarize_turns(cfg, old_summary, new_turns)
    conv["summary"] = summary
    conv["summary_upto"] = fold_target
    try:
        _conv_path(sid).write_text(
            json.dumps(conv, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return summary


# ---------------------------------------------------------------- 参考音频辅助
def load_transcript_map(log_dir: Path) -> dict:
    """解析 logs/<exp>/2-name2text.txt: 切片wav名 -> 中文文本"""
    txt = log_dir / "2-name2text.txt"
    mapping = {}
    if not txt.exists():
        return mapping
    for line in txt.read_text(encoding="utf-8", errors="ignore").splitlines():
        cols = line.rstrip("\n").split("\t")
        if len(cols) >= 4:
            mapping[cols[0]] = cols[3]
        elif len(cols) == 2:
            mapping[cols[0]] = cols[1]
    return mapping


def scan_ref_audios() -> list:
    """扫描可用于「参考音频」的音频，返回 (相对路径, 中文文本) 列表。

    依次找这几处（找到就不重复）：
      1. `logs/<实验>/5-wav32k/*.wav` —— 标准 GPT-SoVITS 训练切片，能配上文本；
      2. `data/raw`、`raw`、`train/raw`、`_train_da/raw` 等常见训练集目录；
      3. `refs/`、`assets/refs/` —— 自己丢进去的参考音频；
      4. `output/voice_chat/` —— 以前合成过的片段（临时凑手用）。
    原来只扫第 1 处，没有 logs 切片时下拉就是空的（看着像坏了）。
    """
    items = []
    seen = set()

    def add(path: Path, text: str = ""):
        try:
            rel = str(path.relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            return
        if rel in seen:
            return
        seen.add(rel)
        items.append((rel, text))

    # 1) 训练日志切片（有对应文本，最理想）
    for sub in sorted((ROOT / "logs").glob("*/5-wav32k")):
        if sub.is_dir():
            mapping = load_transcript_map(sub.parent)
            for wav in sorted(sub.glob("*.wav")):
                add(wav, mapping.get(wav.name, ""))

    # 2) 训练集原始目录
    for cand in ("_train_da/raw", "data/raw", "raw", "train/raw"):
        d = ROOT / cand
        if not d.is_dir():
            continue
        mapping = load_transcript_map(d.parent)
        for ext in ("*.wav", "*.mp3", "*.flac", "*.m4a"):
            for f in sorted(d.glob(ext)):
                add(f, mapping.get(f.name, ""))

    # 3) 专门的参考音频目录 + 4) 历史合成片段
    for cand in ("refs", "assets/refs", "output/voice_chat"):
        d = ROOT / cand
        if not d.is_dir():
            continue
        for ext in ("*.wav", "*.mp3", "*.flac"):
            for f in sorted(d.rglob(ext)):
                add(f)

    return items


def find_ref_text(rel_path: str) -> str:
    p = (ROOT / rel_path).resolve() if rel_path else None
    if not p or not p.exists():
        return ""
    if "5-wav32k" in p.parts:
        log_dir = p.parent.parent
        mapping = load_transcript_map(log_dir)
        return mapping.get(p.name, "")
    return ""


_REF_CHOICE_MAX = 500   # 下拉里最多列这么多（再多渲染会卡）；要找别的直接填路径


def ref_choices_for_dropdown(items=None):
    items = list(scan_ref_audios() if items is None else items)
    if len(items) > _REF_CHOICE_MAX:
        items = items[:_REF_CHOICE_MAX]
    return [(f"{name}    |    文本:{text[:18] + ('…' if len(text) > 18 else '')}" if text else name, name)
            for name, text in items]


# ---------------------------------------------------------------- Gradio 界面逻辑
EMOTION_LABELS = ("开心", "俏皮", "疑惑", "安抚", "难过", "认真", "傲娇",
                  "害怕", "害羞", "亲密")
# 「激烈情绪」：保留各自 speed_factor，不被设置里的默认倍速覆盖（其余情绪统一用默认倍速）
_EMO_KEEP_SPEED = ("开心", "难过", "害怕")
# 把 LLM 常写的近义情绪词归一到标准情绪，保证总能命中对应参考音频
_EMO_SYN = {
    "开心": ["开心", "高兴", "快乐", "愉快", "兴奋", "喜悦", "欢快", "惊喜"],
    "俏皮": ["俏皮", "调皮", "活泼", "可爱", "顽皮", "逗趣", "搞怪"],
    "疑惑": ["疑惑", "好奇", "疑问", "不解", "困惑", "惊讶", "纳闷", "怀疑"],
    "安抚": ["安抚", "安慰", "温柔", "心疼", "怜惜", "关心", "呵护", "平静", "治愈"],
    "难过": ["难过", "伤心", "悲伤", "伤感", "低落", "沮丧", "失落", "忧郁", "消沉",
              "心酸", "无奈", "哀伤", "遗憾", "委屈", "自责", "内疚", "绝望"],
    "认真": ["认真", "郑重", "严肃", "正经", "沉稳", "坦诚"],
    "傲娇": ["傲娇", "嘴硬", "赌气", "别扭", "倔强", "生气", "闹脾气", "使性子"],
    "害怕": ["害怕", "恐惧", "惊慌", "慌乱", "惊吓", "不安", "紧张", "怯生生", "发抖", "怕"],
    "害羞": ["害羞", "腼腆", "羞涩", "脸红", "不好意思", "扭捏", "难为情"],
    "亲密": ["亲密", "亲昵", "亲热", "撒娇", "依恋", "依偎", "甜腻", "软萌", "亲爱"],
}
_EMO_PAT = re.compile(r"^\s*【([^】]{1,6})】\s*\n?")


def _normalize_emotion(emo: str):
    if not emo:
        return None
    for std, syns in _EMO_SYN.items():
        if emo in syns:
            return std
    return None


def parse_emotion_answer(raw: str):
    """从模型回复解析【情绪】标签，返回 (emotion, 纯文本)。未命中则 (None, raw)。

    容错：
    1. 标签用词多变（【伤心】【心疼】【低落】…）时自动归一到 7 类标准情绪；
    2. 模型偶尔漏写方括号，直接以“难过 正文…”/“难过：正文”开头；
    3. 模型偶尔把标签放到正文末尾（“…啦。【开心】”）。
    只有开头标签位置确认后才剥离，避免误删正文。
    """
    if not raw:
        return None, raw
    text = raw.strip()

    # 情况1：整行开头就是【情绪】
    m = _EMO_PAT.match(text)
    if m:
        return _normalize_emotion(m.group(1)), text[m.end():].strip()

    # 情况2：漏方括号，如“难过\n正文 / 难过 正文 / 难过：正文”
    for name in EMOTION_LABELS:
        if text.startswith(name):
            tail = text[len(name):]
            if not tail:
                return name, ""
            c = tail[0]
            if c in ("\n", "\r", " ", "\t", "：", ":", "、", "，", "。", "！", "？", "…"):
                return name, tail.strip(" \t\r\n：:、，。！？…")
            break  # “认真思考…”这类正常正文，不要误删

    # 情况3：标签跑到文末，如“…啦。【开心】”或“…啦。\n【开心】”
    tail_m = re.search(r"【([^】]{1,6})】\s*$", text)
    if tail_m:
        emo = _normalize_emotion(tail_m.group(1))
        if emo:
            body = text[:tail_m.start()].strip(" \t\r\n。！？!?；;…，,、")
            return emo, (body + ("。" if body and body[-1] not in "。！？!?…" else ""))
    return None, text


# 模型漏写标签时的最后兜底：凭正文关键词推断语调，选对参考音频
# 词表同时收录整词(难过/伤心/低落)与偏口语组合(好难过/想哭)，避免漏判
_SOOTHE_WORDS = ("别难过", "别伤心", "不哭", "不要哭", "抱抱", "安慰", "心疼", "我在", "陪你",
                 "深呼吸", "没事的", "没事啦", "放宽心", "慢慢来", "缓缓", "会好", "别怕", "有我")
_SAD_WORDS = ("难过", "伤心", "悲伤", "伤感", "低落", "沮丧", "失落", "委屈", "想哭", "哭了",
              "哭腔", "流泪", "眼泪", "含泪", "心碎", "心痛", "心酸", "心里难受", "好累", "好痛",
              "孤独", "孤单", "寂寞", "迷茫", "撑不住", "撑不下去", "绝望", "崩溃", "遗憾",
              "舍不得", "不舍", "分别", "离别", "离开", "再也不", "回不来", "等不到",
              "听不到", "被丢下", "假装", "发颤", "哽咽", "低沉", "静默", "难受",
              "辛苦了", "熬不住", "空落落", "酸涩", "苦涩", "黑暗", "尽头")
_HAPPY_WORDS = ("哈哈", "嘻嘻", "好开心", "真开心", "开心", "高兴", "快乐", "愉快", "太棒了",
                "好幸福", "幸福", "有趣", "好好笑", "笑", "兴奋", "雀跃", "欢呼", "惊喜",
                "甜", "阳光", "活力", "开怀", "灿烂")
# 亲昵/撒娇语气词：必须优先于"开心/陪你"判定，否则亲密句会因带"开心啦""陪你嘛"被抢判
_CLOSE_WORDS = ("撒娇", "亲昵", "黏你", "黏人", "抱紧你", "亲你", "想多陪你", "陪陪你嘛",
                "陪着你嘛", "陪你玩", "最想你", "好想你", "舍不得你", "离不开你",
                "最喜欢你了", "最喜欢和你", "和你在一起", "想要你陪", "想要你抱", "甜腻")
_AFRAID_WORDS = ("好怕", "害怕", "恐惧", "惊慌", "吓坏", "发抖", "好黑", "会不会", "不敢")
_SHY_WORDS = ("不好意思", "害羞", "腼腆", "脸红", "羞", "心跳加速", "难为情")


def fallback_emotion(text: str, request_hint: str = None):
    """无标签时推断语调：亲密 > 安抚 > 害怕 > 害羞 > 悲伤 > 开心。

    用户请求情绪(request_hint)的优先级高于正文关键词碰巧命中
    （例如撒娇句常带"开心啦""陪你"，会被开心/安抚词表抢走），
    所以先看用户明确要求，再看正文语气词。
    """
    text = text or ""
    # 用户明确要求某情绪（如“说点亲密的/撒娇的话”）：优先采信，
    # 但正文若明确在写“伤心/安慰”内容则尊重正文，避免用户随口一句“说点开心的难过话”乱套。
    if request_hint:
        if request_hint == "难过":
            if not any(w in text for w in _HAPPY_WORDS):
                return "难过"
        elif request_hint == "开心":
            if not any(w in text for w in _SAD_WORDS):
                return "开心"
        elif request_hint == "安抚":
            return "安抚"
        else:  # 亲密/害羞/害怕/傲娇 等用户点名的稀缺情绪：直接采信
            return request_hint
    if any(w in text for w in _CLOSE_WORDS):
        return "亲密"
    if any(w in text for w in _SOOTHE_WORDS):
        return "安抚"
    if any(w in text for w in _AFRAID_WORDS):
        return "害怕"
    if any(w in text for w in _SHY_WORDS):
        return "害羞"
    if any(w in text for w in _SAD_WORDS):
        return "难过"
    if any(w in text for w in _HAPPY_WORDS):
        return "开心"
    if request_hint in ("难过", "安抚", "开心", "害怕", "害羞", "亲密", "傲娇"):
        return request_hint
    return None


# 从用户输入提取“期望的情绪”：让模型漏标签时也能按用户要求定调
_SAD_REQUEST = ("伤心", "难过", "悲伤", "低沉", "低落", "哭", "哭诉", "忧郁", "沮丧", "委屈", "失落", "丧")
_HAPPY_REQUEST = ("开心", "高兴", "快乐", "欢快", "兴奋", "雀跃", "活泼", "阳光", "笑一个", "笑笑")
_SHY_REQUEST = ("害羞", "腼腆", "脸红", "不好意思", "难为情")
_AFRAID_REQUEST = ("害怕", "恐惧", "害怕的话", "胆小", "好怕", "吓人", "紧张", "慌")
_CLOSE_REQUEST = ("撒娇", "亲密", "亲昵", "说点甜的", "软软地", "撒娇的话", "亲近")
_TSUN_REQUEST = ("傲娇", "嘴硬", "别扭", "赌气")


def request_emotion_hint(user: str):
    """若用户明确要求某类情绪的发言，返回对应情绪名；否则 None。"""
    user = user or ""
    if any(w in user for w in _SAD_REQUEST):
        return "难过"
    if any(w in user for w in _AFRAID_REQUEST):
        return "害怕"
    if any(w in user for w in _SHY_REQUEST):
        return "害羞"
    if any(w in user for w in _CLOSE_REQUEST):
        return "亲密"
    if any(w in user for w in _TSUN_REQUEST):
        return "傲娇"
    if any(w in user for w in _HAPPY_REQUEST):
        return "开心"
    return None


def build_messages(cfg: dict, chat: list, summary: str = "") -> list:
    """由界面历史构造 LLM messages（含人设 + 对话质量指南 + 朗读约束 + 早期记忆摘要）。

    summary：超长会话的早期对话摘要（保留最近 keep 轮完整消息，更早的折叠成摘要注入，
    保证几十轮以后仍记得开头聊过什么——类似 chatbox 的长程记忆）。
    """
    keep = _as_int(cfg["history"].get("keep_turns"), 8)
    persona = (cfg["persona"] or {}).get("system_prompt", "").strip()

    msgs = []
    # 1) 角色人设（决定“我是谁 / 我们的关系 / 说话方式”）放最前，权重最高
    if persona:
        msgs.append({"role": "system", "content": persona})
    # 2) 长程记忆摘要：早于截断窗口的内容浓缩在这里
    if summary:
        msgs.append({"role": "system",
                     "content": "【更早的对话记忆摘要（来自本会话开头，供你保持连续性，"
                                "请自然引用，不要复述这份摘要本身）】\n" + summary[:2000]})
    # 3) 对话质量指南：回答像真人、接住话题、连贯有上下文
    msgs.append({"role": "system", "content": _DIALOG_TMPL})
    # 4) 朗读输出规范：每句标情绪、口语短句
    msgs.append({"role": "system", "content": speech_rule(cfg)})

    hist = (chat or [])[-keep * 3:]     # 最近 3k 轮完整（折叠保留 2k，滞后段仍在窗口内，不丢消息）
    for u, a in hist:
        if u:
            msgs.append({"role": "user", "content": u})
        if a:
            msgs.append({"role": "assistant", "content": a})
    return msgs


def cfg_from_live(cfg, persona, llm_key, base_url, model, temperature, max_tokens,
                  ref_path, prompt_text, prompt_lang, text_lang, text_split, speed, keep,
                  top_k, top_p, gpt_temperature, repetition_penalty,
                  reply_sents, reply_chars, emotion_on=False):
    """把界面实时参数叠加到磁盘配置上。"""
    cfg["persona"]["system_prompt"] = persona or ""
    cfg["llm"].update(api_key=resolve_api_key(llm_key), base_url=(base_url or "").strip(),
                      model=(model or "").strip(), temperature=_as_float(temperature, 1.0),
                      max_tokens=_as_int(max_tokens, 1024),
                      max_reply_sents=_as_int(reply_sents, 6),
                      max_reply_chars=_as_int(reply_chars, 120))
    cfg["voice"].update(prompt_lang=prompt_lang, text_lang=text_lang,
                        text_split_method=text_split, speed_factor=_as_float(speed, 1.0),
                        top_k=_as_int(top_k, 5), top_p=_as_float(top_p, 1.0),
                        gpt_temperature=_as_float(gpt_temperature, 1.0),
                        repetition_penalty=_as_float(repetition_penalty, 1.35),
                        emotion_mode=bool(emotion_on))
    # ★ 界面的「参考音频/提示文本」为空时保留磁盘配置里的值，不要覆盖成空。
    #   覆盖成空后，一旦某句没识别出情绪（emotion=None）就会回落到空 ref，
    #   TTS 直接返回 400 {"message":"ref_audio_path is required"} 且不写任何日志，
    #   表现为「重新加载时语音合成失败」。有情绪时走情绪自己的参考，所以平时不暴露。
    if (ref_path or "").strip():
        cfg["voice"]["ref_audio_path"] = ref_path.strip()
    if (prompt_text or "").strip():
        cfg["voice"]["prompt_text"] = prompt_text
    cfg["history"]["keep_turns"] = _as_int(keep, 8)
    return cfg


def on_send(user, chat, persona, llm_key, base_url, model, temperature, max_tokens,
            ref_path, prompt_text, prompt_lang, text_lang, text_split, speed, keep,
            top_k, top_p, gpt_temperature, repetition_penalty,
            reply_sents, reply_chars, emotion_on=False,
            convid=None):
    """发送一条消息：LLM回复 -> TTS 合成 -> 返回语音自动播放。生成器便于显示进度。

    返回元素末尾追加当前会话 id：convid 为 None 时自动新建本地会话。
    每次完成一轮后会把整段对话写入 conversations/<sid>.json。
    """
    if chat is None:
        chat = []
    if user is None:
        user = ""
    user = user.strip()
    if not user:
        yield chat, None, "⚠️ 请输入内容", "", _replay_update(convid), _latest_replay_text(convid), convid
        return
    convid = convid or _conv_sid()
    if not conv_load(convid):
        conv_save(convid, CONV_DEFAULT_TITLE, [])
    cfg = cfg_from_live(load_config(), persona, llm_key, base_url, model, temperature,
                        max_tokens, ref_path, prompt_text, prompt_lang, text_lang,
                        text_split, speed, keep, top_k, top_p, gpt_temperature,
                        repetition_penalty, reply_sents, reply_chars, emotion_on)

    chat = chat + [(user, "……")]
    yield chat, None, "🧠 AI 思考中…", "", _replay_update(convid), _latest_replay_text(convid), convid

    # 1) LLM 对话
    try:
        # 历史 = 已完成的对话轮次（不含当前待回复的 user）
        hist = chat[:-1] if chat and chat[-1][1] == "……" else chat
        # 长程记忆：先按当前会话历史折叠已被挤出的更早轮次（攒够才真调 LLM），再读取最新摘要
        refresh_session_summary(convid, cfg, hist)
        _mem = (conv_load(convid) or {}).get("summary") or ""   # 长程记忆：已折叠的早期对话
        msgs = build_messages(cfg, hist, _mem)
        msgs.append({"role": "user", "content": user})  # 当前问题必须追加
        answer = llm_chat(msgs, cfg)
    except Exception as e:  # noqa: BLE001
        final = chat[:-1] + [(user, "")]
        persist_chat(convid, final)
        yield final, None, f"❌ AI 回复失败：{e}", "", _replay_update(convid), _latest_replay_text(convid), convid
        return
    if not answer:
        final = chat[:-1] + [(user, "")]
        persist_chat(convid, final)
        yield final, None, "❌ AI 返回了空内容", "", _replay_update(convid), _latest_replay_text(convid), convid
        return

    # 按句切分情绪（新格式每句带【情绪】；旧格式一标到底也兼容），正文存入对话历史
    segs = split_reply_segments(answer)
    pure = join_segment_display(segs) or (answer or "").strip()
    pure = pure[:500]
    chat = chat[:-1] + [(user, pure)]
    yield chat, None, "🎙️ 正在合成语音…", "", _replay_update(convid), _latest_replay_text(convid), convid

    # 2) TTS 合成：情绪模式开=逐句用各自情绪参考合成后拼接；关=整段用默认音色一次合成
    try:
        emo_mode = cfg["voice"].get("emotion_mode", True)
        if not segs:
            raise RuntimeError("AI 回复为空，无法朗读")
        user_hint = request_emotion_hint(user)   # 用户要求“说伤心的话”等先验
        speak_parts = []   # [(emo|None, 朗读文本, 原文含旁白)]
        for s in segs:
            raw = s["body"]
            read = sanitize_for_tts(raw)
            if not read:
                read = raw
            read = strip_stage_notes(read).strip()
            if not read:
                continue
            speak_parts.append((s["emo"], read, raw))

        used_emos = []
        if emo_mode:
            # 情绪连贯自然化：连续同情绪句子合并成段、情绪切换处留呼吸停顿
            wav, used_emos = build_emotional_speech(speak_parts, cfg, user_hint)
            if not wav:
                raise RuntimeError("AI 回复没有可朗读的文字")
        else:
            whole = "".join(t for _, t in speak_parts)
            if not whole:
                raise RuntimeError("AI 回复没有可朗读的文字")
            parts = _synth_segment(whole, None, cfg)
            if not parts:
                raise RuntimeError("AI 回复没有可朗读的文字")
            wav = concat_wavs(parts, gap=0.18)
            used_emos = ["默认"]
    except Exception as e:  # noqa: BLE001
        persist_chat(convid, chat)
        yield chat, None, f"❌ 语音合成失败：{e}", "", _replay_update(convid), _latest_replay_text(convid), convid
        return
    persist_chat(convid, chat)  # 整段对话落盘：退出/重启后仍在，可续聊
    conv_settings_save(convid, snapshot_from_cfg(cfg))  # 本会话用的设置存档，切回自动还原
    tag_flow = "→".join(dict.fromkeys(used_emos))
    record_replay(convid, pure, wav, emo=tag_flow)  # 语音按当前会话归档，随时可回放
    _turn_audio_set(convid, -1, wav)  # 这一轮回答的语音（界面上的 ▶ 按钮用它）
    status = f"✅ 语音已生成（{len(speak_parts)}句 / {tag_flow}）"
    yield chat, wav, status, "", _replay_update(convid), pure, convid


def on_voice_input(audio, auto_send, user, chat, persona, llm_key, base_url, model,
                   temperature, max_tokens, ref_path, prompt_text, prompt_lang, text_lang,
                   text_split, speed, keep, top_k, top_p, gpt_temperature,
                   repetition_penalty, reply_sents, reply_chars, emotion_on,
                   convid=None):
    """🎤 语音转文字：识别麦克风录音。

    - 勾选「识别后自动发送」= 对讲模式(A)：识别完自动走完整对话+语音回答；
    - 不勾选 = 纯转写(C)：只把识别文字填进输入框，由用户回车/点发送。
    返回与 on_send 一致的输出顺序。
    """
    convid = convid or _conv_sid()
    if not audio:
        return (chat, None, "⚠️ 没有录到声音，请重试",
                user or "", _replay_update(convid), _latest_replay_text(convid), convid)
    # 先给出"识别中"提示（首次会加载模型，可能较久）
    try:
        text = asr_recognize(audio)
    except Exception as e:  # noqa: BLE001
        return (chat, None, f"❌ 语音识别失败：{e}",
                user or "", _replay_update(convid), _latest_replay_text(convid), convid)
    if not text.strip():
        return (chat, None, "⚠️ 没听清你说什么，请再试一次",
                "", _replay_update(convid), _latest_replay_text(convid), convid)
    text = text.strip()
    if not auto_send:
        # 纯转写模式：文字填入输入框，由用户确认后发送
        return (chat, None, f"🎤 已识别：{text}", text,
                _replay_update(convid), _latest_replay_text(convid), convid)
    # 对讲模式：以识别文本为消息，跑一遍完整对话+朗读
    try:
        last = None
        for last in on_send(text, chat, persona, llm_key, base_url, model, temperature,
                            max_tokens, ref_path, prompt_text, prompt_lang, text_lang,
                            text_split, speed, keep, top_k, top_p, gpt_temperature,
                            repetition_penalty, reply_sents, reply_chars, emotion_on,
                            convid):
            pass
        return last
    except Exception as e:  # noqa: BLE001
        return (chat, None, f"❌ 对话处理失败：{e}", text,
                _replay_update(convid), _latest_replay_text(convid), convid)


# ---------------------------------------------------------------- 单条消息工具条（重新加载 / 改写 / 删除）
def _msg_op_out(chat, audio, status, sid):
    """消息操作的界面输出：顺序须与 msgop_btn 的 outputs 完全一致。"""
    c = conv_load(sid) if sid else None
    title = (c.get("title") if c else "") or CONV_DEFAULT_TITLE
    dd, tt, info = _replay_trio(sid)
    return (chat, audio, status, _convs_ui(), title, dd, tt, info)


def _audio_backfill(sid: str) -> None:
    """老会话没有「每轮回答的语音」记录：拿回放记录里的文字跟回答对上号，补一份。

    只影响工具栏上的 ▶ 按钮；对不上就留空（那条不显示 ▶）。
    """
    if not sid:
        return
    c = conv_load(sid) or {}
    M = [list(p) for p in (c.get("messages") or [])]
    if not M:
        return
    A = [str(x or "") for x in (c.get("audio") or [])]
    A = (A + [""] * len(M))[:len(M)]
    if all(A):
        return                                  # 已经齐了
    by_text = {}
    for it in _replay_list(sid):
        t = str(it.get("text") or "").strip()
        w = str(it.get("wav") or "")
        if t and w:
            by_text[t] = w                      # 同文字以最后那条为准（更近的一次）
    changed = False
    for i, p in enumerate(M):
        if A[i]:
            continue
        r = str((p[1] if len(p) > 1 else "") or "").strip()
        w = by_text.get(r) if r else None
        if w:
            A[i] = w
            changed = True
    if changed:
        _conv_state_save(sid, M, A, c.get("versions") or {})


def _branch_info(sid: str) -> str:
    """给前端的一段隐藏 JSON：每轮回答有没有语音可播、有没有多个版本、当前第几版。

    前端（ui.js）读它来决定工具栏上显示 ▶ 和 < 2/2 >。之所以要用组件传：
    网页上的按钮是 JS 注进去的，它没法直接问服务端「这轮有几个版本」。
    """
    turns = {}
    if sid:
        _audio_backfill(sid)
        _c, M, A, V = _conv_state(sid)
        for i in range(len(M)):
            if not (M[i][1] or "").strip():
                continue                       # 没有回答（刚删掉）：不给按钮
            item = {}
            audio_url = ("/vc_audio/%s/%d" % (sid, i)) if (i < len(A) and A[i]) else None
            e = V.get(str(i)) or {}
            # 容错：versions 里的脏数据（alts 不是 list、元素不是 dict、cur 非数字）
            # 在旧文件或手工编辑后可能出现，不能让整个 _branch_info 崩掉导致工具栏全丢。
            alts = e.get("alts")
            alts = [a if isinstance(a, dict) else {} for a in alts] if isinstance(alts, list) else []
            cur = _as_int(e.get("cur"), 0)
            # 只认「确实属于这一轮」的版本：重新加载会截断对话，之后接着聊时，
            # 新的一轮可能正好落在旧分支某个版本的下标上 —— 用回答文字对一下号，对不上就不显示，
            # 这些旧版本原样留在文件里（切回旧分支时还要用），只是不挂在新对话上。
            if len(alts) > 1 and 0 <= cur < len(alts) \
                    and str((alts[cur] or {}).get("reply") or "").strip() == (M[i][1] or "").strip():
                item["n"] = len(alts)
                item["cur"] = cur + 1
                # ★ 带版本号：切版本时同一个 /vc_audio 路由的响应会变，
                # 前端按 URL 缓存波形，带上版本号才不会拿旧版本的波形。
                if audio_url:
                    audio_url += "?v=%d" % cur
            if audio_url:
                item["a"] = audio_url
            if item:
                turns[str(i)] = item
    data = json.dumps({"turns": turns}, ensure_ascii=False, separators=(",", ":"))
    return _html.escape(data)


def on_message_action(spec_json, chat, persona, llm_key, base_url, model, temperature,
                      max_tokens, ref_path, prompt_text, prompt_lang, text_lang, text_split,
                      speed, keep, top_k, top_p, gpt_temperature, repetition_penalty,
                      reply_sents, reply_chars, emotion_on=False, convid=None):
    """对话区每条消息下方工具条：重新加载 / 改写 / 删除 / 切换回答版本。

    复制(copy) 和 播放语音(play) 完全在前端完成，不走这里。
    spec = {"op": "regen|edit|del|ver", "turn": 第几条提问(0 起), "role": "user|bot",
            "text": 改写后的文字, "d": 版本切换方向(-1/+1)}
    """
    chat = [[(p[0] if p and p[0] is not None else ""),
             (p[1] if p and len(p) > 1 and p[1] is not None else "")] for p in (chat or [])]
    try:
        spec = json.loads(spec_json or "{}")
    except Exception:  # noqa: BLE001
        spec = {}
    op = str(spec.get("op") or "")
    try:
        turn = int(spec.get("turn", -1))
    except Exception:  # noqa: BLE001
        turn = -1
    try:
        delta = int(spec.get("d") or 0)
    except Exception:  # noqa: BLE001
        delta = 0
    is_user = str(spec.get("role") or "") == "user"
    convid = convid or _conv_sid()
    if not conv_load(convid):
        conv_save(convid, CONV_DEFAULT_TITLE, [])

    _c, M, A, V = _conv_state(convid)
    if not M:
        M, A = [list(p) for p in chat], [""] * len(chat)
    if op not in ("regen", "edit", "del", "ver", "play") or turn < 0 or turn >= len(M):
        yield _msg_op_out(M or chat, None, "⚠️ 这条消息已经不在界面上了，请刷新后重试", convid)
        return
    A = (A + [""] * len(M))[:len(M)]

    # ── 播放：把这一轮回答的语音交给下方那个「AI 语音回复」大播放器 ──
    # 它 autoplay=True，写进去就会自动播放并把波形显示在原位置（替代以前的小行内波形）。
    # 只更新 audio 组件，别的输出一律 gr.update() 保持不变。
    if op == "play":
        p = to_abs(A[turn]) if A[turn] else ""
        if not p or not os.path.isfile(p):
            yield _msg_op_out(M, None, "⚠️ 这条语音文件已不在（可能被清理过）", convid)
            return
        # 状态栏回显这条语音实际用的情绪（从回放记录里按 wav 路径找；旧记录没有就留空）
        emo = ""
        for it in _replay_list(convid):
            if str((it or {}).get("wav") or "") == str(A[turn] or ""):
                emo = str((it or {}).get("emo") or "").strip()
        if not emo:
            # 重新加载过的轮次：情绪存在版本信息 alt["emo"] 里
            e = V.get(str(turn)) or {}
            alts = e.get("alts")
            alts = [a if isinstance(a, dict) else {} for a in alts] if isinstance(alts, list) else []
            cur = _as_int(e.get("cur"), 0)
            if 0 <= cur < len(alts):
                emo = str(alts[cur].get("emo") or "").strip()
        if not emo:
            # 还是没有（很旧的数据）时，从回放条目的 label "[情绪] 正文" 里兜底取一次
            for it in _replay_list(convid):
                if str((it or {}).get("wav") or "") != str(A[turn] or ""):
                    continue
                m = re.search(r"\[([^\]]{1,24})\]\s", str((it or {}).get("label") or ""))
                if m:
                    emo = m.group(1).strip()
        status = ("🎭 当前语音情绪：%s" % emo) if emo else "🔊 已在下方播放这条语音"
        yield _msg_op_out(M, p, status, convid)
        return

    # ── 切换回答版本（工具栏上的 < 2/2 >）──
    if op == "ver":
        e = V.get(str(turn)) or {}
        alts = e.get("alts")
        alts = [a if isinstance(a, dict) else {} for a in alts] if isinstance(alts, list) else []
        if len(alts) < 2:
            yield _msg_op_out(M, None, "⚠️ 这一轮只有一个回答，没有别的版本", convid)
            return
        cur = (_as_int(e.get("cur"), 0) + (1 if delta >= 0 else -1)) % len(alts)
        alt = alts[cur] if isinstance(alts[cur], dict) else {}
        M2 = M[:turn] + [[M[turn][0], alt.get("reply") or ""]] + \
             [list(p) for p in (alt.get("tail") or [])]
        A2 = A[:turn] + [alt.get("wav") or ""] + list(alt.get("tail_audio") or [])
        # 别动其它轮次的版本信息（切回来时它们还得在）
        V2 = dict(V)
        V2[str(turn)] = {"cur": cur, "alts": alts}
        _conv_state_save(convid, M2, A2, V2)
        # 带上这一版自己的情绪说明（重新加载时存进 alt["emo"] 的），旧版没有就只报版本号
        _emo = str(alt.get("emo") or "").strip()
        yield _msg_op_out(M2, None,
                          "↔️ 已切换到第 %d/%d 个回答%s"
                          % (cur + 1, len(alts), ("（%s）" % _emo) if _emo else ""), convid)
        return

    # ── 改写：把这一条的文字换成新内容 ──
    if op == "edit":
        text = str(spec.get("text") or "").strip()
        if not text:
            yield _msg_op_out(M, None, "⚠️ 改写内容不能为空", convid)
            return
        M[turn][0 if is_user else 1] = text
        if not is_user:
            # 文字改了，原来那条语音就对不上了：一并清掉（文件 + 回放记录）
            _forget_audio(convid, A[turn])
            A[turn] = ""
        _conv_state_save(convid, M, A, V)
        yield _msg_op_out(M, None, f"✏️ 已改写这{'条提问' if is_user else '条回答'}", convid)
        return

    # ── 删除：点提问=连同回答一起删；点回答=只删这一句回答。语音一并删除 ──
    if op == "del":
        wav = A[turn]
        if is_user:
            M.pop(turn)
            A.pop(turn)
            status = "🗑️ 已删除这条提问（连同它的回答和语音）"
        else:
            M[turn][1] = ""
            A[turn] = ""
            status = "🗑️ 已删除这条回答（连同它的语音）"
        _forget_audio(convid, wav)
        # 结构变了：被删那一轮的版本信息作废，它后面那些轮次的版本号往前挪一位
        _conv_state_save(convid, M, A, _versions_shift(V, turn))
        yield _msg_op_out(M, None, status, convid)
        return

    # ── 重新加载：生成新的回答；原回答留着，做成 <2/2> 可以切回去 ──
    user = (M[turn][0] or "").strip()
    if not user:
        yield _msg_op_out(M, None, "⚠️ 这条提问没有内容，无法重新生成", convid)
        return
    e = V.get(str(turn)) or {}
    if not (M[turn][1] or "").strip():
        # ★ 这条回答已被删除（内容为空）：重新加载就是「重写这一轮」，
        #   新回答直接当第 1 版，不把那条空回答留成一个可切换的旧版本 ——
        #   否则界面会显示成「2/2」，看着像多出来一句话。
        e = {"cur": 0, "alts": []}
    elif not (e.get("alts")):
        # 第一次重新加载：把「现在这个回答 + 它后面的整段」存成第 1 版
        e = {"cur": 0, "alts": [{"reply": M[turn][1], "wav": A[turn],
                                 "tail": [list(p) for p in M[turn + 1:]],
                                 "tail_audio": list(A[turn + 1:])}]}
    else:
        # 之前切过版本：先把当前这一版刷新一遍，免得丢掉重新加载后又聊的内容
        e = _versions_sync(M, A, {str(turn): e}).get(str(turn)) or e
    alts = e["alts"]
    cur_new = len(alts)                          # 新版本的下标

    # 生成过程就按「这一轮之后不显示原内容」的样子推给界面
    last = None
    for part in on_send(user, M[:turn], persona, llm_key, base_url, model, temperature,
                        max_tokens, ref_path, prompt_text, prompt_lang, text_lang,
                        text_split, speed, keep, top_k, top_p, gpt_temperature,
                        repetition_penalty, reply_sents, reply_chars, emotion_on, convid):
        last = part
        yield _msg_op_out(list(part[0]), part[1], part[2], convid)
    if last is None:
        return
    st = last[2] or ""
    if not st.startswith("✅"):
        # 生成失败：原样恢复（不新增版本，也不动原来的回答）
        _conv_state_save(convid, M, A, V)
        yield _msg_op_out(M, None, st or "❌ 重新加载失败", convid)
        return
    new_reply = ""
    if last[0]:
        t = last[0][-1]
        new_reply = (t[1] if len(t) > 1 else "") or ""
    new_wav = last[1] or ""
    # on_send 的收尾状态是「✅ 语音已生成（N句 / 情绪流）」——把那截情绪说明摘出来，
    # 存进这一版并回显。否则重新加载/切版本后只剩「已重新加载」，看不到用的什么情绪。
    emo_raw = ""
    _m = re.search(r"（\s*\d+句\s*/\s*[^）]+）", st)
    if _m:
        emo_raw = _m.group(0)[1:-1]              # 去掉外层括号，例：3句 / 开心→亲密
    alts = alts + [{"reply": new_reply, "wav": new_wav, "tail": [], "tail_audio": [],
                    "emo": emo_raw}]
    M2 = M[:turn] + [[user, new_reply]]
    A2 = A[:turn] + [new_wav]
    # ★ 其它轮次的版本信息一并带上（被折进旧版本尾巴里的那些尤其不能丢，
    #   否则「先重新加载第 2 轮、再重新加载第 1 轮、切回第 1 轮」时第 2 轮的可切换选项会消失）
    V2 = dict(V)
    V2[str(turn)] = {"cur": cur_new, "alts": alts}
    _conv_state_save(convid, M2, A2, V2)
    if len(alts) > 1:
        _msg = ("🔄 已重新加载（第 %d/%d 版%s，点 < 或 > 可切回原回答）"
                % (cur_new + 1, len(alts), (" · " + emo_raw) if emo_raw else ""))
    else:
        _msg = "🔄 已重新加载" + (("（%s）" % emo_raw) if emo_raw else "")
    yield _msg_op_out(M2, new_wav, _msg, convid)


# ---------------------------------------------------------------- 多会话界面操作
def _convs_ui():
    """左侧扁平历史列表的刷新值（喂给 convs_state，触发列表重绘）。"""
    return convs_state_value()


def _new_sid():
    sid = _conv_sid()
    conv_save(sid, CONV_DEFAULT_TITLE, [])
    return sid


def on_new_conversation():
    """➕ 新对话：另起一个空白本地会话，设置区重置为全局默认初始。"""
    sid = _new_sid()
    dd, tt, info = _replay_trio(sid)
    base = (sid, [], None, _convs_ui(), "",
            "🧹 已开启新对话（设置已重置为默认初始），可以开始聊天", dd, tt, info)
    return base + settings_updates_for_sid(sid)   # 新会话 → 全局默认设置


def on_select_conversation(sid):
    """从历史列表选择会话 -> 载入其对话、语音回放，并把设置切为该会话保存的版本。"""
    if not sid:
        dd, tt, info = _replay_trio(None)
        base = (None, [], None, _convs_ui(), "", "请先「➕ 新对话」或在上方选择一条历史会话", dd, tt, info)
        return base + settings_updates_for_sid(None)
    c = conv_load(sid)
    if not c:
        dd, tt, info = _replay_trio(None)
        base = (None, [], None, _convs_ui(), "", "⚠️ 未找到该会话（可能已被删除）", dd, tt, info)
        return base + settings_updates_for_sid(None)
    title = c.get("title") or CONV_DEFAULT_TITLE
    msgs = c.get("messages") or []
    n = sum(1 for m in msgs if m and m[0])
    dd, tt, info = _replay_trio(sid)
    base = (sid, msgs, None, _convs_ui(), title,
            f"✅ 已载入会话「{title}」（{n} 轮提问），设置已切换为该会话的调试参数", dd, tt, info)
    return base + settings_updates_for_sid(sid)


def _reload_for(sid, status=None):
    """载入指定会话的整页状态；status 非空则覆盖状态栏文案。"""
    tup = list(on_select_conversation(sid))
    if status is not None:
        tup[5] = status
    return tup


def on_delete_conv_row(sid, cur):
    """🗑️ 删除某条会话（来自列表每行的「⋮ → 删除」）。"""
    title = (conv_load(sid) or {}).get("title") or CONV_DEFAULT_TITLE
    conv_delete(sid)
    rest = conv_list()
    if cur and cur != sid and any(x["id"] == cur for x in rest):
        target = cur                       # 删的不是当前会话 → 当前会话保持不变
    elif rest:
        target = rest[0]["id"]             # 删的是当前会话 → 切到最近一条
    else:
        target = _new_sid()                # 没有历史了 → 新建空白会话
    return tuple(_reload_for(target, f"🗑️ 已删除会话「{title}」")) + ({},)


def on_pin_conv_row(sid, cur):
    """📌 置顶 / 取消置顶某条会话。"""
    c = conv_load(sid) or {}
    on = not bool(c.get("pinned", False))
    conv_set_pinned(sid, on)
    title = c.get("title") or CONV_DEFAULT_TITLE
    return tuple(_reload_for(cur, f"📌 已{'置顶' if on else '取消置顶'}「{title}」")) + ({},)


def on_rename_conv_row(sid, title, cur):
    """✏️ 重命名某条会话（来自每行「⋮ → 重命名」）。"""
    title = (title or "").strip() or CONV_DEFAULT_TITLE
    _conv_patch(sid, title=title)
    return tuple(_reload_for(cur if cur else sid, f"✅ 已改名为「{title}」")) + ({},)


def on_conv_menu(sid, ui):
    """点某行「⋮」：开/关该行操作菜单（同一时间只开一个）。"""
    ui = dict(ui or {})
    ui["menu"] = None if ui.get("menu") == sid else sid
    ui["renaming"] = None
    return ui


def on_conv_rename_open(sid, ui):
    """切到某行的「重命名」输入态。"""
    ui = dict(ui or {})
    ui["menu"] = None
    ui["renaming"] = sid
    return ui


def on_conv_rename_cancel(ui):
    """取消重命名，回到普通列表。"""
    ui = dict(ui or {})
    ui["renaming"] = None
    return ui


def on_rename_conversation(sid, title):
    """✏️ 主区「更多」里的改名：改当前会话标题。"""
    if not sid:
        return _convs_ui(), "", "⚠️ 请先「➕ 新对话」或选择一条历史会话"
    title = (title or "").strip() or CONV_DEFAULT_TITLE
    _conv_patch(sid, title=title)
    return _convs_ui(), title, f"✅ 已改名为「{title}」"


def _conv_refresh_ui(sid):
    """每轮对话结束后刷新会话列表/标题/回放条数。"""
    c = conv_load(sid) if sid else None
    title = (c.get("title") if c else "") or CONV_DEFAULT_TITLE
    _, _, info = _replay_trio(sid)
    return _convs_ui(), title, info


def _force_phone_toggle_off():
    """每次打开/刷新页面，都把“允许手机访问”开关强制显示为未勾选。

    注意：这只是“显示”层面的归零，不改磁盘里的实际设置；实际是否监听
    全部网卡仍由启动时读取的 chat_config.json 决定。若用户勾选后点保存，
    才会按勾选状态写入（与原来一致）。
    """
    return gr.update(value=False)


def on_set_theme(choice):
    """主题选择写入配置（浅色/深色/跟随系统），下次打开页面仍沿用。"""
    mode = _THEME_TO_CODE.get(choice, "system")
    cfg = load_config()
    cfg.setdefault("ui", {})["theme"] = mode
    save_config(cfg)
    return choice


def _lan_ips() -> list:
    """探测本机可对外访问的 IPv4 地址（默认路由/手机热点地址放最前，去重）。"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            pri = s.getsockname()[0]
            if not pri.startswith(("127.", "169.254.")):
                ips.append(pri)
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("127.", "169.254.")) or ip in ips:
                continue
            ips.append(ip)
    except Exception:  # noqa: BLE001
        pass
    return ips


def _phone_url_md() -> str:
    """查询当前手机/同网设备可用的访问网址（只给最优先的一个）。"""
    ui = (load_config().get("ui") or {})
    on = bool(ui.get("phone_access", False))
    port = _as_int(ui.get("port"), 27865)
    if not on:
        return ("🔒 手机访问未开启——需在设置里勾选「允许手机/局域网访问」并保存、重启网页服务。"
                f"（本机可正常打开：http://127.0.0.1:{port}）")
    ips = _lan_ips()
    if not ips:
        return "📱 暂未查到可用地址——请确认电脑已连接网络（如手机热点/WiFi）后再点一次。"
    tok = str(ui.get("access_token", "") or "")
    s = f"> 📱 手机/同网设备请打开：**http://{ips[0]}:{port}**"
    if tok:
        s += f"\n> 🔐 访问密码：**{tok}**"
    return s


def on_page_load():
    """每次打开/刷新页面自动回到最近会话（文字/语音/该会话设置一并载入）。"""
    items = conv_list()
    if not items:
        dd, tt, info = _replay_trio(None)
        base = (None, _chatbox_value([]), None, _convs_ui(), CONV_DEFAULT_TITLE,
                "就绪。直接输入即可开始（会自动存为新对话）。", dd, tt, info)
        return base + settings_updates_for_sid(None)
    t = items[0]
    c = conv_load(t["id"]) or {}
    title = c.get("title") or CONV_DEFAULT_TITLE
    msgs = c.get("messages") or []
    n = sum(1 for m in msgs if m[0])
    dd, tt, info = _replay_trio(t["id"])
    base = (t["id"], _chatbox_value(msgs), None, _convs_ui(), title,
            f"✅ 已载入最近会话「{title}」（{n} 轮提问），设置已恢复为该会话的调试参数", dd, tt, info)
    return base + settings_updates_for_sid(t["id"])


def on_pick_persona_tpl(choice):
    """🎭 角色模板：点选后把对应人设填入文本框（选中“空模板”=清空人设）。"""
    text = ""
    if choice:
        for label, body in PERSONA_TEMPLATES:
            if label == choice:
                text = body
                break
    return text


def on_save_persona(persona, llm_key, base_url, model, temperature, max_tokens,
                    ref_path, prompt_text, prompt_lang, text_lang, text_split,
                    speed, keep, port, top_k, top_p, gpt_temperature,
                    repetition_penalty, seed, reply_sents, reply_chars, emotion_on=False,
                    phone_access=False, convid=None):
    cfg = load_config()
    cfg["persona"]["system_prompt"] = persona or ""
    cfg["llm"].update(api_key=resolve_api_key(llm_key), base_url=(base_url or "").strip(),
                      model=(model or "").strip(), temperature=_as_float(temperature, 1.0),
                      max_tokens=_as_int(max_tokens, 1024),
                      max_reply_sents=_as_int(reply_sents, 6), max_reply_chars=_as_int(reply_chars, 120))
    cfg["voice"].update(ref_audio_path=(ref_path or "").strip(), prompt_text=(prompt_text or ""),
                        prompt_lang=prompt_lang, text_lang=text_lang,
                        text_split_method=text_split, speed_factor=_as_float(speed, 1.0),
                        top_k=_as_int(top_k, 5), top_p=_as_float(top_p, 1.0),
                        gpt_temperature=_as_float(gpt_temperature, 1.0),
                        repetition_penalty=_as_float(repetition_penalty, 1.35),
                        seed=_as_int(seed, -1),
                        emotion_mode=bool(emotion_on))
    cfg["history"]["keep_turns"] = _as_int(keep, 8)
    # 手机/局域网访问 = 系统级开关：写入 ui，重启网页服务后决定监听 0.0.0.0 还是仅本机。
    old_access = bool((cfg.get("ui") or {}).get("phone_access", False))
    phone_access = bool(phone_access)
    cfg.setdefault("ui", {})["phone_access"] = phone_access
    cfg["ui"]["port"] = _as_int(port, int((cfg.get("ui") or {}).get("port", 27865)))
    try:
        # 端口/种子/手机访问等全局项单独持久化，不影响新对话的默认设置
        base = load_config()
        base["ui"]["port"] = _as_int(port, int((base.get("ui") or {}).get("port", 27865)))
        base["ui"]["phone_access"] = phone_access
        base["voice"]["seed"] = _as_int(seed, -1)
        save_config(base)
        msg = ("已开启手机/局域网访问：重启网页服务后，手机/同网设备输入访问密码即可进入。"
               if phone_access
               else "已关闭手机/局域网访问；重启网页服务后仅本机可打开。")
        if convid:
            # 有当前会话：只把本次设置存入该会话，切回本会话自动还原，不污染全局默认
            conv_settings_save(convid, snapshot_from_cfg(cfg))
            tip = "；⚠️ 手机访问开关需重启网页服务才生效" if phone_access != old_access else ""
            return (f"✅ 设置已保存到当前会话（切换回来仍是这套；「➕新对话」会用默认初始）{tip}"
                    + (("；" + msg) if msg else ""))
        # 没有打开会话：作为全局默认保存，供之后的新对话使用
        save_config(cfg)
        tip = "；⚠️ 手机访问开关需重启网页服务才生效" if phone_access != old_access else ""
        return (f"✅ 已保存为全局默认设置（之后的新对话以此为准）{tip}"
                + (("；" + msg) if msg else ""))
    except Exception as e:  # noqa: BLE001
        return f"❌ 保存失败：{e}"


def on_safe_exit(confirm: bool):
    """设置页「🛑 安全退出」：先给页面回一句提示，再延迟关闭后台进程。

    延迟 1.5 秒是为了让提示先返回浏览器；随后后台（含本次启动的 TTS 服务）整体退出。
    """
    if not confirm:
        return "请先勾选上方「我确认要关闭后台服务」，再点安全退出。"
    threading.Timer(1.5, shutdown_backend).start()
    return ("🛑 正在安全退出…网页后台与语音模型将在大约 1~2 秒内停止，本页面会失联，可直接关闭。"
            "需要时重新运行启动脚本即可再次打开。")


def on_pick_ref(choice):
    if not choice:
        return choice or "", ""
    return choice, find_ref_text(choice)


def on_test_voice(ref_path, prompt_text, prompt_lang, text_lang, text_split, speed,
                  top_k, top_p, gpt_temperature, repetition_penalty, seed=-1):
    """用固定文本试听当前音色，不经过 LLM。"""
    cfg = load_config()
    cfg["voice"].update(ref_audio_path=(ref_path or "").strip(), prompt_text=(prompt_text or ""),
                        prompt_lang=prompt_lang, text_lang=text_lang,
                        text_split_method=text_split, speed_factor=_as_float(speed, 1.0),
                        top_k=_as_int(top_k, 5), top_p=_as_float(top_p, 1.0),
                        gpt_temperature=_as_float(gpt_temperature, 1.0),
                        repetition_penalty=_as_float(repetition_penalty, 1.35),
                        seed=_as_int(seed, -1))
    try:
        wav = synthesize(
            "嗨，我是用你的声音说话的语音伙伴！今天心情超好，你猜猜为什么？"
            "哈哈，因为又见到你啦，想跟你多聊几句呢！", cfg)
        return wav, "✅ 试听语音已生成"
    except Exception as e:  # noqa: BLE001
        return None, f"❌ 试听失败：{e}"


def _weight_file(path, label):
    """把权重路径（相对项目根 / 绝对路径都行）校验成存在的绝对路径。"""
    raw = str(path or "").strip().strip('"')
    if not raw:
        raise RuntimeError("还没选「%s」：下拉里选一个，或把 .ckpt/.pth 的绝对路径粘进去" % label)
    p = Path(to_abs(raw))
    if p.is_dir():
        raise RuntimeError("「%s」给的是文件夹，要选到文件：%s" % (label, p))
    if not p.is_file():
        raise RuntimeError("「%s」找不到文件：%s（绝对路径、相对项目根目录的路径都可以）" % (label, p))
    return str(p)


def on_switch_weights(gpt_path, sovits_path):
    """应用 GPT / SoVITS 权重（热切换，需等待重载完成）。"""
    try:
        g = _weight_file(gpt_path, "GPT 模型（语音生成器）")
        s = _weight_file(sovits_path, "SoVITS 模型（声码器）")
    except Exception as e:  # noqa: BLE001
        return "❌ " + str(e)
    try:
        msg = switch_weights(load_config(), g, s)
        return "✅ " + msg if ":" in msg else msg
    except Exception as e:  # noqa: BLE001
        return f"❌ 切换失败：{e}"


# ---------------------------------------------------------------- 界面组装
# 全屏简洁布局：左侧竖向会话栏（DeepSeek 风格）+ 右侧主区，聊天区占满剩余高度。
# 主题色改用蓝/石板灰（默认 Soft 的 indigo 偏紫，观感不搭），并配合浅色/深色/跟随系统。
_THEME = gr.themes.Soft(primary_hue="blue", secondary_hue="slate", neutral_hue="slate").set(
    # Soft 主题默认会给每个标题/标签套一个浅蓝底块（深色下是深蓝底块），看起来像按钮，太吵。
    # 这里统一去掉底块、圆角、内边距，只保留蓝色文字（深色模式保留白字）。
    block_label_background_fill="transparent",
    block_label_background_fill_dark="transparent",
    block_label_border_width="0px",
    block_label_shadow="none",
    block_label_radius="0px",
    block_label_padding="0px",
    block_label_margin="0px 0px 6px 0px",
    # 表单/折叠区标题同理
    block_title_background_fill="transparent",
    block_title_background_fill_dark="transparent",
    block_title_border_width="0px",
    block_title_radius="0px",
    block_title_padding="0px",
)
_THEME_LABELS = ["浅色", "深色", "跟随系统"]
_THEME_TO_CODE = {"浅色": "light", "深色": "dark", "跟随系统": "system"}
_THEME_FROM_CODE = {v: k for k, v in _THEME_TO_CODE.items()}

# 界面样式（ui.css）与纯前端脚本（ui.js）放在磁盘文件里，由 FastAPI 路由「每次请求现读磁盘」。
# 好处：改样式 / 改前端逻辑只要刷新浏览器（Ctrl+F5）就生效，不用重启服务 ——
# 而写在 Python 字符串里的 CSS/JS 是启动时读进内存的，改完必须重启。
# head 里的 <link>/<script> 只是引用，内容跟着文件走，不受启动时机影响。
_UI_CSS_FILE = ROOT / "ui.css"
_UI_JS_FILE = ROOT / "ui.js"
_UI_HEAD = ('<link rel="stylesheet" href="/vc_ui.css">'
            '<script src="/vc_ui.js"></script>')


# 侧栏收起/展开、每个消息下的小工具条等纯前端逻辑都在 ui.js 里（内联 !important 那套做法
# 的由来也挪到那里说明了）。这里只留必须挂在 Gradio 事件上的几段 js。


# ---------------------------------------------------------------- 头像（每条消息旁边显示）
# 头像文件路径存在配置的 ui.avatar_user / ui.avatar_bot 里，图片本身放在 assets/avatars/。
# 前端 <img src="/vc_avatar/user"> 由路由「现读现发」，所以换图刷新即生效，不用重启、不用清缓存。
_AVATAR_FILES = {"user": "avatar_user", "bot": "avatar_bot"}
_AVATAR_DIR = ROOT / "assets" / "avatars"
_AVATAR_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def _avatar_path(which: str) -> str:
    """取配置里某侧头图的绝对路径；没配或文件不在则返回空串。"""
    key = _AVATAR_FILES.get(which)
    if not key:
        return ""
    rel = str(((load_config().get("ui") or {}).get(key)) or "").strip()
    if not rel:
        return ""
    p = to_abs(rel)
    return p if os.path.isfile(p) else ""


def _avatar_filedata() -> list:
    """给 Chatbot 的 avatar_images 用：[我的, TA 的]；没配的那侧给 None（不显示头像占位）。"""
    out = []
    for which in ("user", "bot"):
        out.append({"path": _avatar_path(which), "url": f"/vc_avatar/{which}"
                    } if _avatar_path(which) else None)
    return out


def _chatbox_value(msgs):
    """聊天框的更新值：把头像一起带上。这样改了头像只要刷新页面就生效（不用重启服务）。"""
    return gr.update(value=msgs, avatar_images=_avatar_filedata())


def _save_avatar(which: str, src) -> str:
    """把上传的图片存到 assets/avatars/，返回相对项目根的路径；没上传则返回空串。"""
    p = str(src or "").strip()
    if not p:
        return ""
    src_path = Path(to_abs(p))
    if not src_path.is_file():
        return ""
    if src_path.parent.resolve() == _AVATAR_DIR.resolve():
        return str(src_path.relative_to(ROOT))          # 已经是存好的那张，不用再拷
    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    ext = src_path.suffix.lower() if src_path.suffix.lower() in _AVATAR_EXTS else ".png"
    dst = _AVATAR_DIR / f"{which}{ext}"
    for old in _AVATAR_DIR.glob(f"{which}.*"):          # 换扩展名时把旧的删掉，避免歧义
        if old.resolve() != dst.resolve():
            try:
                old.unlink()
            except Exception:  # noqa: BLE001
                pass
    shutil.copyfile(src_path, dst)
    return str(dst.relative_to(ROOT))


def _gradio_audio(path):
    """把音频路径变成 Gradio 能直接播放的值。

    项目内的文件原样给；项目外的（比如你放在 D 盘的参考音频）复制一份到 output/ 下再给，
    否则 Gradio 会以「文件不在允许目录」为由拒绝播放。
    """
    raw = str(path or "").strip().strip('"')
    if not raw:
        return None
    p = Path(to_abs(raw))
    if not p.is_file():
        return None
    try:
        pr, root = p.resolve(), ROOT.resolve()
        if pr == root or root in pr.parents:
            return str(pr)
    except Exception:  # noqa: BLE001
        return str(p)
    dest = OUTPUT_DIR / ("ref_" + p.name)
    try:
        if not dest.exists() or dest.stat().st_size != p.stat().st_size:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
        return str(dest)
    except Exception:  # noqa: BLE001
        return None


def on_emotion_load(label: str):
    """载入某个情绪的当前设置到「情绪微调」表单（含参考音频试听）。"""
    c = load_config()
    e = (c.get("emotions") or {}).get(label) or {}
    return (
        gr.update(value=e.get("ref_audio_path", "") or ""),
        gr.update(value=e.get("prompt_text", "") or ""),
        gr.update(value=float(e.get("speed_factor", 1.0) or 1.0)),
        gr.update(value=float(e.get("gpt_temperature", 1.4) or 1.4)),
        gr.update(value=int(e.get("top_k", 12) or 12)),
        gr.update(value=float(e.get("top_p", 0.98) or 0.98)),
        gr.update(value=float(e.get("repetition_penalty", 1.25) or 1.25)),
        gr.update(value=_gradio_audio(e.get("ref_audio_path", ""))),
    )


def _emo_form_to_cfg(cfg, label, ref, prompt_txt, spd, temp, topk, topp, rep):
    """把表单里的情绪参数塞进内存 cfg（不落盘），供试听用。"""
    emos = cfg.setdefault("emotions", {})
    cur = dict(emos.get(label) or {})
    cur.update({
        "ref_audio_path": (ref or "").strip(),
        "prompt_text": (prompt_txt or "").strip(),
        "speed_factor": round(float(spd), 2),
        "gpt_temperature": round(float(temp), 2),
        "top_k": int(topk),
        "top_p": round(float(topp), 2),
        "repetition_penalty": round(float(rep), 2),
    })
    emos[label] = cur
    cfg["emotions"] = emos
    return cfg


def on_emo_ref_change(ref_path):
    """参考音频路径一变，播放器就跟着换成那段音频。"""
    return gr.update(value=_gradio_audio(ref_path))


def on_emotion_try(label, ref, prompt_txt, spd, temp, topk, topp, rep, text):
    """🔊 用当前表单参数合成一句，听听这个情绪实际是什么效果（不保存、不写配置）。"""
    if not str(ref or "").strip():
        return None, "❌ 该情绪还没有参考音频：先填路径（或点「📥 用上面『参考音频』填入」）"
    try:
        cfg = _emo_form_to_cfg(load_config(), label, ref, prompt_txt, spd, temp, topk, topp, rep)
        # 试听固定用表单里的语速，方便直接比；实战里「非激烈情绪」仍走默认倍速。
        cfg["voice"]["speed_factor"] = round(float(spd), 2)
        line = (text or "").strip() or "今天天气真好，我们一起出去走走吧！"
        wav = synthesize(line, cfg, emotion=label)
        return wav, "✅ 已用「%s」的参数合成（语速 %s / temperature %s / top_k %s）。" \
                    "满意就点「💾 保存该情绪」。" % (label, spd, temp, topk)
    except Exception as e:  # noqa: BLE001
        return None, f"❌ 合成失败：{e}"



def on_emotion_save(label, ref, prompt_txt, spd, temp, topk, topp, rep):
    """保存某个情绪的设置到 chat_config.json 的 emotions 下。"""
    c = load_config()
    _emo_form_to_cfg(c, label, ref, prompt_txt, spd, temp, topk, topp, rep)
    save_config(c)
    cur = (c.get("emotions") or {}).get(label) or {}
    if cur.get("ref_audio_path"):
        return "✅ 已保存「%s」：%s" % (label, cur["ref_audio_path"])
    return "✅ 已保存「%s」：参考音频为空，该情绪暂不可用（会回落到默认音色）" % label


def on_key_edit_show():
    """✏️ 修改 API 接口：展开弹窗（密钥输入框清空，接口地址/模型保持现值）。"""
    return gr.update(visible=True), gr.update(value="")


def _save_llm_settings(new_key, base_url, model, clear_key=False):
    """把接口三项写进本机 chat_config.json，返回 (掩码, 状态文案)。"""
    k = (new_key or "").strip()
    c = load_config()
    c.setdefault("llm", {})
    if clear_key:
        c["llm"]["api_key"] = ""
    elif k:                      # 留空 = 不改动已保存的密钥
        c["llm"]["api_key"] = k
    c["llm"]["base_url"] = (base_url or "").strip()
    c["llm"]["model"] = (model or "").strip()
    save_config(c)
    return mask_api_key(c["llm"].get("api_key")), c["llm"]


def on_key_save(new_key, base_url, model):
    """保存接口设置：只写本机，界面上密钥立刻变回掩码、弹窗收起。"""
    try:
        masked, llm = _save_llm_settings(new_key, base_url, model)
    except Exception as e:  # noqa: BLE001
        return gr.update(), gr.update(visible=True), gr.update(), f"❌ 保存失败：{e}"
    tail = ("密钥：%s" % masked) if llm.get("api_key") else "密钥：未设置"
    return (gr.update(value=masked), gr.update(visible=False), gr.update(value=""),
            "✅ 已保存到本机 chat_config.json　·　接口 `%s`　·　模型 `%s`　·　%s"
            % (llm.get("base_url"), llm.get("model"), tail))


def on_key_clear(base_url, model):
    """🗑 清除密钥（接口地址与模型保留）。"""
    try:
        masked, llm = _save_llm_settings("", base_url, model, clear_key=True)
    except Exception as e:  # noqa: BLE001
        return gr.update(), gr.update(visible=True), gr.update(), f"❌ 清除失败：{e}"
    return (gr.update(value=masked), gr.update(visible=False), gr.update(value=""),
            "✅ 已清除 API Key（接口与模型保留），界面上不再显示密钥")


def on_key_cancel():
    """取消：收起弹窗，什么都不改。"""
    return gr.update(visible=False), gr.update(value=""), ""


def on_probe(base_url, new_key, saved_mask, model):
    """🔌 测试连通：优先用弹窗里刚输入的新密钥，没输入就用已保存的。"""
    key = (new_key or "").strip() or resolve_api_key(saved_mask)
    return probe_llm(base_url, key, model)


def on_save_global(persona, temperature, max_tokens, reply_sents, reply_chars,
                   speed, avatar_user, avatar_bot):
    """🌐 全局设置：写进 chat_config.json，作为「新对话」的默认值。

    已开着的会话有自己的设置快照，不受影响（这正是「全局做默认、会话可覆盖」）。
    返回 (状态文案, chatbox 更新)：头像就地生效，不用刷新。
    """
    cfg = load_config()
    cfg["persona"]["system_prompt"] = persona or ""
    cfg["llm"].update(temperature=_as_float(temperature, 1.0),
                      max_tokens=_as_int(max_tokens, 1024),
                      max_reply_sents=_as_int(reply_sents, 6),
                      max_reply_chars=_as_int(reply_chars, 120))
    cfg["voice"]["speed_factor"] = _as_float(speed, 1.0)   # 全局默认倍速（TTS 语速）
    ui = cfg.setdefault("ui", {})
    saved = []
    for which, val in (("user", avatar_user), ("bot", avatar_bot)):
        rel = _save_avatar(which, val)
        if rel and rel != (ui.get(_AVATAR_FILES[which]) or ""):
            ui[_AVATAR_FILES[which]] = rel
            saved.append("我的头像" if which == "user" else "TA 的头像")
    save_config(cfg)
    head = "✅ 已保存为全局默认（角色人设 / 温度 / 回复属性"
    head += ("；" + "、".join(saved)) if saved else ""
    return (head + "）。新对话会用这套；当前会话仍用它自己的设置。",
            gr.update(avatar_images=_avatar_filedata()))


# 主题切换（前端即时生效，无需刷新）；选择结果同时写入配置，重开页面仍是上次的主题
_THEME_JS = """
(v) => {
  const map = {"浅色":"light", "深色":"dark", "跟随系统":"system"};
  const mode = map[v] || "system";
  try { localStorage.setItem("vc_theme", mode); } catch(e) {}
  const sysDark = () => window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches;
  const dark = mode === "dark" || (mode === "system" && sysDark());
  document.body.classList.toggle("dark", dark);
  // 同步通知「实时通话」iframe：切主题时它不会自己重载，否则会停在旧配色上
  try {
    const ifr = document.querySelector('iframe[src*="/call"]');
    if (ifr && ifr.contentWindow) ifr.contentWindow.postMessage({type:"vc_theme", mode:mode}, location.origin);
  } catch(e) {}
  return [v];
}
"""

# 打开页面时按配置里的主题立即生效（跟随系统时监听系统深浅色变化）
_THEME_INIT_JS = """
() => {
  let mode = "%s";
  try { mode = localStorage.getItem("vc_theme") || mode; } catch(e) {}
  const sysDark = () => window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches;
  const apply = () => {
    const dark = mode === "dark" || (mode === "system" && sysDark());
    document.body.classList.toggle("dark", dark);
    // 让「实时通话」iframe 与主页面同步（首次打开时 localStorage 可能还没写过）
    try {
      const ifr = document.querySelector('iframe[src*="/call"]');
      if (ifr && ifr.contentWindow) ifr.contentWindow.postMessage({type:"vc_theme", mode:mode}, location.origin);
    } catch(e) {}
  };
  apply();
  // 首次访问时把配置里的主题写进 localStorage，保证 /call 页面读到同一个值
  try { if (!localStorage.getItem("vc_theme")) localStorage.setItem("vc_theme", mode); } catch(e) {}
  try {
    window.matchMedia("(prefers-color-scheme: dark)")
      .addEventListener("change", () => { if (mode === "system") apply(); });
  } catch(e) {}
}
"""

_SCROLL_JS = (
    "() => {"
    " const s=()=>{ if(window.__vcScrollBottom){ window.__vcScrollBottom(); return; }"
    "  const el=document.querySelector('#vc_chatbox'); if(!el)return;"
    "  const arr=[el,...el.querySelectorAll('*')];"
    "  arr.forEach(t=>{if(t.scrollHeight>t.clientHeight+5)t.scrollTop=t.scrollHeight;});};"
    " s(); setTimeout(s,120); setTimeout(s,320); }"
)

# 隐藏按钮的 js：Gradio 约定「指定 js 时，js 的返回值按位置当作各 input 的值」——
# 注意不是「追加在入参后面」，而是正好 n 个值对应 n 个声明的 input。
# 所以第 0 个 input 专门放一个隐藏文本框装「这次点了什么」，返回时把它换成操作说明，
# 其余 input 原样回传（args[1..n-1]，args 的最后几项是 outputs 的值，要丢掉）。
_MSG_OP_RETURN_JS = """
(...args) => {
  const n = __VC_N_INPUTS__;
  let spec = "{}";
  try { spec = JSON.stringify(window.__vcMsgOp || {}); } catch (e) {}
  return [spec].concat(args.slice(1, n));
}
"""


def build_ui(cfg: dict):
    _ref_all = scan_ref_audios()
    _ref_total = len(_ref_all)
    ref_choices = ref_choices_for_dropdown(_ref_all)
    default_ref = cfg["voice"]["ref_audio_path"]
    default_prompt = cfg["voice"]["prompt_text"]
    default_persona = cfg["persona"]["system_prompt"]

    theme0 = _THEME_FROM_CODE.get(str((cfg.get("ui") or {}).get("theme", "system")), "跟随系统")
    with gr.Blocks(title="AI 语音聊天", theme=_THEME, head=_UI_HEAD) as demo:
        # 左会话栏 + 右主区：用 Gradio 原生 Row/Column 并排（不用绝对定位，避免错位）
        with gr.Row(elem_id="vc_root", equal_height=False):
            # 侧栏收起后在原位出现的“展开”按钮：宽度 0↔40px 过渡，和侧栏同节奏
            show_btn = gr.Button("⏵", elem_id="vc_show")
            # 左侧竖向会话栏（DeepSeek 风格：历史对话逐条平铺，操作收进每行「⋮」）
            with gr.Column(scale=0, min_width=232, elem_id="vc_sidebar"):
                gr.Markdown("🎙️ AI 语音聊天", elem_id="vc_brand")
                new_btn = gr.Button("＋ 新对话", variant="primary", elem_id="vc_newbtn")
                gr.Markdown("历史对话", elem_id="vc_sidelabel")
                convid = gr.State(None)                  # 当前打开的会话 id
                convs_state = gr.State(convs_state_value())   # 扁平历史列表数据
                conv_ui = gr.State({})                   # 界面态：哪行开着「⋮」/哪行在重命名
                with gr.Column(elem_id="vc_convlist"):

                    # 历史对话平铺：每条一行（标题 + 「⋮」）；重命名/置顶/删除都收进「⋮」
                    @gr.render(inputs=[convs_state, conv_ui, convid])
                    def _render_convs(convs, ui, cur):
                        convs, ui = (convs or []), (ui or {})
                        if not convs:
                            gr.Markdown("还没有历史对话，点上面「＋ 新对话」开始。",
                                        elem_id="vc_empty")
                            return
                        for c in convs:
                            sid, title = c["id"], c["title"]
                            with gr.Row(elem_classes=["vc_convrow" + (" vc_on" if sid == cur else "")]):
                                gr.Button(title, elem_classes=["vc_convtit"], scale=8).click(
                                    fn=lambda s=sid: tuple(on_select_conversation(s)) + ({},),
                                    inputs=None, outputs=_conv_out + [conv_ui]) \
                                    .then(**_info_step)
                                gr.Button("⋮", elem_classes=["vc_dots"], scale=0,
                                          min_width=34).click(
                                    fn=lambda u, s=sid: on_conv_menu(s, u),
                                    inputs=[conv_ui], outputs=[conv_ui])
                            if ui.get("menu") == sid:
                                with gr.Column(elem_id="vc_menu_pop", elem_classes=["vc_menu"]):
                                    gr.Button("✏️ 重命名", size="sm").click(
                                        fn=lambda u, s=sid: on_conv_rename_open(s, u),
                                        inputs=[conv_ui], outputs=[conv_ui])
                                    gr.Button("📌 取消置顶" if c["pinned"] else "📌 置顶",
                                              size="sm").click(
                                        fn=lambda cu, s=sid: on_pin_conv_row(s, cu),
                                        inputs=[convid], outputs=_conv_out + [conv_ui]) \
                                        .then(**_info_step)
                                    gr.Button("🗑️ 删除", size="sm").click(
                                        fn=lambda cu, s=sid: on_delete_conv_row(s, cu),
                                        inputs=[convid], outputs=_conv_out + [conv_ui]) \
                                        .then(**_info_step)
                            if ui.get("renaming") == sid:
                                with gr.Row(elem_classes=["vc_renamerow"]):
                                    rtb = gr.Textbox(value=title, show_label=False,
                                                     container=False, scale=8, max_lines=1)
                                    gr.Button("保存", size="sm", scale=0,
                                              elem_classes=["vc_mini"]).click(
                                        fn=lambda t, cu, s=sid: on_rename_conv_row(s, t, cu),
                                        inputs=[rtb, convid], outputs=_conv_out + [conv_ui])
                                    gr.Button("取消", size="sm", scale=0,
                                              elem_classes=["vc_mini"]).click(
                                        fn=on_conv_rename_cancel, inputs=[conv_ui],
                                        outputs=[conv_ui])
                                    rtb.submit(
                                        fn=lambda t, cu, s=sid: on_rename_conv_row(s, t, cu),
                                        inputs=[rtb, convid], outputs=_conv_out + [conv_ui])
                collapse_btn = gr.Button("⏴ 收起侧栏", size="sm", elem_id="vc_collapsebtn")
                theme_radio = gr.Radio(label="主题", choices=_THEME_LABELS, value=theme0,
                                       elem_id="vc_theme")
            # 右侧主区：全部功能标签页
            with gr.Column(scale=1, min_width=0, elem_id="vc_main"):
                with gr.Tab("对话"):
                    with gr.Column(elem_id="vc_chat_main"):
                        chatbox = gr.Chatbot(label="对话", value=[], height="100%", show_label=False,
                                             show_copy_button=False,
                                             avatar_images=tuple(_avatar_filedata()),
                                             elem_id="vc_chatbox")
                        # 隐藏按钮：每条消息下方工具条的点击，都由前端「点」它一次，
                        # 让 js 把 [操作, ...界面实时输入] 回传给 on_message_action（见 ui.js）
                        # 注意 elem_id 会直接落在 <button> 上（Gradio 的 Button 没有外层 div），
                        # 前端只能用 getElementById 找它；隐藏靠 ui.css 的 #vc_msgop_btn{display:none}
                        msgop_btn = gr.Button("", interactive=True, elem_id="vc_msgop_btn")
                        # 隐藏文本框：专门装「这次点了哪条消息的哪个操作」，由前端 js 赋值后回传。
                        # 必须是一个真实的 input（State 的值由服务端会话状态决定，收不到前端塞的值）。
                        msgop_spec = gr.Textbox(value="", elem_id="vc_msgop_spec", show_label=False)
                        # 隐藏的一段 JSON：告诉前端每轮回答有没有语音▶、有没有多个版本可切换。
                        # 由前端 JS 读取（见 _branch_info / ui.js），页面上看不见。
                        branchinfo = gr.HTML(value="", elem_id="vc_branchinfo")
                        audio = gr.Audio(label="AI 语音回复", type="filepath", autoplay=True,
                                         show_label=False, elem_id="vc_audio")
                        status = gr.Markdown("就绪。输入消息开始聊天。", elem_id="vc_status")
                        with gr.Row(elem_id="vc_inputrow"):
                            msg = gr.Textbox(placeholder="给 TA 发消息，回车发送…", scale=9, lines=1,
                                             show_label=False, container=False, max_lines=8)
                            send = gr.Button("发送", variant="primary", scale=1, min_width=84)
                        with gr.Accordion("⚙️ 更多（改名 / 语音对话 / 测试音色）", open=False):
                            with gr.Row():
                                conv_title = gr.Textbox(label="会话标题", value="", scale=8,
                                                        placeholder="例如：聊难过的事 / 给朋友的语音…")
                                rename_btn = gr.Button("✏️ 保存标题", scale=1)
                            with gr.Row():
                                clear = gr.Button("🧹 新对话")
                                test_voice = gr.Button("🔊 仅测试当前音色")
                            with gr.Row():
                                mic = gr.Audio(label="🎤 语音对话：点一下开始说话，说完自动识别",
                                               type="filepath", sources=["microphone"], scale=4)
                                voice_auto = gr.Checkbox(value=True, label="识别后自动发送", scale=1)
                            gr.Markdown("提示：首次语音识别需下载中文模型（约 1GB），请耐心等待一次；"
                                        "之后识别秒回。合成较慢时请稍候，语音会自动播放。")
                with gr.Tab("📞 实时通话"):
                    gr.Markdown("像打电话一样连续对话：**按住下方大圆钮说话，松开自动发送**；"
                                "TA 说话时随时按住即可抢话打断。识别→AI→语音各需几秒，属于正常节奏。"
                                "（使用本地麦克风需本页为 localhost 或 HTTPS）")
                    gr.HTML(
                        '<iframe src="/call" style="width:100%;height:640px;border:0;border-radius:12px;" '
                        'allow="microphone; autoplay; clipboard-write"></iframe>'
                    )
                with gr.Tab("设置"):
                    # ── 全局设置：新对话的默认值（已在聊的会话仍可在下方临时覆盖）──
                    with gr.Accordion("🌐 全局设置（角色人设 / 温度 / 回复属性 / 头像）", open=False,
                                      elem_classes=["vc_sec"], elem_id="vc_gacc"):
                        gr.Markdown("这里是**全局默认**：保存后「＋ 新对话」与刷新页面都用它。"
                                    "正在聊的会话保留各自的设置（想改当前会话，用本页下方那几项 + 「保存设置」）。")
                        g_persona = gr.Textbox(label="全局 角色人设 / System Prompt",
                                               value=default_persona, lines=5,
                                               placeholder="例如：你是×××，性格……，你称呼我为……")
                        with gr.Row():
                            g_temperature = gr.Slider(0, 2, value=float(cfg["llm"].get("temperature", 1.0)),
                                                      step=0.1, label="全局 temperature（越高越活泼）")
                            g_max_tokens = gr.Number(value=int(cfg["llm"].get("max_tokens", 1024)),
                                                     precision=0, label="全局 最大输出 Token")
                        with gr.Row():
                            g_reply_sents = gr.Number(value=int(cfg["llm"].get("max_reply_sents", 4)),
                                                      precision=0, label="全局 每轮回复句数上限")
                            g_reply_chars = gr.Number(value=int(cfg["llm"].get("max_reply_chars", 60)),
                                                      precision=0, label="全局 每轮回复总字数上限")
                        gr.Markdown("**全局 默认倍速**（TTS 生成语速）：之后「＋ 新对话」默认都用这个速度"
                                    "（开心 / 难过 / 害怕 三种情绪保留各自语速）。拖滑块，或直接输入自定义值。")
                        with gr.Row():
                            g_speed = gr.Slider(0.5, 2.0, value=float(cfg["voice"]["speed_factor"]),
                                                step=0.01, label="全局 默认倍速")
                            g_speed_num = gr.Number(value=float(cfg["voice"]["speed_factor"]),
                                                    label="全局 默认倍速（可输入自定义值）", precision=2)
                        gr.Markdown("头像：上传后存到 `assets/avatars/`，每条消息旁边会显示（留空=不改）。")
                        with gr.Row():
                            g_avatar_user = gr.Image(label="我的头像", type="filepath", height=140,
                                                     sources=["upload"], value=(_avatar_path("user") or None))
                            g_avatar_bot = gr.Image(label="TA 的头像", type="filepath", height=140,
                                                    sources=["upload"], value=(_avatar_path("bot") or None))
                        g_save = gr.Button("💾 保存为全局默认", variant="primary")
                        g_status = gr.Markdown("")
                    gr.Markdown("#### 角色人设（当前会话，可覆盖上面的全局默认）\n例如：`你是×××，性格……，说话……，你称呼我为……，与我的关系……`。留空则使用通用助手。")
                    persona_tpl = gr.Radio(
                        label="🎭 一键角色模板（点一下即套用下方人设，可再修改；选中“空模板”则清空）",
                        choices=[t[0] for t in PERSONA_TEMPLATES], value=None)
                    persona = gr.Textbox(label="角色人设 / System Prompt", value=default_persona,
                                         lines=6, placeholder="在这里描述你想要的任何人物…")
                    gr.Markdown(
                        "#### AI 接口（只要对方是「OpenAI 兼容」接口就能用）\n"
                        "DeepSeek / OpenAI / 各类中转站 / 本地 Ollama（`http://127.0.0.1:11434/v1`）/ "
                        "vLLM / LM Studio 都行，**不绑定任何一家**。\n"
                        "接口地址、模型、API Key 都收进「✏️ 修改 API 接口」的弹窗里，"
                        "密钥在页面上永远只显示掩码。")
                    with gr.Row():
                        _saved_key = str(cfg["llm"].get("api_key") or "")
                        llm_key = gr.Textbox(
                            label="API Key（已保存，只显示掩码）",
                            value=mask_api_key(_saved_key),
                            interactive=False, scale=3, elem_id="vc_keymask",
                            placeholder="还没设置 API Key，点右边「✏️ 修改」")
                        key_edit_btn = gr.Button("✏️ 修改 API 接口", scale=0, min_width=180,
                                                 elem_id="vc_keyedit_btn")
                    # 保存/清除的结果显示在弹窗外面，弹窗一收起也看得到
                    key_status = gr.Markdown("")
                    with gr.Group(visible=False, elem_id="vc_keymodal") as key_panel:
                        gr.Markdown("#### 🔌 AI 接口设置\n"
                                    "只写入本机 `chat_config.json`，不会上传到任何地方。")
                        llm_key_new = gr.Textbox(
                            label="API Key", type="password",
                            placeholder="sk-… 粘贴进来；留空保存 = 不改密钥")
                        base_url = gr.Textbox(label="接口地址 base_url",
                                              value=cfg["llm"].get("base_url", ""),
                                              info="形如 https://api.deepseek.com（填到 /v1 或直接贴 "
                                                   "/chat/completions 都能识别）")
                        _model_choices = [str(m) for m in (cfg["llm"].get("models") or []) if str(m).strip()]
                        _cur_model = (cfg["llm"].get("model") or "").strip()
                        if _cur_model and _cur_model not in _model_choices:
                            _model_choices.insert(0, _cur_model)
                        with gr.Row():
                            model = gr.Dropdown(label="模型", choices=_model_choices,
                                                value=_cur_model or None, scale=3,
                                                allow_custom_value=True, filterable=True,
                                                info="点「🔌 测试连通」自动拉取该接口支持的模型列表；"
                                                     "没拉到时也可以直接输入模型名")
                            llm_probe_btn = gr.Button("🔌 测试连通", scale=0, min_width=150)
                        llm_probe_out = gr.Markdown("")
                        with gr.Row():
                            key_save_btn = gr.Button("💾 保存", variant="primary", scale=2)
                            key_clear_btn = gr.Button("🗑 清除密钥", scale=1)
                            key_cancel_btn = gr.Button("取消", scale=1)
                    with gr.Row():
                        temperature = gr.Slider(0, 2, value=float(cfg["llm"].get("temperature", 1.0)),
                                                step=0.1, label="temperature（越高越活泼）")
                        max_tokens = gr.Number(value=int(cfg["llm"].get("max_tokens", 1024)),
                                               label="最大输出 Token（含思考，建议 512~2048）",
                                               precision=0)
                    # ── 其余为「高级设置」：默认折叠，只在使用时才展开，避免设置页过长 ──
                    with gr.Accordion("⚙️ 高级设置（模型权重 / 参考音频 / 回复长度 / 语气采样 / 记忆与系统）",
                                      open=False, elem_classes=["vc_sec"], elem_id="vc_aacc"):
                        gr.Markdown("> 下面这些都是**推理（合成）参数**，不是训练选项 —— "
                                    "模型已经训练好了，这里只决定「怎么用它说话」。")
                        gr.Markdown("#### 回复长度（控制 AI 每轮说多少）")
                        with gr.Row():
                            reply_sents = gr.Number(value=int(cfg["llm"].get("max_reply_sents", 4)),
                                                    label="AI 每轮回复句数上限（语音按句分段朗读）",
                                                    precision=0)
                            reply_chars = gr.Number(value=int(cfg["llm"].get("max_reply_chars", 60)),
                                                    label="AI 每轮回复总字数上限（越多越完整，太长易读不完）",
                                                    precision=0)
                        gpt_choices = scan_gpt_weights()
                        sovits_choices = scan_sovits_weights()
                        gr.Markdown("#### 模型权重（这就是「已经训练好的模型」，选好点下面按钮切过去）\n"
                                    "切换后需等待 10~60 秒重新加载；之后点「🔊 仅测试当前音色」即可对比。\n"
                                    "扫到 **%d** 个 GPT 权重、**%d** 个 SoVITS 权重"
                                    "（来源：项目里的 `GPT_weights*` / `SoVITS_weights*` 目录、`logs/` 训练目录、"
                                    "以及 `GPT_SoVITS/pretrained_models` 官方底模）。\n"
                                    "- **GPT 模型**（`.ckpt`）= 语音生成器，决定「怎么说」；"
                                    "**SoVITS 模型**（`.pth`）= 声码器，决定「音色像不像」。两个都要选。\n"
                                    "- 下拉里没有你要的？把权重放进 `GPT_weights_v2Pro` / `SoVITS_weights_v2Pro`；"
                                    "或者**直接把绝对路径粘进框里回车**（例如 `E:\\models\\nahida\\xxx.ckpt`）。"
                                    % (len(gpt_choices), len(sovits_choices)))
                        with gr.Row():
                            gpt_weights_dd = gr.Dropdown(
                                label="GPT 模型（语音生成器）", choices=gpt_choices,
                                value=cfg["voice"].get("gpt_weights"), allow_custom_value=True,
                                filterable=True,
                                info="" if gpt_choices else "没扫到 .ckpt：放进 GPT_weights_v2Pro，或直接粘绝对路径")
                            sovits_weights_dd = gr.Dropdown(
                                label="SoVITS 模型（声码器）", choices=sovits_choices,
                                value=cfg["voice"].get("sovits_weights"), allow_custom_value=True,
                                filterable=True,
                                info="" if sovits_choices else "没扫到 .pth：放进 SoVITS_weights_v2Pro，或直接粘绝对路径")
                        with gr.Row():
                            switch_btn = gr.Button("🔄 应用权重并重载（较慢）", variant="primary")
                        wstatus = gr.Markdown("")
                        gr.Markdown("#### 参考音频（决定音色与说话风格，建议选语气生动的长句）\n"
                                    "下拉会列：训练切片 `logs/*/5-wav32k`、常见训练集目录（`data/raw`、`raw` 等）、"
                                    "`refs/` 目录、以及以前合成的片段。**要把自己的音频加进来，"
                                    "丢进项目根目录的 `refs/` 文件夹**，刷新页面就出现在下拉里；"
                                    "也可以直接在右边填路径（相对项目根目录或绝对路径）。\n"
                                    + ("" if not _ref_total else
                                       ("扫到 **%d** 个音频%s。"
                                        % (_ref_total, "，下拉只列前 %d 个" % _REF_CHOICE_MAX
                                           if _ref_total > _REF_CHOICE_MAX else ""))))
                        with gr.Row():
                            ref_dd = gr.Dropdown(label="从训练数据选择参考音频", choices=ref_choices,
                                                 value=next((c for _, c in ref_choices if c == default_ref), None),
                                                 allow_custom_value=False, filterable=True,
                                                 visible=bool(ref_choices),
                                                 info="" if ref_choices else "没扫到参考音频：建个 refs/ 放音频，或直接填右边路径")
                            ref_path = gr.Textbox(label="参考音频路径（相对项目根目录或绝对路径）",
                                                  value=default_ref)
                        prompt_text = gr.Textbox(label="参考音频对应的文字（prompt_text，务必与音频一致）",
                                                 value=default_prompt, lines=2)
                        with gr.Row():
                            prompt_lang = gr.Dropdown(label="参考音频语言", value=cfg["voice"]["prompt_lang"],
                                                      choices=["zh", "en", "ja", "ko", "yue", "auto"])
                            text_lang = gr.Dropdown(label="朗读语言", value=cfg["voice"]["text_lang"],
                                                    choices=["zh", "all_zh", "en", "ja", "ko", "yue", "auto"])
                            text_split = gr.Dropdown(label="切分方式", value=cfg["voice"]["text_split_method"],
                                                     choices=["cut5", "cut0", "cut1", "cut2", "cut3", "cut4",
                                                              "凑四句", "凑50字", "按中文句号。", "按英文句号 .",
                                                              "按标点符号切"])
                        gr.Markdown("**默认倍速**（TTS 生成语速）：调了它，本会话所有语音都按这个速度合成"
                                    "（开心 / 难过 / 害怕 三种情绪保留各自语速）。拖滑块，或在右边直接输入自定义值。")
                        with gr.Row():
                            speed = gr.Slider(0.5, 2.0, value=float(cfg["voice"]["speed_factor"]),
                                              step=0.01, label="默认倍速（本会话）")
                            speed_num = gr.Number(value=float(cfg["voice"]["speed_factor"]),
                                                  label="默认倍速（可输入自定义值）", precision=2)
                        gr.Markdown("#### 语气/采样（高级。越大语气越多变，过大易口胡，配合试听调节）")
                        with gr.Row():
                            top_k = gr.Slider(1, 40, value=int(cfg["voice"].get("top_k", 5)), step=1,
                                              label="top_k")
                            top_p = gr.Slider(0.5, 1, value=float(cfg["voice"].get("top_p", 1.0)), step=0.05,
                                              label="top_p")
                            gpt_temperature = gr.Slider(0.3, 1.5, value=float(cfg["voice"].get("gpt_temperature", 1.0)),
                                                        step=0.05, label="temperature")
                            repetition_penalty = gr.Slider(1.0, 2.0, value=float(cfg["voice"].get("repetition_penalty", 1.35)),
                                                           step=0.05, label="repetition_penalty")
                        gr.Markdown("#### 朗读稳定性\n"
                                    "- **随机种子** = `-1`：每次朗读都会变（情绪时好时坏）；\n"
                                    "- 填一个**固定数字**（如 `42`）：同一句话永远读得完全一样，稳定下来；\n"
                                    "- 多试几个数字，把听感最好的一次定下来。改后点「保存设置」生效。")
                        with gr.Row():
                            seed = gr.Number(value=int(cfg["voice"].get("seed", -1) or -1), precision=0,
                                             label="朗读随机种子（-1=随机 / 固定数字=稳定一致）")
                        with gr.Row():
                            keep = gr.Slider(1, 20, value=int(cfg["history"]["keep_turns"]),
                                             step=1, label="记住最近对话轮数")
                            port = gr.Number(value=int(cfg["ui"]["port"]), precision=0,
                                         label="网页端口（改完需重启网页服务才生效）")
                        gr.Markdown("> 说明：打开「情绪切换」后，AI 会先判断这句话的语调（开心/难过/安抚…），"
                                    "再选用对应的原声参考音频朗读，这样声音才能真正有情绪起伏（说伤心话会真的低沉），"
                                    "建议保持开启。个别情绪样本若嫌慢/不够自然，可在下方情绪列表中更换。")
                        emotion_on = gr.Checkbox(label="AI 回复时按情绪切换参考音频（推荐开启）",
                                                 value=bool(cfg["voice"].get("emotion_mode", True)))
                        gr.Markdown("> **系统：手机/局域网访问**（系统级选项，**保存后需重启网页服务才生效**）：\n"
                                    "- 勾选 = 网页同时对本机 + 局域网开放（手机可连，如电脑连手机热点/同一 WiFi）；\n"
                                    "- 不勾选 = 仅本机可打开，对外完全关闭。\n"
                                    "- 每次打开网页此项都**默认显示为不勾选**（安全起见）。要开启手机访问："
                                    "**勾选它并点「保存设置」**，再重启本服务。")
                        with gr.Row():
                            phone_access = gr.Checkbox(
                                label="✅ 允许手机/局域网访问（勾选=监听全部网卡；不勾选=仅本机，不暴露）",
                                value=False)
                            addr_query = gr.Button("📱 查询手机访问网址", size="sm")
                        phone_url = gr.Markdown("点击上方「📱 查询手机访问网址」即可显示手机当前能连接的地址。")
                    with gr.Row():
                        save_btn = gr.Button("💾 保存设置", variant="primary")
                    save_status = gr.Markdown("")
                    with gr.Accordion("🎭 情绪微调（每种情绪的参考音频与朗读参数）", open=False,
                                      elem_classes=["vc_sec"], elem_id="vc_emoacc"):
                        gr.Markdown(
                            "开启上面的「情绪切换」后，AI 会先判断这句话的语调，再用**对应情绪的参考音频**朗读。"
                            "这里给每种情绪单独指定参考音频与参数。\n"
                            "- **参考音频**：留空 = 该情绪不可用，会自动回落到默认音色；\n"
                            "- **参考文本**：必须和参考音频里说的内容一致，否则音色会飘；\n"
                            "- 改完点「💾 保存该情绪」，写到 chat_config.json 的 `emotions` 下。")
                        with gr.Row():
                            _emo0 = (cfg.get("emotions") or {}).get(EMOTION_LABELS[0]) or {}
                            emo_pick = gr.Dropdown(label="选择要调整的情绪", choices=list(EMOTION_LABELS),
                                                   value=EMOTION_LABELS[0], scale=1, elem_id="vc_emopick")
                            emo_reload = gr.Button("🔄 载入该情绪当前设置", size="sm", scale=0)
                        emo_ref = gr.Textbox(label="参考音频路径（相对 GPT-SoVITS 根目录或绝对路径）",
                                             value=_emo0.get("ref_audio_path", ""), lines=1,
                                             elem_id="vc_emoref")
                        emo_ref_audio = gr.Audio(label="▶ 试听这段参考音频（决定的音色/语气）",
                                                 type="filepath", interactive=False,
                                                 elem_id="vc_emorefaudio",
                                                 value=_gradio_audio(_emo0.get("ref_audio_path", "")))
                        emo_prompt = gr.Textbox(label="参考文本（必须与参考音频内容一致）",
                                                value=_emo0.get("prompt_text", ""), lines=2)
                        with gr.Row():
                            emo_speed = gr.Slider(0.5, 2.0, value=float(_emo0.get("speed_factor", 1.0) or 1.0),
                                                  step=0.05, label="语速")
                            emo_temp = gr.Number(value=float(_emo0.get("gpt_temperature", 1.4) or 1.4),
                                                 precision=2, label="gpt_temperature")
                        with gr.Row():
                            emo_topk = gr.Number(value=int(_emo0.get("top_k", 12) or 12), precision=0, label="top_k")
                            emo_topp = gr.Slider(0.5, 1.0, value=float(_emo0.get("top_p", 0.98) or 0.98),
                                                 step=0.01, label="top_p")
                            emo_rep = gr.Slider(0.8, 2.0, value=float(_emo0.get("repetition_penalty", 1.25) or 1.25),
                                                step=0.05, label="repetition_penalty")
                        gr.Markdown("**🔊 试听这个情绪的实际效果**：下面这句会用「当前表单里的参数」直接合成"
                                    "（语速/temperature/top_k… 立刻能听出差别），满意再保存。")
                        with gr.Row():
                            emo_try_text = gr.Textbox(label="试听用的文字（可改）",
                                                      value="今天天气真好，我们一起出去走走吧！", scale=3,
                                                      elem_id="vc_emotrytext")
                            emo_try_btn = gr.Button("🔊 用该情绪合成试听", variant="primary",
                                                    scale=0, min_width=170, elem_id="vc_emotry")
                        emo_try_audio = gr.Audio(label="▶ 试听合成结果", type="filepath", interactive=False,
                                                 elem_id="vc_emotryaudio")
                        emo_try_status = gr.Markdown("")
                        with gr.Row():
                            emo_save = gr.Button("💾 保存该情绪", variant="primary")
                            emo_fill = gr.Button("📥 用上面「参考音频」填入路径与文本", size="sm")
                        emo_status = gr.Markdown("")
                    gr.Markdown("---\n> **🛑 关闭后台服务**：安全退出会停止网页后台进程（含本次启动的语音模型），"
                                "释放内存占用；之后重新运行启动脚本即可再打开。")
                    with gr.Row():
                        exit_confirm = gr.Checkbox(label="我确认要关闭后台服务", value=False)
                        exit_btn = gr.Button("🛑 安全退出（停止后台，释放内存）", variant="stop")
                    exit_status = gr.Markdown("")

                    # 选择参考音频 -> 自动填路径与文本
                    ref_dd.change(fn=on_pick_ref, inputs=[ref_dd], outputs=[ref_path, prompt_text])
                    # 一键角色模板 -> 填入人设文本框
                    persona_tpl.change(fn=on_pick_persona_tpl, inputs=[persona_tpl],
                                       outputs=[persona])
                    # 默认倍速：滑块 <-> 自定义输入框 双向同步（夹到 0.5~2.0、保留两位小数）。
                    # ★ 必须「值已一致就不回写」：Gradio 对程序化赋值也会触发 .change，
                    #   若两边互相无条件回写就会无限循环 -> 界面疯狂闪烁。相等时返回空更新即可打断。
                    def _sync_speed(v, other):
                        try:
                            nv = round(max(0.5, min(2.0, float(v))), 2)
                        except (TypeError, ValueError):
                            return gr.update()
                        try:
                            if other is not None and abs(float(other) - nv) < 1e-9:
                                return gr.update()        # 已一致：不回写，打断循环
                        except (TypeError, ValueError):
                            pass
                        return gr.update(value=nv)
                    speed.change(fn=_sync_speed, inputs=[speed, speed_num], outputs=[speed_num])
                    speed_num.change(fn=_sync_speed, inputs=[speed_num, speed], outputs=[speed])
                    g_speed.change(fn=_sync_speed, inputs=[g_speed, g_speed_num], outputs=[g_speed_num])
                    g_speed_num.change(fn=_sync_speed, inputs=[g_speed_num, g_speed], outputs=[g_speed])

                with gr.Tab("输入即朗读"):
                    gr.Markdown("想说什么就直接输入，点按钮立即朗读，**不经过 AI 对话**。"
                                "支持**长文/长对话**：直接粘贴一大段也能完整读完（会自动分段、不丢句）。\n"
                                "「朗读情绪」可选：**✨ 自动判断**（AI 读懂这句话的情感后自动用相应情绪朗读，"
                                "判断需额外一次联网、会稍慢）、某一种**具体情绪**（开心/难过/亲密…），"
                                "或**默认(当前音色)**。结果自动存入「回放历史」。")
                    with gr.Row():
                        demotion = gr.Dropdown(
                            label="朗读情绪",
                            choices=["默认(当前音色)", AUTO_EMO_MARK] + list(EMOTION_LABELS),
                            value="默认(当前音色)", scale=1)
                        dtext = gr.Textbox(label="要朗读的内容（可粘贴长文）", lines=6, scale=3,
                                           placeholder="例如：今天天气真好，我们一起去散步吧！\n也可以直接粘贴一大段话/故事/聊天记录，回车换行…",
                                           show_copy_button=True)
                    with gr.Row():
                        dspeak = gr.Button("🎤 开始朗读", variant="primary")
                    daudio = gr.Audio(label="朗读结果", type="filepath")
                    dstatus = gr.Markdown("")

                with gr.Tab("回放历史"):
                    gr.Markdown("这里显示**当前打开的会话**的全部语音。每一条 AI 回复 / 直达朗读都会归档到"
                                "所属会话，**在左侧「历史对话」切换会话即可查看对应会话的语音**；"
                                "重启或退出重登后依然可查、可回放。")
                    with gr.Row():
                        rp_refresh = gr.Button("🔄 刷新当前会话列表")
                        rp_clear = gr.Button("🧹 清空当前会话语音")
                    replay_info = gr.Markdown("📼 当前会话暂无语音")
                    replay_dd = gr.Dropdown(label="选择一条语音回放（时间+文字摘要）", choices=[],
                                            interactive=True)
                    replay_text = gr.Textbox(label="该条完整文字（自动带出，可选中复制）", lines=3,
                                             interactive=False, show_copy_button=True)
                    replay_audio = gr.Audio(label="点击 ▶ 回放", type="filepath")

        # 事件绑定
        _adv = [top_k, top_p, gpt_temperature, repetition_penalty]
        _send_inputs = [msg, chatbox, persona, llm_key, base_url, model, temperature, max_tokens,
                        ref_path, prompt_text, prompt_lang, text_lang, text_split, speed,
                        keep] + _adv + [reply_sents, reply_chars, emotion_on, convid]
        # 输出：聊天/音频/状态/输入清空/回放下拉/回放文字 + 当前会话 id
        _send_outputs = [chatbox, audio, status, msg, replay_dd, replay_text, convid]
        # 会话级设置控件（顺序必须与 settings_values() 的返回值严格一致）
        _settings_widgets = [persona, llm_key, base_url, model, temperature, max_tokens,
                             reply_sents, reply_chars, ref_dd, ref_path, prompt_text,
                             prompt_lang, text_lang, text_split, speed, speed_num,
                             top_k, top_p, gpt_temperature, repetition_penalty,
                             keep, emotion_on]
        # 会话/回放全套输出（新对话/删除/切换/刷新页面共用，顺序固定）
        # 末尾追加设置控件：切会话/开新对话时同步整页设置
        _conv_out = [convid, chatbox, audio, convs_state, conv_title, status,
                     replay_dd, replay_text, replay_info] + _settings_widgets
        # 每轮回答的「有没有语音 / 有几个版本」推给前端（隐藏组件，ui.js 读它）
        _info_step = dict(fn=_branch_info, inputs=[convid], outputs=[branchinfo])
        send.click(fn=on_send, inputs=_send_inputs, outputs=_send_outputs) \
            .then(fn=_conv_refresh_ui, inputs=[convid],
                  outputs=[convs_state, conv_title, replay_info]) \
            .then(**_info_step) \
            .then(fn=None, js=_SCROLL_JS)
        msg.submit(fn=on_send, inputs=_send_inputs, outputs=_send_outputs) \
            .then(fn=_conv_refresh_ui, inputs=[convid],
                  outputs=[convs_state, conv_title, replay_info]) \
            .then(**_info_step) \
            .then(fn=None, js=_SCROLL_JS)
        # 每条消息下方的工具条（重新加载 / 改写 / 复制 / 删除）：
        # 前端注入按钮 → 点隐藏按钮 → js 把操作写进隐藏文本框并回传 → on_message_action
        # inputs = [隐藏文本框] + 与「发送」一致的实时设置（去掉输入框本身）
        _msgop_inputs = [msgop_spec] + _send_inputs[1:]
        _msgop_outputs = [chatbox, audio, status, convs_state, conv_title,
                          replay_dd, replay_text, replay_info]
        msgop_btn.click(fn=on_message_action, inputs=_msgop_inputs, outputs=_msgop_outputs,
                        js=_MSG_OP_RETURN_JS.replace("__VC_N_INPUTS__", str(len(_msgop_inputs)))) \
            .then(**_info_step)
        # 消息工具条与侧栏的注入逻辑都在 ui.js 里（页面加载时自动执行，见 _UI_HEAD）
        # 语音对话：麦克风录音 -> 识别 ->（按勾选）自动走 on_send
        mic.stop_recording(fn=on_voice_input, inputs=[mic, voice_auto] + _send_inputs,
                           outputs=_send_outputs) \
            .then(fn=_conv_refresh_ui, inputs=[convid],
                  outputs=[convs_state, conv_title, replay_info]) \
            .then(**_info_step) \
            .then(fn=None, js=_SCROLL_JS)
        # 会话管理：新对话 / 清空（历史列表里每行的「⋮」管重命名/置顶/删除）
        new_btn.click(fn=on_new_conversation, outputs=_conv_out) \
            .then(**_info_step).then(fn=None, js=_SCROLL_JS)
        clear.click(fn=on_new_conversation, outputs=_conv_out) \
            .then(**_info_step).then(fn=None, js=_SCROLL_JS)
        rename_btn.click(fn=on_rename_conversation, inputs=[convid, conv_title],
                         outputs=[convs_state, conv_title, status])
        test_voice.click(fn=on_test_voice,
                         inputs=[ref_path, prompt_text, prompt_lang, text_lang, text_split, speed] + _adv + [seed],
                         outputs=[audio, status])
        llm_probe_btn.click(fn=on_probe, inputs=[base_url, llm_key_new, llm_key, model],
                            outputs=[llm_probe_out, model])
        # API 接口弹窗：展开 → 输入/测试 → 保存（只写本机）→ 收回、密钥变回掩码
        key_edit_btn.click(fn=on_key_edit_show, outputs=[key_panel, llm_key_new])
        key_save_btn.click(fn=on_key_save, inputs=[llm_key_new, base_url, model],
                           outputs=[llm_key, key_panel, llm_key_new, key_status])
        key_clear_btn.click(fn=on_key_clear, inputs=[base_url, model],
                            outputs=[llm_key, key_panel, llm_key_new, key_status])
        key_cancel_btn.click(fn=on_key_cancel,
                             outputs=[key_panel, llm_key_new, key_status])
        switch_btn.click(fn=on_switch_weights, inputs=[gpt_weights_dd, sovits_weights_dd],
                         outputs=[wstatus])
        save_btn.click(fn=on_save_persona,
                       inputs=[persona, llm_key, base_url, model, temperature, max_tokens,
                               ref_path, prompt_text, prompt_lang, text_lang, text_split,
                               speed, keep, port] + _adv +
                              [seed, reply_sents, reply_chars, emotion_on,
                               phone_access, convid],
                       outputs=[save_status])
        _emo_outs = [emo_ref, emo_prompt, emo_speed, emo_temp, emo_topk, emo_topp, emo_rep,
                     emo_ref_audio]
        emo_pick.change(fn=on_emotion_load, inputs=[emo_pick], outputs=_emo_outs)
        emo_reload.click(fn=on_emotion_load, inputs=[emo_pick], outputs=_emo_outs)
        emo_ref.change(fn=on_emo_ref_change, inputs=[emo_ref], outputs=[emo_ref_audio])
        emo_try_btn.click(fn=on_emotion_try,
                          inputs=[emo_pick, emo_ref, emo_prompt, emo_speed, emo_temp,
                                  emo_topk, emo_topp, emo_rep, emo_try_text],
                          outputs=[emo_try_audio, emo_try_status])
        emo_fill.click(fn=lambda r, p: (gr.update(value=r or ""), gr.update(value=p or "")),
                       inputs=[ref_path, prompt_text], outputs=[emo_ref, emo_prompt])
        emo_save.click(fn=on_emotion_save,
                       inputs=[emo_pick, emo_ref, emo_prompt, emo_speed, emo_temp,
                               emo_topk, emo_topp, emo_rep],
                       outputs=[emo_status])

        # 🌐 全局设置：保存为新对话的默认值（头像就地更新，不用刷新）
        g_save.click(fn=on_save_global,
                     inputs=[g_persona, g_temperature, g_max_tokens, g_reply_sents,
                             g_reply_chars, g_speed, g_avatar_user, g_avatar_bot],
                     outputs=[g_status, chatbox])

        # 输入即朗读：按所选情绪直接合成，归档到「当前会话」的回放历史（无会话则自动建档）
        _direct_inputs = [dtext, demotion, ref_path, prompt_text, prompt_lang, text_lang,
                          text_split, speed] + _adv + [convid]
        _direct_outputs = [daudio, dstatus, convid, convs_state, conv_title,
                           replay_dd, replay_text, replay_info]
        dspeak.click(fn=on_direct_tts, inputs=_direct_inputs, outputs=_direct_outputs)
        dtext.submit(fn=on_direct_tts, inputs=_direct_inputs, outputs=_direct_outputs)

        # 回放历史：选择/刷新/清空（均按当前会话操作）
        replay_dd.change(fn=on_pick_replay, inputs=[replay_dd, convid],
                         outputs=[replay_audio, replay_text])
        rp_refresh.click(fn=on_refresh_replay, inputs=[convid],
                         outputs=[replay_dd, replay_info, replay_text])
        rp_clear.click(fn=on_clear_replay, inputs=[convid],
                        outputs=[replay_dd, replay_info, replay_audio, replay_text])

        # 每次打开/刷新页面自动回到最近会话（文字与语音历史一并载入）
        demo.load(fn=on_page_load, outputs=_conv_out) \
            .then(**_info_step).then(fn=None, js=_SCROLL_JS)
        # 每次打开/刷新页面都把“允许手机访问”开关显示为未勾选（安全默认，避免误以为已开启）
        demo.load(fn=_force_phone_toggle_off, outputs=[phone_access])
        # 查询手机当前可访问网址（点一下即可，不用自动刷新）
        addr_query.click(fn=_phone_url_md, outputs=[phone_url])
        # 安全退出：停止后台进程与语音模型，释放内存
        exit_btn.click(fn=on_safe_exit, inputs=[exit_confirm], outputs=[exit_status])
        # 主题：选择后前端立即生效并记住；打开页面按配置/上次选择应用
        theme_radio.change(fn=on_set_theme, inputs=[theme_radio], outputs=[theme_radio],
                           js=_THEME_JS)
        demo.load(fn=None, js=_THEME_INIT_JS % _THEME_TO_CODE.get(theme0, "system"))
        # 收起 / 展开侧栏：完全由 ui.js 里的原生事件处理，不走 Gradio 事件，
        # 避免重渲染/事件失效导致按钮点了没反应。
    return demo


# ---------------------------------------------------------------- 📞 对讲式实时通话（与主界面同端口）
# 浏览器按住说话 → 本地 FunASR 识别 → LLM → GPT-SoVITS 逐句合成 SSE 流式推送，边播边打字。
# 页面: /call   接口: POST /api/turn（audio=语音 或 text=文字）→ SSE
_CALL_PAGE = ROOT / "call_page.html"
_CALL_TMP = ROOT / "output" / "call_tmp"
_CALL_TMP.mkdir(parents=True, exist_ok=True)
_CALL_LOG = ROOT / "output" / "call_log.txt"
_CALL_FFMPEG = ROOT / "runtime" / "ffmpeg.exe"
_CALL_HISTORY = []          # 多轮上下文 [(用户, AI全文), ...]
_CALL_ASR_LOCK = asyncio.Lock()
_CALL_SPECIALS = ("难过", "安抚", "害怕", "害羞")


def _call_asr_wav(src: str) -> str:
    """webm/mp4 → 16k 单声道 wav，交给本地 FunASR。"""
    dst = str(_CALL_TMP / f"{uuid.uuid4().hex}.wav")
    subprocess.run(
        [_CALL_FFMPEG, "-y", "-i", src, "-ar", "16000", "-ac", "1",
         "-c:a", "pcm_s16le", dst], check=True, capture_output=True)
    return dst


def _call_plan_groups(user: str) -> dict:
    """LLM 回复 → 情绪连贯自然化 → 朗读块序列。

    返回 {"pure": 全文, "blocks": [(emo, 单块文本, gap_s, special)]}；
    gap_s 表示该块相对上一块开播前的停顿秒数（同情绪句间=short，情绪切换=long）。
    """
    global _CALL_HISTORY
    cfg = load_config()
    msgs = build_messages(cfg, _CALL_HISTORY)   # build_messages 内部保留最近 keep 轮
    msgs.append({"role": "user", "content": user})
    answer = llm_chat(msgs, cfg)
    if not answer:
        raise RuntimeError("AI 没有说出内容，请再说一次试试")
    segs = split_reply_segments(answer)
    pure = join_segment_display(segs) or answer.strip()
    hint = request_emotion_hint(user)
    # 组内连续同情绪句子合并成一段 → 稳定基调；组间留给自然呼吸停顿
    groups = _merge_emotion_groups(
        [(s["emo"], s["body"], s["body"]) for s in segs], cfg, hint)
    voice = cfg.get("voice") or {}
    gap_same = _as_float(voice.get("emotion_gap_same"), 0.14)
    gap_change = _as_float(voice.get("emotion_gap_change"), 0.32)
    blocks = []
    for gi, (emo, joined) in enumerate(groups):
        special = emo in _CALL_SPECIALS
        chunks = _split_tts_chunks(joined)
        for cj, ch in enumerate(chunks):
            if special:
                ch = compress_pauses(ch) or ch
            gap = 0.0 if (gi == 0 and cj == 0) else (gap_change if cj == 0 else gap_same)
            blocks.append((emo, ch, gap, special))
    return {"pure": pure, "blocks": blocks}


def cfg_history_keep() -> int:
    return int((load_config().get("history") or {}).get("keep_turns", 8))


def _call_synth_block(block) -> bytes:
    """阻塞线程中合成单个朗读块 → wav 字节。"""
    emo, ch, _gap, special = block
    c = load_config()
    wav = synthesize(ch, c, emotion=emo)
    if special:
        wav = tighten_silences(wav)
    else:
        wav = tighten_silences(wav, cap=0.9, keep=0.5, head_cap=0.4, head_keep=0.2)
    with open(wav, "rb") as f:
        return f.read()


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _call_log_turn(user: str, ai: str) -> None:
    try:
        with open(_CALL_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 你：{user}\nAI：{ai}\n")
    except Exception:  # noqa: BLE001
        pass


async def _call_turn_gen(audio_path, text: str):
    """一次通话回合的 SSE 事件流生成器。audio 与 text 二选一。"""
    global _CALL_HISTORY
    user = ""
    if audio_path:
        yield _sse({"type": "status", "phase": "asr", "text": "正在听你说…"})
        wav = None
        try:
            async with _CALL_ASR_LOCK:
                wav = await asyncio.to_thread(_call_asr_wav, audio_path)
                user = await asyncio.to_thread(asr_recognize, wav)
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "msg": f"语音识别失败：{e}"})
            return
        finally:
            if wav:
                try:
                    os.remove(wav)
                except Exception:  # noqa: BLE001
                    pass
        user = (user or "").strip()
        if not user:
            yield _sse({"type": "error", "msg": "没听清你说什么，请按住重说一次～"})
            return
        yield _sse({"type": "user", "text": user})
    else:
        user = (text or "").strip()
        if not user:
            yield _sse({"type": "error", "msg": "内容为空"})
            return
        yield _sse({"type": "user", "text": user})

    yield _sse({"type": "status", "phase": "think", "text": "TA 在想怎么回答…"})
    try:
        plan = await asyncio.to_thread(_call_plan_groups, user)
    except Exception as e:  # noqa: BLE001
        yield _sse({"type": "error", "msg": f"AI 回复失败：{e}"})
        return
    if not plan["blocks"]:
        yield _sse({"type": "error", "msg": "AI 没有说话内容"})
        return

    yield _sse({"type": "status", "phase": "speak", "text": "TA 正在说话…（按住可随时打断）"})
    yield _sse({"type": "begin", "full": plan["pure"]})
    loop = asyncio.get_running_loop()
    try:
        for block in plan["blocks"]:
            data = await loop.run_in_executor(None, _call_synth_block, block)
            yield _sse({"type": "audio", "text": block[1],
                        "gap": round(block[2] * 1000),   # 相对上一块的开播停顿(ms)
                        "b64": base64.b64encode(data).decode()})
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        yield _sse({"type": "error", "msg": f"语音合成失败：{e}"})
        return
    _CALL_HISTORY = (_CALL_HISTORY + [(user, plan["pure"])])[-cfg_history_keep():]
    _call_log_turn(user, plan["pure"])
    yield _sse({"type": "done", "full": plan["pure"], "turns": len(_CALL_HISTORY)})


# ---------------------------------------------------------------- 局域网访问口令
# 防止校园网/同一局域网里的其他人打开本页面（看到对话历史、用你的 API Key）。
# 规则：本机 127.0.0.1 直接放行；手机等局域网访问先输密码（首次输对后记住）。
_ACCESS_COOKIE = "vc_auth"


def _access_token() -> str:
    return str((load_config().get("ui") or {}).get("access_token", "") or "")


def _request_is_local(request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1")


def _login_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>访问验证</title>
<style>
 body{{font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
      background:#11141c;color:#e8ecf4;display:flex;align-items:center;justify-content:center;
      min-height:100vh;margin:0;padding:16px;box-sizing:border-box}}
 .box{{background:#1b2030;border:1px solid #2c3450;border-radius:16px;padding:32px 26px;
      width:100%;max-width:340px;text-align:center}}
 h1{{font-size:19px;margin:0 0 6px}} p{{font-size:13px;color:#9aa4bf;margin:0 0 18px}}
 input{{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #3a4568;
      background:#0f1320;color:#fff;font-size:18px;text-align:center;letter-spacing:6px;
      outline:none}} input:focus{{border-color:#6d8bff}}
 button{{margin-top:16px;width:100%;padding:12px;border:0;border-radius:10px;background:#4f6bff;
      color:#fff;font-size:16px;cursor:pointer}} button:disabled{{opacity:.5}}
 .err{{color:#ff7a7a;font-size:13px;min-height:18px;margin-top:10px}}
</style></head>
<body><div class="box">
<h1>🔐 AI 语音聊天</h1>
<p>此网页需要访问密码<br>（密码在电脑启动窗口里查看）</p>
<input id="pwd" type="password" inputmode="numeric" autocomplete="current-password"
       placeholder="输入密码" enterkeyhint="go">
<button id="go" onclick="login()">进 入</button>
<div class="err" id="err"></div>
</div>
<script>
 async function login(){{
  var p=document.getElementById('pwd').value.trim();
  if(!p)return;
  var b=document.getElementById('go');b.disabled=true;
  try{{
   var r=await fetch('/api/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},
       body:JSON.stringify({{password:p}})}});
   if(r.ok){{location.href='/';return}}
   var j=await r.json().catch(()=>({{}}));
   document.getElementById('err').textContent=j.detail||'密码错误，请重试';
  }}catch(e){{document.getElementById('err').textContent='网络错误，请重试';}}
  b.disabled=false;
 }}
 document.getElementById('pwd').addEventListener('keydown',function(e){{if(e.key==='Enter')login();}});
 document.getElementById('pwd').focus();
</script></body></html>"""


def _register_login_routes(app: FastAPI) -> None:
    """口令验证路由：本机不用输；远端输对密码后种 cookie。"""

    @app.get("/login", response_class=HTMLResponse)
    async def _login_page():
        if not _access_token():
            return '<meta http-equiv="refresh" content="0;url=/">'
        return _login_html()

    @app.post("/api/login")
    async def _api_login(payload: dict):
        if not _access_token():
            return {"ok": True}
        if str((payload or {}).get("password", "") or "") == _access_token():
            resp = JSONResponse({"ok": True})
            resp.set_cookie(_ACCESS_COOKIE, _access_token(), max_age=30 * 24 * 3600,
                            httponly=True, samesite="lax", path="/")
            return resp
        return JSONResponse({"detail": "密码错误"}, status_code=401)


def _access_guard_middleware(app: FastAPI) -> None:
    """HTTP 门禁（密码锁）：
    1. 本机 127.0.0.1 直接放行；
    2. 其它任何设备都需输密码（登录页）；已登录（cookie 对）的直接放行。
    """

    @app.middleware("http")
    async def _gate(request, call_next):
        # 1) 本机放行
        if _request_is_local(request):
            return await call_next(request)

        tok = _access_token()
        if not tok:                      # 没设密码 = 不再限制
            return await call_next(request)
        path = request.url.path
        # 登录页 / 登录接口 放行
        if path in ("/login",) or (path == "/api/login" and request.method == "POST"):
            return await call_next(request)
        # 已登录（cookie 正确）放行
        if request.cookies.get(_ACCESS_COOKIE) == tok:
            return await call_next(request)
        # 未登录：网页/普通 GET 一律返回登录页，其它请求（POST/SSE…）返回 401
        if request.method == "GET":
            return HTMLResponse(_login_html(), status_code=200)
        return JSONResponse({"detail": "未授权，请先访问 /login"}, status_code=401)


def _register_ui_asset_routes(app: FastAPI) -> None:
    """界面样式 / 前端脚本：每次请求现读磁盘，所以改完刷新浏览器即可，不用重启服务。"""

    @app.get("/vc_ui.css")
    async def _ui_css():
        return Response(_UI_CSS_FILE.read_text(encoding="utf-8"),
                        media_type="text/css", headers={"Cache-Control": "no-store"})

    @app.get("/vc_ui.js")
    async def _ui_js():
        return Response(_UI_JS_FILE.read_text(encoding="utf-8"),
                        media_type="text/javascript", headers={"Cache-Control": "no-store"})

    @app.get("/vc_avatar/{which}")
    async def _avatar(which: str):
        """头像图片：每次现读配置 + 现读文件，换图刷新即生效。"""
        p = _avatar_path(which)
        if not p:
            return Response(status_code=404)
        return FileResponse(p, headers={"Cache-Control": "no-store"})

    @app.get("/vc_audio/{sid}/{turn}")
    async def _turn_audio(sid: str, turn: int):
        """某会话第 turn 轮回答的语音（界面上的 ▶ 按钮播它）。"""
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", sid or ""):
            return Response(status_code=404)      # 别让人用 ../ 之类的路径乱翻文件
        _c, M, A, _V = _conv_state(sid)
        if not (0 <= turn < len(M) and turn < len(A)):
            return Response(status_code=404)
        p = to_abs(A[turn]) if A[turn] else ""
        if not p or not os.path.isfile(p):
            return Response(status_code=404)
        return FileResponse(p, headers={"Cache-Control": "no-store"})


def _saved_theme_mode() -> str:
    """读出「设置」里保存的主题（light/dark/system），供 /call 页面初始化用。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            mode = str(((json.load(f).get("ui") or {}).get("theme")) or "system")
    except Exception:  # noqa: BLE001
        mode = "system"
    return mode if mode in ("light", "dark", "system") else "system"


def _register_call_routes(app: FastAPI) -> None:
    @app.get("/call", response_class=HTMLResponse)
    async def _call_page():
        # 把当前主题注入页面：浏览器 localStorage 里还没有值时（换端口/新设备/清过缓存），
        # 通话页也能用和主页面同一套配色。每次请求现读配置，改了主题刷新就生效。
        html = _CALL_PAGE.read_text(encoding="utf-8")
        inject = '<script>window.__VC_THEME__="%s";</script>\n</head>' % _saved_theme_mode()
        if "</head>" in html:
            html = html.replace("</head>", inject, 1)
        return HTMLResponse(html)

    @app.get("/api/call/memory")
    async def _call_memory():
        """返回实时通话的上下文记忆轮数（含简要前几轮）。"""
        n = len(_CALL_HISTORY)
        return {"turns": n, "recent": [{"u": u, "a": a[:40]} for u, a in _CALL_HISTORY[-5:]]}

    @app.post("/api/call/reset")
    async def _call_reset():
        global _CALL_HISTORY
        _CALL_HISTORY = []
        return {"turns": 0}

    @app.post("/api/turn")
    async def _api_turn(audio: UploadFile = File(None), text: str = Form(None)):
        audio_path = None
        if audio is not None and audio.filename:
            ext = audio.filename.rsplit(".", 1)[-1] or "webm"
            audio_path = str(_CALL_TMP / f"{uuid.uuid4().hex}.{ext}")
            with open(audio_path, "wb") as f:
                f.write(await audio.read())

        async def _gen():
            async for ev in _call_turn_gen(audio_path, text or ""):
                yield ev

        return StreamingResponse(_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- 主入口
def _lan_ip() -> str:
    """探测本机局域网 IP（供手机/同网设备访问）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        try:
            return socket.gethostbyname_ex(socket.gethostname())[2][0]
        except Exception:  # noqa: BLE001
            return ""
    finally:
        s.close()


def main():
    cfg = load_config()
    # 0) 访问口令：没设就生成一个 6 位数字并保存（本机 127.0.0.1 免输，手机/局域网要输）
    ui_cfg = cfg.setdefault("ui", {})
    if not str(ui_cfg.get("access_token", "") or "").strip():
        tok = f"{secrets.randbelow(1000000):06d}"
        ui_cfg["access_token"] = tok
        save_config(cfg)
    else:
        tok = str(ui_cfg["access_token"]).strip()
    # 1) 确保 TTS 服务在线
    try:
        ensure_tts_server(cfg)
        # 配置里写了权重就直接加载，别让用户还得再点一次「应用权重并重载」
        _vc = cfg.get("voice") or {}
        _g = str(_vc.get("gpt_weights") or "").strip()
        _s = str(_vc.get("sovits_weights") or "").strip()
        if _g or _s:
            print("[TTS] 按 chat_config.json 加载权重：%s / %s" % (_g or "(不变)", _s or "(不变)"))
            print("[TTS] 正在让 TTS 服务重新加载权重，通常需要 10~60 秒，请稍候……", flush=True)
            try:
                print("[TTS] 完成：" + switch_weights(cfg, _g, _s), flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[WARN] 配置里的权重没加载成功：{e}")
                print("       可在「设置 → 高级设置 → 应用权重并重载」里重试，或手动启动 api_v2.py")
        else:
            print("[WARN] 没有配置 GPT/SoVITS 权重 —— TTS 会用 api_v2.py 的默认底模，"
                  "音色不是你训练的那个。请在设置里选权重，或填 voice.gpt_weights / sovits_weights。")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] {e}\n仍会启动聊天界面，但语音合成前需先手动启动 TTS 服务。")
    # 2) 启动聊天界面（Gradio）+ 通话路由（同端口 /call 与 /api/turn）
    demo = build_ui(cfg)
    port = _as_int(cfg["ui"].get("port"), 27865)

    # 独立 FastAPI：先挂登录路由 + 通话路由，再加门禁，最后挂 Gradio 到根路径
    app = FastAPI(title="AI 语音聊天")
    _register_login_routes(app)
    _register_ui_asset_routes(app)
    _register_call_routes(app)
    try:
        demo.queue(default_concurrency_limit=4)
        gr.mount_gradio_app(app, demo, path="/")
    except Exception:  # noqa: BLE001
        print("[WARN] 无法合并 Gradio 与通话路由，退回纯 Gradio 启动…")
        demo.queue(default_concurrency_limit=4).launch(
            server_name="0.0.0.0", server_port=port, inbrowser=True, show_error=True)
        return
    _access_guard_middleware(app)
    # 是否开放给手机/局域网：由设置页「允许手机访问」开关决定（需重启生效）
    phone_on = bool(ui_cfg.get("phone_access", False))
    bind_host = "0.0.0.0" if phone_on else "127.0.0.1"
    # 端口被占用时别直接抛异常：自动往后找一个空的，并把怎么办说清楚
    if _port_in_use(bind_host, port):
        alt = _find_free_port(bind_host, port + 1)
        print("")
        print("=" * 64)
        print("[WARN] 端口 %d 已经被别的程序占用了" % port)
        print("-" * 64)
        print(" 常见占用的程序：RVC WebUI(7865)、GPT-SoVITS WebUI(9874)、")
        print("                UVR5(9873)、api_v2.py(9880)、ComfyUI(8188)、其它 gradio 应用(7860)")
        print(" 查是谁占的（在命令行里执行）：")
        print("   netstat -ano | findstr :%d" % port)
        print("   然后 tasklist /FI \"PID eq <上面最后一列的数字>\"")
        print("-" * 64)
        if alt:
            print(" 已自动改用 %d 启动（本次临时生效，配置不用改）。" % alt)
            print(" 想固定下来：设置 → 高级设置 → 网页端口 填 %d → 保存 → 重启。" % alt)
            port = alt
        else:
            print(" 附近 20 个端口都被占了，请手动换一个不常用的端口（建议 10000~60000）：")
            print(" 设置 → 高级设置 → 网页端口，或改 chat_config.json 的 ui.port")
            print("=" * 64)
            return
        print("=" * 64)
        print("")
    lan = _lan_ip() if phone_on else ""
    print(f"🎙️  AI 语音聊天已启动: http://127.0.0.1:{port}   （实时通话：http://127.0.0.1:{port}/call）")
    if phone_on:
        print(f"📶  已开启手机/局域网访问（监听全部网卡）")
        print(f"🔐  访问密码: {tok}   （本机打开免输；手机/同网设备第一次打开需输入）")
        if lan and lan != "127.0.0.1":
            print(f"📱  手机/同网设备请访问: http://{lan}:{port}  → 输上面的密码进入")
    else:
        print("🔒  仅本机访问（手机/局域网访问已关闭，端口不对外监听）")
        print("    如需手机连接，请在「设置 → 高级设置 → 允许手机/局域网访问」勾选并保存后重启本服务。")
    uvicorn.run(app, host=bind_host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
