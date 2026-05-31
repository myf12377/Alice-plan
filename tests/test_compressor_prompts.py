"""L2 压缩 prompt 身份泄漏检查测试。"""

from memory.plugin_config import PluginConfig


class TestPromptIdentityLeak:
    """确认 Path A/B prompt 不含角色分配（"你是一个...助手"）。"""

    def test_path_a_prompt_no_persona(self) -> None:
        """Path A prompt 不含 "你是一个" 角色分配。"""
        config = PluginConfig.defaults()
        assert "你是一个" not in config.l2_compress_prompt_a

    def test_path_b_prompt_no_persona(self) -> None:
        """Path B prompt 不含 "你是一个" 角色分配。"""
        config = PluginConfig.defaults()
        assert "你是一个" not in config.l2_compress_prompt_b

    def test_path_a_prompt_is_task_description(self) -> None:
        """Path A prompt 是任务描述开头。"""
        config = PluginConfig.defaults()
        assert config.l2_compress_prompt_a.startswith("请将以下")

    def test_path_b_prompt_is_task_description(self) -> None:
        """Path B prompt 是任务描述开头。"""
        config = PluginConfig.defaults()
        assert config.l2_compress_prompt_b.startswith("请将以下")