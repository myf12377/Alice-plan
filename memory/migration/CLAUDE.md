# Migration Module

## 状态：✅ 完成 — 已迁移到 PluginConfig

## 构造

```python
def __init__(self, config: PluginConfig) -> None
```

## 公开 API

```python
def export_astrmem(self, user_id: str, output_path: Path) -> dict: ...
"""导出为 .astrmem JSON。返回 {l1_count, l2_count, l3_count, total}。"""

def import_astrmem(self, user_id: str, input_path: Path) -> dict: ...
"""从 .astrmem JSON 导入。用户ID不匹配时抛 ValueError。"""

def export_chroma(self, user_id: str, output_dir: Path) -> dict: ...
"""导出 ChromaDB 格式。"""

async def import_chroma(self, user_id: str, input_path: Path) -> dict: ...
"""从 ChromaDB JSON 导入 L3 记忆。"""

def backup(self, backup_dir: Path) -> dict: ...
"""完整备份。返回 {backup_path, l1_count, l2_count, ...}。"""

def restore(self, backup_path: Path) -> dict: ...
"""从备份恢复。返回 {l1_count, l2_count, ...}。"""
```

## 边界

不负责：记忆算法逻辑、调度决策。
依赖方：Main（可选）。
