"""数据模型定义"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class AccountStatus(str, Enum):
    OFFLINE = "offline"          # 未登录
    LOGGING_IN = "logging_in"   # 等待扫码
    ONLINE = "online"           # 正常
    SESSION_EXPIRED = "expired" # 会话过期
    BANNED = "banned"           # 被封/限流


class TaskStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"   # AI 正在生成内容
    READY = "ready"             # 内容已生成, 等待发布
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class NoteType(str, Enum):
    NORMAL = "normal"            # 普通笔记
    PRODUCT = "product"          # 带货笔记 (挂商品)


@dataclass
class ProductInfo:
    """商品信息"""
    keyword: str = ""            # 商品搜索关键词 (用于在发布页搜索商品)
    product_id: str = ""         # 商品 ID (如果已知, 可直接定位)
    product_name: str = ""       # 商品名称 (用于校验选对了商品)
    product_url: str = ""        # 商品链接 (备用)


@dataclass
class Account:
    id: Optional[int] = None
    nickname: str = ""              # 账户昵称 (本地标识)
    xhs_user_id: str = ""          # 小红书 user_id (登录后获取)
    xhs_nickname: str = ""         # 小红书昵称 (登录后获取)
    proxy: str = ""                # 绑定的代理地址
    persona: str = ""              # AI 人设提示词
    status: AccountStatus = AccountStatus.OFFLINE
    state_path: str = ""           # 浏览器 storage state 文件路径
    last_health_check: Optional[datetime] = None
    consecutive_failures: int = 0
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class PublishTask:
    id: Optional[int] = None
    account_id: int = 0
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    image_paths: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    error_msg: str = ""
    # 发布前身份校验结果
    verified_user_id: str = ""
    verified_proxy_ip: str = ""
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    # ── 带货相关 ──
    note_type: NoteType = NoteType.NORMAL       # 笔记类型
    products: list[ProductInfo] = field(default_factory=list)  # 挂载的商品列表


@dataclass
class ContentPlan:
    """每个账户的内容计划"""
    account_id: int = 0
    topic: str = ""         # 主题方向, 如 "穿搭分享"
    style: str = ""         # 风格, 如 "轻松日常"
    keywords: list[str] = field(default_factory=list)
    reference: str = ""     # 参考笔记风格描述
    # ── 带货相关 ──
    note_type: NoteType = NoteType.NORMAL
    products: list[ProductInfo] = field(default_factory=list)  # 要推广的商品
