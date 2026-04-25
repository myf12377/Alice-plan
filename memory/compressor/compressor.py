"""
压缩器模块 — Path A/B 双路对话压缩。

Path A（渐进周摘要）：已有周摘要 + 当日 L1 + Path B 日摘要 → 合并式周摘要。
Path B（每日磁盘摘要）：指定日期 L1 对话 → 提取式日摘要。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory.plugin_config import PluginConfig
    from memory.storage.storage import MemoryStorage


class DialogueCompressor:
    """Path A/B 双路对话压缩器。

    使用 LLM 将对话内容压缩为摘要。
    """

    def __init__(
        self, context: Any, storage: MemoryStorage, config: PluginConfig,
    ) -> None:
        """初始化压缩器。

        Args:
            context: 具有 llm_generate 能力的 AstrBot 上下文。
            storage: 记忆存储实例（MemoryStorage）。
            config: 插件配置（PluginConfig）。
        """
        self._context = context
        self._storage = storage
        self._config = config

    # ==================================================================
    # Path B：每日磁盘摘要
    # ==================================================================

    async def compress_day(
        self, user_id: str, date: str, hidden: bool = False,
    ) -> str | None:
        """将用户某一天的对话压缩为 L2 日摘要。

        Args:
            user_id: 用户标识符。
            date: 日期字符串（YYYY-MM-DD）。
            hidden: 是否在注入时隐藏。

        Returns:
            生成的摘要；无对话时返回 None。
        """
        dialogues = self._get_dialogues(user_id, date)
        if not dialogues:
            return None

        content = self._format_dialogues(dialogues)
        summary = await self._generate_summary(content, path="b")
        importance = await self._estimate_importance(summary)

        # 修复：user_id 作为第一参数
        self._storage.add_summary(user_id, date, summary, importance, hidden=hidden)
        return summary

    # ==================================================================
    # Path A：渐进周摘要
    # ==================================================================

    async def compress_context_summary(self, user_id: str) -> str | None:
        """生成渐进周摘要（合并模式）。

        原料：已有周摘要 + 当日 L1 + Path B 日摘要（不含 L3）。

        Returns:
            合并后的周摘要；无内容时返回 None。
        """
        weekly = self._storage.get_weekly_summary(user_id)
        weekly_text = weekly["summary"] if weekly else "（暂无）"

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_dialogues = self._storage.get_l1_dialogues(user_id, date=today)
        today_text = self._format_dialogues(
            [f"{d.role}: {d.content}" for d in today_dialogues]
        ) if today_dialogues else "（今日暂无对话）"

        daily_summaries = self._storage.get_daily_summaries(
            user_id,
            last=self._config.l2_daily_inject_count,
        )
        daily_text = "\n".join(s.date + ": " + s.summary for s in daily_summaries) \
            if daily_summaries else "（暂无日摘要）"

        # 如果没有任何实质内容，跳过
        if not today_dialogues and not daily_summaries and weekly is None:
            return None

        prompt = self._config.l2_compress_prompt_a.format(
            weekly_summary=weekly_text,
            today_dialogues=today_text,
            daily_summaries=daily_text,
        )
        summary = await self._call_llm(prompt)
        return summary.strip()

    # ==================================================================
    # 内部
    # ==================================================================

    def _get_dialogues(self, user_id: str, date: str) -> list[str]:
        start_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_date = start_date + timedelta(days=1)
        start_ts = start_date.timestamp()
        end_ts = end_date.timestamp()

        dialogues = self._storage.get_l1_dialogues(user_id)
        filtered = [d for d in dialogues if start_ts <= d.timestamp < end_ts]
        return [f"{d.role}: {d.content}" for d in filtered]

    @staticmethod
    def _format_dialogues(dialogues: list[str]) -> str:
        return "\n".join(dialogues)

    async def _generate_summary(self, content: str, *, path: str = "b") -> str:
        """调用 LLM 生成摘要。

        Args:
            content: 要摘要的内容。
            path: "a" 使用 l2_compress_prompt_a，"b" 使用 l2_compress_prompt_b。
        """
        template = (
            self._config.l2_compress_prompt_a if path == "a"
            else self._config.l2_compress_prompt_b
        )
        prompt = template.format(content=content)
        return (await self._call_llm(prompt)).strip()

    async def _estimate_importance(self, summary: str) -> int:
        prompt = (
            "请评估以下摘要的重要性，评分范围为0-10分。\n"
            "0分：完全无关紧要\n5分：一般重要\n10分：极其重要\n\n"
            f"摘要：{summary}\n\n"
            "请只输出一个0-10的数字分数，不要有其他文字。"
        )
        response = await self._call_llm(prompt)
        return self._parse_score(response)

    async def _call_llm(self, prompt: str) -> str:
        kwargs: dict[str, Any] = {
            "max_tokens": self._config.llm_max_tokens,
            "temperature": self._config.llm_temperature,
        }
        if self._config.compress_model:
            kwargs["model"] = self._config.compress_model
        return await self._context.llm_generate(prompt=prompt, **kwargs)

    @staticmethod
    def _parse_score(response: str) -> int:
        cleaned = response.strip()
        match = re.search(r"-?\d+", cleaned)
        if match:
            return max(0, min(10, int(match.group())))
        return 5
