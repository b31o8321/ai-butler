# ai-butler（占位名）· 个人本地 AI 网关 + 运行时管家 —— 总体设计

日期：2026-10-13 ｜ 状态：S0 设计稿 v0.2（并入运行时管家，见 §1.5/§2.5）｜ 作者：b31o8321 + ZCode

## 0. 一句话定位

**把散落在各处的本地 AI 能力（ComfyUI 出图、Ollama/LM Studio 对话、云 API/订阅计划）
收拢成一个配置可 git 同步、换机一键恢复、端点永远稳定的个人网关——
并且能检测硬件、一键安装/启动/配置这些运行时本身，让本地 AI 的上手门槛降到"双击安装、点点按钮"。**

## 0.5 产品形态（是什么、怎么装、怎么用）

**一个常驻后台服务，长出三个操作面**（同一个进程，不是三个程序）：

| 操作面 | 形态 | 给谁用 |
|---|---|---|
| **后台服务** | Windows 服务 / 计划任务开机自启（mac LaunchAgent）| 常驻，承载网关端点与运行时看护；DZMM 等消费方只依赖它 |
| **Web 控制台** | 服务自带的本地网页 `http://localhost:9000`，浏览器打开（局域网手机也能开）| **人的主界面**：一键安装/启动运行时、模型清单、预设管理、状态监控 |
| **CLI** | `butler check / sync / run / lock / service install` | 脚本化、换机恢复、进阶操作 |

安装方式：`pipx install ai-butler` 后 `butler service install`（或提供一个小安装器）。
后续可选加 Tauri 壳变成托盘 App（复用 DZMM 的桌面经验），但那只是壳——**内核永远是这个常驻服务**。

选型理由：网关必须常驻（DZMM 依赖稳定端点）→ 服务是必然形态；Web 控制台天然跨设备
（手机 M4 场景直接复用）；CLI 承载换机恢复这种脚本化场景。三者共享同一内核，零重复。

解决的真实痛点（均来自实际使用）：
1. 换机后 ComfyUI 的模型/工作流/参数配方无法迁移（模型几十 GB 重下、配方只存在脑子里）
2. 中国网络环境下 ModelScope/hf-mirror/官方源的选源与断点续传全靠手动
3. DZMM 等应用与 ComfyUI/Ollama 的绑定在换机后需要逐个重配
4. ComfyUI 生图与 LLM 推理抢 GPU，需要排队与回退策略
5. 订阅计划额度想以 API 形式在个人应用中使用（ToS 风险自担，见 §7）

## 1. 核心设计原则（回应"通用性 vs 特殊性"的矛盾）

**通用内核 + 领域适配器 + 声明式配置**三层隔离：

- 内核不知道任何具体系统的存在。它只认识四个原语：
  `probe`（探测）、`sync`（补齐环境）、`execute`（执行一次生成/对话）、`task`（异步任务）
- 一切特殊性 = **适配器模块**（代码，可插拔）+ **清单/预设文件**（数据，git 同步）
- 新领域（TTS、新云厂商、新订阅计划）= 新适配器 + 新配置，**内核零改动**

反例（禁止）：在内核里出现 "checkpoint"、"workflow length"、"ollama" 这类字眼。

## 2. 总体架构

