"""
迁移模块 - 记忆数据导入/导出。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory.plugin_config import PluginConfig


class MigrationModule:
    """记忆数据迁移模块。

    支持导出/导入 .astrmem JSON 格式和 ChromaDB parquet 格式。
    """

    def __init__(self, config: PluginConfig) -> None:
        """初始化迁移模块。"""
        self._config = config
        self._data_dir = config.data_dir
        self._l1_dir = config.data_dir / "l1"
        self._l2_dir = config.data_dir / "l2"
        self._l3_dir = config.data_dir / "l3"
        self._chroma_dir = config.data_dir / "chroma"

    def export_astrmem(
        self,
        user_id: str,
        output_path: Path,
    ) -> dict[str, Any]:
        """导出用户记忆到 .astrmem JSON 文件。

        Args:
            user_id: 用户标识符。
            output_path: 输出文件路径。

        Returns:
            导出统计信息。
        """
        from memory.storage.storage import MemoryStorage

        storage = MemoryStorage(self._config)

        l1_dialogues = storage.get_l1_dialogues(user_id)
        l2_summaries = storage.get_daily_summaries(user_id)
        l3_memories = storage.get_l3_memories(user_id)

        export_data = {
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "l1_dialogues": [asdict(d) for d in l1_dialogues],
            "l2_summaries": [asdict(s) for s in l2_summaries],
            "l3_memories": [asdict(m) for m in l3_memories],
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

        return {
            "l1_count": len(l1_dialogues),
            "l2_count": len(l2_summaries),
            "l3_count": len(l3_memories),
            "total": len(l1_dialogues) + len(l2_summaries) + len(l3_memories),
        }

    def import_astrmem(
        self,
        user_id: str,
        input_path: Path,
    ) -> dict[str, Any]:
        """从 .astrmem JSON 文件导入记忆。

        Args:
            user_id: 用户标识符。
            input_path: 输入文件路径。

        Returns:
            导入统计信息。
        """
        from memory.storage.storage import (
            L1MemoryItem,
            L2SummaryItem,
            L3MemoryItem,
            MemoryStorage,
        )

        storage = MemoryStorage(self._config)

        with open(input_path, "r", encoding="utf-8") as f:
            import_data = json.load(f)

        version = import_data.get("version", "1.0")
        if version != "1.0":
            raise ValueError(f"不支持的导出版本: {version}")

        imported_user_id = import_data.get("user_id")
        if imported_user_id and imported_user_id != user_id:
            raise ValueError(
                f"用户ID不匹配: 期望 {user_id}, 文件中为 {imported_user_id}"
            )

        l1_count = 0
        for d in import_data.get("l1_dialogues", []):
            item = L1MemoryItem.from_dict(d)
            storage.append_dialogue(item.user_id, item.role, item.content)
            l1_count += 1

        l2_count = 0
        for s in import_data.get("l2_summaries", []):
            s_item = L2SummaryItem.from_dict(s)
            storage.add_summary(
                user_id, s_item.date, s_item.summary, s_item.importance, s_item.hidden,
            )
            l2_count += 1

        l3_count = 0
        for m in import_data.get("l3_memories", []):
            m_item = L3MemoryItem.from_dict(m)
            storage.add_l3_memory(m_item.user_id, m_item.content, m_item.metadata)
            l3_count += 1

        return {
            "l1_count": l1_count,
            "l2_count": l2_count,
            "l3_count": l3_count,
            "total": l1_count + l2_count + l3_count,
        }

    def export_chroma(
        self,
        user_id: str,
        output_dir: Path,
    ) -> dict[str, Any]:
        """导出用户 L3 记忆到 ChromaDB parquet 格式。

        Args:
            user_id: 用户标识符。
            output_dir: 输出目录。

        Returns:
            导出统计信息。
        """
        from memory.vector_store.vector_store import VectorStore

        vector_store = VectorStore(self._data_dir, self._config)
        memories = vector_store.get_user_memories(user_id)

        output_dir.mkdir(parents=True, exist_ok=True)

        export_file = output_dir / f"{user_id}_l3.json"
        with open(export_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": "1.0",
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "user_id": user_id,
                    "memories": memories,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        vector_store.close()

        return {
            "l3_count": len(memories),
            "output_path": str(output_dir / f"{user_id}_l3.json"),
        }

    async def import_chroma(
        self,
        user_id: str,
        input_path: Path,
    ) -> dict[str, Any]:
        """从 ChromaDB parquet 格式导入 L3 记忆。

        Args:
            user_id: 用户标识符。
            input_path: 输入文件路径。

        Returns:
            导入统计信息。
        """
        from memory.vector_store.vector_store import VectorStore

        with open(input_path, "r", encoding="utf-8") as f:
            import_data = json.load(f)

        version = import_data.get("version", "1.0")
        if version != "1.0":
            raise ValueError(f"不支持的导出版本: {version}")

        vector_store = VectorStore(self._data_dir, self._config)

        memories = import_data.get("memories", [])
        imported_count = 0

        for memory in memories:
            content = memory.get("content", "")
            metadata = memory.get("metadata", {})
            await vector_store.add_memory(user_id, content, metadata)
            imported_count += 1

        vector_store.close()

        return {
            "l3_count": imported_count,
        }

    def backup(
        self,
        backup_dir: Path,
    ) -> dict[str, Any]:
        """备份所有数据到指定目录。

        Args:
            backup_dir: 备份目录。

        Returns:
            备份统计信息。
        """
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_subdir = backup_dir / f"astrmemory_backup_{timestamp}"

        l1_backup = backup_subdir / "l1"
        l2_backup = backup_subdir / "l2"
        l3_backup = backup_subdir / "l3"
        chroma_backup = backup_subdir / "chroma"

        l1_backup.mkdir(parents=True, exist_ok=True)
        l2_backup.mkdir(parents=True, exist_ok=True)
        l3_backup.mkdir(parents=True, exist_ok=True)

        l1_count = 0
        if self._l1_dir.exists():
            for file in self._l1_dir.glob("*.json"):
                shutil.copy2(file, l1_backup / file.name)
                l1_count += 1

        l2_count = 0
        if self._l2_dir.exists():
            for file in self._l2_dir.glob("*.json"):
                shutil.copy2(file, l2_backup / file.name)
                l2_count += 1

        l3_count = 0
        if self._l3_dir.exists():
            for file in self._l3_dir.glob("*.json"):
                shutil.copy2(file, l3_backup / file.name)
                l3_count += 1

        chroma_count = 0
        if self._chroma_dir.exists():
            shutil.copytree(self._chroma_dir, chroma_backup, dirs_exist_ok=True)
            chroma_count = len(list(chroma_backup.rglob("*.parquet")))

        manifest = {
            "backup_at": datetime.now(timezone.utc).isoformat(),
            "l1_count": l1_count,
            "l2_count": l2_count,
            "l3_count": l3_count,
            "chroma_count": chroma_count,
        }

        manifest_path = backup_subdir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        return {
            "backup_path": str(backup_subdir),
            **manifest,
        }

    def restore(
        self,
        backup_path: Path,
    ) -> dict[str, Any]:
        """从备份恢复所有数据。

        Args:
            backup_path: 备份目录路径。

        Returns:
            恢复统计信息。
        """
        manifest_path = backup_path / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"备份清单文件不存在: {manifest_path}")

        with open(manifest_path, "r", encoding="utf-8") as f:
            json.load(f)

        l1_backup = backup_path / "l1"
        l2_backup = backup_path / "l2"
        l3_backup = backup_path / "l3"
        chroma_backup = backup_path / "chroma"

        l1_count = 0
        if l1_backup.exists():
            shutil.rmtree(self._l1_dir, ignore_errors=True)
            shutil.copytree(l1_backup, self._l1_dir)
            l1_count = len(list(self._l1_dir.glob("*.json")))

        l2_count = 0
        if l2_backup.exists():
            shutil.rmtree(self._l2_dir, ignore_errors=True)
            shutil.copytree(l2_backup, self._l2_dir)
            l2_count = len(list(self._l2_dir.glob("*.json")))

        l3_count = 0
        if l3_backup.exists():
            shutil.rmtree(self._l3_dir, ignore_errors=True)
            shutil.copytree(l3_backup, self._l3_dir)
            l3_count = len(list(self._l3_dir.glob("*.json")))

        chroma_count = 0
        if chroma_backup.exists():
            shutil.rmtree(self._chroma_dir, ignore_errors=True)
            shutil.copytree(chroma_backup, self._chroma_dir)
            chroma_count = len(list(self._chroma_dir.rglob("*.parquet")))

        return {
            "l1_count": l1_count,
            "l2_count": l2_count,
            "l3_count": l3_count,
            "chroma_count": chroma_count,
        }
