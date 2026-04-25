"""
迁移模块测试。
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from memory.migration.migration import MigrationModule
from memory.plugin_config import PluginConfig
from memory.storage.storage import MemoryStorage


class TestMigrationModule:
    """MigrationModule类的测试。"""

    @pytest.fixture
    def temp_dir(self) -> Iterator[Path]:
        """创建测试用临时目录。"""
        import shutil
        tmp = tempfile.mkdtemp()
        yield Path(tmp)
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir: Path) -> PluginConfig:
        """创建测试配置。"""
        return PluginConfig(data_dir=temp_dir)

    @pytest.fixture
    def migration(self, config: PluginConfig) -> MigrationModule:
        """创建迁移模块实例。"""
        return MigrationModule(config)

    @pytest.fixture
    def storage(self, config: PluginConfig) -> MemoryStorage:
        """创建存储实例。"""
        return MemoryStorage(config)

    def test_export_astrmem(
        self, migration: MigrationModule, storage: MemoryStorage
    ) -> None:
        """测试导出 .astrmem 文件。"""
        storage.append_dialogue("user1", "user", "Hello")
        storage.append_dialogue("user1", "assistant", "Hi")
        storage.add_summary("user1", "2024-04-20", "Test summary", 5)
        storage.add_l3_memory("user1", "Important memory")

        output_path = Path(tempfile.mktemp(suffix=".astrmem"))
        result = migration.export_astrmem("user1", output_path)

        assert result["l1_count"] == 2
        assert result["l2_count"] == 1
        assert result["l3_count"] == 1
        assert result["total"] == 4
        assert output_path.exists()

    def test_import_astrmem(
        self, migration: MigrationModule, storage: MemoryStorage
    ) -> None:
        """测试导入 .astrmem 文件。"""
        storage.append_dialogue("user1", "user", "Hello")
        storage.add_summary("user1", "2024-04-20", "Test summary", 5)

        export_path = Path(tempfile.mktemp(suffix=".astrmem"))
        migration.export_astrmem("user1", export_path)

        storage.delete_l1_dialogue(
            "user1", storage.get_l1_dialogues("user1")[0].message_id
        )
        storage.delete_old_summaries("user1", ttl=0)

        result = migration.import_astrmem("user1", export_path)

        assert result["l1_count"] == 1
        assert result["l2_count"] == 1
        assert result["l3_count"] == 0

    def test_import_astrmem_user_id_mismatch(
        self, migration: MigrationModule, storage: MemoryStorage
    ) -> None:
        """测试导入时用户ID不匹配。"""
        storage.append_dialogue("user1", "user", "Hello")

        export_path = Path(tempfile.mktemp(suffix=".astrmem"))
        migration.export_astrmem("user1", export_path)

        with pytest.raises(ValueError, match="用户ID不匹配"):
            migration.import_astrmem("user2", export_path)

    async def test_export_chroma(
        self, migration: MigrationModule
    ) -> None:
        """测试导出 ChromaDB 格式。"""
        import shutil
        from memory.vector_store.vector_store import VectorStore

        vs = VectorStore(migration._data_dir, migration._config)
        await vs.add_memory("user1", "Memory 1")
        await vs.add_memory("user1", "Memory 2")
        vs.close()

        output_dir = Path(tempfile.mkdtemp())
        try:
            result = migration.export_chroma("user1", output_dir)
            assert result["l3_count"] == 2
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    async def test_import_chroma(
        self, migration: MigrationModule
    ) -> None:
        """测试导入 ChromaDB 格式。"""
        import shutil
        from memory.vector_store.vector_store import VectorStore

        vs = VectorStore(migration._data_dir, migration._config)
        await vs.add_memory("user1", "Memory 1")
        vs.close()

        export_dir = Path(tempfile.mkdtemp())
        try:
            migration.export_chroma("user1", export_dir)
            export_file = export_dir / "user1_l3.json"
            result = await migration.import_chroma("user1", export_file)
            assert result["l3_count"] >= 1
        finally:
            shutil.rmtree(export_dir, ignore_errors=True)

    def test_backup(self, migration: MigrationModule, storage: MemoryStorage) -> None:
        """测试备份功能。"""
        storage.append_dialogue("user1", "user", "Hello")
        storage.add_summary("user1", "2024-04-20", "Summary", 5)

        backup_dir = Path(tempfile.mkdtemp())
        result = migration.backup(backup_dir)

        assert "backup_path" in result
        assert result["l1_count"] == 1
        assert result["l2_count"] == 1

    def test_restore(self, migration: MigrationModule, storage: MemoryStorage) -> None:
        """测试恢复功能。"""
        storage.append_dialogue("user1", "user", "Hello")
        storage.add_summary("user1", "2024-04-20", "Summary", 5)

        backup_dir = Path(tempfile.mkdtemp())
        result = migration.backup(backup_dir)
        backup_path = Path(result["backup_path"])

        storage.delete_l1_dialogue(
            "user1", storage.get_l1_dialogues("user1")[0].message_id
        )

        result = migration.restore(backup_path)

        assert result["l1_count"] == 1
        assert result["l2_count"] == 1
