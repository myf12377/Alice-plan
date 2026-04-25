"""pytest 共享配置。"""

import sys
from pathlib import Path

# 使 astrbot 模块在测试环境可导入
astrbot_path = Path("C:/Users/lenovo/Projects/test/astrbot")
if astrbot_path.exists() and str(astrbot_path) not in sys.path:
    sys.path.insert(0, str(astrbot_path))
