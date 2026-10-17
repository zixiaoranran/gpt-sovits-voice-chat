# AI 语音聊天 WebUI（GPT-SoVITS）

一个跑在**本地**的 AI 语音聊天网页：文字对话 → 大模型出回复 → 用**你自己训练的 GPT-SoVITS 音色**念出来，
还能按住说话实时对讲、按情绪切换语气、手机/局域网访问。

> **本项目只包含「网页 + 交互层」**，不含任何模型权重、参考音频、人声音色或个人配置。
> 你用自己的 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)（v2 之后带 `api_v2.py` 的版本都行，实测 v2Pro）训练出音色后，把本项目放进它的根目录即可运行。

## 最快路径（一键部署）

已经有 GPT-SoVITS（任一版本）+ 自己训练好的音色？只需要三步：

```text
1. 把本项目全部文件放进 GPT-SoVITS 根目录（与 api_v2.py 同级）
2. 双击 start.bat
3. 选 [1] Deploy and start
```

`start.bat` 会自动做完这些事：

- 切到整合包自带的 `runtime\python.exe`（避免装错环境）
- 检查依赖，缺就自动装（已装则跳过，不会动你的 torch）
- **自动扫出你的 GPT / SoVITS 权重**，挑最新的帮你选中（v2Pro 优先）
- **自动扫出可用的参考音频**（训练切片 → `refs/` → 训练集目录），能连原文一起读
- 问一次 API Key（隐私内容，只写进本机 `chat_config.json`）
- 启动网页并打印地址（默认 **http://127.0.0.1:27865/** ）

想全自动（不提问）：

```bat
runtime\python.exe deploy.py --yes --api-key sk-你的密钥
```

只体检不改任何东西：

```bat
runtime\python.exe deploy.py --check-only
```

> 说明：装完依赖、跑完向导之后**不需要再改任何文件**，剩下的设置都能在网页里点。

## 别人拿到这个项目，完整流程长什么样

```text
① 看到项目 → 读这份 README
      ↓
② 没有 GPT-SoVITS 本体？
   去下 Windows 整合包（第三章有链接），解压，双击 go-webui.bat 确认能跑
      ↓
③ 有一个自己的音色了吗？
   没有 → 用整合包的 WebUI 训练：切片 → ASR → 标注 → 训练（约 1 分钟素材即可）
   有   → 确认产出在 GPT_weights_v2Pro/ 与 SoVITS_weights_v2Pro/
      ↓
④ 下载本项目（git clone 或 下载 zip 解压）
   把全部文件放进 GPT-SoVITS 根目录（与 api_v2.py 同级）
      ↓
⑤ 双击 start.bat → 选 [1] Deploy and start
   它自动：切对 python → 查/装依赖 → 认出权重与参考音频 → 问一次 API Key → 写配置 → 启动
      ↓
⑥ 浏览器打开 http://127.0.0.1:27865/
   发一句话 → 听到你自己音色的回复
```

**每一步会遇到什么、怎么判断成没成：**

| 步骤 | 成功标志 | 出错会怎样 |
|---|---|---|
| ② 装 GPT-SoVITS | `go-webui.bat` 能打开它的 WebUI | 后面全白搭 —— 这一步没法绕过 |
| ③ 训练音色 | `GPT_weights_v2Pro/` 里有 `.ckpt`、`SoVITS_weights_v2Pro/` 里有 `.pth` | 网页能开但没声音 / 只有官方底模音色 |
| ④ 放对位置 | `voice_chat.py` 和 `api_v2.py` 在同一层 | `deploy.py` 会红字提示「不在这里」 |
| ⑤ 一键部署 | 打印 `服务就绪: http://127.0.0.1:9880` 和网页地址 | 缺依赖会自动装；端口被占会提示改端口 |
| ⑥ 听到声音 | AI 回一句话并有语音播放 | 没 API Key → 报错提示去设置页填；没参考音频 → 提示指定 |

**最少要自己动手的两件事**（隐私/素材原因，程序没法替你决定）：

