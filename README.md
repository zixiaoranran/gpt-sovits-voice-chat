# GPT-SoVITS Voice Chat WebUI

基于 GPT-SoVITS 的本地语音聊天 Web 界面，提供大语言模型对话、语音合成、情绪切换与实时对讲四项能力。

本程序不包含模型权重与音频素材，也不执行模型推理。对话由使用者配置的 OpenAI 兼容接口完成；语音由同目录下 GPT-SoVITS 提供的 `api_v2.py` 服务合成。部署时需将本目录置于 GPT-SoVITS 根目录（与 `api_v2.py` 同级），程序以自身所在目录定位全部资源。

## 功能

- **多会话对话**：新建、重命名、置顶、删除；单条回复可重新生成并在多个版本间切换。
- **长程记忆**：超出上下文窗口的早期对话自动压缩为摘要，供后续轮次引用。
- **语音合成**：AI 回复自动朗读；另设「输入即朗读」页，可粘贴长文整段合成，不写入会话记录。
- **情绪切换**：回复前判断语调，切换至对应情绪的参考音频。共十种情绪，每种可独立配置参考音频、参考文本、语速与采样参数，并支持逐项试听。
- **实时对讲**：`/call` 页面按住说话，录音经识别后交由模型回复并语音播报（需可选识别依赖）。
- **接口兼容**：适用于任何 OpenAI 兼容接口（DeepSeek、OpenAI、第三方中转、本地 Ollama 等），内置连通性测试。
- **凭据保护**：API Key 仅存储于本机配置文件，界面与浏览器端配置中仅呈现掩码，修改需在独立弹窗内完成。
- **访问控制**：默认仅监听 `127.0.0.1`；可选开启局域网访问并设置访问口令。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11（已在整合包环境验证） |
| Python | 3.9 及以上；推荐直接使用整合包自带的 `runtime\python.exe` |
| GPT-SoVITS | v2、v2Pro、v2ProPlus、v3、v4、v5（均提供 `api_v2.py`）。v1 使用 `api.py`，接口不兼容 |
| 模型资产 | 自行训练的 GPT 权重（`.ckpt`）、SoVITS 权重（`.pth`）、3~10 秒参考音频及其原文 |
| 大语言模型 | 任一 OpenAI 兼容接口及其 API Key |
| FFmpeg | 需置于 `runtime\ffmpeg.exe` 或系统 PATH（对讲录音转码使用） |

权重架构须与所安装的 GPT-SoVITS 版本一致；跨版本混用将导致 `api_v2.py` 加载失败。

## 安装

将本仓库全部文件复制至 GPT-SoVITS 根目录，双击 `start.bat`，选择 `[1] Deploy and start`。

该入口依次执行：切换至整合包自带解释器、检查并安装缺失依赖、扫描可用权重与参考音频并选中最新项、请求输入 API Key、写入 `chat_config.json`、启动服务。默认访问地址为 `http://127.0.0.1:27865/`。

非交互方式：

```bat
runtime\python.exe deploy.py --yes --api-key sk-xxxx
```

仅检查环境，不安装依赖、不写入文件：

```bat
runtime\python.exe deploy.py --check-only
```

依赖亦可单独安装。`install_deps.py --asr` 会附加安装实时对讲所需的语音识别依赖（约 2 GB；首次识别另需下载约 1 GB 中文模型）。

<details>
<summary>手动安装依赖</summary>

```bat
runtime\python.exe -m pip install -r requirements.txt
runtime\python.exe -m pip install -r requirements-asr.txt
```

国内网络可通过 `-i https://pypi.tuna.tsinghua.edu.cn/simple` 指定镜像。

</details>

## 配置

配置集中于 `chat_config.json`。文件不存在时程序按模板生成，其中参考音频为空：界面可正常打开，但朗读时会提示缺少参考音频。