```
消费方: DZMM / 任何 OpenAI 兼容客户端 / Web 控制台 / CLI
              │ 稳定端点 http://<host>:9000（配置一次永不变）
┌─────────────┴──────────────────────────────────┐
│ L1 内核（通用）                                   │
│  · OpenAI 兼容 API（/v1/chat/completions 等）      │
│  · 预设端点（/presets/{name}/run + /tasks/{id}）   │
│  · 异步任务队列（queued→running→done|failed）      │
│  · 策略引擎（并发上限/GPU 独占窗口/回退路由）         │
│  · 注册表（sqlite：任务历史/资源状态/用量）           │
│  · 密钥库（keyring；密钥永不入 git）                │
├────────────────────────────────────────────────┤
│ L2 适配器 SPI（特殊，可插拔）                       │
│  probe() / sync(manifest) / execute(preset)      │
│  ├── comfyui-adapter   工作流注入·模型目录·镜像源    │
│  ├── ollama-adapter    发现·pull·chat 透传         │
│  ├── openai-compat     LM Studio / 任意兼容端点     │
│  ├── plan-adapter      订阅计划 → OpenAI 兼容 ⚠️    │
│  └── runtime-manager   硬件检测·一键安装·服务看护    │
├────────────────────────────────────────────────┤
│ L0 配置层（数据，git 同步；密钥除外）                 │
│  manifest.yaml（模型/工作流/节点清单）               │
│  presets/*.yaml（生成预设=工作流+参数+提示词骨架）     │
│  gateway.yaml（端口/路由策略/适配器开关）             │
└────────────────────────────────────────────────┘
落地运行时（原生安装，网关可代为安装/启动/看护——见 §2.5 runtime-manager）:
  ComfyUI(8188) · Ollama(11434) · LM Studio(1234) · 云端端点
```

## 2.5 runtime-manager（运行时管家——降低上手门槛的关键模块）

> 动机：新玩家从"想玩"到"跑起来"要跨五道坎（装哪个版本、去哪下、模型放哪、
> 怎么启动、怎么配端点）。本模块把这五道坎全部收敛成 Web 控制台里的按钮。

### 2.5.1 硬件检测（install/recommend 的输入）

| 检测项 | 手段 | 用途 |
|---|---|---|
| GPU 厂商/型号 | WMI (Win32_VideoController)、nvidia-smi、rocm-smi | 选对构建（Ollama 老版在 RDNA4 上崩溃的教训）+ 显存档位推荐模型 |
| 显存/内存/磁盘余量 | WMI | 模型推荐过滤（13GB 模型不进 8GB 卡）|
| 已装运行时探测 | 常见安装路径 + 端口探测 + 版本命令 | 控制台显示"已装/未装/在跑" |

### 2.5.2 一键安装（按 OS 分渠道配方，配方本身也是数据）

| 运行时 | Windows 首选渠道 | 备选 |
|---|---|---|
| Ollama | winget（微软 CDN，实测快）| 官网直链 |
| LM Studio | winget | 官网 |
| ComfyUI | 便携包镜像（npmmirror/ghproxy 模式）+ 内嵌 Python 自动配 | git clone + uv |

安装后自动做：注册开机自启、健康探测（各端口 /health 或握手）、
把实际端口写回 gateway.yaml（路由层自动感知，无需手填）。

### 2.5.3 生命周期管理（Web 控制台按钮 ↔ 后端动作）

| 按钮 | 动作 |
|---|---|
| 启动/停止 ComfyUI | 拉起便携包 run_nvidia_gpu.bat（或裸 python main.py）；停止 = 结束进程树 |
| 打开界面 | 打开 http://127.0.0.1:8188 |
| 启动/停止 Ollama | ollama serve 隐藏进程 + 端口探测确认 |
| 拉起 LM Studio 服务 | lms server start（官方 CLI）|
| 全部一键启动 | 按依赖顺序（先运行时后网关自检）|

看护：进程意外退出 → 控制台标红 + 可选自动重启。

### 2.5.4 预设包（curated channels——"点点按钮就有能玩的东西")

清单仓库里预置几条"全家桶"频道，勾选即批量落位（模型走 §3.1 manifest 的
镜像测速下载，工作流走 presets，参数配方随包带）：

| 频道 | 内容 | 适配硬件 |
|---|---|---|
| 跑团全家桶（DZMM）| NoobAI 立绘 + t2v/i2v 14B 视频对 + Qwen3-14B GM + DZMM 全套预设 | 16GB VRAM |
| 轻量入门 | SD1.5/SDXL base + qwen3-8b + 基础工作流 | 8GB VRAM |
| 写实人像 | RealVisXL + 基础 i2i | 12GB+ |

