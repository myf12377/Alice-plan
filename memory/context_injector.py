"""
记忆上下文注入器 — 四管线独立注入。

L1  → request.contexts（无标记，自然消失）
L2-A → extra_user_content_parts（[周摘要]，覆盖式）
L2-B → extra_user_content_parts（[L2记忆]，覆盖式）
L3  → extra_user_content_parts（[L3记忆]，覆盖式）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from astrbot.api.provider import ProviderRequest

if TYPE_CHECKING:
    from memory.identity.identity import IdentityModule
    from memory.plugin_config import PluginConfig
    from memory.storage.storage import MemoryStorage
    from memory.vector_store.vector_store import VectorStore


# 上下文中的记忆标记（管线级自主覆盖）
L2_PATH_A_MARKER = "[周摘要]"
L2_PATH_B_MARKER = "[L2记忆]"
L3_MARKER = "[L3记忆]"


class ContextInjector:
    """记忆上下文注入器 — 四管线独立管理。

    设计原则：每条管线持有独立标记，只管理自己的内容，
    互不污染。注入只读，不写。
    """

    def __init__(
        self,
        storage: MemoryStorage,
        vector_store: VectorStore | None,
        identity_module: IdentityModule,
        config: PluginConfig,
    ) -> None:
        self._storage = storage
        self._vector_store = vector_store
        self._identity_module = identity_module
        self._config = config

    # ==================================================================
    # 统一入口
    # ==================================================================

    async def inject_all(
        self, user_id: str, request: ProviderRequest,
    ) -> None:
        """按 config 开关调度四条注入管线。"""
        if self._config.inject_l1:
            await self.inject_l1(user_id, request)
        if self._config.inject_l2_path_a:
            await self.inject_l2_path_a(user_id, request)
        if self._config.inject_l2_path_b:
            await self.inject_l2_path_b(user_id, request)
        if self._config.inject_l3:
            await self.inject_l3(user_id, request)

    # ==================================================================
    # L1 — 日内原始对话
    # ==================================================================

    async def inject_l1(
        self, user_id: str, request: ProviderRequest,
    ) -> None:
        """注入今日 L1 对话到 request.contexts（无标记，自然消失）。"""
        dialogues = self._storage.get_today_dialogues(user_id)
        if not dialogues:
            return

        for d in dialogues[: self._config.l1_search_limit]:
            request.contexts.append({
                "role": d.role,
                "content": d.content,
            })

    # ==================================================================
    # L2 Path A — 渐进周摘要
    # ==================================================================

    async def inject_l2_path_a(
        self, user_id: str, request: ProviderRequest,
    ) -> None:
        """注入周摘要到 extra_user_content_parts [周摘要]。

        周一跳过（Scheduler 凌晨已清空）。
        """
        if self._is_monday():
            return

        weekly = self._storage.get_weekly_summary(user_id)
        if not weekly or not weekly.get("summary"):
            return

        self._clean_marker(request, L2_PATH_A_MARKER)
        request.extra_user_content_parts.append({
            "type": "text",
            "text": f"{L2_PATH_A_MARKER}\n本周摘要：{weekly['summary']}",
        })

    # ==================================================================
    # L2 Path B — 每日磁盘摘要
    # ==================================================================

    async def inject_l2_path_b(
        self, user_id: str, request: ProviderRequest,
    ) -> None:
        """注入最近 N 天日摘要到 extra_user_content_parts [L2记忆]（周一不跳过）。"""
        summaries = self._storage.get_daily_summaries(
            user_id, last=self._config.l2_daily_inject_count,
        )
        if not summaries:
            return

        combined = "\n".join(
            f"[{s.date}] {s.summary}" for s in summaries if not s.hidden
        )
        if not combined:
            return

        self._clean_marker(request, L2_PATH_B_MARKER)
        request.extra_user_content_parts.append({
            "type": "text",
            "text": f"{L2_PATH_B_MARKER}\n{combined}",
        })

    # ==================================================================
    # L3 — 长期向量记忆
    # ==================================================================

    async def inject_l3(
        self, user_id: str, request: ProviderRequest,
    ) -> None:
        """语义检索 L3 记忆，注入到 extra_user_content_parts [L3记忆]。"""
        if not self._vector_store:
            return

        query = getattr(request, "prompt", "") or ""
        if not query:
            return

        # 修复：参数顺序 → search(user_id, query, top_k)
        results = await self._vector_store.search(user_id, query, top_k=3)

        self._clean_marker(request, L3_MARKER)

        threshold = self._config.l3_merge_similarity
        for r in results:
            score = r.get("distance", 0)
            # distance 越低越相似（cosine distance = 1 - similarity）
            similarity = 1.0 - score
            if similarity >= threshold:
                content = r.get("content", "")
                if content:
                    request.extra_user_content_parts.append({
                        "type": "text",
                        "text": f"{L3_MARKER}\n{content}",
                    })

    # ==================================================================
    # 工具
    # ==================================================================

    @staticmethod
    def _is_monday() -> bool:
        return datetime.now(timezone.utc).weekday() == 0

    @staticmethod
    def _clean_marker(request: ProviderRequest, marker: str) -> None:
        """移除 extra_user_content_parts 中以指定 marker 开头的旧内容。"""
        request.extra_user_content_parts = [
            p for p in request.extra_user_content_parts
            if not str(p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "")).startswith(marker)
        ]
