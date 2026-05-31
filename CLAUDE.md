# AstrBot Alice Memory Plugin

`astrbot_alice_memory_tier` — 三层记忆存储系统（L1原始对话 / L2双路中期记忆 / L3长期向量记忆）。

> **v3.1.1** — 修复 Scheduler.start() 同步调用导致的冷启动崩溃；包含 v3.1.0 全部功能：L1 轮次滑窗 + L2 双路径压缩 + L3 向量记忆完整链路（外部嵌入/自校准/双写/多候选项合并 + ChromaDB 恢复增强）。主动层对接接口就绪（6 property + 4 get_*_context）。95 项测试通过。

## AI 行为规则

- **听从用户指挥**：每一步听用户指令，仅完成用户当前要求的工作，不做额外操作，不过度设计。
- **Plan 阶段展示完整 Plan**：进入计划模式时，将完整方案写入 plan 文件供用户审阅。
- **每次对话汇报上下文占用**：在对话开始时主动报告当前上下文使用量（token 数或百分比），帮助用户判断是否需要压缩。

## 信息归属规则

- **why**（概念解释/设计理由）→ plan 文件
- **what**（API 定义/边界）→ 模块 CLAUDE.md
- **how**（依赖顺序/调度路径）→ 本文件
- 互不复制，引用不重述

## 测试环境

| 项目 | 路径 |
|------|------|
| 插件源码 | `C:\Users\lenovo\Projects\Masterpiece\astrbot_alice_memory_tier\` |
| AstrBot 源码 | `C:\Users\lenovo\Projects\test\astrbot\` |
| 插件部署位置 | `test/astrbot/data/plugins/astrbot_alice_memory_tier/` |
| 插件数据目录 | `test/astrbot/data/plugin_data/astrbot_alice_memory_tier/` |

**部署工作流**：源码目录编辑 → Git commit → 复制到部署位置 → AstrBot 集成测试。只改源码，不改副本。

## 项目结构

```
astrbot_alice_memory_tier/
├── main.py                        # ✅ Star 子类主入口（第5层）— C2 完成（4命令+silent反馈）
├── _conf_schema.json              # ✅ 31键框架配置 schema
├── metadata.yaml                  # ✅ v2.0.1
├── memory/
│   ├── plugin_config.py           # ✅ PluginConfig 31字段 Pydantic 模型（第0层）
│   ├── context_injector.py        # ✅ 上下文注入（第3层）— B2 完成
│   ├── identity/                  # 跨平台身份 [稳定]
│   ├── storage/                   # ✅ JSON 持久化（第1层）— A1 完成
│   ├── vector_store/              # ✅ ChromaDB 向量（第1层）— A2 完成
│   ├── analyzer/                  # ✅ LLM 重要性分析（第1层）— A3 完成
│   ├── compressor/                # ✅ Path A/B 压缩（第2层）— B1 完成
│   ├── scheduler/                 # ✅ 6段定时调度（第4层）— C1 完成
│   └── migration/                 # 导入导出 [稳定]
```

## 依赖拓扑

```
PluginConfig (0) → Identity(1) / Storage(1) / VectorStore(1) / Analyzer(1)
                                     │
                              Compressor(2) / Migration(2)
                                     │
                              ContextInjector(3)
                                     │
                               Scheduler(4)
                                     │
                       EmbeddingResolver(Main内部)  ← P10 新增：延迟解析
                                     │
                                Main(5)
