# Scheduler Module

## 角色

6 段定时任务编排，负责记忆的日常维护。

## 状态：✅ 完成 — 已迁移到 PluginConfig，6 段定时任务全部实现

## 构造

```python
def __init__(
    self, context: Any, storage: MemoryStorage, identity_module: IdentityModule,
    vector_store: VectorStore | None, config: PluginConfig,
    compressor: DialogueCompressor | None = None, analyzer: ImportanceAnalyzer | None = None,
) -> None
```

## 公开 API

```python
async def start(self) -> None: ...
"""向 AstrBot CronJobManager 注册 6 个定时任务。无 cron_manager 时静默返回。
注册前先清理同名旧任务，防止重启累积重复。"""
```

6 个任务入口（均为 async，遍历全部用户执行）：

| 时间 | 方法 | 操作 |
|------|------|------|
| 01:00 | `_compress_daily()` | Storage(L1)→Compressor→Storage(L2) |
| 02:00 | `_l1_cleanup()` | Storage.trim_to_recent_rounds(l1_save_rounds) + Storage.delete_old_summaries(ttl=7) |
| 03:00 | `_l3_maintenance()` | VectorStore.apply_decay→get_gray→Analyzer.batch_recheck |
| 04:00 | `_compress_context()` | Storage(L1+L2+周)→Compressor(内部写入周摘要) |
| 周一05:00 | `_reset_weekly()` | Storage.clear_weekly_summary |
| 每月1日06:00 | `_l3_merge()` | VectorStore.find_similar→Analyzer.merge→VectorStore.merge_memories |

## 边界

不负责：压缩/衰减/清理/合并算法（调各模块），钩子注册（Main 的职责），命令处理。`_compress_context` 不直接操作 Storage，周摘要写入由 Compressor 管理。
依赖方：Main.initialize()（唯一调用方，Star 生命周期钩子）。
