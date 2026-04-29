"""
调度器模块 — 6 段定时任务编排。

01:00 Path B 日压缩 / 02:00 L1 清理 / 03:00 L3 衰减+灰区重评
04:00 Path A 周压缩 / 周一 05:00 周摘要重置 / 1日06:00 L3 月度合并
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from astrbot.api import logger

if TYPE_CHECKING:
    from memory.analyzer.analyzer import ImportanceAnalyzer
    from memory.compressor.compressor import DialogueCompressor
    from memory.identity.identity import IdentityModule
    from memory.plugin_config import PluginConfig
    from memory.storage.storage import MemoryStorage
    from memory.vector_store.vector_store import VectorStore


class Scheduler:
    """记忆定时任务调度器 — 6 段编排。

    不负责压缩/衰减/合并/清理算法，仅调用各模块。
    """

    def __init__(
        self,
        context: Any,
        storage: MemoryStorage,
        identity_module: IdentityModule,
        vector_store: VectorStore | None,
        config: PluginConfig,
        compressor: DialogueCompressor | None = None,
        analyzer: ImportanceAnalyzer | None = None,
    ) -> None:
        self._context = context
        self._storage = storage
        self._identity_module = identity_module
        self._vector_store = vector_store
        self._config = config
        self._compressor = compressor
        self._analyzer = analyzer

    # ==================================================================
    # 注册入口
    # ==================================================================

    async def start(self) -> None:
        """向 AstrBot CronJobManager 注册 6 项定时任务。"""
        cron_manager = getattr(self._context, "cron_manager", None)
        if cron_manager is None:
            logger.warning("[AliceMemory] CronManager 不可用，跳过定时任务注册")
            return

        jobs = [
            ("0 1 * * *",   self._safe_wrap(self._compress_daily),  "Path B 日压缩"),
            ("0 2 * * *",   self._safe_wrap(self._l1_cleanup),      "L1 过期清理"),
            ("0 3 * * *",   self._safe_wrap(self._l3_maintenance),  "L3 衰减+灰区重评"),
            ("0 4 * * *",   self._safe_wrap(self._compress_context), "Path A 周压缩"),
            ("0 5 * * 1",   self._safe_wrap(self._reset_weekly),    "周一重置周摘要"),
            ("0 6 1 * *",   self._safe_wrap(self._l3_merge),        "L3 月度合并"),
        ]
        for i, (cron, handler, desc) in enumerate(jobs):
            await cron_manager.add_basic_job(
                name=f"AliceMemory_{desc.replace(' ', '_')}",
                cron_expression=cron,
                handler=handler,
                description=f"[AliceMemory] {desc}",
                timezone="Asia/Shanghai",
                persistent=True,
            )

        logger.info("[AliceMemory] 定时任务注册 | tasks=%d", len(jobs))

    # ==================================================================
    # 安全包装：异步执行 + 错误日志
    # ==================================================================

    def _safe_wrap(self, coro_func):
        """同步包装器：在事件循环中执行异步任务，捕获并记录异常。"""
        import asyncio

        def wrapper():
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(coro_func())
            except Exception:
                logger.error("[AliceMemory] 定时任务调度失败", exc_info=True)

        return wrapper

    # ==================================================================
    # 01:00 — Path B 日压缩
    # ==================================================================

    async def _compress_daily(self) -> None:
        if not self._compressor or not self._config.l2_path_b_enabled:
            return
        try:
            from datetime import datetime, timedelta
            cst = ZoneInfo("Asia/Shanghai")
            yesterday = (datetime.now(cst) - timedelta(days=1)).strftime("%Y-%m-%d")
            for uid in self._identity_module.get_all_users():
                try:
                    result = await self._compressor.compress_day(uid, yesterday)
                    if result:
                        logger.info("[AliceMemory] Path B 压缩完成 | uid=%s... | date=%s", uid[:8], yesterday)
                except Exception as e:
                    logger.error("[AliceMemory] Path B 压缩失败 | uid=%s | %s", uid[:8], e)
        except Exception:
            logger.error("[AliceMemory] Path B 日压缩异常", exc_info=True)

    # ==================================================================
    # 02:00 — L1 清理
    # ==================================================================

    async def _l1_cleanup(self) -> None:
        if not self._config.l1_enabled:
            return
        try:
            total = 0
            for uid in self._identity_module.get_all_users():
                total += self._storage.delete_old_l1_dialogues(uid)
            if total:
                logger.info("[AliceMemory] L1 清理 | 删除=%d 条", total)
        except Exception:
            logger.error("[AliceMemory] L1 清理异常", exc_info=True)

    # ==================================================================
    # 03:00 — L3 衰减 + 灰区重评
    # ==================================================================

    async def _l3_maintenance(self) -> None:
        if not self._vector_store or not self._config.l3_enabled:
            return
        try:
            for uid in self._identity_module.get_all_users():
                deleted, gray = self._vector_store.apply_decay(uid)
                if deleted or gray:
                    logger.info(
                        "[AliceMemory] L3 衰减 | uid=%s... | 删除=%d | 灰区=%d",
                        uid[:8], deleted, gray,
                    )
                if gray and self._analyzer:
                    gray_memories = self._vector_store.get_gray_zone_memories(uid)
                    if gray_memories:
                        results = await self._analyzer.batch_recheck(gray_memories)
                        for r in results:
                            if r["should_keep"]:
                                self._vector_store.update_metadata(
                                    r["vector_id"],
                                    {"importance": r["new_score"]},
                                )
                            else:
                                self._vector_store.delete_memory(r["vector_id"])
        except Exception:
            logger.error("[AliceMemory] L3 维护异常", exc_info=True)

    # ==================================================================
    # 04:00 — Path A 周压缩
    # ==================================================================

    async def _compress_context(self) -> None:
        if not self._compressor or not self._config.l2_path_a_enabled:
            return
        try:
            for uid in self._identity_module.get_all_users():
                try:
                    summary = await self._compressor.compress_context_summary(uid)
                    if summary:
                        from datetime import datetime
                        cst = ZoneInfo("Asia/Shanghai")
                        week_start = datetime.now(cst).strftime("%Y-%m-%d")
                        self._storage.set_weekly_summary(uid, summary, week_start)
                        logger.info("[AliceMemory] Path A 压缩完成 | uid=%s... | week_start=%s", uid[:8], week_start)
                except Exception as e:
                    logger.error("[AliceMemory] Path A 压缩失败 | uid=%s | %s", uid[:8], e)
        except Exception:
            logger.error("[AliceMemory] Path A 周压缩异常", exc_info=True)

    # ==================================================================
    # 周一 05:00 — 重置周摘要
    # ==================================================================

    async def _reset_weekly(self) -> None:
        try:
            count = 0
            for uid in self._identity_module.get_all_users():
                if self._storage.clear_weekly_summary(uid):
                    count += 1
            if count:
                logger.info("[AliceMemory] 周一重置 | 清除=%d 个周摘要", count)
        except Exception:
            logger.error("[AliceMemory] 周一重置异常", exc_info=True)

    # ==================================================================
    # 每月 1 日 06:00 — L3 月度合并
    # ==================================================================

    async def _l3_merge(self) -> None:
        """贪心法合并 L3 相似记忆。"""
        if not self._vector_store or not self._analyzer or not self._config.l3_enabled:
            return
        threshold = self._config.l3_merge_similarity
        try:
            for uid in self._identity_module.get_all_users():
                memories = self._vector_store.get_user_memories(uid)
                if len(memories) < 2:
                    continue
                # 按 importance 降序排列，优先保留高价值记忆
                memories.sort(
                    key=lambda m: m["metadata"].get("importance", 0),
                    reverse=True,
                )
                consumed: set[str] = set()
                merged_count = 0

                for m1 in memories:
                    if m1["id"] in consumed:
                        continue
                    similar = await self._vector_store.find_similar_by_content(
                        uid, m1["content"], threshold,
                    )
                    for s in similar:
                        if s["id"] in consumed or s["id"] == m1["id"]:
                            continue
                        merged = await self._analyzer.merge_content(
                            m1["content"], s["content"],
                        )
                        if not merged:
                            continue
                        new_score = max(
                            m1["metadata"].get("importance", 0),
                            s["metadata"].get("importance", 0),
                        ) + 0.5
                        await self._vector_store.merge_memories(
                            m1["id"], s["id"], merged, new_score,
                        )
                        consumed.add(m1["id"])
                        consumed.add(s["id"])
                        merged_count += 1
                        break  # m1 已消费

                if merged_count:
                    logger.info(
                        "[AliceMemory] L3 合并 | uid=%s... | 合并=%d 对",
                        uid[:8], merged_count,
                    )
        except Exception:
            logger.error("[AliceMemory] L3 合并异常", exc_info=True)
