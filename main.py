"""Alice Memory Plugin — AstrBot 三层记忆系统主入口。

L1 原始对话 / L2 双路中期记忆 / L3 长期向量记忆（衰减模型）。
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
from .memory.scheduler.scheduler import Scheduler
from .memory.storage.storage import MemoryStorage
from .memory.vector_store.vector_store import VectorStore


class AliceMemoryPlugin(Star):
    """Alice 三层记忆系统插件主类。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)

        # Layer 0: 配置
        self.plugin_config = PluginConfig.from_framework_config(config or {})
        logger.info(
            "[AliceMemory] PluginConfig 加载完成 | fields=%d | data_dir=%s",
            len(self.plugin_config.model_fields),
            self.plugin_config.data_dir,
        )

        # Layer 1: 身份 & 存储 & 向量 & 分析
        self._identity = IdentityModule(self.plugin_config.data_dir)
        self._storage = MemoryStorage(self.plugin_config)
        self._vector_store = VectorStore(
            self.plugin_config.data_dir, self.plugin_config,
        )
        self._analyzer = ImportanceAnalyzer(context, self.plugin_config)
        logger.info(
            "[AliceMemory] 模块就绪 | Identity ✓ | Storage ✓ | VectorStore ✓ | Analyzer ✓"
        )

        # Layer 2+3: 压缩 & 注入
        self._compressor = DialogueCompressor(context, self._storage, self.plugin_config)
        self._injector = ContextInjector(
            self._storage, self._vector_store, self._identity, self.plugin_config,
        )
        logger.info("[AliceMemory] 模块就绪 | Compressor ✓ | ContextInjector ✓")

        # Layer 4: 调度
        self._scheduler = Scheduler(
            context, self._storage, self._identity, self._vector_store,
            self.plugin_config, self._compressor, self._analyzer,
        )
        self._scheduler.start()
        logger.info("[AliceMemory] 定时调度就绪 | Scheduler ✓")

        logger.info("[AliceMemory] 插件初始化完成")

    # =========================================================================
    # LLM 钩子
    # =========================================================================

    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """LLM 请求前 — 存储对话 + 注入记忆上下文 + L3 晋升判断。"""
        try:
            if not self.plugin_config.hook_enabled:
                return

            platform = event.get_platform_name()
            platform_user_id = event.get_sender_id()
            user_id = self._identity.get_user_id(platform, platform_user_id)
            if not user_id:
                user_id = self._identity.register_user(platform, platform_user_id)

            content = event.get_message_str() or ""
            if not content.strip():
                return

            # silent 模式下 /compact 命令不存入 L1，也不触发后续管线
            # 框架在 on_llm_request 中可能去掉命令前缀 "/"
            msg = content.strip().lstrip("/").lstrip("#")
            if (
                msg.startswith("compact")
                and self.plugin_config.manual_compress_feedback_mode == "silent"
            ):
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

            # L3 晋升判断
            if self.plugin_config.l3_enabled:
                try:
                    score = await self._analyzer.analyze(
                        content, event.unified_msg_origin,
                    )
                    logger.debug(
                        "[AliceMemory] 重要性评分 | score=%d | threshold=%d",
                        score, self.plugin_config.importance_threshold,
                    )
                    if score >= self.plugin_config.importance_threshold:
                        vid = await self._vector_store.add_memory(
                            user_id, content, {"importance": score},
                        )
                        logger.info("[AliceMemory] L3 晋升 | vid=%s | score=%d", vid[:8], score)
                except Exception as e:
                    logger.error("[AliceMemory] L3 晋升失败 | %s", e)

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
    # /compact — 手动压缩
    # =========================================================================

    @filter.command("compact")
    async def cmd_compact(
        self, event: AstrMessageEvent, date: str | None = None
    ):
        """手动压缩记忆。/compact → Path A 周压缩，/compact 2026-04-25 → Path B 日压缩。"""
        # silent 模式：阻止事件继续传播到 LLM 管线
        silent = self.plugin_config.manual_compress_feedback_mode == "silent"
        if silent:
            event.stop_event()

        platform = event.get_platform_name()
        platform_user_id = event.get_sender_id()
        user_id = self._identity.get_user_id(platform, platform_user_id)
        if not user_id:
            if not silent:
                yield event.plain_result("[AliceMemory] 未能识别用户身份")
            return

        try:
            if date:
                item = await self._compressor.compress_day(
                    user_id, date, umo=event.unified_msg_origin,
                )
                if item is None:
                    if not silent:
                        yield event.plain_result(f"[AliceMemory] {date} 无对话可压缩")
                    return
                result_text = f"{date} 对话已压缩为日摘要"
            else:
                item = await self._compressor.compress_context_summary(
                    user_id, event.unified_msg_origin,
                )
                if item is None:
                    if not silent:
                        yield event.plain_result("[AliceMemory] 无内容可压缩为周摘要")
                    return
                result_text = "周摘要已更新"

            # silent 模式不反馈，也不调 LLM 生成反馈（节省调用）
            if silent:
                return

            feedback = await self._build_feedback(
                user_id, result_text, event.unified_msg_origin,
            )
            yield event.plain_result(feedback)

        except Exception as e:
            logger.error("[AliceMemory] /compact 失败 | %s", e, exc_info=True)
            if not silent:
                yield event.plain_result(f"[AliceMemory] 压缩失败: {e}")

    # =========================================================================
    # /important — 标记重要记忆 → L3
    # =========================================================================

    @filter.command("important")
    async def cmd_important(
        self, event: AstrMessageEvent, *, content: str = ""
    ):
        """手动标记重要记忆。/important <内容> → 分析并存入 L3。"""
        if not content.strip():
            yield event.plain_result("[AliceMemory] 用法: /important <内容>")
            return

        platform = event.get_platform_name()
        platform_user_id = event.get_sender_id()
        user_id = self._identity.get_user_id(platform, platform_user_id)
        if not user_id:
            yield event.plain_result("[AliceMemory] 未能识别用户身份")
            return

        try:
            score = await self._analyzer.analyze(content, event.unified_msg_origin)
            vid = await self._vector_store.add_memory(
                user_id, content, {"importance": score},
            )
            yield event.plain_result(
                f"[AliceMemory] 已存入 L3 | id={vid[:8]} | 重要性={score}/10"
            )
            logger.info("[AliceMemory] /important | vid=%s... | score=%d", vid[:8], score)
        except Exception as e:
            logger.error("[AliceMemory] /important 失败 | %s", e, exc_info=True)
            yield event.plain_result(f"[AliceMemory] 存入失败: {e}")

    # =========================================================================
    # /forget — 删除记忆
    # =========================================================================

    @filter.command("forget")
    async def cmd_forget(
        self, event: AstrMessageEvent, memory_id: str = ""
    ):
        """删除 L3 记忆。/forget <vector_id>。"""
        if not memory_id.strip():
            yield event.plain_result("[AliceMemory] 用法: /forget <记忆ID>")
            return

        if self._vector_store.delete_memory(memory_id):
            yield event.plain_result(f"[AliceMemory] 记忆 {memory_id[:8]} 已删除")
        else:
            yield event.plain_result(f"[AliceMemory] 记忆 {memory_id[:8]} 未找到")

    # =========================================================================
    # /show_memory — 搜索 L3 记忆
    # =========================================================================

    @filter.command("show_memory")
    async def cmd_show_memory(
        self, event: AstrMessageEvent, *, query: str = ""
    ):
        """搜索 L3 记忆。/show_memory <查询>。"""
        if not query.strip():
            yield event.plain_result("[AliceMemory] 用法: /show_memory <查询>")
            return

        platform = event.get_platform_name()
        platform_user_id = event.get_sender_id()
        user_id = self._identity.get_user_id(platform, platform_user_id)
        if not user_id:
            yield event.plain_result("[AliceMemory] 未能识别用户身份")
            return

        try:
            results = await self._vector_store.search(user_id, query, top_k=5)
            if not results:
                yield event.plain_result("[AliceMemory] 未找到相关记忆")
                return

            lines = ["[AliceMemory] L3 记忆搜索结果:"]
            for i, r in enumerate(results, 1):
                content = r.get("content", "")[:80]
                vid = r.get("id", "?")[:8]
                meta = r.get("metadata", {})
                score = meta.get("importance", "?")
                lines.append(f"  {i}. [{score}] {content}... (id:{vid})")
            yield event.plain_result("\n".join(lines))

        except Exception as e:
            logger.error("[AliceMemory] /show_memory 失败 | %s", e, exc_info=True)
            yield event.plain_result(f"[AliceMemory] 搜索失败: {e}")

    # =========================================================================
    # 压缩反馈
    # =========================================================================

    async def _build_feedback(
        self, user_id: str, default_text: str, umo: str = "",
    ) -> str:
        """根据 manual_compress_feedback_mode 生成压缩反馈。"""
        mode = self.plugin_config.manual_compress_feedback_mode

        if mode == "silent":
            return default_text  # 静默仅在 cron 场景，命令仍给反馈
        elif mode == "fixed":
            return self.plugin_config.manual_compress_feedback_text
        elif mode == "visible":
            weekly = self._storage.get_weekly_summary(user_id)
            if weekly and weekly.get("summary"):
                return f"[AliceMemory] 压缩完成\n\n{weekly['summary']}"
            return default_text
        elif mode == "llm":
            try:
                prompt = self.plugin_config.manual_compress_llm_prompt
                kwargs = {
                    "chat_provider_id": await self.context.get_current_chat_provider_id(umo),
                    "max_tokens": self.plugin_config.llm_max_tokens,
                    "temperature": self.plugin_config.llm_temperature,
                }
                if self.plugin_config.compress_model:
                    kwargs["model"] = self.plugin_config.compress_model
                resp = await self.context.llm_generate(
                    prompt=prompt, **kwargs,
                )
                text = getattr(resp, "completion_text", "") or ""
                if text.strip():
                    return text.strip()
            except Exception:
                return default_text
        else:
            return default_text
