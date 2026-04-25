# Compressor Module

## 角色

Path A/B 双路对话压缩，使用 LLM 将对话内容压缩为摘要。

## 状态：✅ 完成 — 已迁移到 PluginConfig，Path A/B 双路压缩全部实现

## 构造

```python
def __init__(self, context: Any, storage: MemoryStorage, config: PluginConfig) -> None
```

## 公开 API

```python
async def compress_context_summary(self, user_id: str, umo: str = "") -> str | None: ...
"""Path A：合并已有周摘要 + 当日L1 + Path B日摘要 → 渐进周摘要（不含L3）。"""

async def compress_day(self, user_id: str, date: str, hidden: bool = False, umo: str = "") -> str | None: ...
"""Path B：压缩指定日期L1对话 → 日摘要。"""
```

内部：`_generate_summary(content, path)` 按 path="a"/"b" 选用 `l2_compress_prompt_a`/`l2_compress_prompt_b` 模板调 LLM。

## 边界

不负责：数据读写（从参数接收，结果返回）、调度决策。
依赖方：Scheduler, Main(/compact)。
