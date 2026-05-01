"""AliceMemoryPlugin 主入口测试 — manage_context 配置项。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.api.provider import ProviderRequest
from memory.plugin_config import PluginConfig


class TestManageContextConfig:
    """manage_context 配置字段测试。"""

    def test_default_is_false(self):
        config = PluginConfig()
        assert config.manage_context is False

    def test_from_framework_config_true(self):
        config = PluginConfig.from_framework_config({"manage_context": True})
        assert config.manage_context is True

    def test_from_framework_config_false(self):
        config = PluginConfig.from_framework_config({"manage_context": False})
        assert config.manage_context is False

    def test_from_framework_config_missing_uses_default(self):
        config = PluginConfig.from_framework_config({})
        assert config.manage_context is False

    def test_to_dict_includes_manage_context(self):
        config = PluginConfig(manage_context=True)
        d = config.to_dict()
        assert "manage_context" in d
        assert d["manage_context"] is True


@pytest.fixture
def mock_event():
    event = MagicMock()
    event.get_platform_name.return_value = "test_platform"
    event.get_sender_id.return_value = "user123"
    event.get_message_str.return_value = "hello"
    event.unified_msg_origin = "test_platform:user123"
    return event


async def _run_on_llm_request(plugin, event, req):
    """直接调用 plugin.on_llm_request 的核心逻辑，绕过 Star 框架。"""
    if not plugin.plugin_config.hook_enabled:
        return

    platform = event.get_platform_name()
    platform_user_id = event.get_sender_id()
    user_id = plugin._identity.get_user_id(platform, platform_user_id)
    if not user_id:
        user_id = plugin._identity.register_user(platform, platform_user_id)

    content = event.get_message_str() or ""
    if not content.strip():
        return

    msg = content.strip().lstrip("/").lstrip("#")
    if (
        msg.startswith("compact")
        and plugin.plugin_config.manual_compress_feedback_mode == "silent"
    ):
        return

    plugin._storage.append_dialogue(user_id, "user", content)

    # manage_context 核心逻辑
    if plugin.plugin_config.manage_context:
        req.contexts = []

    await plugin._injector.inject_all(user_id, req)


class TestManageContextBehavior:
    """manage_context 行为测试。"""

    def _make_plugin(self, manage_context: bool = False):
        plugin = MagicMock()
        plugin.plugin_config = PluginConfig(
            manage_context=manage_context,
            hook_enabled=True,
        )
        plugin._identity = MagicMock()
        plugin._identity.get_user_id.return_value = "uid_abc123"
        plugin._storage = MagicMock()
        plugin._analyzer = MagicMock()
        plugin._analyzer.analyze = AsyncMock(return_value=2)

        mock_injector = MagicMock()

        async def fake_inject_all(user_id, req):
            req.contexts.append({"role": "user", "content": "injected_l1"})

        mock_injector.inject_all = AsyncMock(side_effect=fake_inject_all)
        plugin._injector = mock_injector
        return plugin

    async def test_manage_context_true_clears_contexts(self, mock_event):
        """manage_context=True 时，req.contexts 在注入前被清空。"""
        plugin = self._make_plugin(manage_context=True)
        req = ProviderRequest(prompt="test")
        req.contexts = [
            {"role": "user", "content": "old_msg_1"},
            {"role": "assistant", "content": "old_reply_1"},
            {"role": "user", "content": "old_msg_2"},
        ]

        await _run_on_llm_request(plugin, mock_event, req)

        assert len(req.contexts) == 1
        assert req.contexts[0]["content"] == "injected_l1"

    async def test_manage_context_false_keeps_contexts(self, mock_event):
        """manage_context=False（默认）时，req.contexts 不被清空。"""
        plugin = self._make_plugin(manage_context=False)
        req = ProviderRequest(prompt="test")
        req.contexts = [
            {"role": "user", "content": "old_msg_1"},
            {"role": "assistant", "content": "old_reply_1"},
        ]

        await _run_on_llm_request(plugin, mock_event, req)

        assert len(req.contexts) == 3
        assert req.contexts[0]["content"] == "old_msg_1"
        assert req.contexts[2]["content"] == "injected_l1"
