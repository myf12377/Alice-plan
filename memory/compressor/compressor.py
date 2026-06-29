"""
压缩器模块 — Path A/B 双路对话压缩。

Path A（渐进周摘要）：已有周摘要 + 当日 L1 + Path B 日摘要 → 合并式周摘要。
Path B（每日磁盘摘要）：指定日期 L1 对话 → 提取式日摘要。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from ..utils import parse_score

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
        self, user_id: str, date: str, hidden: bool | None = None, umo: str = "",
    ) -> str | None:
        """将用户某一天的对话压缩为 L2 日摘要。

        Args:
            user_id: 用户标识符。
            date: 日期字符串（YYYY-MM-DD）。
            hidden: 是否在注入时隐藏。
            umo: unified_message_origin，用于获取当前会话的 provider ID。

        Returns:
            生成的摘要；无对话时返回 None。
        """
        dialogues = self._get_dialogues(user_id, date)
        if not dialogues:
            logger.info("[AliceMemory] Path B 跳过 | date=%s | 无对话", date)
            return None

        content = self._format_dialogues(dialogues)
        logger.info("[AliceMemory] Path B 压缩 | date=%s | 对话=%d条 | 字数=%d",
                    date, len(dialogues), len(content))
        if hidden is None:
            hidden = self._config.l2_summary_hidden

        summary = await self._generate_summary(content, path="b", umo=umo)
        if not summary.strip():  # P22 _looks_valid 拦截 → 空摘要，不存储
            return None
        importance = await self._estimate_importance(summary, umo=umo)

        self._storage.add_summary(user_id, date, summary, importance, hidden=hidden)
        return summary

    # ==================================================================
    # Path A：渐进周摘要
    # ==================================================================

    async def compress_context_summary(
        self, user_id: str, umo: str = "",
    ) -> str | None:
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
        summary = await self._call_llm(prompt, umo)
        summary = summary.strip()
        if not summary:  # P22 _looks_valid 拦截或 LLM 返回空 → 不存储
            return None
        today_date = datetime.now(timezone.utc).date()
        week_start = today_date - timedelta(days=today_date.weekday())
        self._storage.set_weekly_summary(
            user_id, summary, week_start.strftime("%Y-%m-%d"),
        )
        return summary

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

    async def _generate_summary(
        self, content: str, *, path: str = "b", umo: str = "",
    ) -> str:
        """调用 LLM 生成摘要。

        Args:
            content: 要摘要的内容。
            path: "a" 使用 l2_compress_prompt_a，"b" 使用 l2_compress_prompt_b。
            umo: unified_message_origin，用于获取当前会话的 provider ID。
        """
        template = (
            self._config.l2_compress_prompt_a if path == "a"
            else self._config.l2_compress_prompt_b
        )
        prompt = template.format(content=content)
        return (await self._call_llm(prompt, umo)).strip()

    async def _estimate_importance(self, summary: str, umo: str = "") -> int:
        prompt = (
            "请评估以下摘要的重要性，评分范围为0-10分。\n"
            "0分：完全无关紧要\n5分：一般重要\n10分：极其重要\n\n"
            f"摘要：{summary}\n\n"
            "请只输出一个0-10的数字分数，不要有其他文字。"
        )
        response = await self._call_llm(prompt, umo, raw=True)
        return parse_score(response, default=5)

    async def _call_llm(self, prompt: str, umo: str = "", raw: bool = False) -> str:
        kwargs: dict[str, Any] = {
            "max_tokens": self._config.llm_max_tokens,
            "temperature": self._config.llm_temperature,
        }
        if self._config.compress_model:
            kwargs["model"] = self._config.compress_model
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
        try:
            resp = await self._context.llm_generate(prompt=prompt, **kwargs)
        except Exception:
            if "model" in kwargs:
                del kwargs["model"]
                resp = await self._context.llm_generate(prompt=prompt, **kwargs)
            else:
                raise
        text = getattr(resp, "completion_text", "") or ""
        if not raw and not self._looks_valid(text.strip()):
            return ""
        return text.strip()

    @staticmethod
    def _looks_valid(text: str) -> bool:
        """校验 LLM 返回是否为有效摘要（P4 回移植 + P22 增强）。

        检测无效模式：
        - 太短（<10字符）
        - 太长且包含模板特征（>300字符 + 包含"模板"/"时间范围"等）→ LLM 捏造
        - 包含 prompt 回显特征（全文检测，不限于开头）
        """
        if not text or len(text) < 10:
            return False
        # prompt 回显/LLM 拒绝：全文检测
        echo_markers = [
            "请提供", "请根据", "请按照", "请输出", "请仔细",
            "请将以下", "请将以上", "以下包含",
            "昨日对话",  # LLM 问"请提供昨日对话"
            # 身份泄漏：LLM 以引导语开头污染摘要正文
            "作为您的", "作为你的", "好的，作为", "好的，我是",
            "作为记忆", "我是您的", "我是你的",
            "好的，我来", "好的，以下",
        ]
        for marker in echo_markers:
            if marker in text:
                return False
        return True

