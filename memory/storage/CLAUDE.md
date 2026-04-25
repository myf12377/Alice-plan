# Storage Module

## 角色

L1/L2/L3 三层记忆的 JSON 文件持久化，按 bot 隔离。

## 状态：✅ 完成 — 已迁移到 PluginConfig，L2 按用户隔离

## 构造

```python
def __init__(self, data_dir: Path, config: PluginConfig) -> None
```

## 数据模型

```python
@dataclass
class L1MemoryItem:
    message_id: str; user_id: str; role: str  # "user"|"assistant"
    content: str; timestamp: float; compressed: bool
    content_type: str; media_url: str | None

@dataclass
class L2SummaryItem:
    summary_id: str; user_id: str; date: str   # YYYY-MM-DD
    summary: str; importance: int; timestamp: float; hidden: bool

@dataclass
class L3MemoryItem:
    memory_id: str; user_id: str; content: str
    metadata: dict; timestamp: float
```

## 公开 API

```python
# L1
def append_dialogue(uid, role, content) -> L1MemoryItem: ...
def get_today_dialogues(uid) -> list[L1MemoryItem]: ...
def get_l1_dialogues(uid) -> list[L1MemoryItem]: ...
def mark_dialogues_compressed(uid, before_ts) -> int: ...
def delete_old_l1_dialogues(uid, retention_days=3) -> int: ...

# L2 Path B
def add_summary(uid, date, summary, importance, hidden=False) -> L2SummaryItem: ...
def get_daily_summaries(uid=None, date=None, last=None) -> list[L2SummaryItem]: ...
def delete_old_summaries(uid, ttl=7) -> int: ...

# L2 Path A
def get_weekly_summary(uid) -> dict | None: ...
def set_weekly_summary(uid, summary, week_start) -> None: ...
def clear_weekly_summary(uid) -> None: ...

# L3
def add_l3_memory(uid, content, metadata=None) -> str: ...
def get_l3_memories(uid) -> list[L3MemoryItem]: ...
def delete_l3_memory(uid, memory_id) -> bool: ...

# Session
def get_active_dialogue(uid) -> list[L1MemoryItem]: ...
def end_dialogue(uid) -> None: ...
```

## 边界

不负责：LLM 调用、向量操作、调度决策、身份解析。
依赖方：Main, Compressor, ContextInjector, Scheduler, Migration。
