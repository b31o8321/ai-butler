# ai-butler 总 Goal（主控文档）

> 本文件是通宵自动开发的唯一依据。每次自动运行：先读本文件与 PROGRESS.md，
> 按里程碑顺序取下一个未完成项，实现→测试→记录→提交推送，一次只做一个里程碑。

## 总目标

把 ai-butler 建成可用的个人本地 AI 网关 + 运行时管家：
配置可 git 同步、换机一键恢复、端点稳定、硬件检测与一键安装、
ComfyUI/Ollama/LM Studio 统一调度、订阅计划可选接入（ToS 风险自担）。
设计全文：docs/DESIGN.md。

## 通用硬性约束（每个里程碑都必须遵守）

1. pytest 全绿才能提交；修不好就回滚本次改动并在 PROGRESS.md 记录阻塞原因
2. 内核模块零领域词泄漏（checkpoint/workflow/ollama 不出现在 kernel/ 代码中）
3. 密钥/token 不入 git；push 前自查
4. 一次运行只做一个里程碑或其独立切片；宁可少而精
5. 工作目录仅限 F:\dev\ai-butler；不碰其他项目
6. 禁止全局杀进程（taskkill /IM python.exe 之类）；只按 PID 操作
7. 本地运行时探测永远 trust_env=False
8. 每完成一项：更新 PROGRESS.md（做了什么/如何验证/遗留）→ commit → push（SSH）
9. 系统级变更（注册自启服务）允许，但须同时验证 uninstall 可用并记录

## 里程碑清单（顺序执行；[x]=已完成）

### [x] S1 内核骨架（2026-10-12，commit 4ec07ce）
FastAPI 服务 + 适配器 SPI + 3 真实探针 + 配置装载 + sqlite 注册表
+ Web 控制台 + CLI（serve/status/check/service）+ 13 测试。
验收：/admin/status 真实探测本机运行时；pytest 绿。✅ 已达成。

### [ ] S2 runtime-manager（硬件检测 + 生命周期）
- `runtime_manager.py`：GPU 型号/显存（WMI Win32_VideoController + nvidia-smi/rocm-smi 探测）、
  内存/磁盘余量、运行时已装探测（路径+端口+版本命令）
- API：GET /admin/hardware、GET/POST /admin/runtimes/{name}/start|stop
- comfyui 启动 = 拉起 F:\ComfyUI\run_nvidia_gpu.bat 等价命令（路径从 manifest/gateway 读，
  不硬编码）；ollama 启动 = ollama serve；停止一律按 PID
- Web 控制台：硬件卡 + 运行时启停按钮（调新 API）
- 测试：hardware 解析用 mock WMI 数据单测；启停用假进程/假命令注入单测
- 验收：控制台能看到本机 GPU 与三个运行时状态；按钮能启动/停止 ollama（ComfyUI 只读状态即可，
  避免影响正在使用的实例）

### [ ] S3 manifest check/sync（环境同步）
- `butler check` 升级：对照 manifest 逐项报告 PRESENT/MISSING（含 models_root 解析）
- `butler sync`：对 MISSING 项按 source 优先级下载（modelscope/hf-mirror/hf/url），
  镜像测速（range 采样选最快）、断点续传（-C -）、落位到正确目录、可选 sha256 校验
- 下载走 detached curl 或 httpx 流式，全程事件写注册表
- 测试：本地 http server 夹具测下载/续传/测速逻辑
- 验收：删除一个真实模型文件后 sync 自动补齐并校验通过（用小文件验证，大模型不强制）

### [ ] S4 comfyui-adapter execute（预设执行）
- 按 preset 的 inject 映射把参数写入工作流 JSON → POST /prompt → 轮询 /history → 取回产物落盘
- POST /presets/{name}/run + GET /tasks/{id}（任务状态机 queued→running→done|failed）
- 用 configs/presets/snow-cat-t2v.example.yaml 对真实 ComfyUI 跑通一次 4 步 T2V
- 验收：一条命令/一个 curl 出图，产物路径在任务结果里

### [ ] S5 预设包频道（Web 控制台可勾选）
- manifest 增加 channel 概念（跑团全家桶/轻量入门/写实人像）
- 控制台展示频道内容与安装状态，勾选缺失项触发 sync
- 验收：控制台能展示频道 diff 并触发同步

### [ ] S6 LLM 别名路由（OpenAI 兼容端点）
- /v1/chat/completions：按 gateway.yaml routes.llm.prefer 顺序路由到 ollama/lmstudio 适配器，
  别名（butler/qwen3-14b）→ 实际模型映射存 gateway.yaml
- 流式透传（SSE）；回退链按序尝试
- 测试：mock 上游的端到端单测
- 验收：DZMM 模型档案指向网关别名，完成一次真实对话

### [ ] S7 第一个 plan-adapter（按用户实际订阅选型，开工前询问用户）
- 独立模块，实现 OpenAI 兼容出口；仅个人使用；探活失败告警
- ⚠️ ToS 风险自担；默认 enabled: false，用户显式开启
- 验收：网关别名路由到适配器返回对话

### [ ] S8 策略引擎 + 收尾
- GPU 独占窗口（ComfyUI 执行中 → LLM 走回退链）；max_concurrent_tasks 生效
- `butler service install` 实测注册/卸载/开机自启
- README 更新到真实功能；v0.1.0 打 tag
- 验收：生图任务运行时发起对话走回退链（mock 验证）

## 停止条件

- 所有里程碑完成并勾选；或
- 本地时间 >= 2026-10-13 09:00（此时不得修改任何文件，只输出总结）