频道 = manifest 的一个命名字集，无新机制——**推荐能力是数据，不是代码**。

### 2.5.5 边界

- 不做模型训练/微调；不做运行时版本升级的自动迁移（只提示）
- 多 OS：**Windows 先行**（本机即验证场），mac/Linux 配方后续补
- 一键安装全部走**固定 URL + 校验和**，绝不裸拉最新（可复现性）

## 3. L0 配置层 schema（草案）

### 3.1 manifest.yaml（环境清单——换机恢复的依据）

```yaml
version: 1
runtimes:
  comfyui: { url: "http://127.0.0.1:8188" }
models:                      # 期望存在的模型（check/sync 的依据）
  - name: noobaiXL_v1.1
    kind: checkpoint         # checkpoint | lora | diffusion_model | vae | text_encoder
    dir: checkpoints
    source:
      primary:   { type: modelscope, repo: Laxhar/noobai-XL-1.1, file: NoobAI-XL-v1.1.safetensors }
      fallback:
        - { type: hf-mirror, repo: Laxhar/noobai-XL-1.1, file: NoobAI-XL-v1.1.safetensors }
    sha256: null             # 可选校验
  - name: t2v_nsfw_HN
    kind: lora
    dir: loras
    source: { type: hf, repo: jaysowen/wan2.2-nsfw-loras, path: "wan2.2 high/NSFW运动增强器_HN_V1.safetensors" }
custom_nodes:                # 兼容 ComfyUI-Manager 快照导入
  snapshot_file: snapshots/comfy_nodes.json
```

### 3.2 presets/*.yaml（生成预设——"特殊性"的归宿）

```yaml
name: 雪景猫-t2v
kind: text-to-video          # 决定由哪个适配器执行
adapter: comfyui
workflow: workflows/snow_cat_t2v.json     # ComfyUI API 格式（或内置模板名）
defaults:
  width: 832
  height: 480
  length: 81
  fps: 16
  steps: 4
  boundary: 2
  cfg: 1.0
inputs:                      # 暴露给调用方的参数（含类型与校验）
  - { name: prompt, type: string, required: true, max: 4000 }
  - { name: seed, type: seed }
inject:                      # 注入映射：值写到工作流哪个节点哪个字段
  - { node: "15", field: text, from: prompt }
  - { node: "30", field: noise_seed, from: seed }
  - { node: "17", field: length, from: length }
policy: { queue: gpu, priority: normal }
```

> 设计要点：**注入是数据驱动的**（node/field/from 映射），内核不做任何
> "认识 CLIPTextEncode" 的假设——comfyui-adapter 负责按映射写值。
> 换任何工作流 = 换一份 preset + inject 映射，内核零改动。

### 3.3 gateway.yaml（网关自身）

```yaml
server: { host: 0.0.0.0, port: 9000 }
routes:
  llm:
    prefer: [ollama, lmstudio]     # 优先本地
    fallback: [plan-zai]           # GPU 忙/本地宕机时的回退链
policy:
  gpu_exclusive_window: { minutes: 30 }   # ComfyUI 生图期间 LLM 走回退
  max_concurrent_tasks: 1
adapters: { comfyui: on, ollama: on, lmstudio: on, plan-zai: off }
```

## 4. 端点面（消费方看到的世界）

| 端点 | 用途 | 消费方 |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI 兼容对话（模型=别名） | DZMM 模型档案填这里，一劳永逸 |
| `POST /presets/{name}/run` | 按预设生成（异步任务） | DZMM 视觉层 / 脚本 |
| `GET /tasks/{id}` | 任务状态与产物路径 | 轮询 |
| `GET /v1/models` | 可用别名列表 | 发现 |
| `POST /admin/sync` `GET /admin/status` | 环境补齐 / 全局健康 | CLI、控制台 |
| Web 控制台（P2） | 状态/清单 diff/手动触发的可视化管理 | 人 |

## 5. 关键流程

### 5.1 换机恢复（本项目存在的意义）

