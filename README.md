# AstrBot Alice Memory Plugin v2.3.0

AstrBot 三层记忆插件 — 让 AI 拥有类人记忆：短期对话、中期概括、长期沉淀。

## 概述

- **L1 短期记忆**：最近 N 轮全量注入（按日期分组），磁盘保存 200 轮
- **L2 中期概括**：每日摘要 + 渐进周摘要，双路径自动压缩
- **L3 长期沉淀**：重要记忆向量存储，艾宾浩斯衰减模型自然遗忘
- **静默运行**：压缩后台自动执行，用户无感知
- **前端可调**：37 项配置即插即用，全部有默认值
- **数据安全**：支持完整备份/还原/导出/导入

## 快速开始

```bash
pip install chromadb
```

将插件文件夹复制到 AstrBot 插件目录：
`<ASTRBOT_ROOT>/data/plugins/astrbot_alice_memory_modul/`

重启 AstrBot，插件自动加载。正常对话即可积累记忆。发送 `/compact` 手动压缩，或等待凌晨定时任务。

## 三层记忆架构

| 层级 | 类型 | 生命周期 | 说明 |
|------|------|---------|------|
| **L1** | 原始对话 | 最近 80 轮注入 / 磁盘 200 轮 | 按日期分组全量注入，轮次裁剪 |
| **L2** | 周摘要 + 日摘要 | 周摘要一周 / 日摘要 7 天 TTL | 去重合并注入（周摘要+非本周日摘要） |
| **L3** | 长期向量记忆 | 衰减模型 | 重要性评估 → 向量存储 → 语义检索 → 自然遗忘 |

### 记忆流转

```
用户对话
   ↓
L1 存储（原始对话，200轮）
   ├─→ 全量注入（最近80轮，按日期分组）→ LLM 上下文
   ├─→ Path B 日压缩（凌晨 1:00）→ L2 日摘要（7天TTL）
   ├─→ Path A 上下文压缩（凌晨 4:00）→ 渐进周摘要（周一重置）
   └─→ 重要性分析 → L3 向量存储（衰减+合并+灰区重评）
```

### L3 衰减模型

L3 使用艾宾浩斯遗忘曲线模拟自然遗忘，而非暴力删除。

```
effective_score = importance × 0.995^days + min(access_count, 10) × 0.3
```

| 区间 | 判定 | 动作 |
|------|------|------|
| < 3.0 | 遗忘 | 自动删除 |
| 3.0 – 5.0 | 灰区 | 触发 LLM 重新评估 |
| > 5.0 | 稳固 | 保留 |

被频繁访问的记忆更牢固——每次检索增加 0.3 分（上限 10 次）。

## 上下文注入

每条管线独立标记，互不污染：

| 管线 | 注入位置 | 标记 |
|------|---------|------|
| L1 | `contexts` | system 日期标记 `[YYYY-MM-DD 对话]` |
| L2 | `extra_user_content_parts` | `[L2记忆]`（周摘要+非本周日摘要，去重合并） |
| L3 | `extra_user_content_parts` | `[L3记忆]`（按需语义检索） |

## 配置

在 AstrBot Web 管理界面配置，共 37 项，全部有默认值。

| 分类 | 关键配置项 |
|------|-----------|
| 通用 | `data_dir`、`log_level`、`hook_enabled` |
| L1 | `l1_enabled`、`l1_save_rounds`(200)、`l1_inject_rounds`(80)、`l1_retention_days`(7) |
| L2 | `l2_enabled`、Path A/B 独立开关、`l2_ttl`(7)、`l2_daily_inject_count`(3) |
| L3 | `l3_enabled`、`importance_threshold`(8)、`l3_decay_rate`(0.995)、`l3_delete_threshold`(3.0) |
| LLM | `compress_model`、`importance_analyze_model`、`llm_max_tokens`(1024)、`llm_temperature`(0.7) |
| 注入 | `inject_l1`、`inject_l2_path_a`、`inject_l2_path_b`、`inject_l3` |
| 上下文 | `manage_context`(false) — 插件全权管理上下文，清空 AstrBot 对话历史 |
| 反馈 | `manual_compress_feedback_mode`(llm) + 固定文本/LLM prompt |

