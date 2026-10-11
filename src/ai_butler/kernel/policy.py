"""Policy engine: GPU exclusive windows and concurrency limits.

Data-driven: which task kinds occupy the GPU is configuration
(gateway.yaml policy.gpu_task_kinds), never kernel code.
"""
from __future__ import annotations

from typing import Any


class PolicyEngine:
    def __init__(self, task_store: Any, policy_cfg: dict[str, Any]) -> None:
        self.task_store = task_store
        self.max_concurrent_tasks = int(policy_cfg.get("max_concurrent_tasks", 1))
        self.gpu_task_kinds = set(policy_cfg.get("gpu_task_kinds")
                                  or ["text-to-video", "text-to-image", "image", "video"])

    def running_gpu_tasks(self) -> list[dict[str, Any]]:
        tasks = []
        for task in self.task_store.recent_tasks(200):
            if task["status"] == "running" and task["kind"] in self.gpu_task_kinds:
                tasks.append(task)
        return tasks

    def llm_should_fallback(self) -> tuple[bool, str]:
        """True when a GPU generation is running and LLM traffic should defer
        to the fallback chain (config: routes.llm.fallback)."""
        running = self.running_gpu_tasks()
        if running:
            return True, f"gpu busy: {len(running)} running task(s) " \
                         f"({', '.join(t['kind'] for t in running)})"
        return False, ""

    def can_start_gpu_task(self) -> tuple[bool, str]:
        running = self.running_gpu_tasks()
        if len(running) >= self.max_concurrent_tasks:
            return False, f"max_concurrent_tasks={self.max_concurrent_tasks} reached " \
                          f"({len(running)} running)"
        return True, ""
