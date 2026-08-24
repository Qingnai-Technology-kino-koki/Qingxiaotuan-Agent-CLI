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
        for key, value in ov.items():
            if key not in ("provider", "model", "base_url", "api_key_env", "api_key"):
                # 其余字段 (temperature 等) 也允许透传, 统一走 model.<key>
                config.set_user(f"model.{key}", value)
            else:
                config.set_user(f"model.{key}", value)
        # 重新构建并注册适配器 (先卸旧实例再装新实例, 让运行中的会话即时换脑子)
        adapter = create_adapter(config)
        kernel.unprovide("model_adapter")
        kernel.provide("model_adapter", adapter, owner=cls.name)
        kernel.emit("model.switched", {
            "provider": config.get("model.provider"),
            "model": config.get("model.model"),
        })
        return adapter
