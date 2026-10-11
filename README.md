# ai-butler

**个人本地 AI 网关 + 运行时管家**——把 ComfyUI 出图、Ollama/LM Studio 对话、云 API
收拢成一个常驻网关：配置可 git 同步、换机一键恢复、端点永远稳定、硬件检测与
一键安装、GPU 争抢统一调度。

- 状态：v0.1.0（S1-S6、S8 已交付；S7 plan 适配器待定，见 ROADMAP）
- 平台：Windows（先行验证场）；架构跨平台，服务配方待补
- 设计全文：[docs/DESIGN.md](docs/DESIGN.md)

## 解决什么

| 痛点 | 对应能力 |
|---|---|
| 换机后模型/工作流/参数配方无法迁移 | manifest 环境清单随 git 同步，`butler sync` 镜像测速+断点续传一键补齐 |
| 本地 AI 上手门槛高 | Web 控制台：硬件检测、一键安装/启动/停止运行时（S2）|
| 应用与运行时绑定换机要重配 | 网关端点固定；别名路由背后随便换（S6）|
| ComfyUI 生图与 LLM 推理抢 GPU | 策略引擎：独占窗口内 LLM 自动走回退链（S8）|
| 订阅计划想以 API 形式在个人应用中使用 | plan-adapter 模块位（S7，ToS 风险自担）|

## 产品形态

一个常驻服务，三个操作面：

- **后台服务**：`butler service install`（Windows 计划任务开机自启；卸载 `butler service uninstall`）
- **Web 控制台**：`http://localhost:9000/console/`——硬件/运行时启停/模型清单 diff/频道安装/策略状态
- **CLI**：`butler serve / status / check / sync / service`

## 端点面（消费方视角）

| 端点 | 说明 |
|---|---|
| `POST /v1/chat/completions` | OpenAI 兼容对话（model=别名，支持 SSE 流式），带回退链 |
| `GET /v1/models` | 可用别名列表 |
| `POST /presets/{id}/run` `GET /tasks/{id}` | 按预设生成（异步任务：T2V/T2I/任意 ComfyUI 工作流）|
| `GET /admin/status` `/admin/hardware` `/admin/runtimes` | 健康与硬件快照 |
| `GET /admin/channels` `POST /admin/channels/{id}/install` | 预设包频道 diff 与一键安装 |

## 快速开始

```bash
pip install -e .
butler serve                # 默认 9000；配置在 configs/（示例齐全）
butler status               # 探测本机 ComfyUI/Ollama/LM Studio
butler check                # manifest 对照本地环境报缺失
butler sync                 # 自动补齐缺失模型（镜像测速+断点续传）
```

消费方（如 DZMM）只需把模型档案指向 `http://127.0.0.1:9000/v1` +
`llm_aliases` 里的别名——**换机/换运行时永远不用重配**。

## 配置（git 同步的核心资产）

| 文件 | 内容 |
|---|---|
| `configs/manifest.yaml` | 模型清单：名称/类型/落位目录/双源下载地址/校验和 |
| `configs/gateway.yaml` | 端口/别名映射/回退链/GPU 策略/适配器开关 |
| `configs/presets/*.yaml` | 生成预设：工作流 + 参数默认值 + 数据驱动注入映射 |
| `configs/channels/*.yaml` | 预设包频道：manifest 的命名字集（一键安装）|

示例见 `configs/*.example.yaml`。密钥走系统 keyring / `.env`，永不入库。

## 里程碑

S1 内核骨架 ✅ → S2 运行时管家 ✅ → S3 环境同步 ✅ → S4 预设执行 ✅ →
S5 预设包频道 ✅ → S6 LLM 别名路由 ✅ → S7 plan 适配器（待定）→ S8 策略引擎 ✅

过程账本：[PROGRESS.md](PROGRESS.md)（每步的实现/验证/遗留）。