```

**关键约束**：Injector 不依赖 Compressor（注入只读，压缩只写），Scheduler 是唯一的编排者，Main 是唯一的框架接触点。
- EmbeddingResolver 延迟到首次 L3 操作时才解析 Provider（P10）
- L3 双写：vector_store 写入 ChromaDB 时同步写 l3/{uid}.json（P11）
- ChromaDB 强制 cosine 距离，自动检测嵌入维度变化并重建（P14+P15）

## 调度索引

| 场景 | 入口 | 调用链 |
|------|------|--------|
| 用户消息注入 | Main.on_llm_request | Identity→Storage(写L1)→Injector(读全部→注入req) |
| 判断晋升L3 | Main.on_llm_request | Analyzer.analyze→VectorStore.add→(_ensure_migrated延迟迁移)→find_similar→merge |
| 01:00 Path B | Scheduler | Storage(L1)→Compressor(LLM)→Storage(写L2) |
| 02:00 L1清理 | Scheduler | Storage.trim_to_recent_rounds |
| 03:00 L3衰减 | Scheduler | VectorStore.apply_decay→get_gray→Analyzer.batch_recheck |
| 04:00 Path A | Scheduler | Storage(L1+L2+周)→Compressor(LLM)→Storage(覆写周) |
| 周一05:00 | Scheduler | Storage.clear_weekly_summary |
| 动态cron L3合并 | Scheduler | VectorStore.merge_similar_for_user(P20) |
| /l3_merge | Main命令 | VectorStore.merge_similar_for_user(user_id) — 仅处理当前用户 |
| /l3_stats | Main命令 | VectorStore.get_effective_threshold + get_user_memories |
| /compact | Main命令 | Compressor→Storage |
| /show_memory | Main命令 | VectorStore.search（含相似度阈值过滤 P13） |
| /forget | Main命令 | VectorStore.delete_memory（前缀匹配 P12）+ Storage.delete_l3_memory |
| 导出/导入 | Main命令 | MigrationModule |

上下文字段注入位置：
- L1 → `request.contexts`（无标记，自然消失）
- L2 Path A → `extra_user_content_parts`（标记 `[周摘要]`，覆盖式）
- L2 Path B → `extra_user_content_parts`（标记 `[L2记忆]`，覆盖式）
- L3 → `extra_user_content_parts`（标记 `[L3记忆]`，覆盖式）

## 主动层对接

记忆层是"记忆仓库"，主动层是"对话编排者"。通过 `context.get_all_stars()` 获取实例，调用公开 API：

```python
for star in context.get_all_stars():
    if star.name == "astrbot_alice_memory_tier":
        plugin = star.star_cls

# 6 个 @property（只读访问）
plugin.storage       # L1/L2/L3 JSON 存储读写
plugin.vector_store  # L3 向量检索
plugin.identity      # 跨平台用户身份映射
plugin.injector      # 记忆上下文注入器
plugin.compressor    # L2 压缩器
plugin.analyzer      # LLM 重要性分析

# 4 个纯读取方法（不影响注入管线）
l1 = plugin.injector.get_l1_context(uid)
l2a = plugin.injector.get_l2_path_a_context(uid)
l2b = plugin.injector.get_l2_path_b_context(uid)
l3 = await plugin.injector.get_l3_context(uid, query)

# 写入
plugin.storage.append_dialogue(uid, "user", content)
results = await plugin.vector_store.search(uid, query)
```

切换方式：WebUI `hook_enabled=false` → 记忆层静默钩子，主动层全权接管。

## 钩子系统

### 导入路径

```python
from astrbot.api.star import Star, Context
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import PermissionType, permission_type
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api import logger
```

### Main 构造模板

```python
class AliceMemoryPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)  # 必须！否则 self.context 为空
        self.plugin_config = PluginConfig.from_framework_config(config or {})

        # 按拓扑顺序初始化（Layer 0 → 5）
        self._identity = IdentityModule(self.plugin_config.data_dir)
        self._storage = MemoryStorage(self.plugin_config)
        # EmbeddingResolver 延迟解析 — 不在 __init__ 中立即获取 Provider
        resolver = EmbeddingResolver(context, self.plugin_config.l3_embedding_provider)
        self._vector_store = VectorStore(self.plugin_config.data_dir,
                                         self.plugin_config, embedding_func=resolver)
        self._analyzer = ImportanceAnalyzer(context, self.plugin_config)
        self._compressor = DialogueCompressor(context, self._storage, self.plugin_config)
        self._injector = ContextInjector(self._storage, self._vector_store,
                                          self._identity, self.plugin_config)
        self._scheduler = Scheduler(context, self._storage, self._identity,
                                     self._vector_store, self.plugin_config,
                                     self._compressor, self._analyzer)
        self._scheduler.start()
