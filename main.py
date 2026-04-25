"""Alice Memory Plugin — AstrBot 三层记忆系统主入口。

L1 原始对话 / L2 双路中期记忆 / L3 长期向量记忆（衰减模型）。

重构中 — 当前为 A0 骨架，后续迭代逐步接入各模块。
"""

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star


class AliceMemoryPlugin(Star):
    """Alice 三层记忆系统插件主类。

    按拓扑顺序初始化全部模块，注册钩子和命令。
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        """初始化插件。

        Args:
            context: AstrBot 框架上下文（持有 llm_generate 等能力）。
            config: 框架传入的插件配置 dict（AstrBotConfig）。
        """
        super().__init__(context)
        logger.info("[AliceMemory] 插件初始化开始（A0 骨架）")

        # TODO: 后续迭代按拓扑顺序初始化模块
        # Layer 0: PluginConfig.from_framework_config(config or {})
        # Layer 1: IdentityModule / MemoryStorage / VectorStore / ImportanceAnalyzer
        # Layer 2: DialogueCompressor / MigrationModule
        # Layer 3: ContextInjector
        # Layer 4: Scheduler

        logger.info("[AliceMemory] 插件初始化完成（A0 骨架）")

    # =========================================================================
    # LLM 钩子
    # =========================================================================

    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """LLM 请求前 — 存储对话 + 注入全部记忆管线。"""
        pass  # TODO: A1+ 接入 ContextInjector + Storage

    @filter.on_llm_response()
    async def on_llm_response(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        """LLM 响应后 — 存储助手回复到 L1。"""
        pass  # TODO: A1+ 接入 Storage

    # =========================================================================
    # 命令处理器
    # =========================================================================

    @filter.command("compact")
    async def cmd_compact(
        self, event: AstrMessageEvent
    ):
        """手动压缩记忆。用法: /compact [日期] [--hidden|--visible]"""
        yield event.plain_result("[AliceMemory] compact 命令尚未实现（A0 骨架）")

    @filter.command("important")
    async def cmd_important(
        self, event: AstrMessageEvent
    ):
        """标记重要记忆。用法: /important [消息ID]"""
        yield event.plain_result("[AliceMemory] important 命令尚未实现（A0 骨架）")

    @filter.command("forget")
    async def cmd_forget(
        self, event: AstrMessageEvent
    ):
        """删除指定记忆。用法: /forget [记忆ID]"""
        yield event.plain_result("[AliceMemory] forget 命令尚未实现（A0 骨架）")

    @filter.command("show_memory")
    async def cmd_show_memory(
        self, event: AstrMessageEvent
    ):
        """搜索 L3 记忆。用法: /show_memory [查询]"""
        yield event.plain_result("[AliceMemory] show_memory 命令尚未实现（A0 骨架）")
