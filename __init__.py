"""
astrbot_alice_memory_modul — Alice 三层记忆系统插件
提供 L1/L2/L3 三层记忆存储功能，支持 ChromaDB 向量检索与 LLM 压缩/分析。
"""

__version__ = "2.1.3"

# 延迟导入 — 插件部署在 data/plugins/ 下时，memory 是子包而非顶层模块。
# 模块通过 memory/__init__.py 使用相对导入统一导出。
# 主入口: main.py 的 AliceMemoryPlugin(Star)
