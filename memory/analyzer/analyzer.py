"""
分析器模块 — 基于 LLM 的重要性分析。

单条打分、灰区批量重评、L3 记忆合并。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory.plugin_config import PluginConfig


class ImportanceAnalyzer:
    """基于 LLM 分析内容重要性。

    支持单条分析、灰区批量重评、相似记忆合并。
    """

    def __init__(self, context: Any, config: PluginConfig) -> None:
        """初始化分析器。

        Args:
            context: 具有 llm_generate 能力的 AstrBot 上下文。
            config: 插件配置（PluginConfig）。
        """
        self._context = context
        self._config = config

    # ==================================================================
    # 单条分析
    # ==================================================================

    async def analyze(self, content: str, umo: str = "") -> int:
        """分析单条内容重要性。

        Returns:
            0-10 分数。
        """
        prompt = self._build_analyze_prompt(content)
        response = await self._call_llm(prompt, umo)
        return self._parse_score(response)

    async def should_promote_to_l3(self, content: str) -> bool:
        """分数 ≥ importance_threshold → True。"""
        score = await self.analyze(content)
        return score >= self._config.importance_threshold

    # ==================================================================
    # 灰区批量重评
    # ==================================================================

    async def batch_recheck(
        self,
        memories: list[dict[str, Any]],
        umo: str = "",
    ) -> list[dict[str, Any]]:
        """对灰区记忆批量 LLM 重评。

        Args:
            memories: [{id, content, metadata, ...}, ...]
            umo: unified_message_origin，用于获取当前会话的 provider ID。

        Returns:
            [{vector_id, new_score, should_keep}, ...]
        """
        if not memories:
            return []

        results: list[dict[str, Any]] = []
        # 每批最多 5 条
        batch_size = 5
        for i in range(0, len(memories), batch_size):
            batch = memories[i : i + batch_size]
            prompt = self._build_batch_prompt(batch)
            response = await self._call_llm(prompt, umo)
            batch_results = self._parse_batch_response(response, batch)
            results.extend(batch_results)

        return results

    # ==================================================================
    # 记忆合并
    # ==================================================================

    async def merge_content(
        self,
        content_1: str,
        content_2: str,
        umo: str = "",
    ) -> str:
        """LLM 合并两条相似记忆，去冗余保留关键信息。

        Returns:
            合并后的内容字符串。
        """
        prompt = self._build_merge_prompt(content_1, content_2)
        response = await self._call_llm(prompt, umo)
        return response.strip()

    # ==================================================================
    # 内部：LLM 调用
    # ==================================================================

    async def _call_llm(self, prompt: str, umo: str = "") -> str:
        kwargs: dict[str, Any] = {
            "max_tokens": self._config.llm_max_tokens,
            "temperature": self._config.llm_temperature,
        }
        if self._config.importance_analyze_model:
            kwargs["model"] = self._config.importance_analyze_model
        if umo:
            try:
                kwargs[
                    "chat_provider_id"
                ] = await self._context.get_current_chat_provider_id(umo)
            except Exception:
                pass
        if "chat_provider_id" not in kwargs:
            try:
                prov = self._context.get_using_provider()
                if prov:
                    kwargs["chat_provider_id"] = prov.meta().id
            except Exception:
                pass
        resp = await self._context.llm_generate(prompt=prompt, **kwargs)
        return getattr(resp, "completion_text", "") or ""

    # ==================================================================
    # 内部：Prompt 构建
    # ==================================================================

    def _build_analyze_prompt(self, content: str) -> str:
        return (
            "请分析以下内容的重要性，评分范围为0-10分。\n"
            "0分：完全无关紧要的信息，如问候、闲聊\n"
            "5分：一般重要的信息，有一定保留价值\n"
            "10分：极其重要的信息，如个人偏好、关键决策、长期目标等\n\n"
            f"内容：{content}\n\n"
            "请只输出一个0-10的数字分数，不要有其他文字。"
        )

    def _build_batch_prompt(self, memories: list[dict[str, Any]]) -> str:
        items = []
        for i, m in enumerate(memories):
            content = m.get("content", "")
            score = m.get("metadata", {}).get("effective_score", "?")
            items.append(f"[{i}] effective_score={score} | {content}")
        items_text = "\n".join(items)
        return (
            '以下是一些处于"灰区"的记忆（分数偏低，面临淘汰）。\n'
            "请重新评估每条记忆的重要性，给出新的分数（0-10），"
            "并判断是否应保留（keep）或删除（drop）。\n\n"
            f"{items_text}\n\n"
            "请按以下格式输出（每条一行）：\n"
            "[序号] 新分数 keep/drop 简短理由\n\n"
            "示例：\n"
            "[0] 7 keep 涉及用户重要偏好\n"
            "[1] 2 drop 信息已过时"
        )

    def _build_merge_prompt(self, content_1: str, content_2: str) -> str:
        return (
            "以下两条记忆高度相似，请合并为一条，去除重复，保留所有关键信息。\n\n"
            f"记忆1：{content_1}\n\n"
            f"记忆2：{content_2}\n\n"
            "请输出合并后的记忆内容（直接输出合并文本，不要添加说明）："
        )

    # ==================================================================
    # 内部：解析
    # ==================================================================

    @staticmethod
    def _parse_score(response: str) -> int:
        cleaned = response.strip()
        match = re.search(r"-?\d+", cleaned)
        if match:
            return max(0, min(10, int(match.group())))
        return 0

    @staticmethod
    def _parse_batch_response(
        response: str,
        batch: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        lines = response.strip().split("\n")
        for line in lines:
            match = re.match(
                r"\[(\d+)\]\s+(\d+)\s+(keep|drop)", line.strip(), re.IGNORECASE
            )
            if match:
                idx = int(match.group(1))
                new_score = int(match.group(2))
                should_keep = match.group(3).lower() == "keep"
                if idx < len(batch):
                    results.append(
                        {
                            "vector_id": batch[idx].get("id", ""),
                            "new_score": max(0, min(10, new_score)),
                            "should_keep": should_keep,
                        }
                    )
        return results
