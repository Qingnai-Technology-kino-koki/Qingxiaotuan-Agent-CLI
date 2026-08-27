"""模型插件 —— 按需创建模型适配器并注册为内核服务。

同时提供运行时热切换能力: 用户想换"脑子" (Claude/Gemini/本地网关) 无需重启会话,
调用 switch_model 即可重建适配器并重新注册到内核。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..core.kernel import Kernel, Plugin
from . import create_adapter


class ModelPlugin(Plugin):
    name = "model"
    provides = ["model_adapter"]
    requires = ["config"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.require("config")
        kernel.provide("model_adapter", create_adapter(config), owner=self.name)

    @classmethod
    def switch_model(
        cls,
        kernel: Kernel,
        overrides: Optional[Dict[str, Any]] = None,
        *,
        persist: bool = False,
    ) -> Any:
        """运行时热切换模型适配器 (青小团的"换脑子"能力)。

        用法:
            ModelPlugin.switch_model(kernel, {
                "provider": "openai-compatible",
                "model": "claude-3-5-sonnet",
                "base_url": "https://gateway.example/v1",
                "api_key_env": "GATEWAY_KEY",
            })

        - overrides 中的键会写回内核配置视图 (内存层), 新建适配器立即生效。
        - persist=True 时还会写入用户层 config.yaml, 下次启动默认即用新脑子。
        返回新建的 ModelAdapter 实例。
        """
        config = kernel.require("config")
        ov = overrides or {}
        if persist:
            # 写用户层 config.yaml: 下次启动默认即用新脑子
            for key, value in ov.items():
                config.set_user(f"model.{key}", value)
        else:
            # 临时切换只改内存视图, 不落盘 (swarm 子任务 / 自动路由等运行时换脑子
            # 不应把用户的 config.yaml 永久改掉 —— set_user 会写磁盘)
            from ..config.loader import patch_replace
            config.data = patch_replace(
                config.data, {f"model.{key}": value for key, value in ov.items()})
        # 重新构建并注册适配器 (先卸旧实例再装新实例, 让运行中的会话即时换脑子)
        adapter = create_adapter(config)
        kernel.unprovide("model_adapter")
        kernel.provide("model_adapter", adapter, owner=cls.name)
        kernel.emit("model.switched", {
            "provider": config.get("model.provider"),
            "model": config.get("model.model"),
        })
        return adapter
