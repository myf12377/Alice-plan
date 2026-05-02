# Vector Store Module

## 角色

ChromaDB 向量存储，管理 L3 重要记忆的嵌入、衰减、合并。

## 状态：✅ 完成 — 已迁移到 PluginConfig，衰减/灰区/合并全部实现

## 构造

```python
def __init__(self, data_dir: Path, config: PluginConfig,
             embedding_func: Callable | None = None) -> None
```

## 公开 API

```python
# CRUD
async def add_memory(uid, content, metadata=None) -> str: ...
async def search(uid, query, top_k=5) -> list[dict]: ...
def delete_memory(vector_id) -> bool: ...
def get_user_memories(uid) -> list[dict]: ...
def update_metadata(vector_id, metadata) -> bool: ...

# 衰减
def apply_decay(uid) -> tuple[int, int]: ...
"""effective = importance × (decay_rate ^ days) + min(access_count,10) × access_bonus
   < delete_threshold → 删除 | ∈ (delete_threshold, gray_zone_upper] → 灰区 | > gray_zone_upper → 保留
   返回 (deleted_count, gray_zone_count)"""

def get_gray_zone_memories(uid) -> list[dict]: ...
"""获取灰区记忆。"""

# 合并
async def find_similar_by_content(uid, content, threshold, top_k=20) -> list[dict]: ...
"""按内容文本查找相似记忆。"""

async def merge_memories(vid1, vid2, merged_content, new_score) -> str: ...
"""删除旧条目，创建合并条目，返回新 vector_id。"""

# 工具
def close() -> None: ...
```

## 边界

不负责：重要性判定（来自 Analyzer）、LLM 合并内容（来自 Analyzer）、存储调度（来自 Scheduler）、JSON 文件管理（来自 Storage）。
依赖方：Main, ContextInjector, Scheduler, Migration。
