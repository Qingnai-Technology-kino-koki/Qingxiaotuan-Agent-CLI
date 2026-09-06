"""Agent 模型路由器 —— 从 Agent 拆出的独立组件。

职责:
- 自动模型选择/切换 (按任务难度路由到合适的 provider/model)
- 路由决策的持久化 (供 /route 与 /cost 展示)
- 与 AutoRouter (core/auto_route) 协作实现规划/执行模式的模型切换

拆出原因:
- Agent.__init__ 与 _maybe_route_model 合计 ~120 行路由逻辑, 与 ReAct 主循环
  的职责边界模糊; 拆出后 Agent 只需调用 self._router.maybe_route(task)
- DevLoop、Swarm Worker 等也需要路由能力, 共享同一实现避免重复
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)


class AgentModelRouter:
    """模型路由器: 按任务难度自动选择/切换模型。

    用法::

        router = AgentModelRouter(config, kernel)
        router.maybe_route(task, override=0, has_images=None)
    """

    def __init__(self, config: Any, kernel: Any) -> None:
        self.config = config
        self.kernel = kernel
        self._model_switcher: Optional[Callable[..., Any]] = None
        self.last_route: Optional[Dict[str, Any]] = None

    def maybe_route(
        self,
        task: str,
        override: int = 0,
        has_images: Optional[bool] = None,
        current_model: Any = None,
        pending_images: Optional[list] = None,
    ) -> None:
        """按 router 配置自动选择/切换模型 (失败保险: 仅切换到已配置密钥的供应商)。

        仅当「升级 / 降级省钱 / 当前缺失必需能力(如视觉)」时才切换, 避免同档抖动。
        任何异常都 fail-safe: 保持当前模型继续工作, 不打断用户任务。

        Args:
            task: 本轮任务文本 (用于难度估算)。
            override: 难度覆盖值 (0=自动评估; 2=便宜; 10=强)。由 core/auto_route 计算,
                      用于实现「规划用强模型 / 执行用便宜模型 / 卡住升级」。
            has_images: 是否有待发送的图片 (None=从 pending_images 推断)。
            current_model: 当前模型适配器 (用于读取 capabilities)。
            pending_images: 待发送的图片列表 (用于推断 has_images)。
        """
        if not self.config.get("router.enabled", False):
            return
        if not self.config.get("router.auto_switch", True):
            return
        try:
            from ..models.router import ModelRouter
        except Exception:  # noqa: BLE001
            return

        router = ModelRouter(
            default_provider=self.config.get("model.provider", "deepseek"),
            default_model=self.config.get("model.model", "deepseek-chat"),
        )
        cfg_override = int(self.config.get("router.difficulty_override", 0) or 0)
        override = override or cfg_override
        if has_images is None:
            has_images = bool(pending_images)
        cur_provider = self.config.get("model.provider")
        cur_model = self.config.get("model.model")
        avail = ModelRouter.available_provider_names()
        decision = router.decide(
            task, context="", current_provider=cur_provider, current_model=cur_model,
            has_images=has_images, available_providers=avail, difficulty_override=override,
        )
        self.last_route = decision
        if not decision.get("switch"):
            return

        overrides = {
            "provider": decision["provider"],
            "model": decision["model"],
            "base_url": decision.get("base_url", ""),
            "api_key_env": decision.get("api_key_env", ""),
        }
        try:
            if self._model_switcher is None:
                from ..models.plugin import ModelPlugin
                self._model_switcher = ModelPlugin.switch_model
            self._model_switcher(self.kernel, overrides)
            self.kernel.emit("model.routed", decision)
            log.info("自动路由切换: %s/%s — %s", decision["provider"], decision["model"], decision["reason"])
        except Exception as exc:  # noqa: BLE001
            log.debug("模型自动路由切换失败, 保持当前模型: %s", exc)
