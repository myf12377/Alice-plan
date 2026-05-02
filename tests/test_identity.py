"""
身份模块测试。
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from memory.identity.identity import IdentityModule


class TestIdentityModule:
    """IdentityModule类的测试。"""

    @pytest.fixture
    def temp_dir(self) -> Iterator[Path]:
        """创建测试用临时目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def identity_module(self, temp_dir: Path) -> IdentityModule:
        """创建身份模块实例。"""
        return IdentityModule(temp_dir)

    def test_register_new_user(self, identity_module: IdentityModule) -> None:
        """测试注册新用户。"""
        user_id = identity_module.register_user("qqofficial", "user123")
        assert user_id is not None
        assert len(user_id) > 0

    def test_register_same_user_twice(
        self,
        identity_module: IdentityModule,
    ) -> None:
        """测试同一用户注册两次返回相同ID。"""
        id1 = identity_module.register_user("qqofficial", "user123")
        id2 = identity_module.register_user("qqofficial", "user123")
        assert id1 == id2

    def test_get_user_id_exists(
        self,
        identity_module: IdentityModule,
    ) -> None:
        """测试获取已存在的用户ID。"""
        expected_id = identity_module.register_user("qqofficial", "user123")
        actual_id = identity_module.get_user_id("qqofficial", "user123")
        assert actual_id == expected_id

    def test_get_user_id_not_exists(
        self,
        identity_module: IdentityModule,
    ) -> None:
        """测试获取不存在的用户ID返回None。"""
        actual_id = identity_module.get_user_id("qqofficial", "nonexistent")
        assert actual_id is None

    def test_get_all_users(self, identity_module: IdentityModule) -> None:
        """测试获取所有用户。"""
        id1 = identity_module.register_user("qqofficial", "user1")
        id2 = identity_module.register_user("qqofficial", "user2")
        all_users = identity_module.get_all_users()
        assert id1 in all_users
        assert id2 in all_users

