# AI 语音聊天（GPT-SoVITS 音色）

一个跑在自己电脑上的语音聊天网页。文字对话交给大模型，念出来用你自己训练的 GPT-SoVITS 音色。

这里只有网页和交互这一层。模型、音色、参考音频都不在里面，需要你自己准备，然后把整个文件夹放进 GPT-SoVITS 根目录（和 `api_v2.py` 同级）跑。

## 需要准备什么

- **GPT-SoVITS**：v2 之后的版本都行（v2 / v2Pro / v3 / v4 / v5），只要能跑 `api_v2.py`。v1 用的是 `api.py`，接口不一样，没做兼容。我这里一直在 v2Pro 上测。
- **你自己的音色**：训练出来的 GPT 权重（`.ckpt`）、SoVITS 权重（`.pth`），外加一段 3~10 秒的参考音频和它说的原话。
- **一个 OpenAI 兼容接口**：DeepSeek、OpenAI、中转站、本地 Ollama 都成，要个 API Key。
- Python 不用管，用整合包自带的 `runtime\python.exe` 就行。

权重架构要和装的 GPT-SoVITS 版本对得上。混着装的话 `api_v2.py` 加载会失败。

## 装

把仓库里的文件全部拷到 GPT-SoVITS 根目录，然后双击 `start.bat`，选 `[1] Deploy and start`。

它会依次做这几件事：切到整合包自带的 python、检查并安装缺的依赖、扫出你的权重和参考音频并挑一个最新的、问一次 API Key、写好 `chat_config.json`、启动网页。

地址是 `http://127.0.0.1:27865/`。

不想被提问就一条命令：

```bat
runtime\python.exe deploy.py --yes --api-key sk-你的key
```

只想看看环境有没有问题，不装东西也不改文件：

```bat
runtime\python.exe deploy.py --check-only
```

依赖也能单独装：`install_deps.py --asr` 会把对讲要用的识别依赖一起装上（约 2 GB，首次识别还会下载 1 GB 的中文模型）。

<details>
<summary>手动装（不想用脚本）</summary>

```bat
runtime\python.exe -m pip install -r requirements.txt
runtime\python.exe -m pip install -r requirements-asr.txt
```

国内慢就加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

</details>

## 配置

全在 `chat_config.json`。没有这个文件时会按模板生成一份，参考音频是空的——页面能开，点朗读会提示没有参考音频。

| 字段 | 说明 |
| --- | --- |
| `llm.api_key` / `base_url` / `model` | 接口三件套。`base_url` 填到 `/v1` 或者直接贴 `/chat/completions` 都能认 |
| `persona.system_prompt` | 角色人设，留空就是通用助手 |
| `voice.gpt_weights` / `sovits_weights` | 权重路径，相对根目录或绝对路径都行 |
| `voice.ref_audio_path` / `prompt_text` | 参考音频和它说的原话，**必须一致**，不一致音色会飘 |
| `voice.speed_factor` | 语速，1.0 是原速。开心、难过、害怕这三种情绪用各自的语速，不受这里影响 |
| `voice.top_k` / `top_p` / `gpt_temperature` / `repetition_penalty` | 采样参数，调大语气更活，也更容易口胡 |
| `voice.seed` | `-1` 每次都不一样，填固定数字同一句永远读得一样 |
| `voice.tts_api_url` | 默认 `http://127.0.0.1:9880` |
| `emotions` | 十种情绪，每种一套参考音频和上面那些参数 |
| `history.keep_turns` | 记住最近几轮对话，默认 8 |
| `ui.port` / `phone_access` / `access_token` / `theme` | 端口、手机访问开关、访问口令、主题 |

最小能跑的配置：

```json
{
  "llm": {
    "api_key": "sk-你的key",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash"
  },
  "voice": {
    "gpt_weights": "GPT_weights_v2Pro/myvoice-e10.ckpt",
    "sovits_weights": "SoVITS_weights_v2Pro/myvoice_e4_s108.pth",
    "ref_audio_path": "refs/ref_01.wav",
    "prompt_text": "这段音频里说的那句话，一字不差"
  }
}
```

其余字段不写就用默认值。大部分设置网页里也能改。

## 界面上有五个标签

对话是主界面。多会话，消息可以重新生成好几个版本挑一个，聊太久会自动把前面的对话压成摘要。

实时通话是按住说话的对讲页，录音转文字、回话、播报，要装识别依赖。

设置页里 AI 接口那部分点了弹窗出来改，密钥平时只显示 `sk-••••••••abcd` 这样的掩码。

输入即朗读适合粘一段长文直接念完，不进对话历史。

回放历史是这次会话所有语音的列表。

情绪微调在设置页下面。十种情绪各有一套参考音频，可以单独调语速和采样参数，改完能当场试听：先播参考音频，再用当前参数合成一句，听着满意再保存。

## 端口

网页端口默认 **27865**。没用 7865 是因为同一个整合包里 RVC 的 WebUI 默认就占 7865，一起开必撞。

TTS 走 9880，是 `api_v2.py` 的端口，一般不用动。

端口被占了不会崩，会自己往后找一个空的：

