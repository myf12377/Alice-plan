"""
分析器模块测试 — 使用 PluginConfig。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from memory.analyzer.analyzer import ImportanceAnalyzer
from memory.plugin_config import PluginConfig


class TestImportanceAnalyzer:
    """ImportanceAnalyzer 类的测试。"""

    @pytest.fixture
    def config(self) -> PluginConfig:
        return PluginConfig(
            importance_threshold=8,
            importance_analyze_model="test-model",
            llm_max_tokens=1024,
            llm_temperature=0.7,
        )

    @pytest.fixture
    def mock_context(self) -> MagicMock:
        context = MagicMock()
        context.llm_generate = AsyncMock()
        context.get_current_chat_provider_id = AsyncMock(return_value="test-provider")
        default_provider = MagicMock()
        default_provider.meta.return_value.id = "default-provider"
        context.get_using_provider.return_value = default_provider
        return context

    @pytest.fixture
    def analyzer(
        self,
        mock_context: MagicMock,
        config: PluginConfig,
    ) -> ImportanceAnalyzer:
        return ImportanceAnalyzer(mock_context, config)

    # 单条分析
    # ================================================================

    def test_build_prompt(self, analyzer: ImportanceAnalyzer) -> None:
        prompt = analyzer._build_analyze_prompt("Test content")
        assert "Test content" in prompt
        assert "0-10" in prompt

    def test_parse_score_valid(self, analyzer: ImportanceAnalyzer) -> None:
        assert analyzer._parse_score("8") == 8
        assert analyzer._parse_score("  5  ") == 5
        assert analyzer._parse_score("The score is 9.") == 9

    def test_parse_score_boundary(self, analyzer: ImportanceAnalyzer) -> None:
        assert analyzer._parse_score("0") == 0
        assert analyzer._parse_score("10") == 10
        assert analyzer._parse_score("15") == 10
        assert analyzer._parse_score("-3") == 0

    def test_parse_score_invalid(self, analyzer: ImportanceAnalyzer) -> None:
        assert analyzer._parse_score("no number here") == 0
        assert analyzer._parse_score("") == 0
        assert analyzer._parse_score("three") == 0

    @pytest.mark.asyncio
    async def test_analyze_success(
        self,
        analyzer: ImportanceAnalyzer,
        mock_context: MagicMock,
    ) -> None:
        mock_context.llm_generate.return_value = MagicMock(completion_text="8")
        score = await analyzer.analyze("Important personal preference")
        assert score == 8
        mock_context.llm_generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_with_custom_model(
        self,
        mock_context: MagicMock,
        config: PluginConfig,
    ) -> None:
        config.importance_analyze_model = "custom-model"
        analyzer = ImportanceAnalyzer(mock_context, config)
        mock_context.llm_generate.return_value = MagicMock(completion_text="7")
        await analyzer.analyze("Test content")
        kwargs = mock_context.llm_generate.call_args.kwargs
        assert kwargs["model"] == "custom-model"

    @pytest.mark.asyncio
    async def test_analyze_empty_model_uses_default(
        self,
        mock_context: MagicMock,
        config: PluginConfig,
    ) -> None:
        config.importance_analyze_model = ""
        analyzer = ImportanceAnalyzer(mock_context, config)
        mock_context.llm_generate.return_value = MagicMock(completion_text="5")
        await analyzer.analyze("Test content")
        kwargs = mock_context.llm_generate.call_args.kwargs
        assert "model" not in kwargs

    # 灰区批量重评
    # ================================================================

    @pytest.mark.asyncio
    async def test_batch_recheck_empty(
        self,
        analyzer: ImportanceAnalyzer,
    ) -> None:
        result = await analyzer.batch_recheck([])
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_recheck(
        self,
        analyzer: ImportanceAnalyzer,
        mock_context: MagicMock,
    ) -> None:
        mock_context.llm_generate.return_value = MagicMock(
            completion_text=("[0] 7 keep 用户偏好信息\n[1] 2 drop 信息已过时")
        )
        memories = [
            {
                "id": "vid-1",
                "content": "用户喜欢咖啡",
                "metadata": {"effective_score": 4.0},
            },
            {
                "id": "vid-2",
                "content": "天气闲聊",
                "metadata": {"effective_score": 3.5},
            },
        ]
        results = await analyzer.batch_recheck(memories)
        assert len(results) == 2
        assert results[0]["vector_id"] == "vid-1"
        assert results[0]["new_score"] == 7
        assert results[0]["should_keep"] is True
        assert results[1]["vector_id"] == "vid-2"
        assert results[1]["should_keep"] is False

    # 记忆合并
    # ================================================================

    @pytest.mark.asyncio
    async def test_merge_content(
        self,
        analyzer: ImportanceAnalyzer,
        mock_context: MagicMock,
    ) -> None:
        mock_context.llm_generate.return_value = MagicMock(
            completion_text="用户偏好美式咖啡，每天早晨一杯",
        )
        result = await analyzer.merge_content(
            "用户喜欢咖啡",
            "用户每天早晨喝美式咖啡",
        )
        assert "咖啡" in result

    # _call_llm 默认 provider 场景
    # ================================================================

    @pytest.mark.asyncio
    async def test_call_llm_uses_default_provider_when_no_umo(
        self,
        analyzer: ImportanceAnalyzer,
        mock_context: MagicMock,
    ) -> None:
        """umo 为空时应通过 get_using_provider 获取默认 provider。"""
        mock_context.llm_generate.return_value = MagicMock(
            completion_text="test response"
        )
        result = await analyzer._call_llm("test prompt", umo="")
        assert result == "test response"
        kwargs = mock_context.llm_generate.call_args.kwargs
        assert kwargs["chat_provider_id"] == "default-provider"
        mock_context.get_using_provider.assert_called_once()