| 字段 | 说明 |
| --- | --- |
| `llm.api_key` / `base_url` / `model` | 接口地址、模型名与密钥。`base_url` 支持填写至 `/v1` 或直接填写 `/chat/completions` |
| `persona.system_prompt` | 角色人设；留空时使用通用助手 |
| `voice.gpt_weights` / `sovits_weights` | 权重路径，支持相对根目录或绝对路径 |
| `voice.ref_audio_path` / `prompt_text` | 参考音频及其原文，两者须逐字一致，否则音色不稳定 |
| `voice.speed_factor` | 语速系数，`1.0` 为原速。开心、难过、害怕三种情绪使用各自语速 |
| `voice.top_k` / `top_p` / `gpt_temperature` / `repetition_penalty` | 采样参数；数值越大语气越丰富，同时误读概率上升 |
| `voice.seed` | `-1` 为每次随机；填写固定整数可使同一文本的合成结果保持一致 |
| `voice.tts_api_url` | TTS 服务地址，默认 `http://127.0.0.1:9880` |
| `emotions` | 十种情绪，各自独立的参考音频、参考文本与采样参数 |
| `history.keep_turns` | 保留的最近对话轮数，默认 8 |
| `ui.port` / `phone_access` / `access_token` / `theme` | 服务端口、局域网访问开关、访问口令、界面主题 |

最小可用配置：

```json
{
  "llm": {
    "api_key": "sk-xxxx",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash"
  },
  "voice": {
    "gpt_weights": "GPT_weights_v2Pro/myvoice-e10.ckpt",
    "sovits_weights": "SoVITS_weights_v2Pro/myvoice_e4_s108.pth",
    "ref_audio_path": "refs/ref_01.wav",
    "prompt_text": "参考音频对应的原文，须逐字一致"
  }
}
```

未填写的字段使用默认值。绝大部分配置项亦可在界面「设置」中修改。

## 使用

界面由五个标签页组成。

**对话**：主界面。支持多会话管理；单条回复可重新生成并保留多个版本；上下文过长时自动压缩为摘要。

**实时通话**：按住说话的对讲页，依次完成录音、识别、回复与播报，需安装语音识别依赖。

**设置**：分为全局默认与会话覆盖两层。AI 接口相关配置在弹窗内修改，日常仅显示掩码。高级设置包含模型权重、参考音频、切分方式与采样参数。情绪微调位于页面下方，每种情绪可独立配置并试听。

**输入即朗读**：粘贴文本直接合成，不写入会话历史。

**回放历史**：列出当前会话的全部语音，可逐条重放。

## 端口

Web 服务默认使用 **27865**。未采用 7865，原因是同一整合包内 RVC WebUI 默认占用该端口。

TTS 服务使用 9880，由 `api_v2.py` 提供，通常无需修改。

端口被占用时程序不会异常退出，将自动向后查找空闲端口并在控制台说明情况：

```
[WARN] 端口 27865 已经被别的程序占用了
       查是谁占的：netstat -ano | findstr :27865
       已自动改用 27866 启动（本次临时生效，配置不用改）
```

如需固定端口，可在「设置 → 高级设置 → 网页端口」中修改并重启服务。

## 故障排查

建议先执行 `runtime\python.exe deploy.py --check-only`，该命令会输出目录检测结果、依赖状态、FFmpeg 可用性、权重与参考音频扫描数量，以及配置缺失项。

**启动阶段**

| 现象 | 处理方法 |
| --- | --- |
| 窗口闪退，或报 `ModuleNotFoundError: gradio` | 使用了错误的 Python 解释器。通过 `start.bat` 启动可自动切换；或直接执行 `runtime\python.exe voice_chat.py` |
| 报 `httpx.InvalidURL: Invalid port: ':1]'` | 代理工具将 `NO_PROXY` 设置为含 `::1` 或 `[::1]` 的形式。程序启动时会自动清洗；若仍报错，先执行 `set NO_PROXY=localhost,127.0.0.1` |
| 提示当前目录不是 GPT-SoVITS 根目录 | 目录层级不正确，需与 `api_v2.py` 同级 |
| 端口已被占用 | 程序自动顺延；如需固定，修改 `ui.port` |
| 提示 gradio 版本不符 | 执行 `runtime\python.exe install_deps.py --force` |

