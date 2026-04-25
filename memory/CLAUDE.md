# memory 子包

完整架构见 [根 CLAUDE.md](../CLAUDE.md) 和 [plan 文件](.claude/plans/playful-dancing-dongarra.md)。

| 目录 | 模块 | 层 | 状态 |
|------|------|----|------|
| [identity/](identity/) | IdentityModule | 1 | 稳定 |
| [storage/](storage/) | MemoryStorage | 1 | ✅ 完成 |
| [vector_store/](vector_store/) | VectorStore | 1 | 重构中 |
| [analyzer/](analyzer/) | ImportanceAnalyzer | 1 | 重构中 |
| [compressor/](compressor/) | DialogueCompressor | 2 | 重构中 |
| [migration/](migration/) | MigrationModule | 2 | 稳定 |
| context_injector.py | ContextInjector | 3 | 重构中（有已知 bug） |
| [scheduler/](scheduler/) | Scheduler | 4 | 重构中 |
| plugin_config.py | PluginConfig | 0 | ✅ 完成 |
| _conf_schema.json | 框架配置 schema | — | ✅ 完成 |
| main.py | AliceMemoryPlugin | 5 | A1（Identity + Storage 已接入） |

模块边界见各自 CLAUDE.md。