1. **API Key**：任何 OpenAI 兼容接口的密钥，向导里会问一次
2. **参考音频对应的原文** `prompt_text`：如果音频在 `logs/<实验>/5-wav32k/` 下，程序能自动从
   `2-name2text.txt` 读出来；在别处就要你手填那句话的原文

## 版本兼容性

**不是死要求**。本项目自己不碰模型推理，它只是调用同目录 GPT-SoVITS 的 `api_v2.py`，
所以 **v2 / v2Pro / v2ProPlus / v3 / v4 / v5 都能跑**（这些版本都带 `api_v2.py`；本项目实测 v2Pro）。
> v1 时代用的是 `api.py`（不是 `api_v2.py`），接口不完全一样，本项目的自动拉起和请求字段不保证兼容 ——
> 真要跑 v1 请自己先把 TTS 服务起在 `127.0.0.1:9880`。

真正有约束的只有一条，而且来自 GPT-SoVITS 本身：

> **权重架构必须和你装的版本一致** —— 拿 v4 的底模去配 v2Pro 的代码，`api_v2.py` 会加载失败。

本项目做的事：

- 权重扫描**不写死版本**：`GPT_weights*` / `SoVITS_weights*` / `logs/` 全扫（v1~v5 的目录名都认）
- 官方底模按**命名规律**识别，同样跨版本：
  - GPT 底模 = `s1*.ckpt`（`s1bert25hz-2kh…`、`s1v3`、v2final 的 `s1bert25hz-5kh…`）
  - SoVITS 底模 = `s2*.pth`（`s2G488k`、`s2Gv3`、`s2Gv2ProPlus`、`s2Gv4`、`s2D2333k`…）
  - 自动排除 `vocoder*.pth`（声码器）和 `sv/` 下的声纹模型（选错必炸）
- 界面里 v2Pro 系的排最前面（整合包默认就是它）

所以：**你自己的训练权重永远会被列出来**，跟版本无关；底模列出来的范围也是跨版本的。
唯一要你自己注意的是「别把不同版本的权重混着选」。

---

## 目录