```
[WARN] 端口 27865 已经被别的程序占用了
 查是谁占的：netstat -ano | findstr :27865
 已自动改用 27866 启动（本次临时生效，配置不用改）
```

想固定就去设置里改「网页端口」，改完重启。

## 声音听着像 AI，怎么调

这几个地方影响最大，按顺序试：

1. **参考音频**。换一段 3~10 秒、一整句说完、语气自然、没有背景音的。这一步最有效，比调参数管用。
2. **`prompt_text` 抄对**。和音频里说的每个字都要对得上，差一个字音色就飘、就吞字。
3. **固定 `seed`**。`-1` 是每次重掷，情绪时好时坏。多试几个数字，把最好听的那次定下来。
4. **语速别拉**。0.95~1.0 最自然，1.1 以上听着像播报。
5. **切分方式用 `cut5`**。别用 `cut0`，长句一口气念完特别像念稿。
6. **`repetition_penalty` 别太高**。1.2~1.25 差不多，到 1.5 语调会发硬。
7. **情绪参考音频用同一套音色、不同语气的片段**。几个情绪共用一段音频，听感就分不出来。

## 出问题的时候

先跑一遍 `runtime\python.exe deploy.py --check-only`，它会告诉你目录对不对、依赖齐不齐、权重和参考音频扫到几个、配置还缺哪几项。

**启动**

| 现象 | 怎么处理 |
| --- | --- |
| 双击窗口一闪就没，或者 `ModuleNotFoundError: gradio` | 用错 python 了。双击 `start.bat` 它会自动找对的；或者直接 `runtime\python.exe voice_chat.py` |
| `httpx.InvalidURL: Invalid port: ':1]'` | 代理工具把 `NO_PROXY` 写成了 `...::1,[::1]`。程序会自己清洗，还报就先 `set NO_PROXY=localhost,127.0.0.1` |
| 提示不在 GPT-SoVITS 根目录 | 文件夹放错层了，挪到 `api_v2.py` 旁边 |
| 端口被占 | 见上面，会自动换 |
| 说 gradio 版本不对 | `runtime\python.exe install_deps.py --force` |

**没声音或不像**

| 现象 | 怎么处理 |
| --- | --- |
| 提示没有可用的参考音频 | 设置里填参考音频路径 |
| TTS 报 400 `tts failed ... not exists` | 路径不对。相对路径是按 GPT-SoVITS 根目录算的，换成绝对路径或丢进 `refs/` |
| 完全不像训练的音色 | 权重选错，或者 `prompt_text` 和音频对不上 |
| 一句话读得断断续续 | 切分方式换成 `cut5` |
| 每句读得都不一样 | `seed` 改成固定数字 |
| `TTS 服务启动超时` | 看 `tts_server.log`。手动跑 `runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880` 能看到具体报错 |
| 切权重失败 | 权重和装的 GPT-SoVITS 版本不是一个架构 |
| 情绪切换没反应 | 那个情绪没配参考音频，自动回落成默认音色了 |

**对话和网络**

| 现象 | 怎么处理 |
| --- | --- |
| 报 401 / 403 | Key 不对。设置里点「修改 API 接口」重填，再点「测试连通」 |
| 模型名不在 `/models` 清单里 | 很多接口的模型名是别名，清单里没有。测试连通里「试发对话」能通就能用 |
| 一直显示「AI 思考中」 | 接口地址错或者网络不通，点「测试连通」看 |
| 麦克风点不动 | 必须用 `localhost` 或 HTTPS 打开，不是的话浏览器不给权限 |
| 手机打不开 | 设置里勾「允许手机/局域网访问」，保存，重启。手机连同一个 WiFi，用电脑 IP 打开 |
| 改了配置没生效 | 存成 ANSI 了，或者改的是端口、手机访问这类要重启的项 |

## 目录里都有什么

```
voice_chat.py            主程序，界面和路由都在一个文件里
start.bat                双击入口，菜单式的
deploy.py                一键部署：装依赖、认权重、写配置、启动
install_deps.py          只装依赖
install_deps.bat / .sh   双击和命令行包装
ui.css / ui.js           前端样式和脚本，服务端每次请求现读，改完刷新就行
call_page.html           对讲页
chat_config.example.json 配置模板
refs/                    参考音频放这
requirements*.txt        依赖清单
```

跑起来之后会多出 `chat_config.json`、`conversations/`、`output/voice_chat/`、`logs/`、`tts_server.log`，这些不进版本库。

## 隐私

密钥只在本地 `chat_config.json` 里，网页上只显示掩码，浏览器拿到的页面配置里也没有明文。聊天记录和语音留在 `conversations/` 和 `output/`。程序只连两个地方：你自己配的大模型接口，和本机的 TTS 服务。默认只监听 `127.0.0.1`，不开手机访问的话局域网进不来。

## 许可

MIT，和上游一致。

音色模型和参考音频你自己准备、自己负责版权。游戏和影视角色的语音素材基本都有版权，别跟着这个项目一起分发，也别拿去冒充真人。

感谢 GPT-SoVITS、Gradio、FunASR 和 FFmpeg。