```

### 钩子注册

```python
# --- on_llm_request：存储 + 注入（一个钩子完成）---
@filter.on_llm_request()
async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
    """签名必须恰好 (self, event, req)。"""
    try:
        if not self.plugin_config.hook_enabled:
            return
        # 1. 身份解析 → 2. 存储 L1 → 3. 注入全部管线
    except Exception:
        logger.error("[AliceMemory] on_llm_request 异常", exc_info=True)

# --- on_llm_response：存储助手回复 ---
@filter.on_llm_response()
async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
    """签名必须恰好 (self, event, resp)。"""
    try:
        # 存储助手回复到 L1
    except Exception:
        logger.error("[AliceMemory] on_llm_response 异常", exc_info=True)
```

### req 对象操作

```python
# L1 对话 → contexts（list[dict]，框架原生格式）
req.contexts.append({"role": "user", "content": "..."})
req.contexts.append({"role": "assistant", "content": "..."})

# L2/L3 标记内容 → extra_user_content_parts（list[dict] 或 list[ContentPart]）
# 框架 docstring 写明支持 dict 和 ContentPart 两种格式
req.extra_user_content_parts.append({
    "type": "text",
    "text": "[L2记忆]\n摘要内容..."
})
```

### 命令注册

```python
@filter.command_group("memory")
def memory_commands(self):
    pass

@memory_commands.command("compact")
@permission_type(PermissionType.ADMIN)
async def cmd_compact(self, event: AstrMessageEvent, date: str = None):
    """命令处理器必须是 AsyncGenerator。"""
    # ... 压缩逻辑 ...
    yield event.plain_result("压缩完成")

@memory_commands.command("show_memory")
async def cmd_show(self, event: AstrMessageEvent, query: str):
    results = self._vector_store.search(user_id, query)
    yield event.plain_result(format_results(results))
```

### 常见陷阱（来自旧版 _legacy 的教训）

| 陷阱 | 错误 | 正确 |
|------|------|------|
| **未调 super().__init__** | `self.context = context` 手动赋值 | `super().__init__(context)` |
| **忽略框架 config** | `self.config = PluginConfig()` 始终默认值 | `PluginConfig.from_framework_config(config or {})` |
| **钩子签名不匹配** | `def handler(self, event)` 少参数 | `(self, event: AstrMessageEvent, req: ProviderRequest)` |
| **钩子内无异常处理** | 异常直接抛出，LLM 请求失败 | 顶层 `try/except` + `logger.error(exc_info=True)` |
| **分两个钩子处理存储和注入** | `on_message` + `on_llm_request` 重复解析身份 | 在 `on_llm_request` 一个钩子内完成 |
| **AstrBotConfig 当 dict 用** | `config.get("key")` | 用 `PluginConfig.from_framework_config(raw)` 转换 |
| **命令处理器用 return** | `return event.plain_result(...)` | `yield event.plain_result(...)` |

### 注入管线开关

每条管线由 config 独立控制，ContextInjector 内部判断：

```python
# Main.on_llm_request 中：
user_id = self._identity.get_user_id(platform, platform_user_id)
if not user_id:
    return

# 存储（不受注入开关影响）
self._storage.append_dialogue(user_id, role, content)

# 注入（按管线开关独立控制）
if self.plugin_config.inject_l1:
    await self._injector.inject_l1(user_id, req)
if self.plugin_config.inject_l2_path_b:
    await self._injector.inject_l2_path_b(user_id, req)
