"""
存储模块测试 — 使用 PluginConfig。
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from memory.plugin_config import PluginConfig
from memory.storage.storage import MemoryStorage


class TestMemoryStorage:
    """MemoryStorage 类的测试。"""

    @pytest.fixture
    def temp_dir(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def config(self, temp_dir: Path) -> PluginConfig:
        """创建测试用 PluginConfig，数据写入临时目录。"""
        return PluginConfig(data_dir=temp_dir, l1_retention_days=3, l2_ttl=7)

    @pytest.fixture
    def storage(self, config: PluginConfig) -> MemoryStorage:
        return MemoryStorage(config)

    # L1 — 原始对话
    # ================================================================

    def test_append_dialogue(self, storage: MemoryStorage) -> None:
        item = storage.append_dialogue("user1", "user", "Hello")
        assert item.user_id == "user1"
        assert item.role == "user"
        assert item.content == "Hello"
        assert item.message_id

    def test_get_l1_dialogues(self, storage: MemoryStorage) -> None:
        storage.append_dialogue("user1", "user", "Hello")
        storage.append_dialogue("user1", "assistant", "Hi")
        dialogues = storage.get_l1_dialogues("user1")
        assert len(dialogues) == 2

    # L2 Path B — 每日摘要
    # ================================================================

    def test_add_summary(self, storage: MemoryStorage) -> None:
        item = storage.add_summary("user1", "2024-04-20", "Test summary", 5)
        assert item.user_id == "user1"
        assert item.date == "2024-04-20"
        assert item.summary == "Test summary"
        assert item.importance == 5

    def test_add_summary_overwrite(self, storage: MemoryStorage) -> None:
        """同一日期重复写入应覆盖旧摘要。"""
        storage.add_summary("user1", "2024-04-20", "First", 3)
        storage.add_summary("user1", "2024-04-20", "Second", 7)
        summaries = storage.get_daily_summaries("user1")
        assert len(summaries) == 1
        assert summaries[0].summary == "Second"

    def test_get_daily_summaries(self, storage: MemoryStorage) -> None:
        storage.add_summary("user1", "2024-04-20", "Summary A", 5)
        storage.add_summary("user1", "2024-04-21", "Summary B", 7)
        all_s = storage.get_daily_summaries("user1")
        assert len(all_s) == 2

    def test_get_daily_summaries_last(self, storage: MemoryStorage) -> None:
        """last=N 应限制返回数量。"""
        storage.add_summary("user1", "2024-04-19", "Old", 3)
        storage.add_summary("user1", "2024-04-20", "Mid", 5)
        storage.add_summary("user1", "2024-04-21", "New", 7)
        recent = storage.get_daily_summaries("user1", last=2)
        assert len(recent) == 2
        assert recent[0].date == "2024-04-21"

    def test_delete_old_summaries(self, storage: MemoryStorage) -> None:
        """过期摘要应被清理。"""
        storage.add_summary("user1", "2020-01-01", "Very old", 3)
        storage.add_summary("user1", "2099-12-31", "Future", 8)
        removed = storage.delete_old_summaries("user1", ttl=7)
        assert removed == 1  # 只有过期的那条被删

    # L2 Path A — 周摘要
    # ================================================================

    def test_weekly_summary_crud(self, storage: MemoryStorage) -> None:
        """周摘要的完整 CRUD。"""
        assert storage.get_weekly_summary("user1") is None
        storage.set_weekly_summary("user1", "本周摘要内容", "2024-04-22")
        ws = storage.get_weekly_summary("user1")
        assert ws is not None
        assert ws["summary"] == "本周摘要内容"
        assert ws["week_start"] == "2024-04-22"
        # 覆盖
        storage.set_weekly_summary("user1", "新摘要", "2024-04-22")
        ws2 = storage.get_weekly_summary("user1")
        assert ws2["summary"] == "新摘要"
        # 清空
        assert storage.clear_weekly_summary("user1") is True
        assert storage.get_weekly_summary("user1") is None

    def test_clear_weekly_summary_no_file(self, storage: MemoryStorage) -> None:
        """清空不存在的文件返回 False。"""
        assert storage.clear_weekly_summary("nobody") is False

    # L3 — 重要记忆
    # ================================================================

    def test_add_l3_memory(self, storage: MemoryStorage) -> None:
        memory_id = storage.add_l3_memory("user1", "Important info", {"key": "value"})
        assert memory_id
        assert len(memory_id) > 0

    def test_get_l3_memories(self, storage: MemoryStorage) -> None:
        storage.add_l3_memory("user1", "Memory 1")
        storage.add_l3_memory("user1", "Memory 2")
        assert len(storage.get_l3_memories("user1")) == 2

