"""
存储模块 — L1/L2/L3 三层记忆的 JSON 文件持久化。

L1: 原始对话，按 user_id 存储，保留 N 天。
L2: 每日摘要 / 周摘要，Path A（周摘要）和 Path B（日摘要）逻辑分离。
L3: 重要记忆元数据，按 user_id 存储。

文件结构:
    {data_dir}/
      l1/{user_id}.json       # list[L1MemoryItem]
      l2/{user_id}.json       # list[L2SummaryItem]
      l3/{user_id}.json       # list[L3MemoryItem]
      weekly/{user_id}.json   # dict: {user_id, summary, week_start, updated_at}
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from memory.plugin_config import PluginConfig


@dataclass
class L1MemoryItem:
    """L1 记忆项 — 原始对话。

    Attributes:
        message_id: 消息唯一标识符。
        user_id: 用户标识符。
        role: 角色（user / assistant）。
        content: 对话内容。
        timestamp: Unix 时间戳。
        compressed: 是否已被压缩为 L2 摘要。
        content_type: 内容类型（text / image / audio）。
        media_url: 原始媒体 URL。
    """

    message_id: str
    user_id: str
    role: str
    content: str
    timestamp: float
    compressed: bool = False
    content_type: str = "text"
    media_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "user_id": self.user_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "compressed": self.compressed,
            "content_type": self.content_type,
            "media_url": self.media_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> L1MemoryItem:
        return cls(
            message_id=data["message_id"],
            user_id=data["user_id"],
            role=data["role"],
            content=data["content"],
            timestamp=data["timestamp"],
            compressed=data.get("compressed", False),
            content_type=data.get("content_type", "text"),
            media_url=data.get("media_url"),
        )


@dataclass
class L2SummaryItem:
    """L2 记忆项 — 每日摘要（Path B）。

    Attributes:
        summary_id: 摘要唯一标识符。
        user_id: 用户标识符。
        date: 日期字符串（YYYY-MM-DD）。
        summary: 摘要内容。
        importance: 重要性分数（0-10）。
        timestamp: Unix 时间戳。
        hidden: 是否隐藏（不注入前端对话）。
    """

    summary_id: str
    user_id: str
    date: str
    summary: str
    importance: int
    timestamp: float
    hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "user_id": self.user_id,
            "date": self.date,
            "summary": self.summary,
            "importance": self.importance,
            "timestamp": self.timestamp,
            "hidden": self.hidden,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> L2SummaryItem:
        return cls(
            summary_id=data["summary_id"],
            user_id=data["user_id"],
            date=data["date"],
            summary=data["summary"],
            importance=data["importance"],
            timestamp=data["timestamp"],
            hidden=data.get("hidden", False),
        )


@dataclass
class L3MemoryItem:
    """L3 记忆项 — 重要记忆元数据。

    Attributes:
        memory_id: 记忆唯一标识符。
        user_id: 用户标识符。
        content: 记忆内容。
        metadata: 附加元数据。
        timestamp: Unix 时间戳。
    """

    memory_id: str
    user_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "user_id": self.user_id,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> L3MemoryItem:
        return cls(
            memory_id=data["memory_id"],
            user_id=data["user_id"],
            content=data["content"],
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp", 0.0),
        )


class MemoryStorage:
    """三层记忆存储。

    使用 JSON 文件持久化 L1 / L2 / L3 记忆，全部按 user_id 隔离。
    """

    def __init__(self, config: PluginConfig) -> None:
        """初始化存储。

        Args:
            config: 插件配置（PluginConfig）。
        """
        self._config = config
        data_dir = config.data_dir
        self._l1_dir = data_dir / "l1"
        self._l2_dir = data_dir / "l2"
        self._l3_dir = data_dir / "l3"
        self._weekly_dir = data_dir / "weekly"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for d in (self._l1_dir, self._l2_dir, self._l3_dir, self._weekly_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 文件操作工具
    # ------------------------------------------------------------------

    def _get_l1_path(self, user_id: str) -> Path:
        return self._l1_dir / f"{user_id}.json"

    def _get_l2_path(self, user_id: str) -> Path:
        return self._l2_dir / f"{user_id}.json"

    def _get_l3_path(self, user_id: str) -> Path:
        return self._l3_dir / f"{user_id}.json"

    def _get_weekly_path(self, user_id: str) -> Path:
        return self._weekly_dir / f"{user_id}.json"

    @staticmethod
    def _load_json(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cast(list[dict[str, Any]], json.load(f))
        except (json.JSONDecodeError, IOError):
            return []

    @staticmethod
    def _save_json(path: Path, data: list[dict[str, Any]]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _now_ts() -> float:
        return datetime.now(timezone.utc).timestamp()

    # ==================================================================
    # L1 — 原始对话
    # ==================================================================

    def append_dialogue(
        self, user_id: str, role: str, content: str,
    ) -> L1MemoryItem:
        """添加一条对话到 L1。

        Args:
            user_id: 用户标识符。
            role: 角色（"user" 或 "assistant"）。
            content: 对话内容。

        Returns:
            创建的 L1MemoryItem。
        """
        item = L1MemoryItem(
            message_id=str(uuid.uuid4()),
            user_id=user_id,
            role=role,
            content=content,
            timestamp=self._now_ts(),
        )
        path = self._get_l1_path(user_id)
        data = self._load_json(path)
        data.append(item.to_dict())
        self._save_json(path, data)
        return item

    def get_l1_dialogues(
        self, user_id: str, date: str | None = None,
    ) -> list[L1MemoryItem]:
        """获取用户 L1 对话。

        Args:
            user_id: 用户标识符。
            date: 可选日期（YYYY-MM-DD），为 None 返回全部。

        Returns:
            L1MemoryItem 列表。
        """
        items = self._load_all_l1(user_id)
        if date:
            items = [
                i for i in items
                if _ts_to_date(i.timestamp) == date
            ]
        return items

    def get_today_dialogues(self, user_id: str) -> list[L1MemoryItem]:
        """获取用户当日 L1 对话。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.get_l1_dialogues(user_id, date=today)

    def delete_l1_dialogue(self, user_id: str, message_id: str) -> bool:
        """按 message_id 删除单条 L1 对话。"""
        path = self._get_l1_path(user_id)
        data = self._load_json(path)
        original_len = len(data)
        data = [d for d in data if d.get("message_id") != message_id]
        if len(data) < original_len:
            self._save_json(path, data)
            return True
        return False

    def delete_old_l1_dialogues(
        self, user_id: str, retention_days: int | None = None,
    ) -> int:
        """删除超过保留天数的 L1 对话。

        Args:
            user_id: 用户标识符。
            retention_days: 保留天数，为 None 使用 config.l1_retention_days。

        Returns:
            删除的条目数。
        """
        if retention_days is None:
            retention_days = self._config.l1_retention_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff_ts = cutoff.timestamp()
        path = self._get_l1_path(user_id)
        data = self._load_json(path)
        original_len = len(data)
        data = [d for d in data if d.get("timestamp", 0) >= cutoff_ts]
        removed = original_len - len(data)
        if removed > 0:
            self._save_json(path, data)
        return removed

    def mark_dialogues_compressed(
        self, user_id: str, before_ts: float,
    ) -> int:
        """将指定时间之前的对话标记为已压缩。

        Returns:
            标记的条目数。
        """
        path = self._get_l1_path(user_id)
        data = self._load_json(path)
        count = 0
        for d in data:
            if d.get("timestamp", 0) < before_ts and not d.get("compressed", False):
                d["compressed"] = True
                count += 1
        if count > 0:
            self._save_json(path, data)
        return count

    def _load_all_l1(self, user_id: str) -> list[L1MemoryItem]:
        path = self._get_l1_path(user_id)
        return [L1MemoryItem.from_dict(d) for d in self._load_json(path)]

    # ==================================================================
    # L2 Path B — 每日磁盘摘要
    # ==================================================================

    def add_summary(
        self, user_id: str, date: str, summary: str,
        importance: int, hidden: bool = False,
    ) -> L2SummaryItem:
        """添加/覆盖指定日期的 L2 摘要。

        Args:
            user_id: 用户标识符。
            date: 日期（YYYY-MM-DD）。
            summary: 摘要内容。
            importance: 重要性分数（0-10）。
            hidden: 是否在注入时隐藏。

        Returns:
            创建的 L2SummaryItem。
        """
        item = L2SummaryItem(
            summary_id=str(uuid.uuid4()),
            user_id=user_id,
            date=date,
            summary=summary,
            importance=importance,
            timestamp=self._now_ts(),
            hidden=hidden,
        )
        path = self._get_l2_path(user_id)
        data = self._load_json(path)
        # 同一日期覆盖写入
        data = [d for d in data if d.get("date") != date]
        data.append(item.to_dict())
        self._save_json(path, data)
        return item

    def get_daily_summaries(
        self, user_id: str, *, last: int | None = None,
    ) -> list[L2SummaryItem]:
        """获取用户 L2 日摘要。

        Args:
            user_id: 用户标识符。
            last: 返回最近 N 天，为 None 返回全部。

        Returns:
            L2SummaryItem 列表（按日期降序）。
        """
        items = self._load_all_l2(user_id)
        items.sort(key=lambda i: i.date, reverse=True)
        if last is not None:
            items = items[:last]
        return items

    def get_l2_summaries_for_date(
        self, date: str,
    ) -> list[L2SummaryItem]:
        """按日期查询 L2 摘要（跨用户，用于调度器遍历）。"""
        results: list[L2SummaryItem] = []
        for f in self._l2_dir.glob("*.json"):
            for d in self._load_json(f):
                if d.get("date") == date:
                    results.append(L2SummaryItem.from_dict(d))
        return results

    def delete_old_summaries(
        self, user_id: str, ttl: int | None = None,
    ) -> int:
        """删除超过 TTL 天的 L2 摘要。

        Args:
            user_id: 用户标识符。
            ttl: 保留天数，为 None 使用 config.l2_ttl。

        Returns:
            删除的条目数。
        """
        if ttl is None:
            ttl = self._config.l2_ttl
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=ttl)).strftime(
            "%Y-%m-%d"
        )
        path = self._get_l2_path(user_id)
        data = self._load_json(path)
        original_len = len(data)
        data = [d for d in data if d.get("date", "") >= cutoff_date]
        removed = original_len - len(data)
        if removed > 0:
            self._save_json(path, data)
        return removed

    def _load_all_l2(self, user_id: str) -> list[L2SummaryItem]:
        path = self._get_l2_path(user_id)
        return [L2SummaryItem.from_dict(d) for d in self._load_json(path)]

    # ==================================================================
    # L2 Path A — 渐进周摘要
    # ==================================================================

    def get_weekly_summary(self, user_id: str) -> dict[str, Any] | None:
        """获取用户周摘要。

        Returns:
            dict with keys: user_id, summary, week_start, updated_at；
            无文件时返回 None。
        """
        path = self._get_weekly_path(user_id)
        data = self._load_json(path)
        if not data:
            return None
        return data[0] if isinstance(data, list) else data

    def set_weekly_summary(
        self, user_id: str, summary: str, week_start: str,
    ) -> None:
        """写入/覆盖用户周摘要。

        Args:
            user_id: 用户标识符。
            summary: 周摘要内容。
            week_start: 周起始日期（YYYY-MM-DD，周一）。
        """
        record: dict[str, Any] = {
            "user_id": user_id,
            "summary": summary,
            "week_start": week_start,
            "updated_at": self._now_ts(),
        }
        self._save_json(self._get_weekly_path(user_id), [record])

    def clear_weekly_summary(self, user_id: str) -> bool:
        """清空用户周摘要（周一重置）。

        Returns:
            是否实际删除了文件。
        """
        path = self._get_weekly_path(user_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # ==================================================================
    # L3 — 重要记忆元数据
    # ==================================================================

    def add_l3_memory(
        self, user_id: str, content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """添加 L3 记忆。

        Returns:
            创建的 memory_id。
        """
        memory_id = str(uuid.uuid4())
        item = L3MemoryItem(
            memory_id=memory_id,
            user_id=user_id,
            content=content,
            metadata=metadata or {},
            timestamp=self._now_ts(),
        )
        path = self._get_l3_path(user_id)
        data = self._load_json(path)
        data.append(item.to_dict())
        self._save_json(path, data)
        return memory_id

    def get_l3_memories(self, user_id: str) -> list[L3MemoryItem]:
        """获取用户全部 L3 记忆元数据。"""
        path = self._get_l3_path(user_id)
        return [L3MemoryItem.from_dict(d) for d in self._load_json(path)]

    def delete_l3_memory(self, user_id: str, memory_id: str) -> bool:
        """按 memory_id 删除 L3 记忆。"""
        path = self._get_l3_path(user_id)
        data = self._load_json(path)
        original_len = len(data)
        data = [d for d in data if d.get("memory_id") != memory_id]
        if len(data) < original_len:
            self._save_json(path, data)
            return True
        return False

    # ==================================================================
    # Session
    # ==================================================================

    def get_active_dialogue(self, user_id: str) -> list[L1MemoryItem]:
        """获取用户活跃对话（委托给 get_l1_dialogues）。"""
        return self.get_l1_dialogues(user_id)

    # ==================================================================
    # 工具
    # ==================================================================

    def get_all_users(self) -> list[str]:
        """收集所有存储中有数据的用户 ID（去重）。"""
        users: set[str] = set()
        for d in (self._l1_dir, self._l2_dir, self._l3_dir, self._weekly_dir):
            if d.exists():
                for f in d.glob("*.json"):
                    users.add(f.stem)
        return sorted(users)


def _ts_to_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
