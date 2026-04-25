# AstrBot Memory Plugin

AstrBot 三层记忆存储插件 — L1 原始对话 / L2 双路中期记忆 / L3 长期向量记忆（衰减模型）。

## 记忆层次

| 层级 | 类型 | 生命周期 | 说明 |
|------|------|---------|------|
| **L1** | 原始对话 | 当日注入 / 磁盘保留 3 天 | 日内短期记忆，原始对话内容，每日凌晨清理 |
| **L2-A** | 渐进周摘要 | 一周，周一重置 | 上下文渐进压缩，覆盖式注入，累加更新 |
| **L2-B** | 每日磁盘摘要 | 7 天 TTL | 每日独立摘要，注入最近 N 天（默认 3 天），保持记忆连续性和人格稳定 |
| **L3** | 长期向量记忆 | 衰减模型 | 重要性评估 → 向量存储 → 语义检索 → 自然遗忘 |

### 记忆流转

```
用户对话
   ↓
L1 存储（原始对话，3天）
   ├─→ Path B 日压缩（凌晨 1:00）→ L2 日摘要（7天TTL）
   ├─→ Path A 上下文压缩（凌晨 4:00）→ 渐进周摘要（周一重置）
   └─→ 重要性分析 → L3 向量存储（衰减+合并+灰区重评）
```

### 上下文注入

- L1：注入当日所有对话
- L2-A：渐进周摘要覆盖注入
- L2-B：最近 N 天日摘要覆盖注入
- L3：向量相似度检索按需注入
- 每条管线独立标记，互不污染（管线级自主覆盖）

## 安装

```bash
pip install chromadb pydantic
```

将插件文件夹复制到 AstrBot 插件目录：
`<ASTRBOT_ROOT>/data/plugins/astrbot_alice_memory_modul/`

## 配置

在 AstrBot 管理界面配置，全部默认值即插即用。主要配置项：

- **通用**：`data_dir`, `log_level`(INFO), `hook_enabled`
- **L1**：`l1_enabled`, `l1_retention_days`(3), `l1_search_limit`(10)
- **L2**：`l2_enabled`, Path A/B 独立开关, `l2_ttl`(7), `l2_daily_inject_count`(3)
- **L3**：`l3_enabled`, `importance_threshold`(8), `l3_decay_rate`(0.995), `l3_delete_threshold`(3.0), `l3_gray_zone_upper`(5.0)
- **LLM**：`compress_model`, `importance_analyze_model`, `llm_max_tokens`(1024), `llm_temperature`(0.7)
- **注入开关**：`inject_l1`, `inject_l2_path_a`, `inject_l2_path_b`, `inject_l3`

所有配置项详见 `_conf_schema.json`。

## 插件命令

| 命令 | 功能 |
|------|------|
| `/compact [日期]` | 手动压缩对话（无参数=周摘要，有日期=日摘要） |
| `/important [消息ID]` | 将重要消息存入 L3 |
| `/forget [记忆ID]` | 删除指定记忆 |
| `/show_memory [查询] [数量]` | 搜索并显示 L3 记忆 |

### 压缩反馈模式

`manual_compress_feedback_mode` 配置 `/compact` 命令的响应方式：

| 模式 | 用户看到 | 说明 |
|------|---------|------|
| `silent` | 无任何返回 | 压缩后台静默执行，AI 下次对话仍可看到摘要 |
| `fixed` | 固定文本 | 返回 `manual_compress_feedback_text` 的设定文本 |
| `llm` | AI 动态回复 | 大模型根据对话氛围生成自然反馈（默认） |
| `visible` | 摘要预览 | 显示周摘要内容文本 |

## 开发

- Python 3.10+
- 格式化：`ruff format --isolated .`
- 检查：`ruff check --isolated .`
- 类型：`mypy --strict .`
- 测试：`pytest`

## 许可证

MIT
