# S1 Goal：内核骨架（2026-10-12）

## 目标

为 ai-butler 建立可常驻运行的内核骨架——服务形态成立、适配器 SPI 成立、
配置装载成立，并让用户第一次"看得见"它（Web 控制台）。

## 范围

1. Python 包 `ai_butler`：CLI（`butler serve/status/check/service`）+ FastAPI 服务
2. SPI 协议：Adapter 基类（probe/sync/execute）+ 三个真实现探针
   （comfyui `/system_stats`、ollama `/api/tags`、lmstudio `/v1/models`）
3. 配置装载：gateway.yaml + manifest.yaml（schema 以 `configs/*.example.yaml` 为准）
4. Web 控制台骨架：localhost:9000 展示适配器健康/配置摘要（15s 自动刷新）
5. 注册表骨架：sqlite 事件表（S6 策略引擎地基）
6. Windows 服务自启：`butler service install`（schtasks ONLOGON；本次实现不注册）
7. 基础测试：pytest（配置/探针 mock/API）+ 实机冒烟（真实探测本机运行时）

## 非目标

sync 下载、预设执行、LLM 路由（S3-S6）、托盘壳、mac/Linux 服务配方。

## 验收标准

- [ ] pytest 全绿
- [ ] `butler serve` 后 /health、/admin/status 返回真实探测结果
- [ ] /console 页面可访问且渲染
- [ ] 提交并推送到 GitHub main

## 约束

- 内核模块零领域词泄漏（checkpoint/workflow/ollama 不出现在 kernel/）
- 密钥不入 git
