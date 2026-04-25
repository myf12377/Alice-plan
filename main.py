"""Alice Memory Plugin — AstrBot 三层记忆系统主入口。

L1 原始对话 / L2 双路中期记忆 / L3 长期向量记忆（衰减模型）。

重构中 — 当前 B2 阶段：全链路贯通（存储→压缩→注入）。
"""

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star

from .memory.analyzer.analyzer import ImportanceAnalyzer
from .memory.compressor.compressor import DialogueCompressor
from .memory.context_injector import ContextInjector
from .memory.identity.identity import IdentityModule
from .memory.plugin_config import PluginConfig
from .memory.storage.storage import MemoryStorage
from .memory.vector_store.vector_store import VectorStore


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

        # Layer 0: 配置
        self.plugin_config = PluginConfig.from_framework_config(config or {})
        logger.info(
            "[AliceMemory] PluginConfig 加载完成 | fields=%d | data_dir=%s",
            len(self.plugin_config.model_fields),
            self.plugin_config.data_dir,
        )

        # Layer 1: 身份 & 存储
        self._identity = IdentityModule(self.plugin_config.data_dir)
        self._storage = MemoryStorage(self.plugin_config)
        self._vector_store = VectorStore(
            self.plugin_config.data_dir, self.plugin_config,
        )
        self._analyzer = ImportanceAnalyzer(context, self.plugin_config)
        logger.info(
            "[AliceMemory] 模块就绪 | Identity ✓ | Storage ✓ | VectorStore ✓ | Analyzer ✓"
        )

        # Layer 1 全部完成

        # Layer 2: 压缩器
        self._compressor = DialogueCompressor(context, self._storage, self.plugin_config)

        # Layer 3: 上下文注入器
        self._injector = ContextInjector(
            self._storage, self._vector_store, self._identity, self.plugin_config,
        )
        logger.info("[AliceMemory] 模块就绪 | Compressor ✓ | ContextInjector ✓")

        # TODO: Layer 2: MigrationModule
        # TODO: Layer 4: Scheduler

        logger.info("[AliceMemory] 插件初始化完成（B2）— 全链路贯通")

    # =========================================================================
    # LLM 钩子
    # =========================================================================

    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """LLM 请求前 — 存储对话 + 注入记忆上下文。"""
        try:
            if not self.plugin_config.hook_enabled:
                return

            # 身份解析
            platform = event.get_platform_name()
            platform_user_id = event.get_sender_id()
            user_id = self._identity.get_user_id(platform, platform_user_id)
            if not user_id:
                user_id = self._identity.register_user(platform, platform_user_id)

            # 提取消息内容
            content = event.get_message_str() or ""
            if not content.strip():
                return

            # 存储到 L1
            self._storage.append_dialogue(user_id, "user", content)
            logger.debug(
                "[AliceMemory] L1 存储 | uid=%s... | role=user | len=%d",
                user_id[:8], len(content),
            )

            # 注入全部记忆管线
            await self._injector.inject_all(user_id, req)
            logger.info(
                "[AliceMemory] 注入完成 | contexts=%d | extra_parts=%d",
                len(req.contexts), len(req.extra_user_content_parts),
            )

        except Exception:
            logger.error("[AliceMemory] on_llm_request 异常", exc_info=True)

    @filter.on_llm_response()
    async def on_llm_response(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        """LLM 响应后 — 存储助手回复到 L1。"""
        try:
            if not self.plugin_config.hook_enabled:
                return

            completion = getattr(resp, "completion_text", "") or ""
            if not completion.strip():
                return

            platform = event.get_platform_name()
            platform_user_id = event.get_sender_id()
            user_id = self._identity.get_user_id(platform, platform_user_id)
            if not user_id:
                return

            self._storage.append_dialogue(user_id, "assistant", completion)
            logger.debug(
                "[AliceMemory] L1 存储 | uid=%s... | role=assistant | len=%d",
                user_id[:8], len(completion),
            )

        except Exception:
            logger.error("[AliceMemory] on_llm_response 异常", exc_info=True)

    # =========================================================================
    # 命令处理器
    # =========================================================================

    @filter.command("compact")
    async def cmd_compact(
        self, event: AstrMessageEvent
    ):
        """手动压缩记忆。用法: /compact [日期] [--hidden|--visible]"""
        yield event.plain_result("[AliceMemory] compact 命令尚未实现（A1）")

    @filter.command("important")
    async def cmd_important(
        self, event: AstrMessageEvent
    ):
        """标记重要记忆。用法: /important [消息ID]"""
        yield event.plain_result("[AliceMemory] important 命令尚未实现（A1）")

    @filter.command("forget")
    async def cmd_forget(
        self, event: AstrMessageEvent
    ):
        """删除指定记忆。用法: /forget [记忆ID]"""
        yield event.plain_result("[AliceMemory] forget 命令尚未实现（A1）")

    @filter.command("show_memory")
    async def cmd_show_memory(
        self, event: AstrMessageEvent
    ):
        """搜索 L3 记忆。用法: /show_memory [查询]"""
        yield event.plain_result("[AliceMemory] show_memory 命令尚未实现（A1）")
