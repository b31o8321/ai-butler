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

### [x] S2 runtime-manager（硬件检测 + 生命周期）
- `runtime_manager.py`：GPU 型号/显存（WMI Win32_VideoController + 注册表 qwMemorySize
  best-effort）、内存/磁盘余量、运行时已装探测
- API：GET /admin/hardware、GET /admin/runtimes、POST /admin/runtimes/{name}/start|stop
- 启动命令来自 gateway.yaml runtimes.start_command（列表形式，不硬编码）；
  无 start_command 的运行时 = 只读监控（start 返回 400）；停止一律按 PID
- Web 控制台：硬件卡 + 运行时启停按钮
- 测试：硬件解析 mock 单测 + 启停注入单测（假 spawner/killer/pid_checker）+ API stub 测试
- 验收：pytest 27 绿；实机控制台显示本机 GPU（RX 9070）与三个运行时状态；
  ollama 启停实测通过（start spawn ✓ / stop PID 定点 ✓ / 只读 400 ✓）
  ⚠️ 已知环境问题：本机 Ollama 老版本 RDNA4 崩溃致僵尸进程（stop 如实上报 still_alive）

### [x] S3 manifest check/sync（环境同步）
- sync.py：build_source_url（modelscope/hf/hf-mirror/url，按段转义）、
  measure_speed（Range 采样，容忍 CDN 短读）、pick_source（择速）、
  download_file（.part 断点续传 + sha256 校验，不匹配即删）、sync_missing（聚合报告）
- `butler check` 升级：models_root 三级解析（--models-root > gateway paths > manifest paths）；
  gateway.yaml 缺失时 check/sync 可独立运行（load_gateway_optional）
- `butler sync` 命令 + --only 单项过滤；事件流实时输出
- 测试：本地 Range-capable HTTP 服务器夹具（真服务器非 mock）10 项：
  下载/sha256/续传（断言 Range 头）/择速/聚合
- 验收：实机删除后 sync 从 ModelScope 自动补齐真实文件 → check 转 PRESENT
  ⚠️ 已知边界：部分 CDN 对小文件 Range 响应头不一致（content-length 虚高），
  测速与续传已做容忍；resume 遇此类源可能失败（全量下载不受影响）

### [x] S4 comfyui-adapter execute（预设执行）
- kernel/tasks.py：通用任务状态机（queued→running→done|failed，非法流转拒绝）
- presets.py：PresetStore（多目录扫描）+ resolve_params（defaults 合并/必填/seed
  类型转换/max 校验）+ apply_injection（数据驱动写入节点字段，内核零领域词）
- comfyui-adapter.execute：加载工作流 → 注入 → POST /prompt → 轮询 /history →
  /view 取回产物落盘 media/<task>/
- API：GET /presets、GET/POST /presets/{id}/run、GET /tasks/{id}、GET /tasks
  （后台线程执行，失败标记 failed+error）
- 内置工作流 configs/workflows/snow_cat_t2v.json（已验证的 4 步 T2V 图，
  lightx2v 双 LoRA，节点 id 与 preset inject 对齐）
- 测试：20 项新增（注入/解析/任务状态机/mock ComfyUI 端到端/API 流转）
- 验收：pytest 57 绿；实机 POST /presets/snow-cat-t2v/run → 真实 ComfyUI
  4 步 T2V → done，产物 butler_00001_.mp4 落盘 .data/media/（中文提示词
  注入验证 ✓，seed/length 注入 ✓）

### [x] S5 预设包频道（Web 控制台可勾选）
- channels.py：ChannelStore（configs/channels/*.yaml）+ channel_diff
  （对照 manifest 与 models_root 的 PRESENT/MISSING 状态）+ install_channel（复用 sync_missing）
- API：GET /admin/channels（diff）、POST /admin/channels/{id}/install（异步任务）
- manifest 新增显式 filename 字段（源 basename ≠ 落盘名时声明，避免误报缺失/重复下载）
- 控制台：频道卡（描述/模型状态点/安装缺失项按钮/预设清单）
- 内置频道：dzmm-pack（跑团全家桶 8 项）/ starter（占位）
- 测试：4 项新增（加载/diff 状态/未知模型报错/install 下载）
- 验收：pytest 61 绿；实机 diff 显示 7/8 PRESENT（修复 filename 前误报 2 缺失）、
  install 异步任务真实下载缺失项 → done（present=5 downloaded=2）
  ⚠️ 教训：PowerShell -replace 写回破坏中文 → 用 git checkout 恢复后以 Edit 工具修改

### [x] S6 LLM 别名路由（OpenAI 兼容端点）
- llm_router.py：别名→(backend, model) 解析 + 回退链（prefer/fallback）+
  非流式 chat 与流式 chat_stream（首块提交制：开始流式后不再回退）
- 适配器 chat 能力：ollama 原生 /api/chat ↔ OpenAI 格式双向转换（含 NDJSON 流→SSE）；
  openai_compat 直通（含 SSE 透传）
- API：POST /v1/chat/completions（stream=true → SSE）、/v1/models 列别名；
  gateway.yaml 新增 llm_aliases 段
- 适配器双索引：backend 名（lmstudio）与适配器名（openai_compat）都可解析
- 测试：10 项新增（格式转换/别名解析/回退链/全败/流式/API 端到端/SSE）
- 验收：pytest 71 绿；实机别名 qwen3-14b → 真实 LM Studio Qwen3-14B 对话成功
  （GM 开场叙事，37.5s）——DZMM 只需把模型档案指向网关别名即可
  ⚠️ 已知边界：qwen3 思考模式会在内容里带 <think> 段（LM Studio 侧透传），
  DZMM 端有 think 清洗逻辑；流式回退仅在首块前生效

### [ ] S7 第一个 plan-adapter（⚠️ 等待用户拍板订阅选型——自动运行跳过本项）
- 独立模块，实现 OpenAI 兼容出口；仅个人使用；探活失败告警
- ⚠️ ToS 风险自担；默认 enabled: false，用户显式开启
- 验收：网关别名路由到适配器返回对话
- 状态：**阻塞于用户输入**（订阅的是哪家 coding plan）——通宵自动开发期间跳过

### [x] S8 策略引擎 + 收尾
- kernel/policy.py：PolicyEngine（GPU 独占窗口判定 + max_concurrent 并发上限；
  占用 GPU 的任务种类来自 gateway.yaml policy 配置，数据驱动）
- LLMRouter 接入 should_skip_primary 策略钩子：GPU 忙 → 主后端跳过、回退链接管；
  无回退可用时降级用主后端（可用性优先）；预设启动并发超限返回 429
- `butler service install/uninstall` 实测：注册/删除计划任务双向验证
  （schtasks GBK 输出解码修复）；卸载命令 = `butler service uninstall`
- README 重写到真实功能；v0.1.0 打 tag
- 测试：9 项新增（策略判定/路由器降级与回退联动/429）
- 验收：pytest 80 绿；mock 验证生图运行时 LLM 自动走回退、空闲时恢复主后端

## 停止条件

- 所有里程碑完成并勾选；或
- 本地时间 >= 2026-10-13 09:00（此时不得修改任何文件，只输出总结）
