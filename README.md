# AstrBot Alice Memory Tier v3.1.0

AstrBot 三层记忆插件 — 为主动层/中间层提供完整的类人记忆后端。

## 概述

- **L1 短期记忆**：原始对话轮次滑窗，磁盘保留 200 轮，按日期分组注入
- **L2 中期记忆**：Path A 渐进周摘要 + Path B 日摘要，双路径自动压缩，分层互补注入
- **L3 长期记忆**：通过 AstrBot EmbeddingProvider 语义嵌入（支持任意模型切换），余弦距离检索，艾宾浩斯衰减模型自然遗忘，ChromaDB + JSON 双写防丢失
- **自校准**：换模型后自动计算建议阈值
- **写入去重**：存入前检测相似记忆，自动合并而非重复存储
- **31 项配置**：全部有默认值，即插即用

## 快速开始

```bash
pip install chromadb pydantic
```

将插件文件夹复制到 AstrBot 插件目录：
`<ASTRBOT_ROOT>/data/plugins/astrbot_alice_memory_tier/`

重启 AstrBot，插件自动加载。正常对话即可积累记忆。

**首次使用**：
1. WebUI 确保 `l3_embedding_provider = "auto"`（或填模型名如 `BAAI/bge-m3`）
2. 确保 AstrBot 已配置至少一个 Embedding 类型 Provider
3. 发送 `/compact` 手动压缩，或等待凌晨定时任务

## 三层记忆架构

| 层级 | 类型 | 生命周期 | 说明 |
|------|------|---------|------|
| **L1** | 原始对话 | 轮次滑窗 / 磁盘 200 轮 | 按轮次平滑滑出，按日期分组注入 |
| **L2-A** | 渐进周摘要 | 一周，周一重置 | 合并式周摘要，覆盖式注入 |
| **L2-B** | 每日磁盘摘要 | 7 天 TTL | 独立日摘要，注入最近 N 天 |
| **L3** | 长期向量记忆 | 衰减模型 | 外部嵌入 + 余弦检索 + 自校准阈值 + 双写防丢失 |

### 记忆流转

```
用户对话
   ↓
L1 存储（原始对话，200轮）
   ├─→ Path B 日压缩（凌晨 1:00）→ L2 日摘要（7天TTL）
   ├─→ Path A 周压缩（凌晨 4:00）→ 渐进周摘要（周一重置）
   └─→ 重要性分析 → L3 双写（ChromaDB cosine + l3/{uid}.json 备份）
```

### L3 衰减模型

```
effective_score = importance × 0.995^days + min(access_count, 10) × 0.3
```

| 区间 | 判定 | 动作 |
|------|------|------|
| < delete_threshold | 遗忘 | 自动删除 |
| [delete_threshold, gray_zone_upper] | 灰区 | 触发 LLM 重新评估 |
| > gray_zone_upper | 稳固 | 保留 |

### L3 自校准

切换嵌入模型后自动校准阈值：
- 对所有记忆两两计算余弦相似度
- 取中位数作为该模型的专属检索阈值
- 手动修改 `l3_search_similarity` 可覆盖

## 上下文注入

| 管线 | 注入位置 | 标记 |
|------|---------|------|
| L1 | `contexts` | `[YYYY-MM-DD 对话]` 日期标记 |
| L2-A | `extra_user_content_parts` | `[周摘要]`（覆盖式） |
| L2-B | `extra_user_content_parts` | `[L2记忆]`（覆盖式） |
| L3 | `extra_user_content_parts` | `[L3记忆]`（覆盖式） |

## 主动层对接

当主动层就位后，WebUI 将 `hook_enabled` 设为 `false` → 记忆层静默钩子，主动层接管。

通过 `context.get_all_stars()` 获取记忆层实例：

