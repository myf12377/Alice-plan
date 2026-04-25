"""AstrBot 记忆插件核心模块。

注意：内部模块使用绝对导入（from memory.xxx import ...），
在源码目录运行（memory 为顶层包）时正常，
部署到 data/plugins/ 后需改为相对导入。
各模块将在重构时逐步迁移到相对导入 + PluginConfig。
"""

# 延迟导入 — 重构完成后统一改为相对导入
# from .analyzer.analyzer import ImportanceAnalyzer
# from .compressor.compressor import DialogueCompressor
# from .context_injector import ContextInjector
# from .identity.identity import IdentityModule
# from .plugin_config import PluginConfig
# from .settings import MemorySettings
# from .storage.storage import (
#     L1MemoryItem,
#     L2SummaryItem,
#     L3MemoryItem,
#     MemoryStorage,
# )
# from .vector_store.vector_store import VectorStore

__all__ = [
    "ContextInjector",
    "DialogueCompressor",
    "IdentityModule",
    "ImportanceAnalyzer",
    "L1MemoryItem",
    "L2SummaryItem",
    "L3MemoryItem",
    "MemorySettings",
    "MemoryStorage",
    "PluginConfig",
    "VectorStore",
]
