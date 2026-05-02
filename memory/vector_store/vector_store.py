"""
向量存储模块 — ChromaDB 向量存储。

管理 L3 重要记忆的嵌入、衰减、合并。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import chromadb
from chromadb.config import Settings as ChromaSettings

if TYPE_CHECKING:
    from memory.plugin_config import PluginConfig


class VectorStore:
    """ChromaDB 向量存储 — L3 重要记忆。

    支持衰减模型、灰区检测、相似合并。
    """

    def __init__(
        self,
        data_dir: Path,
        config: PluginConfig,
        embedding_func: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> None:
        """初始化向量存储。

        Args:
            data_dir: 数据存储根目录。
            config: 插件配置（PluginConfig）。
            embedding_func: 外部 embedding 函数，为 None 时使用 ChromaDB 内置。
        """
        self._config = config
        self._embedding_func = embedding_func
        self._collection_name = "astrmemory_l3"
        self._client: Any = None
        self._collection: Any = None
        self._init_client(data_dir)

    def _init_client(self, data_dir: Path) -> None:
        persist_dir = data_dir / "chroma"
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"description": "AstrBot L3 memory storage"},
        )

    # ------------------------------------------------------------------
    # embedding 工具
    # ------------------------------------------------------------------

    async def _call_embedding_func_async(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """调用 embedding 函数（兼容同步/异步）。"""
        if self._embedding_func is None:
            return []
        import inspect

        if inspect.iscoroutinefunction(self._embedding_func):
            return await self._embedding_func(texts)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._embedding_func, texts)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_collection(self) -> bool:
        if self._collection is None:
            return False
        return True

    # ==================================================================
    # CRUD
    # ==================================================================

    async def add_memory(
        self,
        user_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """添加记忆到向量存储。

        Returns:
            vector_id (UUID 字符串)。
        """
        if not self._ensure_collection():
            raise RuntimeError("向量存储未初始化")

        vector_id = str(uuid.uuid4())
        now = self._now_iso()

        doc_metadata: dict[str, Any] = {
            "user_id": user_id,
            "content": content,
            "created_at": now,
            "last_accessed_at": now,
            "access_count": 0,
            "importance": metadata.get("importance", 0) if metadata else 0,
        }
        if metadata:
            doc_metadata.update(metadata)

        vector: list[float] | None = None
        if self._embedding_func:
            vectors = await self._call_embedding_func_async([content])
            vector = vectors[0] if vectors else None

        self._collection.add(
            ids=[vector_id],
            documents=[content],
            metadatas=[doc_metadata],
            embeddings=[vector] if vector else None,
        )
        return vector_id

    async def search(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """语义搜索用户记忆（同时更新访问计数）。

        Returns:
            [{"id", "content", "metadata", "distance"}, ...]
        """
        if not self._ensure_collection():
            return []

        query_vector: list[float] | None = None
        query_texts: list[str] | None = None
        if self._embedding_func:
            query_vectors = await self._call_embedding_func_async([query])
            query_vector = query_vectors[0] if query_vectors else None
        else:
            query_texts = [query]

        results = self._collection.query(
            query_texts=query_texts,
            query_embeddings=[query_vector] if query_vector else None,
            n_results=top_k,
            where={"user_id": user_id},
        )

        memories: list[dict[str, Any]] = []
        ids_to_update: list[str] = []
        new_metadatas: list[dict[str, Any]] = []

        if results["ids"] and results["ids"][0]:
            now = self._now_iso()
            for i, vid in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                # 更新访问计数
                acc = meta.get("access_count", 0) + 1
                meta["access_count"] = acc
                meta["last_accessed_at"] = now
                ids_to_update.append(vid)
                new_metadatas.append(meta)

                memories.append(
                    {
                        "id": vid,
                        "content": results["documents"][0][i]
                        if results["documents"]
                        else "",
                        "metadata": meta,
                        "distance": results["distances"][0][i]
                        if results["distances"]
                        else 0.0,
                    }
                )

            # 批量更新访问信息
            if ids_to_update:
                self._collection.update(ids=ids_to_update, metadatas=new_metadatas)

        return memories

    def delete_memory(self, vector_id: str) -> bool:
        """按 vector_id 删除记忆。"""
        if not self._ensure_collection():
            return False
        try:
            existing = self._collection.get(ids=[vector_id])
            if not existing["ids"]:
                return False
            self._collection.delete(ids=[vector_id])
            return True
        except Exception:
            return False

    def get_user_memories(self, user_id: str) -> list[dict[str, Any]]:
        """获取用户全部记忆。"""
        if not self._ensure_collection():
            return []
        results = self._collection.get(where={"user_id": user_id})
        memories: list[dict[str, Any]] = []
        if results["ids"]:
            for i, vid in enumerate(results["ids"]):
                memories.append(
                    {
                        "id": vid,
                        "content": results["documents"][i]
                        if results["documents"]
                        else "",
                        "metadata": results["metadatas"][i]
                        if results["metadatas"]
                        else {},
                    }
                )
        return memories

    def update_metadata(self, vector_id: str, metadata: dict[str, Any]) -> bool:
        """更新记忆元数据（合并到现有元数据）。"""
        if not self._ensure_collection():
            return False
        try:
            existing = self._collection.get(ids=[vector_id])
            if not existing["ids"]:
                return False
            old = existing["metadatas"][0] if existing["metadatas"] else {}
            new = {**old, **metadata}
            self._collection.update(ids=[vector_id], metadatas=[new])
            return True
        except Exception:
            return False

    # ==================================================================
    # 衰减模型
    # ==================================================================

    def apply_decay(self, user_id: str) -> tuple[int, int]:
        """对用户全部记忆执行衰减计算。

        公式:
            days = (now - created_at).days
            effective = importance × (decay_rate ^ days)
                      + min(access_count, 10) × access_bonus

        规则:
            effective < delete_threshold  → 删除
            effective ∈ [delete_threshold, gray_zone_upper] → 灰区
            effective > gray_zone_upper → 保留

        Returns:
            (deleted_count, gray_zone_count)
        """
        if not self._ensure_collection():
            return (0, 0)

        decay_rate = self._config.l3_decay_rate
        access_bonus = self._config.l3_access_bonus
        delete_threshold = self._config.l3_delete_threshold
        gray_zone_upper = self._config.l3_gray_zone_upper

        memories = self.get_user_memories(user_id)
        if not memories:
            return (0, 0)

        now = datetime.now(timezone.utc)
        to_delete: list[str] = []
        to_update: list[str] = []
        new_metadatas: list[dict[str, Any]] = []
        gray_count = 0

        for m in memories:
            meta = m["metadata"]
            importance = float(meta.get("importance", 0))
            created_str = meta.get("created_at", "")
            access_count = int(meta.get("access_count", 0))

            # 计算创建天数
            try:
                created_at = datetime.fromisoformat(created_str)
                days = max((now - created_at).days, 0)
            except (ValueError, TypeError):
                days = 0

            effective = (
                importance * (decay_rate**days) + min(access_count, 10) * access_bonus
            )

            if effective < delete_threshold:
                to_delete.append(m["id"])
            elif effective <= gray_zone_upper:
                gray_count += 1
                meta["effective_score"] = round(effective, 4)
                to_update.append(m["id"])
                new_metadatas.append(meta)
            else:
                # 保留，更新 effective_score
                meta["effective_score"] = round(effective, 4)
                to_update.append(m["id"])
                new_metadatas.append(meta)

        # 批量删除
        for vid in to_delete:
            try:
                self._collection.delete(ids=[vid])
            except Exception:
                pass

        # 批量更新 effective_score
        if to_update:
            try:
                self._collection.update(ids=to_update, metadatas=new_metadatas)
            except Exception:
                pass

        return (len(to_delete), gray_count)

    def get_gray_zone_memories(self, user_id: str) -> list[dict[str, Any]]:
        """获取灰区记忆（effective_score ∈ [delete_threshold, gray_zone_upper]）。

        必须先调用 apply_decay 以设置 effective_score。
        """
        if not self._ensure_collection():
            return []
        delete_threshold = self._config.l3_delete_threshold
        gray_zone_upper = self._config.l3_gray_zone_upper

        all_memories = self.get_user_memories(user_id)
        gray: list[dict[str, Any]] = []
        for m in all_memories:
            score = m["metadata"].get("effective_score")
            if score is not None and delete_threshold <= score <= gray_zone_upper:
                gray.append(m)
        return gray

    # ==================================================================
    # 相似度 & 合并
    # ==================================================================

    async def find_similar_by_content(
        self,
        user_id: str,
        content: str,
        threshold: float,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """按内容文本查找相似记忆。

        Args:
            user_id: 用户标识符。
            content: 查询内容文本。
            threshold: 余弦相似度阈值（如 0.9）。
            top_k: 最多返回条数。

        Returns:
            相似度 >= threshold 的记忆列表（按相似度降序）。
        """
        if not self._ensure_collection():
            return []
        if not content:
            return []

        query_vector: list[float] | None = None
        if self._embedding_func:
            vectors = await self._call_embedding_func_async([content])
            query_vector = vectors[0] if vectors else None

        total = self._collection.count()
        if total == 0:
            return []

        results = self._collection.query(
            query_texts=[content] if query_vector is None else None,
            query_embeddings=[query_vector] if query_vector else None,
            n_results=min(top_k, total),
            where={"user_id": user_id},
        )

        similar: list[dict[str, Any]] = []
        if results["ids"] and results["ids"][0]:
            for i, vid in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results["distances"] else 1.0
                similarity = 1.0 - distance
                if similarity >= threshold:
                    similar.append(
                        {
                            "id": vid,
                            "content": results["documents"][0][i]
                            if results["documents"]
                            else "",
                            "metadata": results["metadatas"][0][i]
                            if results["metadatas"]
                            else {},
                            "similarity": round(similarity, 4),
                        }
                    )

        similar.sort(key=lambda x: x["similarity"], reverse=True)
        return similar

    async def merge_memories(
        self,
        vid1: str,
        vid2: str,
        merged_content: str,
        new_score: float,
    ) -> str:
        """合并两条记忆：删旧建新。

        Args:
            vid1, vid2: 被合并的两条旧 vector_id。
            merged_content: 合并后的内容（由 Analyzer.merge_content 生成）。
            new_score: 新分数 = max(s1, s2) + 0.5。

        Returns:
            新 vector_id。
        """
        # 收集旧元数据
        meta1: dict[str, Any] = {}
        meta2: dict[str, Any] = {}
        existing = self._collection.get(ids=[vid1, vid2])
        if existing["metadatas"]:
            if existing["metadatas"][0]:
                meta1 = existing["metadatas"][0]
            if len(existing["metadatas"]) > 1 and existing["metadatas"][1]:
                meta2 = existing["metadatas"][1]

        # 删除旧条目
        self._collection.delete(ids=[vid1, vid2])

        # 创建新条目
        new_id = str(uuid.uuid4())
        now = self._now_iso()
        new_metadata = {
            "user_id": meta1.get("user_id", meta2.get("user_id", "")),
            "content": merged_content,
            "created_at": now,
            "last_accessed_at": now,
            "access_count": 0,
            "importance": new_score,
            "merged_from": [vid1, vid2],
        }

        vector: list[float] | None = None
        if self._embedding_func:
            vectors = await self._call_embedding_func_async([merged_content])
            vector = vectors[0] if vectors else None

        self._collection.add(
            ids=[new_id],
            documents=[merged_content],
            metadatas=[new_metadata],
            embeddings=[vector] if vector else None,
        )
        return new_id

    # ==================================================================
    # 工具
    # ==================================================================

    def close(self) -> None:
        """关闭向量存储连接。"""
        self._client = None
        self._collection = None
