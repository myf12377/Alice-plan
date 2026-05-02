"""
PluginConfig — Alice 记忆插件配置模型。

所有字段均有默认值，即插即用。框架传入的 AstrBotConfig(dict)
通过 from_framework_config() 转换为 PluginConfig。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class PluginConfig(BaseModel):
    """Alice 三层记忆系统配置。

    39 字段，扁平结构，Pydantic 校验。
    """

    # ==========================================================================
    # 通用配置
    # ==========================================================================

    data_dir: Path = Field(
        default=Path("data/plugin_data/astrbot_alice_memory_modul"),
        description="插件数据存储根目录",
    )
    log_level: str = Field(
        default="INFO",
        description="日志级别：DEBUG / INFO / WARNING / ERROR",
    )
    hook_enabled: bool = Field(
        default=True,
        description="总钩子开关，关闭后不注入任何记忆也不存储对话",
    )

    # ==========================================================================
    # L1 — 日内短期记忆
    # ==========================================================================

    l1_enabled: bool = Field(default=True, description="L1 存储开关")
    l1_retention_days: int = Field(
        default=7,
        ge=1,
        le=30,
        description="L1 磁盘保留天数，为 Path B 提供原料窗口",
    )
    l1_save_rounds: int = Field(
        default=200,
        ge=50,
        le=500,
        description="L1 磁盘保存轮数（1轮=user+assistant），压缩任务素材池",
    )
    l1_inject_rounds: int = Field(
        default=80,
        ge=0,
        le=200,
        description="L1 注入轮数。0=不注入 L1，仅用 L2+L3",
    )
    l1_search_limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="注入上下文的 L1 对话最大条数（旧字段，保留兼容）",
    )
    store_media_content: bool = Field(
        default=True,
        description="存储媒体内容描述",
    )

    # ==========================================================================
    # L2 Path A — 渐进周摘要（上下文记忆）
    # ==========================================================================

    l2_path_a_enabled: bool = Field(default=True, description="Path A 开关")
    l2_compress_prompt_a: str = Field(
        default=(
            "你是一个记忆合并助手。以下包含：\n"
            "1) 已有的本周摘要（可能为空）\n"
            "2) 今日的对话记录\n"
            "3) 近几日的每日摘要\n\n"
            "请将以上内容融合为一份本周的最新摘要。要求：\n"
            "- 保留所有重要的事件、决定、偏好信息\n"
            "- 去除重复内容\n"
            "- 对本周整体情况给出概括描述\n"
            "- 保持客观、第三人称叙述\n\n"
            "已有周摘要：\n{weekly_summary}\n\n"
            "今日对话：\n{today_dialogues}\n\n"
            "近日摘要：\n{daily_summaries}\n\n"
            "请输出合并后的完整周摘要："
        ),
        description="Path A 压缩 prompt 模板（合并摘要模式）",
    )

    # ==========================================================================
    # L2 Path B — 每日磁盘摘要（历史记忆）
    # ==========================================================================

    l2_path_b_enabled: bool = Field(default=True, description="Path B 开关")
    l2_compress_prompt_b: str = Field(
        default=(
            "你是一个对话摘要助手。请将以下昨日对话提炼为一份日摘要。\n\n"
            "要求：\n"
            "- 提取关键事件和重要信息\n"
            "- 保留用户的偏好、习惯、情感表达方式\n"
            "- 保留助手给出的重要建议或结论\n"
            "- 用简洁的第三人称叙述\n"
            "- 包含用户情绪基调（如：开心/焦虑/平静）\n\n"
            "对话内容：\n{content}\n\n"
            "日摘要："
        ),
        description="Path B 压缩 prompt 模板（提取日摘要模式）",
    )
    l2_ttl: int = Field(
        default=7,
        ge=1,
        le=90,
        description="L2 日摘要保留天数",
    )
    l2_daily_inject_count: int = Field(
        default=7,
        ge=0,
        le=14,
        description="注入上下文的日摘要天数（最近 N 天）",
    )
    l2_summary_hidden: bool = Field(
        default=False,
        description="摘要默认隐藏（不注入前端对话）",
    )

    # ==========================================================================
    # L2 通用
    # ==========================================================================

    l2_enabled: bool = Field(default=True, description="L2 记忆总开关")
    compact_progress_feedback: bool = Field(
        default=True,
        description="压缩时显示进度提示",
    )
    manual_compress_feedback_mode: str = Field(
        default="llm",
        description="手动压缩反馈模式：silent / fixed / llm / visible",
    )
    manual_compress_feedback_text: str = Field(
        default="今日对话已存档 ✨",
        description="固定文本反馈内容",
    )
    manual_compress_llm_prompt: str = Field(
        default=(
            "今日对话已完成存档。请根据今日对话的情绪氛围，以自然、人类化的方式告知用户。"
            "不要透露任何具体内容，如同朋友间简单告知'今天的对话已经记下了'。"
            "语气要贴合今日对话的整体氛围。"
        ),
        description="LLM 动态反馈 prompt 模板",
    )

    # ==========================================================================
    # L3 — 长期向量记忆（衰减模型）
    # ==========================================================================

    l3_enabled: bool = Field(default=True, description="L3 向量记忆开关")
    l3_embedding_provider: str = Field(
        default="auto",
        description="向量嵌入模型：auto（AstrBot EmbeddingProvider）/ chroma（内置）",
    )
    importance_threshold: int = Field(
        default=8,
        ge=0,
        le=10,
        description="重要性阈值，≥此值晋升 L3",
    )
    l3_merge_similarity: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="向量相似度合并阈值",
    )
    l3_merge_interval_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="全量合并周期（天）",
    )
    l3_decay_rate: float = Field(
        default=0.995,
        ge=0.9,
        le=1.0,
        description="每日衰减系数",
    )
    l3_access_bonus: float = Field(
        default=0.3,
        ge=0.0,
        le=5.0,
        description="每次访问的生命加成",
    )
    l3_delete_threshold: float = Field(
        default=3.0,
        ge=0.0,
        le=10.0,
        description="有效分数低于此值删除",
    )
    l3_gray_zone_upper: float = Field(
        default=5.0,
        ge=3.0,
        le=10.0,
        description="灰区上界，灰区内触发 LLM 重评",
    )

    # ==========================================================================
    # LLM 模型配置
    # ==========================================================================

    compress_model: str = Field(
        default="",
        description="压缩用的 LLM 模型，为空使用 AstrBot 默认模型",
    )
    importance_analyze_model: str = Field(
        default="",
        description="重要性分析用的 LLM 模型，为空使用默认模型",
    )
    llm_max_tokens: int = Field(
        default=1024,
        ge=64,
        le=32768,
        description="LLM 最大 Token 数",
    )
    llm_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="LLM 温度参数",
    )

    # ==========================================================================
    # 上下文注入开关（管线级）
    # ==========================================================================

    inject_l1: bool = Field(default=True, description="L1 上下文注入开关")
    inject_l2_path_a: bool = Field(default=True, description="Path A 周摘要注入开关")
    inject_l2_path_b: bool = Field(default=True, description="Path B 日摘要注入开关")
    inject_l3: bool = Field(default=True, description="L3 记忆注入开关")
    manage_context: bool = Field(
        default=False,
        description="由插件全权管理上下文，清空 AstrBot 对话历史",
    )

    # ==========================================================================
    # 工厂方法
    # ==========================================================================

    @classmethod
    def from_framework_config(cls, raw: dict[str, Any]) -> "PluginConfig":
        """从 AstrBot 框架传入的 AstrBotConfig(dict) 构造 PluginConfig。

        自动过滤无效 key，缺失 key 使用默认值。
        """
        valid_keys = set(cls.model_fields.keys())
        filtered = {k: v for k, v in raw.items() if k in valid_keys}
        return cls(**filtered)

    def model_post_init(self, __context: Any) -> None:
        """Pydantic 初始化后钩子：确保数据目录存在 + 约束校验。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.l1_inject_rounds > self.l1_save_rounds:
            self.l1_inject_rounds = self.l1_save_rounds
