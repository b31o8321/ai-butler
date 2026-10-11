# PROGRESS（通宵开发账本）

> 每次自动运行结束时追加一节：时间 | 里程碑 | 做了什么 | 如何验证 | 遗留问题。
> 下一次运行永远从最后一个未勾选项继续。

## 已完成

- 2026-10-12/13 S4 预设执行 — commit e4038dc
  - kernel/tasks.py 通用任务状态机；presets.py 加载/解析/注入引擎（数据驱动）
  - comfyui-adapter.execute 闭环：注入 → /prompt → 轮询 → /view 取回产物落盘
  - API：/presets 列表与详情、/presets/{id}/run（异步线程）、/tasks/{id}、/tasks
  - 内置工作流 snow_cat_t2v.json（节点 id 与 preset inject 对齐）
  - 验证：pytest 57 绿（新增 20）；实机 POST /presets/snow-cat-t2v/run →
    真实 ComfyUI 4 步 T2V → done → butler_00001_.mp4 落盘 .data/media/
  - 调试记录：①workflow 相对路径基准 = configs 根（preset 已带 workflows/ 前缀）；
    ②execute 的 httpx.Client 必须接注入的 transport（可测性）；③mock ComfyUI
    首次 history 轮询须返回 pending
  - 遗留：presets 目录有两个近似文件（.yaml 功能版 + .example.yaml 格式示例），
    S5 做频道时清理；控制台预设 UI 留给 S5

- 2026-10-13 S3 manifest check/sync — commit 4c6c25d
  - sync.py：URL 构建（4 类源、按段转义）、镜像测速、断点续传、sha256 校验、聚合报告
  - CLI：check 升级（models_root 三级解析）+ 新增 sync 命令（--only 过滤）；
    gateway.yaml 缺失时 check/sync 可独立运行
  - 验证：pytest 37 绿（本地 Range-capable HTTP 服务器夹具，非 mock）；
    实机：ModelScope 真实小文件 MISSING → sync 下载 → check PRESENT
  - 调试记录：①httpx.Timeout 需显式 default；②ModelScope 对小文件 Range 响应
    content-length 虚高 → 连接提前关闭 → 测速需容忍短读 + 可达保底分；
    ③url 类型源需在 target 检查前判断
  - 遗留：resume 对"content-length 虚高"类源可能失败（全量下载不受影响，S8 回顾）

- 2026-10-12 S2 runtime-manager — 硬件检测 + 运行时生命周期
  - runtime_manager.py：WMI 硬件检测（GPU/RAM/磁盘，qwMemorySize 未封顶显存 best-effort）、
    RuntimeManager（start/stop/status，PID 定点操作，只读运行时保护，托管 PID 跟踪）
  - API：GET /admin/hardware、GET /admin/runtimes、POST /admin/runtimes/{name}/start|stop
  - 控制台：硬件卡 + 运行时启停按钮（无 start_command 显示"只读"）
  - 验证：pytest 27 绿（新增 14：解析 4 + 硬件降级 2 + 生命周期 5 + API 3）；
    实机冒烟：GPU 正确识别 RX 9070、RAM/双盘、comfyui 只读保护返回 400、
    ollama start 成功 spawn（PID 5212）
  - 遗留：① 本机 Ollama 老版本 RDNA4 崩溃导致 start 后进程变僵尸、stop 报
    still_alive=True（环境问题非管理器缺陷，任务状态如实上报；11434 端口已释放）；
    ② 显存 best-effort 在多显示适配器注册表键时可能取错值（9070 显示 8GB 而非 16GB），
    后续用 nvidia-smi/设备路径匹配改进

- 2026-10-12 S1 内核骨架 — commit 4ec07ce
  - FastAPI 网关 + SPI + 3 真实探针 + 配置装载 + sqlite 注册表 + Web 控制台 + CLI
  - 验证：pytest 13 绿；实机 /admin/status 探测 comfyui 0.39.0 OK、
    lmstudio 14 模型 OK、ollama DOWN（如实）
  - 遗留：service install 未在本机注册（S8 实测）；ollama 本身在本机启动即崩（升级 Ollama 可解）

## 下一个

- S3 manifest check/sync（环境同步）