**语音合成**

| 现象 | 处理方法 |
| --- | --- |
| 提示缺少可用的参考音频 | 在设置中填写参考音频路径 |
| TTS 返回 400 且提示 `tts failed ... not exists` | 路径解析错误。相对路径基于 GPT-SoVITS 根目录解析，建议改用绝对路径或置于 `refs/` |
| 合成音色与训练音色不符 | 权重选择有误，或 `prompt_text` 与音频内容不一致 |
| 语句中断、气息不足 | 将切分方式改为 `cut5` |
| 同一文本每次合成结果不同 | `seed` 为 `-1`，改为固定整数 |
| 提示 TTS 服务启动超时 | 查看 `tts_server.log`；手动执行 `runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880` 可获取具体错误 |
| 切换权重失败 | 权重架构与当前 GPT-SoVITS 版本不匹配 |
| 情绪切换无效果 | 对应情绪未配置参考音频，已回落至默认音色 |

**对话与网络**

| 现象 | 处理方法 |
| --- | --- |
| 返回 401 / 403 | 密钥无效。在「修改 API 接口」中重填并执行「测试连通」 |
| 模型名不在 `/models` 列表中 | 部分接口使用模型别名，不会出现在列表中。以「试发对话」是否成功为准 |
| 长时间停留在「AI 思考中」 | 接口地址错误或网络不通，执行「测试连通」定位 |
| 麦克风不可用 | 页面须通过 `localhost` 或 HTTPS 访问，否则浏览器不授予权限 |
| 局域网设备无法访问 | 勾选「允许手机/局域网访问」后保存并重启；设备需处于同一网络，并使用主机 IP 访问 |
| 修改配置未生效 | 文件保存为 ANSI 编码，或所改项（端口、局域网访问）需重启服务 |

## 目录结构

```
voice_chat.py             主程序（Gradio 界面与 FastAPI 路由）
start.bat                 交互式启动入口
deploy.py                 一键部署：依赖检查、资源配置、启动服务
install_deps.py           依赖安装
install_deps.bat / .sh    依赖安装的 Windows / Unix 包装
ui.css / ui.js            前端样式与脚本（服务端按请求读取，修改后刷新即可）
call_page.html            实时对讲页面
chat_config.example.json  配置模板
refs/                     参考音频存放目录
requirements*.txt         依赖清单
```

运行后生成且不纳入版本控制的文件与目录：`chat_config.json`、`conversations/`、`output/voice_chat/`、`logs/`、`tts_server.log`。

## 版本兼容性

程序本身不执行模型推理，仅调用同目录的 `api_v2.py`，因此适用于所有提供该接口的 GPT-SoVITS 版本。

权重扫描不预设版本号：`GPT_weights*`、`SoVITS_weights*` 与 `logs/` 下的权重全部列出。官方底模按命名规则识别（GPT 为 `s1*.ckpt`，SoVITS 为 `s2*.pth`），并排除 `vocoder` 及 `sv/` 目录下的非 TTS 权重。v2Pro 系列在列表中优先排序。

## 隐私与安全

API Key 仅保存在本机 `chat_config.json`，界面与浏览器端配置中只呈现掩码。会话记录与合成音频分别存放于 `conversations/` 与 `output/`，均由 `.gitignore` 排除。程序仅访问使用者配置的模型接口与本机 TTS 服务。默认监听 `127.0.0.1`，未开启局域网访问时外部设备无法接入。

## 许可证

MIT，与上游 GPT-SoVITS 一致。

音色模型与参考音频由使用者自行提供并承担相应版权责任。影视与游戏角色的语音素材通常受版权保护，不应随本项目分发，亦不得用于冒充真人。

## 致谢

[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)、[Gradio](https://github.com/gradio-app/gradio)、[FunASR](https://github.com/modelscope/FunASR)、[FFmpeg](https://ffmpeg.org/)。