所有配置项详见 `_conf_schema.json`。

## 插件命令

| 命令 | 功能 |
|------|------|
| `/compact [日期]` | 手动压缩（无参=周摘要，有日期=日摘要） |
| `/important <内容>` | 分析重要性并存入 L3 |
| `/show_memory <查询>` | 语义搜索 L3 记忆 |
| `/forget <记忆ID>` | 删除指定 L3 记忆 |

### 压缩反馈模式

`manual_compress_feedback_mode` 控制 `/compact` 的响应方式：

| 模式 | 用户看到 | 说明 |
|------|---------|------|
| `silent` | 无任何返回 | 后台静默执行，AI 后续对话仍可见摘要 |
| `fixed` | 固定文本 | 返回预设文本 |
| `llm` | AI 动态回复 | 大模型根据对话氛围生成自然反馈（默认） |
| `visible` | 摘要预览 | 直接显示周摘要正文 |

## 定时调度

| 时间 | 任务 |
|------|------|
| 01:00 | Path B 日压缩：L1 昨日对话 → L2 日摘要 |
| 02:00 | L1 裁剪 + L2 日摘要清理：保留最近 200 轮 L1 + 删除 7 天前 L2 日摘要 |
| 03:00 | L3 维护：衰减计算 + 灰区重评 + 低分删除 |
| 04:00 | Path A 周压缩：合并生成渐进周摘要 |
| 周一 05:00 | 周摘要重置 |
| 每月 1 日 06:00 | L3 月度合并：全量相似记忆扫描与归并 |

## 数据管理

插件数据存储在 `data/plugin_data/astrbot_alice_memory_modul/`：

```
├── identity_map.json     # 跨平台用户身份映射
├── l1/{uid}.json         # L1 原始对话
├── l2/{uid}.json         # L2 日摘要 + 周摘要
├── l3/{uid}.json         # L3 记忆元数据
├── weekly/{uid}.json     # 周摘要持久化
└── chroma/               # ChromaDB 向量库
```

### 跨设备记忆迁移

将记忆从一台设备转移到另一台：

1. **源设备**：关闭 AstrBot → 复制 `data/plugin_data/astrbot_alice_memory_modul/` 整个目录
2. **目标设备**：部署插件后 `首次启动前`，将复制的目录粘贴到相同位置
3. **启动** AstrBot → 所有 L1/L2/L3 记忆、用户身份、向量库自动恢复

> 注意：`data_dir` 默认路径在 `_conf_schema.json` 中定义，若目标设备修改过路径，需保持配置一致。迁移在插件未启动时进行，避免文件被占用导致数据损坏。

## 开发

- Python 3.10+
- `ruff check --isolated .` / `ruff format --isolated .`
- `pytest` — 104 项测试

## 许可证

MIT

## 更新日志

### v2.3.0（2026-05-02）

**清理：**
- 删除 17 个未调用方法（storage 7 个 / analyzer 1 个 / vector_store 3 个 / identity 3 个 / plugin_config 2 个）
- 删除 L1MemoryItem 3 个死字段（`compressed` / `content_type` / `media_url`）
- 删除未接入的 MigrationModule 模块
- 删除对应死方法的测试用例

**新增：**
- Scheduler 02:00 追加 L2 日摘要 7 天自动清理，防止 `l2/{user_id}.json` 无限膨胀

**文档：**
- 同步更新 8 个文档文件，移除已删除 API 和模块引用

### v2.2.1（2026-05-02）

**修复：**
- Compressor `_call_llm` 增加 model 不兼容自动降级：当 `compress_model` 指定的模型与当前 provider 不匹配时（定时任务场景），自动去掉 `model` 参数重试，修复 04:00 Path A 定时任务 `unknown model` 失败
- `_estimate_importance` 使用 `raw=True` 跳过 `_looks_valid` 校验，修复重要性评分（如 `"7"`）因长度不足被误拦截回退到默认分 5

### v2.2.0（2026-05-01）

