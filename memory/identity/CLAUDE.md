# Identity Module

## 概述

身份模块负责跨平台用户身份映射，支持 qqofficial 和 aiocqhttp 适配器。

## 状态：[稳定，本次重构不变动]

功能完备，API 无变化。

## 核心类

### IdentityModule

```python
class IdentityModule:
    def __init__(self, data_dir: Path) -> None: ...
    def register_user(self, platform: str, platform_user_id: str) -> str: ...
    def get_user_id(self, platform: str, platform_user_id: str) -> str | None: ...
    def get_all_users(self) -> list[str]: ...
```