if self.plugin_config.inject_l2_path_a:
    await self._injector.inject_l2_path_a(user_id, req)
if self.plugin_config.inject_l3:
    await self._injector.inject_l3(user_id, req)
```

## 调试日志

### Logger 导入

```python
from astrbot.api import logger
# 返回标准 Python logging.Logger 实例，经 loguru 拦截
```

### 日志格式约定

```python
logger.info(f"[AliceMemory] 阶段 | 键=值 | 键=值")
logger.error(f"[AliceMemory] 阶段 | 异常描述 | {e}", exc_info=True)
logger.debug(f"[AliceMemory] 阶段 | 详细信息...")
```

### 关键埋点

| 位置 | 级别 | 内容 |
|------|------|------|
| `__init__` 完成 | INFO | `插件初始化 | fields=N | data_dir=...` |
| 各 Layer 模块就绪 | INFO | `模块就绪 | Storage ✓ | VectorStore ✓ | ...` |
| Scheduler 启动 | INFO | `定时任务注册 | tasks=6` |
| on_llm_request 入口 | INFO | `on_llm_request | uid=xxx(8) | msg_len=N` |
| 每条管线注入 | DEBUG | `注入 L1: N条 → contexts` / `注入 L2 Path B: 最近N天` |
| 注入完成 | INFO | `注入完成 | contexts+N | extra_parts+N` |
| on_llm_response | DEBUG | `助手回复存储 | uid=xxx(8) | len=N` |
| L3 晋升判断 | DEBUG | `重要性评分 | score=N | threshold=N | promote=True/False` |
| 钩子异常 | ERROR | `钩子异常 | on_llm_request | {e}` + exc_info=True |
| Scheduler 任务执行 | INFO | `定时任务 | 01:00 Path B | uid=N users` |
| Scheduler 任务异常 | ERROR | `定时任务失败 | Path B | {e}` + exc_info=True |

## 配置速查

完整字段定义见 `memory/plugin_config.py` 的 `PluginConfig` 类（31字段，Pydantic BaseModel）。
框架配置见 `_conf_schema.json`。
工厂方法：`PluginConfig.defaults()` / `PluginConfig.from_framework_config(dict)`。

## 插件命令

| 命令 | 功能 |
|------|------|
| `/compact [日期]` | 手动压缩（无参=Path A 周摘要，指定日期=Path B 日摘要） |
| `/important [消息ID]` | 标记重要记忆 → L3 |
| `/show_memory [查询]` | 搜索 L3 记忆 |
| `/l3_merge` | 手动合并当前用户 L3 相似记忆 |
| `/l3_stats` | 查看 L3 状态（总记忆数、当前阈值） |
| `/forget [记忆ID]` | 删除指定记忆 |

## 合并迭代流程

每次迭代的标准操作序列（9 次迭代详见 plan 文件）：

```
1. 改模块代码（构造函数 MemorySettings → PluginConfig + 新增 API）
2. 改对应 test_*.py（fixture 改用 PluginConfig + 补新方法测试）
3. pytest tests/ -v                    ← 全量测试，非单模块
4. 改 main.py（接入新模块或新方法）
5. python -c "from main import AliceMemoryPlugin"  ← 导入测试
6. ruff check --isolated .
7. git commit（该模块作为独立提交，格式: "A1: MemoryStorage 迁移 PluginConfig + 新增 8 API"）
8. [A1 起] cp 到 /test/astrbot 做实机验证
```

关键规则：
- **A0 不导入子模块** — Main 骨架仅 Star 子类 + 空钩子，避免间接触发 MemorySettings
- **每次全量测试** — 改 Storage 可能影响 Compressor 测试
- **最危险的两次迭代**：A1（第一个改旧代码，模式确立）和 B2（运行时 bug 修复）

## 开发命令

```bash
ruff format --isolated .   # 格式化
ruff check --isolated .    # 代码检查
mypy --strict .            # 类型检查
pytest                     # 运行测试
```
