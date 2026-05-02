# Storage Module

## 角色

L1/L2/L3 三层记忆的 JSON 文件持久化，按 bot 隔离。

## 状态：✅ v2.2.0 — 新增 get_recent_rounds / trim_to_recent_rounds

## 构造

```python
def __init__(self, data_dir: Path, config: PluginConfig) -> None
```

## 数据模型

```python
@dataclass
class L1MemoryItem:
    message_id: str; user_id: str; role: str  # "user"|"assistant"
    content: str; timestamp: float

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
def get_l1_dialogues(uid, date=None) -> list[L1MemoryItem]: ...
def get_recent_rounds(uid, max_rounds=None) -> list[dict]: ...  # v2.2.0 按日期分组
def trim_to_recent_rounds(uid, keep_rounds=None) -> int: ...   # v2.2.0 轮次裁剪

# L2 Path B
def add_summary(uid, date, summary, importance, hidden=False) -> L2SummaryItem: ...
def get_daily_summaries(uid, last=None) -> list[L2SummaryItem]: ...
def delete_old_summaries(uid, ttl=7) -> int: ...

# L2 Path A
def get_weekly_summary(uid) -> dict | None: ...
def set_weekly_summary(uid, summary, week_start) -> None: ...
def clear_weekly_summary(uid) -> bool: ...

# L3
def add_l3_memory(uid, content, metadata=None) -> str: ...
def get_l3_memories(uid) -> list[L3MemoryItem]: ...
```

## 边界

不负责：LLM 调用、向量操作、调度决策、身份解析。
依赖方：Main, Compressor, ContextInjector, Scheduler, Migration。
