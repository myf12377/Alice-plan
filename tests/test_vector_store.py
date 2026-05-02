"""
向量存储模块测试 — 使用 PluginConfig。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from memory.plugin_config import PluginConfig
from memory.vector_store.vector_store import VectorStore


class TestVectorStore:
    """VectorStore 类的测试。"""

    @pytest.fixture
    def temp_dir(self) -> Iterator[Path]:
        tmp = tempfile.mkdtemp()
        path = Path(tmp)
        yield path
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir: Path) -> PluginConfig:
        return PluginConfig(
            data_dir=temp_dir,
            l3_decay_rate=0.995,
            l3_access_bonus=0.3,
            l3_delete_threshold=3.0,
            l3_gray_zone_upper=5.0,
            l3_merge_similarity=0.9,
        )

    @pytest.fixture
    def vector_store(
        self,
        temp_dir: Path,
        config: PluginConfig,
    ) -> Iterator[VectorStore]:
        vs = VectorStore(temp_dir, config)
        yield vs
        vs.close()

    # CRUD
    # ================================================================

    async def test_add_memory(self, vector_store: VectorStore) -> None:
        memory_id = await vector_store.add_memory("user1", "Test content")
        assert memory_id
        assert len(memory_id) > 0

    async def test_add_memory_with_metadata(
        self,
        vector_store: VectorStore,
    ) -> None:
        memory_id = await vector_store.add_memory(
            "user1",
            "Test content",
            {"importance": 8},
        )
        assert memory_id

    async def test_get_user_memories(self, vector_store: VectorStore) -> None:
        await vector_store.add_memory("user1", "Memory 1")
        await vector_store.add_memory("user1", "Memory 2")
        assert len(vector_store.get_user_memories("user1")) == 2

    def test_get_user_memories_empty(self, vector_store: VectorStore) -> None:
        assert vector_store.get_user_memories("nonexistent") == []

    async def test_search(self, vector_store: VectorStore) -> None:
        await vector_store.add_memory("user1", "Apple is a fruit")
        await vector_store.add_memory("user1", "Car is a vehicle")
        results = await vector_store.search("user1", "fruit", top_k=5)
        assert len(results) >= 1

    async def test_search_returns_relevant(
        self,
        vector_store: VectorStore,
    ) -> None:
        """搜索应返回语义相关的结果。"""
        await vector_store.add_memory("user1", "Apple is a fruit")
        await vector_store.add_memory("user1", "Car is a vehicle")
        results = await vector_store.search("user1", "fruit", top_k=5)
        assert len(results) >= 1
        # 第一条结果应包含 Apple
        assert "Apple" in results[0]["content"]

    async def test_delete_memory(self, vector_store: VectorStore) -> None:
        memory_id = await vector_store.add_memory("user1", "To be deleted")
        assert vector_store.delete_memory(memory_id) is True

    def test_delete_memory_not_found(self, vector_store: VectorStore) -> None:
        assert vector_store.delete_memory("nonexistent-id") is False

    async def test_update_metadata(self, vector_store: VectorStore) -> None:
        memory_id = await vector_store.add_memory("user1", "Test content")
        result = vector_store.update_metadata(memory_id, {"new_key": "new_value"})
        assert result is True

    def test_update_metadata_not_found(
        self,
        vector_store: VectorStore,
    ) -> None:
        assert vector_store.update_metadata("nonexistent-id", {"key": "value"}) is False

    # 衰减模型
    # ================================================================

    def test_apply_decay_no_memories(self, vector_store: VectorStore) -> None:
        deleted, gray = vector_store.apply_decay("user1")
        assert deleted == 0
        assert gray == 0

    async def test_apply_decay_keeps_high_score(
        self,
        vector_store: VectorStore,
    ) -> None:
        memory_id = await vector_store.add_memory(
            "user1",
            "Important fact",
            {"importance": 10},
        )
        vector_store.update_metadata(memory_id, {"created_at": "2026-04-25T00:00:00"})
        deleted, gray = vector_store.apply_decay("user1")
        assert deleted == 0

    async def test_get_gray_zone_memories(
        self,
        vector_store: VectorStore,
    ) -> None:
        memory_id = await vector_store.add_memory(
            "user1",
            "Gray zone test",
            {"importance": 5},
        )
        vector_store.update_metadata(memory_id, {"effective_score": 4.0})
        gray = vector_store.get_gray_zone_memories("user1")
        assert len(gray) == 1

    # 相似度 & 合并
    # ================================================================

    async def test_find_similar_by_content_empty(
        self,
        vector_store: VectorStore,
    ) -> None:
        result = await vector_store.find_similar_by_content(
            "user1", "test content", 0.9
        )
        assert result == []

    async def test_find_similar_by_content_empty_content(
        self,
        vector_store: VectorStore,
    ) -> None:
        result = await vector_store.find_similar_by_content("user1", "", 0.9)
        assert result == []

    def test_close(self, vector_store: VectorStore) -> None:
        vector_store.close()
        assert vector_store.get_user_memories("user1") == []