- [一、功能作用](#一功能作用)
- [二、环境依赖](#二环境依赖)
- [三、下载与安装位置](#三下载与安装位置)
- [四、配置要求](#四配置要求)
- [五、启动与首次运行](#五启动与首次运行)
- [六、端口设置](#六端口设置)
- [七、常见错误与解决办法](#七常见错误与解决办法)
- [八、界面说明](#八界面说明)
- [九、目录结构](#九目录结构)
- [十、隐私与安全](#十隐私与安全)
- [十一、许可证与致谢](#十一许可证与致谢)

---

## 一、功能作用

### 1. 网页对话（核心）

- 多会话：新建 / 重命名 / 置顶 / 删除（删除二次确认），历史存本地
- 每条回复可「重新加载」（换个说法重答）、多版本来回切换
- **长程记忆**：超出上下文窗口的旧对话会被自动压缩成一段记忆摘要，聊几十轮还记得开头说过什么

### 2. 语音合成（GPT-SoVITS）

- AI 回复自动转语音播放；也可点「🔊 仅测试当前音色」对比不同权重
- 「输入即朗读」标签页：粘一段长文，直接整段念完
- 「回放历史」：本次会话所有语音都能翻出来重听

### 3. 情绪系统（10 种情绪）

- AI 回复前先判断语调，再用**该情绪自己的参考音频**朗读 —— 说伤心话真的会变低沉
- 情绪：`开心 俏皮 疑惑 安抚 难过 认真 傲娇 害怕 害羞 亲密`（近义词会自动归类）
- 「🎭 情绪微调」里每种情绪可单独设：参考音频、参考文本、语速、`gpt_temperature`、`top_k`、`top_p`、`repetition_penalty`
- 每种情绪都能**当场试听**：先播参考音频，再用当前参数合成一句真听效果，满意再保存

### 4. 实时对讲 `/call`（可选，需 ASR 依赖）

按住说话 → 语音识别 → AI 回复 → 语音播报；TA 说话时按住可抢话打断。

### 5. 接口与安全

- **任何 OpenAI 兼容接口都能用**：DeepSeek / OpenAI / 中转站 / 本地 Ollama / vLLM / LM Studio
- 「🔌 测试连通」：自动拉取该接口的模型列表、试发一句话验证能不能出字
- **API Key 只显示掩码**（`sk-••••••••abcd`），要改密钥在弹窗里输入，只写入本机 `chat_config.json`

### 6. 其他

- 深浅主题 / 跟随系统；侧栏可收起（记在浏览器本地）
- 手机 / 局域网访问（可选，带访问口令；本机免输）
- 模型权重热切换：不用重启，从界面换 GPT / SoVITS 权重

---

## 二、环境依赖

### 1. 操作系统与 Python

| 项 | 要求 |
| --- | --- |
| 系统 | **Windows 10/11 实测可用**；Linux / macOS 理论可行（需自行准备 ffmpeg） |
| Python | **3.9+**（本项目实测 3.9.13 64-bit；上游 GPT-SoVITS 官方推荐 3.10~3.12） |
| 内存 / 显存 | 网页层几乎不吃资源；真正吃显存的是 GPT-SoVITS 推理（建议 6 GB 显存以上） |
| 浏览器 | Chrome / Edge 等现代浏览器；**用麦克风必须 `localhost` 或 HTTPS** |

### 2. 前置：GPT-SoVITS 本体（**必须**）

本项目**不能独立运行**，它依赖同目录下的 GPT-SoVITS：

- 需要能跑起 `api_v2.py`（TTS HTTP 服务，默认 `127.0.0.1:9880`）
- 需要 `GPT_SoVITS/` 推理代码与 `GPT_SoVITS/pretrained_models/` 预训练模型
- 需要你自己的 **GPT 权重（`.ckpt`）+ SoVITS 权重（`.pth`）+ 一段参考音频**

**版本要求**：只要能跑起 `api_v2.py` 即可 —— v2 / v2Pro / v2ProPlus / v3 / v4 / v5 都满足（实测 v2Pro）；v1 用的是 `api.py`，不保证兼容。
唯一约束是「权重架构要和安装的版本一致」—— 详见上面的[版本兼容性](#版本兼容性)一节。

### 3. Python 依赖（不用手动装）

**核心（文字对话 + 语音合成必需）** —— 见 `requirements.txt`，版本是实测可用的组合：

```
gradio==4.44.1        # 网页 UI（★ 必须锁这个版本，高版本 API 变了会跑不起来）
gradio_client==1.3.0
fastapi==0.112.1      # /call 与自定义路由
starlette==0.38.2
uvicorn==0.20.0
httpx==0.27.0
numpy==1.23.4
requests==2.32.3
```

**可选（实时对讲 `/call`、语音输入）** —— 见 `requirements-asr.txt`，约 **2 GB**，
首次运行还会从 ModelScope 下载中文识别模型约 **1 GB**：

```
funasr==1.0.27
torch==2.0.0
torchaudio==2.0.0
```

> 这些**不用你手动 pip**：`start.bat` / `deploy.py` / `install_deps.py` 会自动装，
> 并且会跳过已满足的包、拒绝覆盖整合包自带的 torch（cu118 版换成 CPU 版会让 GPU 失效）。
> 不装 ASR 依赖也完全不影响文字对话和语音朗读。


### 4. 外部程序

| 程序 | 说明 | 放哪 |
| --- | --- | --- |
| **ffmpeg** | 音频转码（对讲录音转 16k 单声道等）。程序先找 `<GPT-SoVITS>/runtime/ffmpeg.exe`，找不到就用系统 PATH 里的 `ffmpeg` | `runtime/ffmpeg.exe` 或加入 PATH |
| **大模型接口** | 任选一家 OpenAI 兼容服务，需要 API Key | 填在设置页，不需要装东西 |

---

## 三、下载与安装位置

### 1. 下载 GPT-SoVITS 本体

| 用途 | 地址 |
| --- | --- |
| 源码仓库（官方） | https://github.com/RVC-Boss/GPT-SoVITS |
| Windows 整合包（含 Python 运行时，**推荐**） | https://github.com/RVC-Boss/GPT-SoVITS/releases |
| Windows 整合包直链（HF） | https://huggingface.co/lj1995/GPT-SoVITS-windows-package |
| 国内加速（语雀指引 / ModelScope） | https://www.yuque.com/baicaigongchang1145haoyuangong/ib3g1e |

整合包解压后的目录里就带 `runtime\python.exe`、`api_v2.py`、`GPT_SoVITS\`，
**后面的命令统一用它自带的 python，不要用系统 python**。

### 2. 下载预训练模型（官方底模）

| 模型 | 下载位置 | 放到哪 |
| --- | --- | --- |
| 通用预训练模型（含 v1/v2/v3/v4/v5） | https://huggingface.co/lj1995/GPT-SoVITS | `GPT_SoVITS/pretrained_models/` |
| **v2Pro 系（本项目用的）**：`v2Pro/s2Gv2Pro.pth`、`s2Gv2ProPlus.pth`、`s2Dv2Pro.pth`、`s2Dv2ProPlus.pth`、`sv/pretrained_eres2netv2w24s4ep4.ckpt` | 同上仓库的 `v2Pro/` 目录 | `GPT_SoVITS/pretrained_models/`（保留 `v2Pro/`、`sv/` 子目录） |
| GPT 底模 `s1v3.ckpt` | 同上仓库根目录 | `GPT_SoVITS/pretrained_models/` |
| 中文 TTS 额外需要 G2PW 模型 | https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained （或 ModelScope 同名） | 解压改名 `G2PWModel` → `GPT_SoVITS/text/` |
| 国内镜像 | 把上面网址里的 `huggingface.co` 换成 `hf-mirror.com` | — |

### 3. 你自己的训练产物（要放到这三个地方）

| 产物 | 位置 | 命名示例 |
| --- | --- | --- |
| **GPT 权重**（`.ckpt`，语音生成器） | `GPT_weights_v2Pro/` | `myvoice-e10.ckpt` |
| **SoVITS 权重**（`.pth`，声码器） | `SoVITS_weights_v2Pro/` | `myvoice_e4_s108.pth` |
| **参考音频**（3~10 秒一整句，无 BGM） | 建议放 `refs/`（刷新网页后自动出现在下拉里） | `ref_01.wav` |

> 训练用 GPT-SoVITS 自带的 WebUI：`go-webui.bat` → `1-GPT-SoVITS-TTS` → 填音频路径 → 切片 → ASR → 标注 → 训练。
> 训练完权重会按所选版本自动写进 `GPT_weights_<版本>/`、`SoVITS_weights_<版本>/`。

### 4. 安装本项目

```text
<GPT-SoVITS 根目录>\
├── api_v2.py
├── GPT_SoVITS\
├── GPT_weights_v2Pro\
├── SoVITS_weights_v2Pro\
├── runtime\python.exe
├── refs\                  ← 参考音频丢这里
└── voice_chat.py          ← 本项目（必须与 api_v2.py 同级）
    ui.css / ui.js / call_page.html / chat_config.json ...
```

本项目用「**自身所在目录**」定位一切资源，所以**必须放在 GPT-SoVITS 根目录下**（与 `api_v2.py` 同级）；
放在子目录里也能跑，但那样它就只会在自己的目录里找权重和参考音频。

### 5. 装依赖（一键）

**Windows：双击 `install_deps.bat` 即可**（自动挑整合包自带的 `runtime\python.exe`，装完停在窗口里给你看结果）。

命令行用法（Windows 用 `runtime\python.exe`，macOS/Linux 用 `python3`）：

```bat
install_deps.bat                    :: 只装核心依赖（文字对话 + 语音合成）
install_deps.bat --asr              :: 连「实时对讲 / 语音输入」一起装（约 2GB）
install_deps.bat --check            :: 只体检，不装任何东西
install_deps.bat --mirror tsinghua  :: 用国内镜像（aliyun / ustc / tencent / huawei）
install_deps.bat --force            :: 强制重装核心依赖（版本被别的东西改坏时）
```

```bash
bash install_deps.sh --asr --mirror tsinghua     # macOS / Linux
```

脚本会做这些事，而且**可以反复跑**：

1. 自动切换到整合包自带的 `runtime\python.exe`（否则依赖会装到别的环境里，最常见的一类「装完还是报错」）
2. 逐个比对版本，**已经是目标版本的包直接跳过**，不重复下载
3. **绝不覆盖 GPT-SoVITS 自带的 torch**（整合包里是 cu118 版，从 PyPI 装 ASR 依赖会把它换成 CPU 版，GPU 就废了）
4. 体检 `ffmpeg`、GPT/SoVITS 权重、预训练模型、参考音频，缺什么直接告诉你去哪补
5. 没有 `chat_config.json` 时从模板生成一份（已存在则不覆盖）

<details>
<summary>手动安装（不想用脚本时）</summary>

```bat
runtime\python.exe -m pip install -r requirements.txt
runtime\python.exe -m pip install -r requirements-asr.txt     :: 可选

:: 国内加速
runtime\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```
</details>


---

## 四、配置要求

### 1. 生成配置文件

```bat
copy chat_config.example.json chat_config.json
```

> ★ 程序**只读 `chat_config.json`**，不会自动使用 example。文件不存在时会自动生成一份
> （所有参考音频为空），页面能开但点朗读会提示「没有可用的参考音频」——这是预期行为。

配置文件是**本机明文 JSON**，全部内容都不会上传到任何地方。

### 2. 字段说明

| 段 | 字段 | 必填 | 说明 |
| --- | --- | --- | --- |
| `llm` | `api_key` | ✅ | 你的接口密钥。**界面上只显示掩码**，改它点「✏️ 修改 API 接口」 |
| `llm` | `base_url` | ✅ | OpenAI 兼容地址，如 `https://api.deepseek.com`；填到 `/v1` 或直接贴 `/chat/completions` 都能识别 |
| `llm` | `model` | ✅ | 模型名。点「🔌 测试连通」可自动拉取该接口的模型列表 |
| `llm` | `temperature` / `max_tokens` | ⬜ | 默认 1.2 / 1024 |
| `llm` | `max_reply_sents` / `max_reply_chars` | ⬜ | 每轮回复的句数 / 字数上限（语音按句分段念） |
| `persona` | `system_prompt` | ⬜ | 角色人设。留空则用通用助手 |
| `voice` | `gpt_weights` | ✅ | GPT 权重路径（相对 GPT-SoVITS 根目录，或绝对路径） |
| `voice` | `sovits_weights` | ⬜ | SoVITS 权重路径；留空则用 TTS 服务当前已加载的 |
| `voice` | `ref_audio_path` | ✅ | 默认参考音频（相对根目录或绝对路径） |
| `voice` | `prompt_text` | ✅ | **参考音频里那句话的原文**，必须逐字一致，否则音色会飘 |
| `voice` | `prompt_lang` / `text_lang` | ⬜ | 参考音频语言 / 朗读语言（`zh` `all_zh` `en` `ja` `ko` `yue` `auto`） |
| `voice` | `text_split_method` | ⬜ | 切分方式，默认 `cut5` |
| `voice` | `speed_factor` | ⬜ | 语速，1.0 = 原速（`开心`/`难过`/`害怕` 用各自情绪语速） |
| `voice` | `top_k` / `top_p` / `gpt_temperature` / `repetition_penalty` | ⬜ | 采样参数，越大越有表现力也越容易口胡 |
| `voice` | `seed` | ⬜ | `-1` = 每次随机；填固定数字则同一句永远读得一样 |
| `voice` | `tts_api_url` | ⬜ | 默认 `http://127.0.0.1:9880` |
| `emotions` | `开心` … `亲密` | ⬜ | 10 种情绪各一套 `ref_audio_path` / `prompt_text` / 采样参数 |
| `history` | `keep_turns` | ⬜ | 记住最近几轮对话，默认 8 |
| `ui` | `port` | ⬜ | 网页端口，默认 **`27865`**（刻意挑了不常用的段，避开同包里 RVC 的 7865） |
| `ui` | `phone_access` | ⬜ | `true` = 监听全部网卡，允许手机/局域网访问（改后需重启） |
| `ui` | `access_token` | ⬜ | 手机访问口令（开启手机访问时自动生成） |
| `ui` | `theme` | ⬜ | `light` / `dark` / `system` |

### 3. 最小可用配置（示例）

```json
{
  "llm": {
    "api_key": "sk-你的密钥",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash"
  },
  "voice": {
    "gpt_weights": "GPT_weights_v2Pro/myvoice-e10.ckpt",
    "sovits_weights": "SoVITS_weights_v2Pro/myvoice_e4_s108.pth",
    "ref_audio_path": "refs/ref_01.wav",
    "prompt_text": "这段参考音频里说的原话，一字不差"
  }
}
```

其余字段留空会自动用默认值。

### 4. 配置小贴士

- **参考音频选择**：3~10 秒、一整句完整的话、语气自然、无背景音乐。太短音色会飘，太长合成变慢
- **`prompt_text` 必须是那段音频的原话**，这是 GPT-SoVITS 的机制要求，不是可选项
- 用记事本改 JSON 记得**存成 UTF-8**（程序已兼容带 BOM 的 UTF-8，但别用 ANSI）
- 大部分项都能在网页「设置」里改，不用手改文件；手改后重启生效

---

## 五、启动与首次运行

```bat
:: 在 GPT-SoVITS 根目录下
runtime\python.exe voice_chat.py
```

打开 **http://127.0.0.1:27865/**（端口可在设置里改，改完需重启）。

启动时会自动尝试拉起 TTS 后端 `api_v2.py -a 127.0.0.1 -p 9880`。

### 首次运行会发生什么

| 现象 | 原因 / 处理 |
| --- | --- |
| 页面能开，点朗读提示「没有可用的参考音频」 | 正常：还没配 `voice.ref_audio_path`，去设置里填 |
| TTS 没起来 | 手动跑 `runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880`，然后刷新页面 |
| 「实时通话」用不了麦克风 | 浏览器要求 `localhost` 或 HTTPS；且需要装 `requirements-asr.txt` |
| 首次语音识别卡很久 | 正常：要从 ModelScope 下载约 1 GB 中文识别模型，只需一次 |
| 端口被占用 | **程序会自动往后挪一个空闲端口**并在控制台告诉你；想固定就改 `ui.port` |

---

## 六、端口设置

本项目只有两个端口：

| 端口 | 谁用 | 默认 | 在哪改 |
| --- | --- | --- | --- |
| **网页端口** | 你在浏览器打开的那个页面 | **27865** | 设置 → 高级设置 → 网页端口，或 `chat_config.json` 的 `ui.port` |
| TTS 端口 | 内部调用 `api_v2.py` | 9880 | `chat_config.json` 的 `voice.tts_api_url` |

**为什么默认是 27865 而不是常见的 7865**：7865 被同包的 **RVC WebUI 占了默认值**，一起开必然撞车；
27865 在 20000~30000 这个基本没人用的段里，还记得住。

**改完要重启**网页服务才生效（端口是在启动时绑定的）。

### 端口冲突会怎样

不用怕，程序**不会直接崩**，会自动往后找一个空闲端口并告诉你：

```
================================================================
[WARN] 端口 27865 已经被别的程序占用了
----------------------------------------------------------------
 常见占用的程序：RVC WebUI(7865)、GPT-SoVITS WebUI(9874)、
                UVR5(9873)、api_v2.py(9880)、ComfyUI(8188)、其它 gradio 应用(7860)
 查是谁占的（在命令行里执行）：
   netstat -ano | findstr :27865
   然后 tasklist /FI "PID eq <上面最后一列的数字>"
----------------------------------------------------------------
 已自动改用 27866 启动（本次临时生效，配置不用改）。
 想固定下来：设置 → 高级设置 → 网页端口 填 27866 → 保存 → 重启。
================================================================
```

### 想手动排查端口

```powershell
netstat -ano | findstr :27865          # 最后一列是 PID
Get-NetTCPConnection -LocalPort 27865  # 或 PowerShell 写法
tasklist /FI "PID eq 12345"            # 看是哪个程序
taskkill /PID 12345 /F                 # 确认能关再关
```

选端口的原则：**1024~65535 之间**，优先 **10000~60000** 里不常用的；避开上表那几个。

---

## 七、常见错误与解决办法

### 启动类

| 症状 | 原因 | 解决 |
| --- | --- | --- |
| 双击 `start.bat` 窗口一闪就没了 / `ModuleNotFoundError: gradio` | 用错了 Python（电脑上有多个） | **双击 `start.bat` 选 [1]** 它会自动用对解释器；或手动 `runtime\python.exe voice_chat.py` |
| `httpx.InvalidURL: Invalid port: ':1]'`（看着跟端口无关） | 代理工具把 `NO_PROXY` 写成 `...::1,[::1]` | 已自动清洗；还报就执行 `set NO_PROXY=localhost,127.0.0.1` 再启动 |
| `[FAIL] 当前目录不是 GPT-SoVITS 根目录` | 项目没放在 `api_v2.py` 旁边 | 把整个文件夹挪到 GPT-SoVITS 根目录 |
| 端口被占用 | 见上一节 | 程序会自动挪；想固定就改 `ui.port` |
| `gradio 是 5.x，本项目需要 4.44.1` | 被别的工具升级过 | `runtime\python.exe install_deps.py --force` |

### 语音类

| 症状 | 原因 | 解决 |
| --- | --- | --- |
| 点朗读 → 「没有可用的参考音频」 | 没配参考音频 | 设置 → 高级设置 → 参考音频路径 |
| TTS 报 400 `tts failed ... not exists` | 参考音频路径不对（相对路径按 GPT-SoVITS 根目录解析） | 改绝对路径，或放进 `refs/` |
| 有声音但**完全不像**训练的音色 | ① 权重没选对 ② `prompt_text` 与音频不一致 ③ 参考音频太短/有 BGM | 设置里换权重；核对 `prompt_text` 原文；换一段 3~10 秒干净的一整句 |
| 说话怪、吞字、突然大喘气 | `prompt_text` 错了，或切分方式不适合 | 核对原文；切分方式换 `cut5` |
| 同一句每次念得都不一样 | `seed = -1`（随机） | 填一个固定数字（如 42）就稳定了 |
| `TTS 服务启动超时` / `api_v2.py` 起不来 | 显存不足 / 模型路径不对 / 端口 9880 被占 | 看 `tts_server.log`；手动 `runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880` 看报错 |
| 切换权重失败 | 权重架构和你装的 GPT-SoVITS 版本不匹配 | 用同版本的权重；见[版本兼容性](#版本兼容性) |
| 情绪切换没效果 | 该情绪没有配参考音频 → 回落默认音色 | 「🎭 情绪微调」里配好并保存 |

### 对话 / 网络类

| 症状 | 原因 | 解决 |
| --- | --- | --- |
| 发消息报 401 / 403 | API Key 错或没填 | 设置 → ✏️ 修改 API 接口 → 填 Key → 🔌 测试连通 |
| `/models` 里没有我填的模型名 | 很多接口的模型名是别名，不列进清单 | 点「🔌 测试连通」，**只要「试发对话」成功就能用** |
| 发消息一直「AI 思考中」 | 接口地址错 / 网络不通 / 代理拦截 | 用「🔌 测试连通」验证；检查 `base_url` |
| 麦克风点不动、`/call` 没反应 | 浏览器要求 `localhost` 或 HTTPS；且要装 ASR 依赖 | 用 `http://127.0.0.1:...` 打开；装 `requirements-asr.txt` |
| 手机打不开 | 没开手机访问 / 不同 WiFi / 防火墙 | 勾选「允许手机/局域网访问」→ 保存 → 重启；用电脑 IP 而不是 localhost；放行防火墙 |
| 改了 `chat_config.json` 没生效 | 存成了 ANSI，或没重启 | 存 UTF-8（BOM 也认）；改端口/手机访问要重启 |
| 改了 `ui.css` / `ui.js` 要重启吗 | 这两个是每次请求现读磁盘的 | **不用重启**，浏览器 Ctrl+F5 刷新即可；改 `voice_chat.py` 才要重启 |

### 还不知道？

```bat
:: 一条命令体检环境 + 列出还缺什么
runtime\python.exe deploy.py --check-only
```

它会告诉你：目录对不对、依赖齐不齐、ffmpeg 有没有、权重和参考音频扫到几个、配置还缺哪几项。

---

## 八、界面说明

| 标签页 | 用途 |
| --- | --- |
| **对话** | 主界面：聊天、语音自动播放、消息多版本、⚙️ 更多（改名 / 语音对话 / 测试音色） |
| **📞 实时通话** | 按住说话的对讲页（`/call`），微信式气泡，语音转文字 + 实时回复 |
| **设置** | 全局设置（人设/温度/头像）、AI 接口（掩码 + 弹窗改密钥）、高级设置（权重 / 参考音频 / 采样）、情绪微调 |
| **输入即朗读** | 直接合成一段文字，不入对话历史 |
| **回放历史** | 本次会话所有语音，可逐条重听 |

设置页是「全局默认 + 会话可覆盖」两层：改完点 **💾 保存设置** 影响当前会话和之后的新对话；
点 **💾 保存为全局默认** 影响以后所有新对话。

---

## 九、目录结构

```
voice_chat.py                 # 主程序：Gradio UI + FastAPI 路由 + LLM/TTS 调用（单文件）
start.bat                     # ★ 双击入口：菜单式一键部署 / 体检 / 只装依赖
deploy.py                     #   一键部署向导：装依赖 + 自动认权重和参考音频 + 写配置 + 启动
install_deps.py               # 只装依赖（可反复跑，只装缺的）
install_deps.bat              #   Windows 双击入口
install_deps.sh               #   macOS / Linux 入口
ui.css / ui.js                # 前端样式与脚本（服务端每次请求现读磁盘，改完刷新即可生效）
call_page.html                # /call 实时对讲页
chat_config.example.json      # 配置模板（脱敏，须复制成 chat_config.json 用）
refs/README.txt               # 参考音频放这里
assets/avatars/placeholder.png# 占位头像（自己的头像不入库）
requirements.txt              # 核心依赖清单
requirements-asr.txt          # 可选：对讲 / 语音输入依赖清单

（运行时生成，均不入库）
chat_config.json              # 你的配置（含密钥）
conversations/*.json          # 会话记录
output/voice_chat/*.wav       # 合成语音
logs/  tts_server.log         # 运行日志
```

---

## 十、隐私与安全

- API Key 只存在本机 `chat_config.json`，**界面上永远只显示掩码**，浏览器拿到的页面配置里也没有明文
- 聊天记录 `conversations/`、语音 `output/` 全部留在本地，`.gitignore` 已排除
- 唯一的外连是：你自己的大模型接口 + 本地 TTS 服务（`127.0.0.1:9880`）
- 默认只监听 `127.0.0.1`，**不开手机访问时局域网访问不到**

---

## 十一、许可证与致谢

MIT（与上游 GPT-SoVITS 一致）。

**音色模型与参考音频由使用者自行准备并自负版权责任** —— 游戏 / 影视角色的语音素材通常受版权保护，
请勿随本项目分发，也不要用于冒充真人。

- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) —— TTS 与训练框架
- [Gradio](https://github.com/gradio-app/gradio) —— 网页 UI
- [FunASR](https://github.com/modelscope/FunASR) —— 语音识别（可选）
- [FFmpeg](https://ffmpeg.org/) —— 音频转码