```python
for star in context.get_all_stars():
    if star.name == "astrbot_alice_memory_tier":
        plugin = star.star_cls

# === 6 个公开 @property ===
plugin.storage      # L1/L2/L3 JSON 存储读写
plugin.vector_store  # L3 向量检索
plugin.identity     # 跨平台用户身份映射
plugin.injector     # 记忆上下文注入器
plugin.compressor   # L2 压缩器
plugin.analyzer     # LLM 重要性分析

# === 4 个纯读取方法（不影响注入管线）===
l1 = plugin.injector.get_l1_context(uid)           # L1 最近 N 轮
l2a = plugin.injector.get_l2_path_a_context(uid)   # 周摘要
l2b = plugin.injector.get_l2_path_b_context(uid)   # 日摘要
l3 = await plugin.injector.get_l3_context(uid, query)  # 向量检索

# === 写入 ===
plugin.storage.append_dialogue(uid, "user", content)
plugin.storage.append_dialogue(uid, "assistant", content)
results = await plugin.vector_store.search(uid, query)
```

## 配置

在 AstrBot Web 管理界面配置，共 31 项，全部有默认值。

| 分类 | 关键配置项 |
|------|-----------|
| 通用 | `data_dir`、`hook_enabled`、`manage_context` |
| L1 | `l1_enabled`、`l1_save_rounds`(200)、`l1_inject_rounds`(80) |
| L2 | Path A/B 独立开关、`l2_ttl`(7)、`l2_daily_inject_count`(3) |
| L3 | `l3_enabled`、`l3_embedding_provider`(auto)、`l3_search_similarity`(0.4/检索)、`l3_merge_similarity`(0.60/合并)、`l3_search_count`(5) |
| LLM | `compress_model`、`importance_analyze_model`、`llm_max_tokens`(1024) |
| 注入 | `inject_l1`、`inject_l2_path_a`、`inject_l2_path_b`、`inject_l3` |

所有配置项详见 `_conf_schema.json`。

## 插件命令

| 命令 | 功能 | 权限 |
|------|------|:--:|
| `/compact [日期]` | 手动压缩（无参=周摘要，有日期=日摘要） | 管理员 |
| `/important <内容>` | 分析重要性并存入 L3（自动去重合并） | 成员 |
| `/show_memory <查询>` | 语义搜索 L3 记忆（带相似度） | 成员 |
| `/l3_merge` | 手动合并当前用户 L3 相似记忆 | 成员 |
| `/l3_stats` | 查看 L3 状态（总记忆数、当前阈值） | 成员 |
| `/forget <记忆ID>` | 删除指定 L3 记忆 | 成员 |

## 定时调度

| 时间 | 任务 |
|------|------|
| 01:00 | Path B 日压缩：L1 昨日对话 → L2 日摘要 |
| 02:00 | L1 轮次裁剪：超过上限的旧轮次滑出 |
| 03:00 | L3 维护：衰减计算 + 灰区重评 + 低分删除 |
| 04:00 | Path A 周压缩：合并生成渐进周摘要 |
| 周一 05:00 | 周摘要重置 |
| 动态 cron | L3 相似记忆合并 |

## 数据管理

```
data/plugin_data/astrbot_alice_memory_tier/
├── identity_mapping.json     # 跨平台用户身份映射
├── l1/{uid}.json             # L1 原始对话
├── l2/{uid}.json             # L2 日摘要
├── l3/{uid}.json             # L3 记忆 JSON 备份（与 ChromaDB 双写）
├── weekly/{uid}.json         # 周摘要持久化
└── chroma/                   # ChromaDB 向量库（cosine 距离，自动维度检测）
```

### 云服务器迁移

只需复制 `l3/{uid}.json`、`l1/{uid}.json`、`l2/{uid}.json`、`identity_mapping.json` 到新服务器对应目录。ChromaDB 启动时自动重建。

旧版 `alice_memory_modul` 升级：复制旧 `chroma/` 目录，首次 L3 操作自动迁移旧 ChromaDB 内置数据到新外部嵌入模型。

## 开发

- Python 3.10+
- `ruff check --isolated .` / `pytest` — 86 项测试

## 许可证

MIT
