"""
调度器模块 — 7 段定时任务编排。

01:00 Path B 日压缩 / 01:30 L2 TTL 清理 / 02:00 L1 清理 / 03:00 L3 衰减+灰区重评
04:00 Path A 周压缩（含周摘要周一检测+过期检测） / 周一 05:00 周摘要重置（冗余）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from astrbot.api import logger

if TYPE_CHECKING:
    from memory.analyzer.analyzer import ImportanceAnalyzer
    from memory.compressor.compressor import DialogueCompressor
    from memory.identity.identity import IdentityModule
    from memory.plugin_config import PluginConfig
    from memory.storage.storage import MemoryStorage
    from memory.vector_store.vector_store import VectorStore


class Scheduler:
    """记忆定时任务调度器 — 5 段编排。

    不负责压缩/衰减/清理算法，仅调用各模块。
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
    # cron 映射
    # ==================================================================

    @staticmethod
    def _days_to_cron(days: int) -> str:
        """将 l3_merge_interval_days 映射为 cron 表达式（P8 动态 cron）。

        因为 cron 不支持"每 N 天"的任意值，将连续值映射为 7 个离散档位:
          每天 → 每周日 → 半月 → 约10天 → 每月 → 每2月 → 每季度 → 每半年 → 每年
        默认 30 天 → "0 6 1 * *"（每月1日6:00），与旧版硬编码完全一致。
        """
        if days <= 1:
            return "0 6 * * *"
        elif days <= 7:
            return "0 6 * * 0"
        elif days <= 14:
            return "0 6 1,15 * *"
        elif days <= 21:
            return "0 6 1,11,21 * *"
        elif days <= 31:
            return "0 6 1 * *"
        elif days <= 62:
            return "0 6 1 */2 *"
        elif days <= 92:
            return "0 6 1 */3 *"
        elif days <= 183:
            return "0 6 1 */6 *"
        else:
            return "0 6 1 1 *"

    # ==================================================================
    # 注册入口
    # ==================================================================

    async def start(self) -> None:
        """向 AstrBot CronJobManager 注册 6 项定时任务。"""
        cron_manager = getattr(self._context, "cron_manager", None)
        if cron_manager is None:
            logger.warning("[AliceMemory] CronManager 不可用，跳过定时任务注册")
            return

        merge_cron = self._days_to_cron(self._config.l3_merge_interval_days)
        logger.info(
            "[AliceMemory] L3 合并周期 | interval=%dd | cron=%s",
            self._config.l3_merge_interval_days,
            merge_cron,
        )

        jobs = [
            ("0 1 * * *",   self._safe_wrap(self._compress_daily),  "Path B 日压缩"),
            ("30 1 * * *",  self._safe_wrap(self._l2_ttl_cleanup),  "L2 TTL清理"),
            ("0 2 * * *",   self._safe_wrap(self._l1_cleanup),      "L1 轮次裁剪"),
            ("0 3 * * *",   self._safe_wrap(self._l3_maintenance),  "L3 衰减+灰区重评"),
            ("0 4 * * *",   self._safe_wrap(self._compress_context), "Path A 周压缩"),
            ("0 5 * * 1",   self._safe_wrap(self._reset_weekly),    "周一重置周摘要"),
            (merge_cron,     self._safe_wrap(self._l3_merge),        "L3 合并"),
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
            from datetime import datetime, timedelta, timezone
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            for uid in self._identity_module.get_all_users():
                try:
                    await self._compressor.compress_day(uid, yesterday)
                except Exception as e:
                    logger.error("[AliceMemory] Path B 压缩失败 | uid=%s | %s", uid[:8], e)
        except Exception:
            logger.error("[AliceMemory] Path B 日压缩异常", exc_info=True)

    # ==================================================================
    # 02:00 — L1 轮次裁剪
    # ==================================================================

    async def _l1_cleanup(self) -> None:
        logger.info("[AliceMemory] 定时触发 | 02:00 L1 轮次裁剪")
        if not self._config.l1_enabled:
            return
        try:
            total = 0
            keep = self._config.l1_save_rounds
            for uid in self._identity_module.get_all_users():
                removed = self._storage.trim_to_recent_rounds(uid, keep)
                total += removed
            if total:
                logger.info(
                    "[AliceMemory] L1 裁剪 | keep_rounds=%d | 删除=%d 条",
                    keep,
                    total,
                )
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
    # 01:30 — L2 TTL 清理
    # ==================================================================

    async def _l2_ttl_cleanup(self) -> None:
        """删除超过 TTL 的 L2 日摘要，防止磁盘持续膨胀。"""
        if not self._config.l2_path_b_enabled:
            return
        try:
            for uid in self._identity_module.get_all_users():
                removed = self._storage.delete_old_summaries(uid)
                if removed:
                    logger.info(
                        "[AliceMemory] L2 TTL 清理 | uid=%s... | 删除=%d条",
                        uid[:8], removed,
                    )
        except Exception:
            logger.error("[AliceMemory] L2 TTL 清理异常", exc_info=True)

    # ==================================================================
    # 04:00 — Path A 周压缩
    # ==================================================================

    async def _compress_context(self) -> None:
        if not self._compressor or not self._config.l2_path_a_enabled:
            return
        try:
            for uid in self._identity_module.get_all_users():
                try:
                    # 周摘要重置逻辑（三层保护，不依赖单独的 cron）：
                    # 第一层：如果今天是周一，先清除旧周摘要。
                    #         04:00 Path A 已确认稳定运行，在此内嵌重置比
                    #         依赖 05:00 的独立 cron 更可靠（cron misfire 风险）。
                    # 第二层：如果 week_start 距今超过 7 天（周一 cron 也 misfire 了），
                    #         主动清除，保证不会无限累积。
                    from datetime import datetime, timezone, timedelta
                    today = datetime.now(timezone.utc)
                    is_monday = today.weekday() == 0

                    weekly = self._storage.get_weekly_summary(uid)
                    if weekly and weekly.get("week_start"):
                        should_reset = False
                        if is_monday:
                            should_reset = True
                            logger.info(
                                "[AliceMemory] 周一检测 | uid=%s... | 主动清除旧周摘要",
                                uid[:8],
                            )
                        else:
                            try:
                                ws = datetime.strptime(
                                    weekly["week_start"], "%Y-%m-%d"
                                ).replace(tzinfo=timezone.utc)
                                age_days = (today - ws).days
                                if age_days > 7:
                                    should_reset = True
                                    logger.warning(
                                        "[AliceMemory] 周摘要过期 | uid=%s... | week_start=%s | age=%dd | 主动清除",
                                        uid[:8], weekly["week_start"], age_days,
                                    )
                            except (ValueError, TypeError):
                                pass
                        if should_reset:
                            self._storage.clear_weekly_summary(uid)

                    summary = await self._compressor.compress_context_summary(uid)
                    if summary:
                        week_start = today - timedelta(days=today.weekday())
                        self._storage.set_weekly_summary(
                            uid, summary, week_start.strftime("%Y-%m-%d")
                        )
                except Exception as e:
                    logger.error("[AliceMemory] Path A 压缩失败 | uid=%s | %s", uid[:8], e)
        except Exception:
            logger.error("[AliceMemory] Path A 周压缩异常", exc_info=True)

    # ==================================================================
    # 周一 05:00 — 重置周摘要
    # ==================================================================

    async def _reset_weekly(self) -> None:
        """周一 05:00 冗余重置（主重置逻辑已内嵌到 04:00 Path A 压缩）。

        保留此 cron 作为冗余保护：如果 04:00 的 Path A 压缩因异常未执行，
        05:00 的独立重置仍可清除周摘要。
        """
        logger.warning("[AliceMemory] [DEBUG] 周一重置 cron 触发 | 正在执行...")
        try:
            count = 0
            for uid in self._identity_module.get_all_users():
                if self._storage.clear_weekly_summary(uid):
                    count += 1
            logger.info("[AliceMemory] 周一重置完成 | 清除=%d 个周摘要", count)
        except Exception:
            logger.error("[AliceMemory] 周一重置异常", exc_info=True)

    # ==================================================================
    # 动态 cron — L3 月度合并
    # ==================================================================

    async def _l3_merge(self) -> None:
        """定时合并 L3 相似记忆（P20：核心逻辑已移至 VectorStore）。

        遍历全部用户，调用 merge_similar_for_user()。
        """
        logger.info(
            "[AliceMemory] 定时触发 | L3 合并 (interval=%dd)",
            self._config.l3_merge_interval_days,
        )
        if not self._vector_store or not self._analyzer or not self._config.l3_enabled:
            return
        threshold = self._config.l3_merge_similarity
        try:
            for uid in self._identity_module.get_all_users():
                merged, details = await self._vector_store.merge_similar_for_user(
                    uid, self._analyzer, threshold,
                )
                # P21 同步 JSON
                for d in details:
                    self._storage.replace_l3_memory(
                        uid, d["old_ids"], d["content"],
                        {"importance": d["score"], "vector_id": d["new_vector_id"]},
                    )
                if merged:
                    logger.info(
                        "[AliceMemory] L3 合并 | uid=%s... | 合并=%d 对",
                        uid[:8], merged,
                    )
        except Exception:
            logger.error("[AliceMemory] L3 合并异常", exc_info=True)
