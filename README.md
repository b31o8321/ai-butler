# ai-butler（占位名）

**个人本地 AI 网关 + 运行时管家**——把 ComfyUI 出图、Ollama/LM Studio 对话、云 API
收拢成一个配置可 git 同步、换机一键恢复、端点永远稳定的个人网关；并且能检测硬件、
一键安装/启动/看护这些运行时本身。

> 状态：S0 设计稿。当前仓库暂只有设计文档与配置示例，代码随里程碑推进
> （S1 内核骨架起）。设计全文见 [`docs/DESIGN.md`](docs/DESIGN.md)。

## 它解决什么

| 痛点 | 方案 |
|---|---|
| 换机后 ComfyUI 模型/工作流/参数配方无法迁移 | 环境清单 manifest + 预设库随 git 同步，`butler sync` 自动补齐 |
| 国内下载模型选源/断点续传全靠手动 | manifest 声明双源（ModelScope/hf-mirror/官方），CLI 测速+续传+校验 |
| DZMM 等应用与运行时绑定换机要重配 | 网关端点固定不变，背后随便换 |
| ComfyUI 生图与 LLM 推理抢 GPU | 策略引擎：独占窗口 + 回退路由 |
| 本地 AI 上手门槛高 | 硬件检测 + 一键安装运行时 + 预设包频道（Web 控制台点点按钮）|

## 产品形态

一个常驻服务，三个操作面：

- **后台服务**：开机自启，承载网关端点与运行时看护
- **Web 控制台**：`localhost:9000`（手机/局域网同源）——一键安装、启动/停止、模型清单、预设管理
- **CLI**：`butler check / sync / run / lock / service install`

## 架构一图

```
DZMM / 任何 OpenAI 兼容客户端 / Web 控制台 / CLI
              │ 稳定端点 :9000
┌─────────────┴──────────────┐
│ 通用内核（路由/队列/策略/注册表/密钥）│
├────────────────────────────┤
│ 适配器 SPI（可插拔）           │
│  comfyui / ollama /          │
│  openai-compat / plan-adapter│
├────────────────────────────┤
│ 配置层（manifest/presets/git）│
└────────────────────────────┘
   ComfyUI · Ollama · LM Studio · 云端
```

## 里程碑

S1 内核骨架 → S2 运行时管家（硬件检测/生命周期）→ S3 一键安装 → S4 comfyui 适配器 →
S5 环境同步 → S6 LLM 路由 → S7 plan 适配器（ToS 风险自担）→ S8 策略引擎。

详见 [docs/DESIGN.md](docs/DESIGN.md)（§2.5 运行时管家 / §3 配置 schema / §8 里程碑）。
