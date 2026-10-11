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
