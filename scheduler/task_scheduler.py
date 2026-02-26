"""任务调度 — 定时生成内容、保活检查、发布笔记"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Optional

from rich.console import Console

from core.account_manager import AccountManager
from core.browser_pool import BrowserPool
from core.content_generator import ContentGenerator
from core.publisher import Publisher
from models.schemas import Account, AccountStatus, ContentPlan, PublishTask, TaskStatus
from storage.database import Database

console = Console()


class TaskScheduler:
    def __init__(
        self,
        db: Database,
        account_manager: AccountManager,
        content_generator: ContentGenerator,
        publisher: Publisher,
        browser_pool: BrowserPool,
        config: dict,
    ):
        self.db = db
        self.account_manager = account_manager
        self.generator = content_generator
        self.publisher = publisher
        self.browser_pool = browser_pool
        self.config = config
        self._running = False

    async def start(self):
        """启动调度循环"""
        self._running = True
        console.print("[bold green]调度器已启动")

        # 并行运行三个循环
        await asyncio.gather(
            self._health_check_loop(),
            self._publish_loop(),
            self._save_state_loop(),
        )

    async def stop(self):
        self._running = False

    async def _health_check_loop(self):
        """定期检查会话健康"""
        interval = self.config.get("session", {}).get("health_check_interval", 15) * 60

        while self._running:
            try:
                console.print("[dim]执行会话健康检查...")
                results = await self.account_manager.check_all_sessions()
                for aid, ok in results.items():
                    account = self.db.get_account(aid)
                    if account:
                        status = "在线" if ok else "异常"
                        console.print(f"  {account.nickname}: {status}")
            except Exception as e:
                console.print(f"[red]健康检查异常: {e}")

            await asyncio.sleep(interval)

    async def _publish_loop(self):
        """检查待发布任务并执行"""
        while self._running:
            try:
                now = datetime.now()
                active_hours = self.config.get("schedule", {}).get("active_hours", [8, 23])
                max_per_day = self.config.get("schedule", {}).get("max_posts_per_day", 3)

                # 只在活跃时段发布
                if not (active_hours[0] <= now.hour < active_hours[1]):
                    await asyncio.sleep(300)
                    continue

                accounts = self.db.get_online_accounts()
                for account in accounts:
                    # 检查今日发布量
                    today_count = self.db.get_today_published_count(account.id)
                    if today_count >= max_per_day:
                        continue

                    # 获取待发布任务
                    tasks = self.db.get_pending_tasks(account.id)
                    ready_tasks = [t for t in tasks if t.status == TaskStatus.READY]

                    if ready_tasks:
                        task = ready_tasks[0]
                        console.print(
                            f"\n[bold]准备发布: {account.nickname} -> {task.title}"
                        )
                        await self.publisher.publish(account, task)

                        # 随机间隔, 模拟人类行为
                        min_interval = self.config.get("schedule", {}).get(
                            "min_interval_minutes", 60
                        )
                        wait_time = random.randint(min_interval * 60, min_interval * 90)
                        console.print(f"[dim]下次发布等待 {wait_time // 60} 分钟")
                        await asyncio.sleep(wait_time)

            except Exception as e:
                console.print(f"[red]发布循环异常: {e}")

            await asyncio.sleep(60)

    async def _save_state_loop(self):
        """定期保存所有会话状态"""
        while self._running:
            await asyncio.sleep(300)  # 每 5 分钟
            try:
                await self.browser_pool.save_all_states()
            except Exception as e:
                console.print(f"[red]保存状态异常: {e}")

    async def generate_and_queue(
        self,
        account: Account,
        plan: ContentPlan,
        count: int = 1,
    ):
        """生成内容并加入发布队列"""
        tasks = await self.generator.generate_batch(account, plan, count)
        for task in tasks:
            task_id = self.db.add_task(task)
            console.print(
                f"[green]内容已入队: ID={task_id} | {account.nickname} | {task.title}"
            )