```
新电脑：装 ComfyUI(便携包) + Ollama(安装器)
  → git clone 网关仓库 && butler run
  → butler check（对照 manifest 报缺口清单）
  → butler sync（镜像测速→断点续传→落到正确目录→sha256 校验）
  → butler probe（全部 ✓）
  → DZMM 原配置直接可用（端点没变过）
```

### 5.2 一次 T2V 预设生成

```
DZMM → POST /presets/雪景猫-t2v/run {prompt, seed}
  → 内核：任务入队（GPU 窗口检查）
  → comfyui-adapter：载入 preset.workflow → 按 inject 映射写值 → POST 8188/prompt
  → 轮询 ComfyUI history → 取回产物 → 落盘 media/ → 任务 done
DZMM → GET /tasks/{id} → 拿到文件路径 → 前端展示
```

### 5.3 GPU 争抢策略

- 状态机：comfy executing → llm 路由临时切 fallback（可配回退链）
- 显式窗口：`butler lock gpu --minutes 30`（跑长视频时手动锁）
- 所有策略是内核数据，不是代码

## 6. 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.12 + FastAPI + httpx + sqlite | 与 DZMM/ComfyUI 生态技能一致，可长期自维护 |
| LLM 路由 | 内核自研轻量路由 + **可选内嵌 LiteLLM** 作为 openai-compat/plan 的实现依赖 | LiteLLM 成熟度省 P3 一半工作；不爽时可替换 |
| 任务存储 | sqlite 单文件 | 与 DZMM 同款模式，够用 |
| 控制台 | 先 CLI + 静态页；P2 再 Vue | 控制台不是核心价值 |
| 分发 | uv tool install / pipx；Windows 计划任务开机自启 | 个人网关要常驻 |

## 7. 风险与边界

| 风险 | 处置 |
|---|---|
| **plan-adapter 违反上游 ToS → 封号** | 模块独立可关；仅个人使用不外分享；额度行为模拟真实使用；风险自担 |
| plan 端点随上游改版失效 | 适配器隔离 + 版本钉死 + 探活报警（probe 失败即通知）|
| 模型源 URL 失效 | manifest 双源（primary/fallback）+ snapshot 可手动覆盖 |
| 网关自身挂了 | 消费方可降级直连运行时（配置回切）；网关无状态可随时重启 |
| 范围蔓延 | 铁律：内核不出现任何领域词；新需求 = 新适配器/新预设 |

## 8. 里程碑（直奔 P3 + 运行时管家，每步可独立交付）

| 步 | 内容 | 验收 |
|---|---|---|
| S0 | 本设计定稿 | 你确认 |
| S1 | 内核骨架 + SPI + 配置装载 + Web 控制台骨架（服务形态：Windows 自启） | 服务常驻，浏览器开 9000 看到控制台 |
| S2 | runtime-manager：硬件检测 + 已装探测 + 生命周期按钮（启动/停止/打开界面） | 控制台按钮能拉起/停掉本机 ComfyUI 与 Ollama |
| S3 | 一键安装：Ollama/LM Studio/ComfyUI 配方（固定 URL+校验和，Windows 先行） | 干净机器上控制台点击装齐三件套 |
| S4 | comfyui-adapter：probe + 预设执行（文生图预设跑通） | 一条命令/一个按钮出图 |
| S5 | manifest check/sync（镜像测速+断点续传+校验落位）+ 预设包频道 | 干净机器一键装齐"跑团全家桶" |
| S6 | ollama/lmstudio 适配器 + OpenAI 兼容端点 | DZMM 模型档案切到网关跑通一局 |
| S7 | 第一个 plan-adapter（按你实际订阅选型） | 对话经适配器返回 |
| S8 | 策略引擎（GPU 窗口/回退链）| 生图期间 LLM 自动走回退 |

> Windows 先行原则：S2/S3 的检测与安装配方只做 Windows（本机即验证场），
> mac/Linux 配方作为社区贡献位预留（配方是数据）。
