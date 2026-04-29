"""
调度器模块测试 — 使用 PluginConfig。
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from memory.identity.identity import IdentityModule
from memory.plugin_config import PluginConfig
from memory.scheduler.scheduler import Scheduler
from memory.storage.storage import MemoryStorage


class TestScheduler:
    """Scheduler 类的测试。"""

    @pytest.fixture
    def temp_dir(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def config(self, temp_dir: Path) -> PluginConfig:
        return PluginConfig(
            data_dir=temp_dir,
            l1_retention_days=3,
            l2_ttl=7,
            l3_merge_interval_days=30,
            l2_path_a_enabled=True,
            l2_path_b_enabled=True,
            l1_enabled=True,
            l3_enabled=True,
        )

    @pytest.fixture
    def mock_context(self) -> MagicMock:
        context = MagicMock()
        context.cron_manager = MagicMock()
        context.cron_manager.add_basic_job = MagicMock()
        return context

    @pytest.fixture
    def mock_compressor(self) -> MagicMock:
        c = MagicMock()
        c.compress_day = AsyncMock()
        c.compress_context_summary = AsyncMock()
        return c

    @pytest.fixture
    def mock_analyzer(self) -> MagicMock:
        a = MagicMock()
        a.batch_recheck = AsyncMock(return_value=[])
        return a

    @pytest.fixture
    def identity_module(self, temp_dir: Path) -> IdentityModule:
        return IdentityModule(temp_dir)

    @pytest.fixture
    def storage(self, config: PluginConfig) -> MemoryStorage:
        return MemoryStorage(config)

    @pytest.fixture
    def scheduler(
        self, mock_context, storage, identity_module, config,
        mock_compressor, mock_analyzer,
    ) -> Scheduler:
        return Scheduler(
            mock_context, storage, identity_module,
            None, config, mock_compressor, mock_analyzer,
        )

    # 注册
    # ================================================================

    def test_start_registers_6_tasks(
        self, scheduler: Scheduler, mock_context: MagicMock,
    ) -> None:
        scheduler.start()
        assert mock_context.cron_manager.add_basic_job.call_count == 6

    def test_start_no_cron_manager(
        self, storage, identity_module, config, mock_compressor, mock_analyzer,
    ) -> None:
        mock_ctx = MagicMock()
        mock_ctx.cron_manager = None
        s = Scheduler(mock_ctx, storage, identity_module, None, config,
                       mock_compressor, mock_analyzer)
        s.start()  # 静默返回，不抛异常

    # Path B 日压缩
    # ================================================================

    @pytest.mark.asyncio
    async def test_compress_daily(
        self, scheduler: Scheduler, identity_module: IdentityModule,
        mock_compressor: MagicMock,
    ) -> None:
        identity_module.register_user("test", "u1")
        mock_compressor.compress_day.return_value = "日摘要"
        await scheduler._compress_daily()
        assert mock_compressor.compress_day.called

    # L1 清理
    # ================================================================

    @pytest.mark.asyncio
    async def test_l1_cleanup(
        self, scheduler: Scheduler,
    ) -> None:
        await scheduler._l1_cleanup()  # 无数据，静默完成

    # L3 维护
    # ================================================================

    @pytest.mark.asyncio
    async def test_l3_maintenance_no_vector_store(
        self, scheduler: Scheduler,
    ) -> None:
        await scheduler._l3_maintenance()  # 无 VectorStore，静默返回

    # L3 月度合并
    # ================================================================

    @pytest.mark.asyncio
    async def test_l3_merge_no_vector_store(
        self, scheduler: Scheduler,
    ) -> None:
        await scheduler._l3_merge()  # 无 VectorStore，静默返回

    @pytest.mark.asyncio
    async def test_l3_merge(
        self, scheduler: Scheduler, mock_analyzer: MagicMock,
        identity_module: IdentityModule,
    ) -> None:
        """有 2 条记忆但相似度不足，不应触发合并。"""
        identity_module.register_user("test", "uid1")
        scheduler._vector_store = MagicMock()
        scheduler._vector_store.get_user_memories.return_value = [
            {"id": "v1", "content": "我喜欢吃苹果", "metadata": {"importance": 8}},
            {"id": "v2", "content": "今天天气真好", "metadata": {"importance": 3}},
        ]
        scheduler._vector_store.find_similar_by_content = AsyncMock(return_value=[])
        mock_analyzer.merge_content = AsyncMock()
        scheduler._vector_store.merge_memories = AsyncMock()
        await scheduler._l3_merge()
        mock_analyzer.merge_content.assert_not_called()
