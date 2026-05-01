"""
压缩器模块测试 — 使用 PluginConfig。
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from memory.compressor.compressor import DialogueCompressor
from memory.plugin_config import PluginConfig
from memory.storage.storage import MemoryStorage


class TestDialogueCompressor:
    """DialogueCompressor 类的测试。"""

    @pytest.fixture
    def temp_dir(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def config(self, temp_dir: Path) -> PluginConfig:
        return PluginConfig(
            data_dir=temp_dir,
            compress_model="test-model",
            l2_compress_prompt_b="请将以下对话内容精简为一段摘要：\n\n{content}",
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
    def storage(self, config: PluginConfig) -> MemoryStorage:
        return MemoryStorage(config)

    @pytest.fixture
    def compressor(
        self,
        mock_context: MagicMock,
        storage: MemoryStorage,
        config: PluginConfig,
    ) -> DialogueCompressor:
        return DialogueCompressor(mock_context, storage, config)

    # 内部方法
    # ================================================================

    def test_format_dialogues(self, compressor: DialogueCompressor) -> None:
        formatted = compressor._format_dialogues(["user: Hello", "assistant: Hi"])
        assert "user: Hello" in formatted
        assert "assistant: Hi" in formatted

    def test_parse_score_valid(self, compressor: DialogueCompressor) -> None:
        assert compressor._parse_score("8") == 8
        assert compressor._parse_score("  5  ") == 5

    def test_parse_score_boundary(self, compressor: DialogueCompressor) -> None:
        assert compressor._parse_score("0") == 0
        assert compressor._parse_score("10") == 10
        assert compressor._parse_score("15") == 10

    def test_parse_score_invalid(self, compressor: DialogueCompressor) -> None:
        assert compressor._parse_score("no number") == 5
        assert compressor._parse_score("") == 5

    def test_get_dialogues_empty(
        self,
        compressor: DialogueCompressor,
    ) -> None:
        assert compressor._get_dialogues("nonexistent", "2024-04-20") == []

    # Path B：每日摘要
    # ================================================================

    @pytest.mark.asyncio
    async def test_compress_day_no_dialogues(
        self,
        compressor: DialogueCompressor,
    ) -> None:
        result = await compressor.compress_day("nonexistent", "2024-04-20")
        assert result is None

    @pytest.mark.asyncio
    async def test_compress_day_success(
        self,
        compressor: DialogueCompressor,
        mock_context: MagicMock,
        storage: MemoryStorage,
    ) -> None:
        item1 = storage.append_dialogue("user123", "user", "Hello")
        item2 = storage.append_dialogue("user123", "assistant", "Hi")
        date_obj = datetime.strptime("2024-04-20", "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        date_ts = date_obj.timestamp()
        storage.update_l1_dialogue_timestamp("user123", item1.message_id, date_ts)
        storage.update_l1_dialogue_timestamp("user123", item2.message_id, date_ts)

        mock_context.llm_generate.return_value = MagicMock(
            completion_text="Summary of conversation"
        )

        result = await compressor.compress_day("user123", "2024-04-20")
        assert result == "Summary of conversation"
        assert mock_context.llm_generate.call_count == 2

        summaries = storage.get_daily_summaries("user123")
        assert len(summaries) == 1
        assert summaries[0].summary == "Summary of conversation"
        assert summaries[0].user_id == "user123"

    @pytest.mark.asyncio
    async def test_compress_day_with_custom_model(
        self,
        mock_context: MagicMock,
        storage: MemoryStorage,
        config: PluginConfig,
    ) -> None:
        config.compress_model = "custom-compress-model"
        compressor = DialogueCompressor(mock_context, storage, config)
        item = storage.append_dialogue("user123", "user", "Test")
        date_obj = datetime.strptime("2024-04-20", "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        storage.update_l1_dialogue_timestamp(
            "user123",
            item.message_id,
            date_obj.timestamp(),
        )
        mock_context.llm_generate.return_value = MagicMock(
            completion_text="This is a summary of the day's conversation"
        )
        await compressor.compress_day("user123", "2024-04-20")
        kwargs = mock_context.llm_generate.call_args.kwargs
        assert kwargs["model"] == "custom-compress-model"

    # Path A：周摘要
    # ================================================================

    @pytest.mark.asyncio
    async def test_compress_context_summary_empty(
        self,
        compressor: DialogueCompressor,
    ) -> None:
        """无内容时应返回 None。"""
        result = await compressor.compress_context_summary("user_empty")
        assert result is None

    @pytest.mark.asyncio
    async def test_compress_context_summary(
        self,
        compressor: DialogueCompressor,
        mock_context: MagicMock,
        storage: MemoryStorage,
    ) -> None:
        """有对话 + 日摘要时生成周摘要。"""
        storage.append_dialogue("user_x", "user", "重要消息")
        mock_context.llm_generate.return_value = MagicMock(
            completion_text="本周摘要：用户讨论了重要话题"
        )
        result = await compressor.compress_context_summary("user_x")
        assert "重要" in result or result is not None

    # _call_llm 默认 provider 场景
    # ================================================================

    @pytest.mark.asyncio
    async def test_call_llm_uses_default_provider_when_no_umo(
        self,
        compressor: DialogueCompressor,
        mock_context: MagicMock,
    ) -> None:
        """umo 为空时应通过 get_using_provider 获取默认 provider。"""
        mock_context.llm_generate.return_value = MagicMock(
            completion_text="test response"
        )
        result = await compressor._call_llm("test prompt", umo="")
        assert result == "test response"
        kwargs = mock_context.llm_generate.call_args.kwargs
        assert kwargs["chat_provider_id"] == "default-provider"
        mock_context.get_using_provider.assert_called_once()
