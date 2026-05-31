"""L3 多候选合并测试 — merge_similar_for_user 多配对 + add_or_merge 多候选项。"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pytest

from memory.plugin_config import PluginConfig
from memory.vector_store.vector_store import VectorStore


def _make_embedding(dim: int = 128):
    """创建 embedding 函数：所有文本映射到近似向量（cosine ≈ 0.99）。

    测试合并逻辑时不需要真实语义相似度，只需让 find_similar 返回结果。
    """
    base_vec = np.random.RandomState(42).randn(dim).astype(np.float32)
    base_vec = base_vec / np.linalg.norm(base_vec)

    def embed(texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            rng = np.random.RandomState(hash(text) % (2**31))
            perturbation = rng.randn(dim).astype(np.float32) * 0.05
            vec = base_vec + perturbation
            vec = vec / (np.linalg.norm(vec) + 1e-8)
            result.append(vec.tolist())
        return result

    return embed


class TestL3MultiMerge:
    """merge_similar_for_user 多候选合并测试。"""

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
            l3_merge_similarity=0.5,
        )

    @pytest.fixture
    def vector_store(
        self, temp_dir: Path, config: PluginConfig,
    ) -> Iterator[VectorStore]:
        vs = VectorStore(temp_dir, config, embedding_func=_make_embedding())
        yield vs
        vs.close()

    @pytest.fixture
    def mock_analyzer(self) -> AsyncMock:
        analyzer = AsyncMock()
        analyzer.merge_content = AsyncMock(
            side_effect=lambda a, b, umo="": f"合并: {a[:8]}+{b[:8]}",
        )
        return analyzer

    async def test_merge_multiple_candidates(
        self, vector_store: VectorStore, mock_analyzer: AsyncMock,
    ) -> None:
        """3 条相似记忆 → 合并。"""
        uid = "user1"
        await vector_store.add_memory(uid, "记忆A内容", {"importance": 5})
        await vector_store.add_memory(uid, "记忆B内容", {"importance": 7})
        await vector_store.add_memory(uid, "记忆C内容", {"importance": 3})

        merged_count, details = await vector_store.merge_similar_for_user(
            uid, mock_analyzer, threshold=0.01,
        )
        assert merged_count >= 1
        remaining = vector_store.get_user_memories(uid)
        assert len(remaining) < 3  # 至少合并掉一些

    async def test_merge_single_memory_noop(
        self, vector_store: VectorStore, mock_analyzer: AsyncMock,
    ) -> None:
        """只有 1 条记忆时无法合并，返回 0。"""
        uid = "user1"
        await vector_store.add_memory(uid, "唯一记忆内容", {"importance": 8})
        merged_count, details = await vector_store.merge_similar_for_user(
            uid, mock_analyzer, threshold=0.5,
        )
        assert merged_count == 0
        assert details == []
        # 原记忆仍在
        remaining = vector_store.get_user_memories(uid)
        assert len(remaining) == 1

    async def test_merge_preserves_highest_importance(
        self, vector_store: VectorStore, mock_analyzer: AsyncMock,
    ) -> None:
        """合并后分数 ≥ max(候选项分数)。"""
        uid = "user1"
        await vector_store.add_memory(uid, "重要记忆内容", {"importance": 9})
        await vector_store.add_memory(uid, "次要记忆内容", {"importance": 5})

        merged_count, details = await vector_store.merge_similar_for_user(
            uid, mock_analyzer, threshold=0.01,
        )
        if details:
            assert details[0]["score"] >= 9.0


class TestAddOrMergeMultiCandidate:
    """add_or_merge 多候选项合并测试。"""

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
            l3_merge_similarity=0.5,
        )

    @pytest.fixture
    def vector_store(
        self, temp_dir: Path, config: PluginConfig,
    ) -> Iterator[VectorStore]:
        vs = VectorStore(temp_dir, config, embedding_func=_make_embedding())
        yield vs
        vs.close()

    @pytest.fixture
    def mock_analyzer(self) -> AsyncMock:
        analyzer = AsyncMock()
        analyzer.merge_content = AsyncMock(
            side_effect=lambda a, b, umo="": f"合并: {a[:8]}+{b[:8]}",
        )
        return analyzer

    async def test_add_or_merge_no_similar(
        self, vector_store: VectorStore, mock_analyzer: AsyncMock,
    ) -> None:
        """高阈值 + 无匹配 → 直接新增。"""
        uid = "user_new"
        result = await vector_store.add_or_merge(
            uid, "全新内容", score=7, analyzer=mock_analyzer,
            merge_threshold=0.99,
        )
        assert result["action"] == "added"
        assert result["old_ids"] == []

    async def test_add_or_merge_with_similar(
        self, vector_store: VectorStore, mock_analyzer: AsyncMock,
    ) -> None:
        """有相似候选项 → 合并。"""
        uid = "user1"
        await vector_store.add_memory(uid, "已有记忆内容", {"importance": 6})
        result = await vector_store.add_or_merge(
            uid, "类似的新记忆", score=5, analyzer=mock_analyzer,
            merge_threshold=0.01,
        )
        assert result["action"] == "merged"
        assert len(result["old_ids"]) >= 2  # 原记忆 + 暂存 ID