**新增：**
- L1 保存与注入轮数解耦：`l1_save_rounds=200`（磁盘）+ `l1_inject_rounds=80`（注入），均前端可调
- L1 全量分组注入：按日期分组，每天插入 system 日期标记，替代旧的滑动窗口
- L2 去重合并注入：周摘要 + 非本周日摘要合并为单条 `[L2记忆]`，消除冗余
- 注入管线从 4 条简化为 3 条（L1/L2 合并/L3）
- `l1_retention_days` 默认值 3→7，`l2_daily_inject_count` 默认值 3→7

**重构：**
- `ContextInjector`：`inject_l2_path_a` + `inject_l2_path_b` → `inject_l2_merged`
- `MemoryStorage`：新增 `get_recent_rounds()` + `trim_to_recent_rounds()`
- `Scheduler`：L1 清理从"按天删除"改为"保留最近 N 轮"

**测试：**
- 重写注入器测试，新增 L2 合并/日期排除/隐藏项测试，总测试数 104

### v2.1.3（2026-05-01）

**新增：**
- `manage_context` 配置项：开启后插件清空 AstrBot 对话历史，由 L1/L2/L3 三层记忆全权管理上下文，可将 prompt_tokens 从 ~850K 降至 ~3-5K
- 默认关闭，需在 WebUI 手动开启

**测试：**
- 新增 7 项 manage_context 测试（配置字段 + 行为验证），总测试数 103

### v2.1.2（2026-05-01）

**修复：**
- Compressor/Analyzer `_call_llm` 增加默认 provider fallback（`get_using_provider()`），修复定时任务场景因缺少 `chat_provider_id` 导致 LLM 调用失败
- Path A 周摘要 `compress_context_summary` 按本周过滤日摘要，防止上周残留数据污染新周摘要
- Scheduler `_compress_context` 移除冗余 `set_weekly_summary` 调用（Compressor 内部已正确写入）
- Scheduler `_safe_wrap` 改为 async wrapper，确保 CronJobManager 正确 await 协程
- 6 个定时任务方法增加入口 INFO 日志，便于运维排查

### v2.1.1（2026-04-30）

**修复：**
- 定时任务注册从同步 `__init__` 移到 `async initialize()` 生命周期钩子，修复 `add_basic_job`（async 方法）因缺少 `await` 导致 6 个定时任务从未实际注册到 APScheduler 的问题
- `Scheduler.start()` 改为 `async def`，`add_basic_job` 调用加 `await`
- 云端部署验证：6 个定时任务已成功注册，`next_run_time` 排队正确

### v2.1.0（2026-04-29）

**新增：**
- L3 月度合并任务：每月 1 日 06:00 自动执行全量相似记忆扫描与归并，消除冗余 L3 记忆
- 调度器任务数从 5 增至 6 段编排

**修复：**
- `context_injector._is_monday()` 从 UTC 改为 CST，修复周一 00:00-07:59 间周摘要注入异常
- `compressor.compress_context_summary()` 日期计算从 UTC 改为 CST，修复 04:00 周压缩日期偏移
- `compressor._get_dialogues()` 时间边界从 UTC 改为 CST，修复日对话过滤偏移 8 小时

**清理：**
- 前端配置移除 6 个冗余字段（`log_level`、`store_media_content`、`l2_summary_hidden`、`l2_enabled`、`compact_progress_feedback`、`l3_embedding_provider`）
- `l3_merge_similarity` 描述修正为"L3 注入检索相似度过滤阈值"

### v2.0.1（2026-04-29）

**修复：**
- L2 压缩现在不依赖前端模板是否包含 `{content}` 占位符，即使模板截断也能正常生成摘要
- 调度器时区从 UTC 修正为 Asia/Shanghai，解决定时压缩日期偏差问题
- `_conf_schema.json` 中 Path A/B 模板默认值与代码对齐
- 内容校验机制 `_looks_valid()` 防止 LLM 返回无效内容被存入磁盘
- 重要性评分不再被内容校验误拦截

**改进：**
- 压缩任务执行时输出 INFO 日志，方便运维确认
- 新增跨设备记忆迁移指南
- 依赖声明文件 requirements.txt
