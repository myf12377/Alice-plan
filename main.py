"""
Alice Memory Plugin — AstrBot 三层记忆系统主入口。

# ==============================================================================
# 架构概览
# ==============================================================================

本插件实现类人三层记忆：
  L1 短期记忆 — 原始对话轮次滑窗（默认保留 200 轮，注入最近 80 轮）
  L2 中期记忆 — Path A 周摘要 + Path B 日摘要，双路径分层互补
  L3 长期记忆 — ChromaDB 向量存储 + 艾宾浩斯衰减模型

# ==============================================================================
# 核心数据流（一次用户消息的完整链路）
# ==============================================================================

用户发送消息
  │
  ▼
on_llm_request() 钩子（本文件）
  │
  ├── [1] 身份识别: platform + platform_user_id → user_id（IdentityModule）
  │
  ├── [2] manage_context 判断:
  │       如果开启 → req.contexts = [] 清空 AstrBot 自带历史
  │       由插件 L1 注入的轮次对话完全替代
  │       好处: 无重叠上下文，节省 token，时间线清晰
  │
  ├── [3] L1 存储: storage.append_dialogue(user_id, "user", content)
  │       将用户消息写入磁盘 JSON（{data_dir}/l1/{uid}.json）
  │
  ├── [4] 记忆注入: injector.inject_all(user_id, req)
  │       四管线独立注入到 req:
  │       ├── inject_l1()  → req.contexts（最近 80 轮 + 日期标记）
  │       ├── inject_l2_path_a() → extra_user_content_parts [周摘要]
  │       ├── inject_l2_path_b() → extra_user_content_parts [L2记忆]
  │       └── inject_l3()  → extra_user_content_parts [L3记忆]
  │       L1 末尾 user 消息会被去重（避免与 req.prompt 重复）
  │
  └── [5] L3 晋升: analyzer.analyze(content) → 重要性评分
          如果 score ≥ importance_threshold → vector_store.add_memory()
          自动从 L1 原始对话晋升为 L3 长期记忆

LLM 生成回复
  │
  ▼
on_llm_response() 钩子（本文件）
  └── [6] 存储助手回复: storage.append_dialogue(user_id, "assistant", completion)

# ==============================================================================
# 独立运行 vs 主动层联动
# ==============================================================================

独立模式（当前默认）:
  记忆层通过 @filter.on_llm_request() 自主注入记忆
  用户感知: 对话中自然出现 [周摘要][L2记忆][L3记忆] 等记忆标记

主动层联动模式（未来）:
  用户在 WebUI 将记忆层 hook_enabled 设为 false
  主动层通过 context.get_all_stars() 获取本插件实例
  调用 6 个公开 @property + 4 个 get_*_context() 方法
  主动层自行决定注入策略，记忆层退居"记忆仓库"角色

# ==============================================================================
# 定时调度
# ==============================================================================

Scheduler 注册 6 段 cron 任务:
  01:00 — Path B 日压缩（将昨日 L1 对话压缩为 L2 日摘要）
  02:00 — L1 轮次裁剪（超过 l1_save_rounds 的旧轮次滑出）
  03:00 — L3 衰减+灰区重评（艾宾浩斯遗忘曲线 + LLM 重评）
  04:00 — Path A 周压缩（合并生成渐进周摘要）
  周一 05:00 — 周摘要重置
  动态 cron — L3 月度相似记忆合并
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


class EmbeddingResolver:
    """延迟解析 EmbeddingProvider，解决插件先于 Provider 初始化的时序问题。

    AstrBot 启动顺序: 插件 __init__ → ProviderManager.initialize()
    __init__ 中调用 get_all_embedding_providers() 始终返回空列表。

    本类不立即解析，而是在首次调用（L3 操作时）才触发解析，
    此时 ProviderManager 已初始化完毕，可正确获取 Provider。

    解析逻辑:
      "auto"               → providers[0]（首个可用）
      "Qwen/Qwen3-VL-..."  → 按模型名匹配
      指定模型未找到        → 降级 providers[0] + warn 日志
      无任何 Provider       → Raise RuntimeError（无 ChromaDB 降级）
    """

    def __init__(self, context: Context, target_provider: str) -> None:
        self._context = context
        self._target = target_provider.strip()
        self._resolved = False
        self._bridge = None  # Callable | None

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        if not self._resolved:
            await self._resolve()
        if self._bridge is None:
            raise RuntimeError(
                "[AliceMemory] L3 不可用：未找到可用的 EmbeddingProvider。"
                "请在 AstrBot 中配置 Embedding 类型的 Provider。"
            )
        return await self._bridge(texts)

    async def _resolve(self) -> None:
        self._resolved = True
        providers = self._context.get_all_embedding_providers()
        if not providers:
            logger.warning(
                "[AliceMemory] 未找到任何 EmbeddingProvider，L3 向量记忆不可用。"
                "请在 AstrBot 中配置 Embedding 类型的 Provider（如 Qwen/Qwen3-VL-Embedding-8B）。"
            )
            return

        provider = None
        if self._target == "auto":
            provider = providers[0]
            logger.info(
                "[AliceMemory] 使用默认 EmbeddingProvider | type=%s",
                provider.__class__.__name__,
            )
        else:
            # 按模型名匹配: 支持完整名称匹配或子串匹配
            for p in providers:
                model = getattr(p, "model_name", "") or getattr(p, "model", "")
                if model == self._target or self._target in model:
                    provider = p
                    logger.info(
                        "[AliceMemory] 匹配 EmbeddingProvider | model=%s | type=%s",
                        model, p.__class__.__name__,
                    )
                    break
            if provider is None:
                provider = providers[0]
                logger.warning(
                    "[AliceMemory] 未找到模型 '%s' 的 EmbeddingProvider，降级为 auto | 使用=%s",
                    self._target, provider.__class__.__name__,
                )

        # embedding_bridge: 适配 AstrBot Provider API → VectorStore 期望签名
        # 优先批量接口 get_embeddings()，降级逐个调用 get_embedding()
        async def bridge(texts: list[str]) -> list[list[float]]:
            if hasattr(provider, "get_embeddings"):
                return await provider.get_embeddings(texts)
            results: list[list[float]] = []
            for t in texts:
                vec = await provider.get_embedding(t)
                results.append(vec)
            return results

        self._bridge = bridge


class AliceMemoryPlugin(Star):
    """Alice 三层记忆系统插件主类。

    继承 AstrBot Star 基类，通过 @filter 装饰器注册钩子。
    __init_subclass__ 机制自动将本类注册为 AstrBot 插件。
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        """
        Args:
            context: AstrBot 上下文，提供 llm_generate/cron_manager/get_all_stars 等能力
            config:  框架从 WebUI 读取的原始配置 dict → PluginConfig.from_framework_config() 转换
        """
        super().__init__(context)

        # =====================================================================
        # Layer 0: 配置加载
        # =====================================================================
        # from_framework_config() 自动过滤无效 key，缺失 key 使用 Field default
        # 这意味着用户可以只在 WebUI 修改关心的配置项，其余全部用默认值
        self.plugin_config = PluginConfig.from_framework_config(config or {})
        logger.info(
            "[AliceMemory] PluginConfig 加载完成 | fields=%d | data_dir=%s",
            len(self.plugin_config.model_fields),
            self.plugin_config.data_dir,
        )

        # =====================================================================
        # Layer 1: 身份 & 存储 & 向量 & 分析（独立不互依赖）
        # =====================================================================
        # IdentityModule: 将不同平台(QQ/微信)的用户 ID 映射为统一内部 UUID
        self._identity = IdentityModule(self.plugin_config.data_dir)
        # MemoryStorage: L1/L2/L3 三层 JSON 文件持久化
        self._storage = MemoryStorage(self.plugin_config)

        # ---- EmbeddingProvider 延迟解析（P10 核心改动） ----
        # EmbeddingResolver 不在 __init__ 中立即解析 Provider，
        # 而是延迟到首次 L3 操作（search/add_memory）时才解析。
        # 这是因为 AstrBot 先初始化插件再初始化 ProviderManager，
        # __init__ 中 get_all_embedding_providers() 始终返回空列表。
        # 延迟解析后，首次 L3 调用时 Provider 已就绪，解析成功。
        resolver = EmbeddingResolver(context, self.plugin_config.l3_embedding_provider)
        self._vector_store = VectorStore(
            self.plugin_config.data_dir,
            self.plugin_config,
            embedding_func=resolver,
        )
        # 注册 collection 重建回调：维度变化时立即触发 JSON 恢复（P16）
        self._vector_store.set_rebuild_callback(self._recover_l3_from_json)
        # ImportanceAnalyzer: 调用 LLM 对内容进行重要性评分（0-10）
        self._analyzer = ImportanceAnalyzer(context, self.plugin_config)
        logger.info(
            "[AliceMemory] 模块就绪 | Identity ✓ | Storage ✓ | VectorStore ✓ | Analyzer ✓"
        )

        # =====================================================================
        # Layer 2+3: 压缩 & 注入
        # =====================================================================
        # DialogueCompressor: Path A（周压缩）和 Path B（日压缩）双路 LLM 摘要
        self._compressor = DialogueCompressor(context, self._storage, self.plugin_config)
        # ContextInjector: 四管线独立注入 — L1/L2-A/L2-B/L3
        self._injector = ContextInjector(
            self._storage, self._vector_store, self._identity, self.plugin_config,
        )
        logger.info("[AliceMemory] 模块就绪 | Compressor ✓ | ContextInjector ✓")

        # =====================================================================
        # Layer 4: 定时调度
        # =====================================================================
        # Scheduler 向 AstrBot CronJobManager 注册 6 项 cron 任务。
        # add_basic_job 是协程，需 create_task 延迟调度（同 _recover_l3_from_json 模式）。
        import asyncio as _asyncio
        _loop = _asyncio.get_event_loop()
        self._scheduler = Scheduler(
            context, self._storage, self._identity, self._vector_store,
            self.plugin_config, self._compressor, self._analyzer,
        )
        self._scheduler_start_task = _loop.create_task(self._scheduler.start())
        logger.info("[AliceMemory] 定时调度就绪 | Scheduler ✓")

        # ---- 延迟恢复: JSON → ChromaDB ----
        # 若 l3/{uid}.json 有数据但 ChromaDB 为空，从 JSON 重建向量索引。
        # create_task 延迟执行，等待 ProviderManager 初始化完毕。
        _loop.create_task(self._recover_l3_from_json())

        logger.info("[AliceMemory] 插件初始化完成")

    # =========================================================================
    # 公开接口 — 供主动层/中间层调用
    #
    # 这些 @property 将内部模块暴露为只读接口。
    # 主动层通过 context.get_all_stars() 获取本插件实例后，
    # 可直接调用 plugin.storage / plugin.injector / ... 等。
    # =========================================================================

    @property
    def storage(self) -> MemoryStorage:
        """公开存储模块，供主动层/中间层调用。"""
        return self._storage

    @property
    def vector_store(self) -> VectorStore:
        """公开向量存储模块。"""
        return self._vector_store

    @property
    def identity(self) -> IdentityModule:
        """公开身份模块。"""
        return self._identity

    @property
    def injector(self) -> ContextInjector:
        """公开上下文注入器。"""
        return self._injector

    @property
    def compressor(self) -> DialogueCompressor:
        """公开压缩器模块。"""
        return self._compressor

    @property
    def analyzer(self) -> ImportanceAnalyzer:
        """公开分析器模块。"""
        return self._analyzer

    # =========================================================================
    # LLM 钩子 — AstrBot 框架在 LLM 调用前后触发
    # =========================================================================

    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """LLM 请求前钩子 — 存储对话 + 注入记忆上下文 + L3 晋升判断。

        执行顺序决定了数据流的完整性:
          (1) 先落盘 → (2) 清空 AstrBot 历史（可选）→ (3) 从盘读回注入 → (4) 重要性评分

        为什么先落盘再注入？
          manage_context=true 时会清空 req.contexts。
          如果先清空再注入，注入的 L1 数据完全来自磁盘 JSON（而非 AstrBot 自带的上下文），
          这样保证了单一数据源，无重叠。
        """
        try:
            # ---- 钩子总开关 ----
            # hook_enabled=false 时插件完全静默：不存储，不注入，不评分
            # 用于紧急禁用或主动层接管模式
            if not self.plugin_config.hook_enabled:
                return

            # ---- 身份识别 ----
            # 跨平台统一身份: platform(如 qq_official) + platform_user_id(如 QQ号)
            # → 内部 UUID。首次出现的用户自动注册。
            platform = event.get_platform_name()
            platform_user_id = event.get_sender_id()
            user_id = self._identity.get_user_id(platform, platform_user_id)
            if not user_id:
                user_id = self._identity.register_user(platform, platform_user_id)

            # 空消息跳过（如图片消息无文本）
            content = event.get_message_str() or ""
            if not content.strip():
                return

            # ---- manage_context: 可选的全权接管模式 ----
            # 开启后: req.contexts = [] → AstrBot 自带对话历史不送入 LLM
            # 由插件 L1 注入的轮次对话完全替代
            # 默认 false，不影响标准 AstrBot 行为
            if self.plugin_config.manage_context:
                req.contexts = []
                logger.info("[AliceMemory] 已清空 AstrBot 对话历史")

            # ---- L1 存储: 先落盘 JSON ----
            # 无论 manage_context 是否开启，都将原始对话存入磁盘
            # 这是所有记忆管线的数据来源
            self._storage.append_dialogue(user_id, "user", content)
            logger.debug(
                "[AliceMemory] L1 存储 | uid=%s... | role=user | len=%d",
                user_id[:8], len(content),
            )

            # ---- 记忆注入: 从磁盘读取 → 注入 req ----
            # inject_all 按 L1/L2-A/L2-B/L3 顺序依次注入
            # L1 末尾的 user 消息（=当前消息）会被去重，因为 req.prompt 已经包含它
            await self._injector.inject_all(user_id, req)
            logger.info(
                "[AliceMemory] 注入完成 | contexts=%d | extra_parts=%d",
                len(req.contexts), len(req.extra_user_content_parts),
            )

            # ---- L3 晋升: 重要性评分 ----
            # 对每条用户消息调用 LLM 评分（0-10 分）
            # 分数 ≥ importance_threshold(默认8) → 自动存入 L3 向量记忆
            if self.plugin_config.l3_enabled:
                try:
                    score = await self._analyzer.analyze(content)
                    logger.debug(
                        "[AliceMemory] 重要性评分 | score=%d | threshold=%d",
                        score, self.plugin_config.importance_threshold,
                    )
                    if score >= self.plugin_config.importance_threshold:
                        # P21 写入前去重
                        result = await self._vector_store.add_or_merge(
                            user_id, content, score, self._analyzer,
                            self.plugin_config.l3_merge_similarity,
                        )
                        if result["action"] == "merged":
                            self._storage.replace_l3_memory(
                                user_id, result["old_ids"], result["merged_content"],
                                {"importance": result["new_score"], "vector_id": result["vector_id"]},
                            )
                            logger.info(
                                "[AliceMemory] L3 合并晋升 | vid=%s | score=%.1f",
                                result["vector_id"][:8], result["new_score"],
                            )
                        else:
                            self._storage.add_l3_memory(
                                user_id, content,
                                {"importance": score, "vector_id": result["vector_id"]},
                            )
                            logger.info(
                                "[AliceMemory] L3 晋升 | vid=%s | score=%d",
                                result["vector_id"][:8], score,
                            )
                except Exception as e:
                    # L3 晋升失败不应阻塞正常对话
                    logger.error("[AliceMemory] L3 晋升失败 | %s", e)

        except Exception:
            # 顶层兜底: 无论如何不让插件崩溃影响 AstrBot 正常运行
            logger.error("[AliceMemory] on_llm_request 异常", exc_info=True)

    @filter.on_llm_response()
    async def on_llm_response(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        """LLM 响应后钩子 — 将助手回复存入 L1 磁盘。

        这使得下一轮对话的 L1 注入能包含完整的 user+assistant 轮次。
        hook_enabled=false 时跳过（与 on_llm_request 保持一致）。
        """
        try:
            if not self.plugin_config.hook_enabled:
                return

            # 提取纯文本回复（兼容不同 LLM Provider 的返回格式）
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
    # 用户命令 — 通过 @filter.command() 注册
    # 所有命令返回 AsyncGenerator，用 yield event.plain_result() 回复
    # =========================================================================

    @filter.command("compact")
    async def cmd_compact(
        self, event: AstrMessageEvent, date: str | None = None
    ):
        """手动压缩记忆。

        无参数 → Path A 周压缩（合并式周摘要）:
          原料: 已有周摘要 + 今日 L1 + Path B 日摘要
          输出: 覆盖式周摘要（写入 weekly/{uid}.json）

        有日期 → Path B 日压缩（提取式日摘要）:
          原料: 指定日期的 L1 对话
          输出: 日摘要（写入 l2/{uid}.json）
        """
        platform = event.get_platform_name()
        platform_user_id = event.get_sender_id()
        user_id = self._identity.get_user_id(platform, platform_user_id)
        if not user_id:
            yield event.plain_result("[AliceMemory] 未能识别用户身份")
            return

        try:
            if date:
                # 带日期参数 = Path B 日压缩
                item = await self._compressor.compress_day(user_id, date)
                if item is None:
                    yield event.plain_result(f"[AliceMemory] {date} 无对话可压缩")
                    return
                if not item.strip():
                    yield event.plain_result(
                        f"[AliceMemory] {date} 压缩失败：LLM 未生成有效摘要，请稍后重试"
                    )
                    return
                result_text = f"{date} 对话已压缩为日摘要"
            else:
                # 无参数 = Path A 周压缩
                item = await self._compressor.compress_context_summary(user_id)
                if item is None:
                    yield event.plain_result("[AliceMemory] 无内容可压缩为周摘要")
                    return
                if not item.strip():
                    yield event.plain_result(
                        "[AliceMemory] 周压缩失败：LLM 未生成有效摘要，请稍后重试"
                    )
                    return
                result_text = "周摘要已更新"

            # 根据 manual_compress_feedback_mode 生成用户可见的反馈
            feedback = await self._build_feedback(user_id, result_text)
            yield event.plain_result(feedback)

        except Exception as e:
            logger.error("[AliceMemory] /compact 失败 | %s", e, exc_info=True)
            yield event.plain_result(f"[AliceMemory] 压缩失败: {e}")

    @filter.command("important")
    async def cmd_important(
        self, event: AstrMessageEvent, *, content: str = ""
    ):
        """手动标记重要记忆 → 分析重要性并直接存入 L3。

        使用方式: /important <内容>
        流程: LLM 评分 → 调用 vector_store.add_memory() 存入 ChromaDB
        与自动晋升的区别: 跳过 importance_threshold 判断，直接存入
        """
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
            score = await self._analyzer.analyze(content)
            # P21 写入前去重：相似度高则合并
            result = await self._vector_store.add_or_merge(
                user_id, content, score, self._analyzer,
                self.plugin_config.l3_merge_similarity,
            )
            if result["action"] == "merged":
                self._storage.replace_l3_memory(
                    user_id, result["old_ids"], result["merged_content"],
                    {"importance": result["new_score"], "vector_id": result["vector_id"]},
                )
                yield event.plain_result(
                    f"[AliceMemory] 已合并到现有记忆 | id={result['vector_id'][:8]} | 分数={result['new_score']:.1f}"
                )
            else:
                self._storage.add_l3_memory(
                    user_id, content, {"importance": score, "vector_id": result["vector_id"]},
                )
                yield event.plain_result(
                    f"[AliceMemory] 已存入 L3 | id={result['vector_id'][:8]} | 重要性={score}/10"
                )
            logger.info("[AliceMemory] /important | vid=%s... | score=%d", result["vector_id"][:8], score)
        except Exception as e:
            logger.error("[AliceMemory] /important 失败 | %s", e, exc_info=True)
            yield event.plain_result(f"[AliceMemory] 存入失败: {e}")

    @filter.command("forget")
    async def cmd_forget(
        self, event: AstrMessageEvent, memory_id: str = ""
    ):
        """删除 L3 记忆。使用方式: /forget <记忆ID>。

        记忆 ID 可以从 /show_memory 的返回结果中获取（id:XXXXXXXX 前8位）。
        """
        if not memory_id.strip():
            yield event.plain_result("[AliceMemory] 用法: /forget <记忆ID>")
            return

        if self._vector_store.delete_memory(memory_id):
            # 同步删除 JSON 存储中的对应条目（遍历全部用户匹配 vector_id）
            for uid in self._identity.get_all_users():
                for m in self._storage.get_l3_memories(uid):
                    if m.metadata.get("vector_id", "").startswith(memory_id):
                        self._storage.delete_l3_memory(uid, m.memory_id)
                        break
            yield event.plain_result(f"[AliceMemory] 记忆 {memory_id[:8]} 已删除")
        else:
            yield event.plain_result(f"[AliceMemory] 记忆 {memory_id[:8]} 未找到")

    @filter.command("show_memory")
    async def cmd_show_memory(
        self, event: AstrMessageEvent, *, query: str = ""
    ):
        """语义搜索 L3 向量记忆。使用方式: /show_memory <查询>。

        流程: 查询词 → embedding → ChromaDB 语义检索 → 返回 top-5 结果。
        每次检索触发 access_count+1，增加记忆的"生命加成"（衰减模型中更难被遗忘）。
        """
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
            results = await self._vector_store.search(
                user_id, query, top_k=self.plugin_config.l3_search_count,
            )
            if not results:
                yield event.plain_result("[AliceMemory] 未找到相关记忆")
                return

            # 相似度阈值过滤（与 inject_l3 一致，P17 自校准优先）
            threshold = self._vector_store.get_effective_threshold()
            lines = ["[AliceMemory] L3 记忆搜索结果:"]
            filtered = 0
            for r in results:
                distance = r.get("distance", 0)
                similarity = 1.0 - distance
                if similarity < threshold:
                    continue
                filtered += 1
                content = r.get("content", "")[:80]
                vid = r.get("id", "?")[:8]
                meta = r.get("metadata", {})
                score = meta.get("importance", "?")
                lines.append(
                    f"  {filtered}. [{score}] {content}... (id:{vid} | 相似度:{similarity:.0%})"
                )

            if filtered == 0:
                yield event.plain_result("[AliceMemory] 未找到相关记忆")
            else:
                yield event.plain_result("\n".join(lines))

        except Exception as e:
            logger.error("[AliceMemory] /show_memory 失败 | %s", e, exc_info=True)
            yield event.plain_result(f"[AliceMemory] 搜索失败: {e}")

    @filter.command("l3_stats")
    async def cmd_l3_stats(self, event: AstrMessageEvent) -> None:
        """查看 L3 记忆状态：模型、总记忆数、当前阈值。"""
        try:
            total = sum(
                len(self._vector_store.get_user_memories(uid))
                for uid in self._identity.get_all_users()
            )
            threshold = self._vector_store.get_effective_threshold()
            yield event.plain_result(
                f"[AliceMemory] L3 状态:\n"
                f"  总记忆: {total} 条\n"
                f"  当前阈值: {threshold:.2f}"
            )
        except Exception as e:
            logger.error("[AliceMemory] /l3_stats 失败 | %s", e, exc_info=True)
            yield event.plain_result(f"[AliceMemory] 状态查询失败: {e}")

    @filter.command("l3_merge")
    async def cmd_l3_merge(self, event: AstrMessageEvent) -> None:
        """手动合并当前用户 L3 相似记忆（P20 新增）。"""
        platform = event.get_platform_name()
        platform_user_id = event.get_sender_id()
        user_id = self._identity.get_user_id(platform, platform_user_id)
        if not user_id:
            yield event.plain_result("[AliceMemory] 未能识别用户身份")
            return

        if not self._vector_store or not self._analyzer:
            yield event.plain_result("[AliceMemory] L3 模块未就绪")
            return

        threshold = self.plugin_config.l3_merge_similarity
        try:
            merged, details = await self._vector_store.merge_similar_for_user(
                user_id, self._analyzer, threshold,
            )
            # P21 同步 JSON
            for d in details:
                self._storage.replace_l3_memory(
                    user_id, d["old_ids"], d["content"],
                    {"importance": d["score"], "vector_id": d["new_vector_id"]},
                )
            if merged:
                yield event.plain_result(
                    f"[AliceMemory] L3 合并完成 | 合并={merged} 对"
                )
            else:
                yield event.plain_result("[AliceMemory] 无可合并记忆（需要 ≥2 条且相似度 ≥%.2f）" % threshold)
        except Exception as e:
            logger.error("[AliceMemory] /l3_merge 失败 | %s", e, exc_info=True)
            yield event.plain_result(f"[AliceMemory] 合并失败: {e}")

    # =========================================================================
    # 内部方法
    # =========================================================================

    async def _recover_l3_from_json(self) -> None:
        """从 JSON 恢复 L3 记忆到 ChromaDB（Provider 就绪后延迟执行）。

        场景: ChromaDB 数据丢失（迁移失败/手动清理），但 l3/{uid}.json 完好。
        遍历所有用户，对 ChromaDB 为空的用户从 JSON 重建向量索引。

        延迟机制: 通过 loop.create_task() 调用，等待 ProviderManager 初始化。
        若首次调用时 Provider 仍未就绪（EmbeddingResolver 抛 RuntimeError），
        静默跳过——后续 L3 操作会在 Provider 就绪后触发 _ensure_migrated。
        """
        import asyncio as _asyncio
        await _asyncio.sleep(3)  # 等待 ProviderManager 初始化
        try:
            for uid in self._identity.get_all_users():
                chroma_memories = self._vector_store.get_user_memories(uid)
                if chroma_memories:
                    continue  # ChromaDB 已有数据，跳过
                json_memories = self._storage.get_l3_memories(uid)
                if not json_memories:
                    continue
                logger.info(
                    "[AliceMemory] JSON → ChromaDB 恢复 | uid=%s... | count=%d",
                    uid[:8], len(json_memories),
                )
                for m in json_memories:
                    try:
                        # ChromaDB metadata 仅支持 str/int/float/bool，过滤 list/dict/None
                        clean_meta = {
                            k: v for k, v in m.metadata.items()
                            if isinstance(v, (str, int, float, bool))
                        }
                        await self._vector_store.add_memory(
                            uid, m.content,
                            {**clean_meta, "vector_id": m.memory_id},
                        )
                    except RuntimeError:
                        # Provider 仍未就绪，跳过本次恢复（下次 L3 操作会触发迁移）
                        logger.warning(
                            "[AliceMemory] L3 恢复跳过 | Provider 未就绪，将在首次 L3 操作时重试"
                        )
                        return
                    except Exception as e:
                        logger.error(
                            "[AliceMemory] L3 恢复单条失败 | %s", e,
                        )
                logger.info(
                    "[AliceMemory] JSON → ChromaDB 恢复完成 | uid=%s... | count=%d",
                    uid[:8], len(json_memories),
                )
            # 恢复完成后自校准阈值（P17）
            await self._vector_store._auto_calibrate()
        except Exception:
            logger.error("[AliceMemory] L3 JSON 恢复异常", exc_info=True)

    async def _build_feedback(self, user_id: str, default_text: str) -> str:
        """根据 manual_compress_feedback_mode 配置生成压缩反馈。

        四种模式:
          silent  — 不反馈（仅用于 cron 定时压缩）
          fixed   — 返回预设固定文本
          llm     — 调用 LLM 根据对话氛围动态生成反馈（默认）
          visible — 直接展示周摘要正文
        """
        mode = self.plugin_config.manual_compress_feedback_mode

        if mode == "silent":
            return default_text
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
                generate_config = {
                    "max_tokens": self.plugin_config.llm_max_tokens,
                    "temperature": self.plugin_config.llm_temperature,
                }
                if self.plugin_config.compress_model:
                    generate_config["model"] = self.plugin_config.compress_model
                feedback = await self._analyzer._context.llm_generate(
                    prompt, generate_config=generate_config,
                )
                return feedback.strip()
            except Exception:
                return default_text
        else:
            return default_text
