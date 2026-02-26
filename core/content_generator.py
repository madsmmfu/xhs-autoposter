"""AI 内容生成 — 根据账户人设生成小红书风格笔记 (支持带货笔记)"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

from anthropic import AsyncAnthropic
from rich.console import Console

from models.schemas import Account, ContentPlan, NoteType, ProductInfo, PublishTask, TaskStatus

console = Console()

SYSTEM_PROMPT = """你是一个小红书内容创作专家。你需要根据给定的人设和主题，生成适合小红书平台的笔记内容。

要求:
1. 标题: 20字以内, 吸引眼球, 可以用 emoji
2. 正文: 300-800字, 口语化, 分段清晰, 有个人感受
3. 标签: 5-8个相关话题标签 (不带#号)
4. 风格: 真实感强, 像真人分享, 避免营销感
5. 适当使用 emoji 但不要过多

输出格式 (严格 JSON):
{
    "title": "标题文字",
    "content": "正文内容...",
    "tags": ["标签1", "标签2", "标签3"]
}

只输出 JSON, 不要有其他文字。"""

SYSTEM_PROMPT_PRODUCT = """你是一个小红书带货笔记创作专家。你需要根据给定的人设、商品信息和主题，生成适合小红书平台的带货种草笔记。

要求:
1. 标题: 20字以内, 吸引眼球, 可以用 emoji, 突出产品亮点或使用体验
2. 正文: 400-1000字, 口语化, 分段清晰
   - 必须自然地融入商品推荐, 不能硬广
   - 以个人真实使用体验/场景切入
   - 突出产品解决了什么问题 / 带来了什么体验
   - 可以提及价格、优惠信息 (如果有)
   - 结尾自然引导 "点击购物车" / "链接在商品栏"
3. 标签: 5-8个相关话题标签 (不带#号), 包含商品相关标签
4. 风格: 种草感强但不假, 像闺蜜推荐, 有真实使用感受
5. 适当使用 emoji 但不要过多

输出格式 (严格 JSON):
{
    "title": "标题文字",
    "content": "正文内容...",
    "tags": ["标签1", "标签2", "标签3"]
}

只输出 JSON, 不要有其他文字。"""


class ContentGenerator:
    def __init__(self, base_url: str, api_key: str, model: str):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncAnthropic(**kwargs)
        self.model = model

    async def generate(
        self,
        account: Account,
        plan: ContentPlan,
        extra_instruction: str = "",
    ) -> Optional[PublishTask]:
        """根据账户人设和内容计划生成一篇笔记 (支持带货笔记)"""

        is_product = plan.note_type == NoteType.PRODUCT and plan.products
        system_prompt = SYSTEM_PROMPT_PRODUCT if is_product else SYSTEM_PROMPT
        user_prompt = self._build_prompt(account, plan, extra_instruction)

        try:
            response = await self.client.messages.create(
                model=self.model,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.85,
                max_tokens=2000,
            )

            raw = response.content[0].text.strip()
            parsed = self._parse_response(raw)

            if not parsed:
                console.print(f"[red]内容解析失败, 原始返回:\n{raw[:200]}")
                return None

            task = PublishTask(
                account_id=account.id,
                title=parsed["title"],
                content=parsed["content"],
                tags=parsed["tags"],
                status=TaskStatus.READY,
                note_type=plan.note_type,
                products=list(plan.products),
            )

            label = "带货笔记" if is_product else "普通笔记"
            console.print(
                f"[green]{label}已生成 ({account.nickname}): {task.title}"
            )
            return task

        except Exception as e:
            console.print(f"[red]AI 生成失败: {e}")
            return None

    async def generate_batch(
        self,
        account: Account,
        plan: ContentPlan,
        count: int = 3,
    ) -> list[PublishTask]:
        """批量生成多篇不同的笔记"""
        tasks = []
        for i in range(count):
            extra = f"这是第 {i+1}/{count} 篇, 请确保和前面的内容不重复, 换一个角度写。"
            task = await self.generate(account, plan, extra)
            if task:
                tasks.append(task)
            await asyncio.sleep(1)  # 避免 API 限流
        return tasks

    def _build_prompt(
        self,
        account: Account,
        plan: ContentPlan,
        extra: str = "",
    ) -> str:
        parts = []

        if account.persona:
            parts.append(f"【人设】\n{account.persona}")

        parts.append(f"【主题方向】\n{plan.topic}")

        if plan.style:
            parts.append(f"【风格要求】\n{plan.style}")

        if plan.keywords:
            parts.append(f"【关键词】\n{', '.join(plan.keywords)}")

        if plan.reference:
            parts.append(f"【参考风格】\n{plan.reference}")

        # 带货笔记: 添加商品信息
        if plan.note_type == NoteType.PRODUCT and plan.products:
            product_lines = []
            for i, p in enumerate(plan.products, 1):
                info = f"商品{i}: {p.product_name or p.keyword}"
                if p.product_url:
                    info += f" (链接: {p.product_url})"
                product_lines.append(info)
            parts.append(f"【推广商品】\n" + "\n".join(product_lines))
            parts.append(
                "【带货要求】\n"
                "这是一篇带货种草笔记, 商品会挂在笔记的商品栏。请注意:\n"
                "1. 内容要自然融入商品推荐, 以个人体验/场景切入\n"
                "2. 不要直接贴商品链接, 商品会自动挂载\n"
                "3. 可以在结尾自然引导 '看我挂的链接' / '商品在购物车里'\n"
                "4. 标签要包含商品相关关键词"
            )

        if extra:
            parts.append(f"【额外要求】\n{extra}")

        return "\n\n".join(parts)

    def _parse_response(self, raw: str) -> Optional[dict]:
        """从 AI 返回中提取 JSON"""
        # 尝试直接解析
        try:
            data = json.loads(raw)
            if self._validate_content(data):
                return data
        except json.JSONDecodeError:
            pass

        # 尝试提取 ```json ... ``` 块
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                if self._validate_content(data):
                    return data
            except json.JSONDecodeError:
                pass

        # 尝试提取第一个 { ... }
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                if self._validate_content(data):
                    return data
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _validate_content(data: dict) -> bool:
        """校验生成的内容格式"""
        if not isinstance(data, dict):
            return False
        if not data.get("title") or not data.get("content"):
            return False
        if not isinstance(data.get("tags", []), list):
            return False
        return True
