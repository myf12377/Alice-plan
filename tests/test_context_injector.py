"""
上下文注入器测试 — v2.2.0 三管线注入。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart
from memory.context_injector import (
    ContextInjector,
    L2_MARKER,
    L3_MARKER,
)
from memory.plugin_config import PluginConfig
from memory.storage.storage import L2SummaryItem


class TestContextInjector:
    """ContextInjector 类的测试。"""

    @pytest.fixture
    def config(self) -> PluginConfig:
        return PluginConfig(
            inject_l1=True,
            inject_l2_path_a=True,
            inject_l2_path_b=True,
            inject_l3=True,
            l1_inject_rounds=80,
            l2_daily_inject_count=7,
            l3_merge_similarity=0.9,
        )

    @pytest.fixture
    def mock_storage(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def mock_vector_store(self) -> MagicMock:
        vs = MagicMock()
        vs.search = AsyncMock(return_value=[])
        return vs

    @pytest.fixture
    def mock_identity(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def injector(
        self,
        mock_storage,
        mock_vector_store,
        mock_identity,
        config,
    ) -> ContextInjector:
        return ContextInjector(mock_storage, mock_vector_store, mock_identity, config)

    @staticmethod
    def make_request(prompt: str = "") -> ProviderRequest:
        return ProviderRequest(prompt=prompt)

    # L1 — 全量分组注入
    # ================================================================

    async def test_inject_l1(
        self,
        injector: ContextInjector,
        mock_storage: MagicMock,
    ) -> None:
        mock_storage.get_recent_rounds.return_value = [
            {"role": "system", "content": "[2026-05-01 对话]"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        req = self.make_request()
        await injector.inject_l1("u1", req)
        assert len(req.contexts) == 3
        assert req.contexts[0]["role"] == "system"
        assert "2026-05-01" in req.contexts[0]["content"]
        assert req.contexts[1]["role"] == "user"

    async def test_inject_l1_empty(
        self,
        injector: ContextInjector,
        mock_storage: MagicMock,
    ) -> None:
        mock_storage.get_recent_rounds.return_value = []
        req = self.make_request()
        await injector.inject_l1("u1", req)
        assert len(req.contexts) == 0

    async def test_inject_l1_zero_rounds(
        self,
        injector: ContextInjector,
        mock_storage: MagicMock,
    ) -> None:
        """l1_inject_rounds=0 时 get_recent_rounds 返回空。"""
        mock_storage.get_recent_rounds.return_value = []
        req = self.make_request()
        await injector.inject_l1("u1", req)
        assert len(req.contexts) == 0
        mock_storage.get_recent_rounds.assert_called_once_with("u1")

    # L2 — 去重合并
    # ================================================================

    async def test_inject_l2_merged_weekly_and_daily(
        self,
        injector: ContextInjector,
        mock_storage: MagicMock,
    ) -> None:
        """周摘要 + 非本周日摘要合并注入。"""
        mock_storage.get_weekly_summary.return_value = {
            "summary": "本周讨论了记忆系统架构",
        }
        mock_storage.get_daily_summaries.return_value = [
            L2SummaryItem(
                "s1", "u1", "2026-04-24", "上周四讨论了...", 5, 1000.0, False
            ),
            L2SummaryItem(
                "s2", "u1", "2026-04-25", "上周五讨论了...", 5, 1000.0, False
            ),
        ]
        req = self.make_request()
        await injector.inject_l2_merged("u1", req)
        assert len(req.extra_user_content_parts) == 1
        text = req.extra_user_content_parts[0].text
        assert L2_MARKER in text
        assert "周摘要" in text
        assert "2026-04-24" in text

    async def test_inject_l2_merged_excludes_current_week(
        self,
        injector: ContextInjector,
        mock_storage: MagicMock,
    ) -> None:
        """本周日摘要应被排除（周摘要已覆盖）。"""
        mock_storage.get_weekly_summary.return_value = {"summary": "本周摘要"}
        from datetime import datetime
        from zoneinfo import ZoneInfo

        cst = ZoneInfo("Asia/Shanghai")
        today = datetime.now(cst).strftime("%Y-%m-%d")
        mock_storage.get_daily_summaries.return_value = [
            L2SummaryItem("s1", "u1", today, "今天的日摘要", 5, 1000.0, False),
        ]
        req = self.make_request()
        await injector.inject_l2_merged("u1", req)
        assert len(req.extra_user_content_parts) == 1
        text = req.extra_user_content_parts[0].text
        assert today not in text  # 本周的被排除
        assert "周摘要" in text  # 周摘要保留

    async def test_inject_l2_merged_empty(
        self,
        injector: ContextInjector,
        mock_storage: MagicMock,
    ) -> None:
        mock_storage.get_weekly_summary.return_value = None
        mock_storage.get_daily_summaries.return_value = []
        req = self.make_request()
        await injector.inject_l2_merged("u1", req)
        assert len(req.extra_user_content_parts) == 0

    async def test_inject_l2_merged_hidden_excluded(
        self,
        injector: ContextInjector,
        mock_storage: MagicMock,
    ) -> None:
        """hidden=True 的日摘要应被排除。"""
        mock_storage.get_weekly_summary.return_value = None
        mock_storage.get_daily_summaries.return_value = [
            L2SummaryItem("s1", "u1", "2026-04-24", "隐藏的", 5, 1000.0, True),
        ]
        req = self.make_request()
        await injector.inject_l2_merged("u1", req)
        assert len(req.extra_user_content_parts) == 0

    # L3 — 按需语义检索
    # ================================================================

    async def test_inject_l3(
        self,
        injector: ContextInjector,
        mock_vector_store: MagicMock,
    ) -> None:
        mock_vector_store.search.return_value = [
            {"content": "用户喜欢咖啡", "distance": 0.05},
        ]
        req = self.make_request(prompt="咖啡")
        await injector.inject_l3("u1", req)
        assert len(req.extra_user_content_parts) == 1
        assert L3_MARKER in str(req.extra_user_content_parts[0])

    async def test_inject_l3_no_vector_store(
        self,
        mock_storage,
        mock_identity,
        config,
    ) -> None:
        injector = ContextInjector(mock_storage, None, mock_identity, config)
        req = self.make_request(prompt="test")
        await injector.inject_l3("u1", req)
        assert len(req.extra_user_content_parts) == 0

    async def test_inject_l3_empty_query(
        self,
        injector: ContextInjector,
    ) -> None:
        req = self.make_request(prompt="")
        await injector.inject_l3("u1", req)
        assert len(req.extra_user_content_parts) == 0

    # inject_all — 三管线编排
    # ================================================================

    async def test_inject_all(
        self,
        injector: ContextInjector,
        mock_storage: MagicMock,
        mock_vector_store: MagicMock,
    ) -> None:
        mock_storage.get_recent_rounds.return_value = [
            {"role": "system", "content": "[2026-05-01 对话]"},
            {"role": "user", "content": "Hello"},
        ]
        mock_storage.get_weekly_summary.return_value = {"summary": "周摘要"}
        mock_storage.get_daily_summaries.return_value = []
        mock_vector_store.search.return_value = [
            {"content": "L3 memory", "distance": 0.05},
        ]
        req = self.make_request(prompt="test")
        await injector.inject_all("u1", req)
        # L1: 2 条 contexts, L2: 1 extra part, L3: 1 extra part
        assert len(req.contexts) == 2
        assert len(req.extra_user_content_parts) == 2

    async def test_inject_all_disabled(
        self,
        mock_storage,
        mock_vector_store,
        mock_identity,
    ) -> None:
        config = PluginConfig(
            inject_l1=False,
            inject_l2_path_a=False,
            inject_l2_path_b=False,
            inject_l3=False,
        )
        injector = ContextInjector(
            mock_storage, mock_vector_store, mock_identity, config
        )
        req = self.make_request()
        await injector.inject_all("u1", req)
        assert len(req.contexts) == 0
        assert len(req.extra_user_content_parts) == 0

    # 标记清理
    # ================================================================

    def test_clean_marker(self, injector: ContextInjector) -> None:
        req = self.make_request()
        req.extra_user_content_parts = [
            TextPart(text=f"{L2_MARKER}\n旧周摘要"),
            TextPart(text="其他内容"),
        ]
        injector._clean_marker(req, L2_MARKER)
        assert len(req.extra_user_content_parts) == 1
        assert req.extra_user_content_parts[0].text == "其他内容"
