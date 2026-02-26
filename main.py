#!/usr/bin/env python3
"""小红书多账户自动发布系统 — 主入口"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, IntPrompt

from core.account_manager import AccountManager
from core.browser_pool import BrowserPool
from core.content_generator import ContentGenerator
from core.proxy_manager import ProxyManager
from core.publisher import Publisher
from models.schemas import Account, AccountStatus, ContentPlan, NoteType, ProductInfo
from scheduler.task_scheduler import TaskScheduler
from storage.database import Database

console = Console()


def load_config() -> dict:
    config_path = Path("config/settings.yaml")
    if not config_path.exists():
        console.print("[red]请先复制 config/settings.example.yaml 为 config/settings.yaml 并填写配置")
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


class App:
    def __init__(self):
        self.config = load_config()
        self.db = Database(self.config.get("database", {}).get("path", "./data/xhs.db"))
        self.proxy_manager = ProxyManager(self.config.get("proxies", []))
        self.browser_pool = BrowserPool(
            proxy_manager=self.proxy_manager,
            states_dir=self.config.get("browser", {}).get("states_dir", "./data/states"),
            headless=self.config.get("browser", {}).get("headless", False),
        )
        self.account_manager = AccountManager(
            db=self.db,
            browser_pool=self.browser_pool,
            proxy_manager=self.proxy_manager,
        )
        llm_cfg = self.config.get("llm", {})
        self.content_generator = ContentGenerator(
            base_url=llm_cfg.get("base_url", ""),
            api_key=llm_cfg.get("api_key", ""),
            model=llm_cfg.get("model", ""),
        )
        self.publisher = Publisher(
            db=self.db,
            browser_pool=self.browser_pool,
            proxy_manager=self.proxy_manager,
            account_manager=self.account_manager,
        )
        self.scheduler = TaskScheduler(
            db=self.db,
            account_manager=self.account_manager,
            content_generator=self.content_generator,
            publisher=self.publisher,
            browser_pool=self.browser_pool,
            config=self.config,
        )

    async def run(self):
        args = sys.argv[1:]

        if not args:
            await self._interactive_menu()
        elif args[0] == "add-account":
            await self._add_account()
        elif args[0] == "login" and len(args) > 1:
            await self._login(int(args[1]))
        elif args[0] == "generate" and len(args) > 1:
            await self._generate(int(args[1]))
        elif args[0] == "generate-product" and len(args) > 1:
            await self._generate_product(int(args[1]))
        elif args[0] == "publish" and len(args) > 1:
            await self._publish_one(int(args[1]))
        elif args[0] == "start":
            await self._start_scheduler()
        elif args[0] == "status":
            self._show_status()
        elif args[0] == "web":
            from web.app import start as start_web
            start_web()
            return
        else:
            console.print(__doc__)

    async def _interactive_menu(self):
        while True:
            console.print("\n[bold]═══ 小红书多账户自动发布系统 ═══[/bold]\n")
            console.print("  1. 查看账户状态")
            console.print("  2. 添加账户")
            console.print("  3. 扫码登录")
            console.print("  4. 生成内容 (普通笔记)")
            console.print("  5. 立即发布")
            console.print("  6. 启动自动调度")
            console.print("  7. 检查所有代理")
            console.print("  8. 查看待发布任务")
            console.print("  [bold magenta]9. 生成带货笔记 (挂商品)[/bold magenta]")
            console.print("  0. 退出\n")

            choice = Prompt.ask("选择操作", choices=["0","1","2","3","4","5","6","7","8","9"])

            if choice == "0":
                await self.browser_pool.stop()
                break
            elif choice == "1":
                self._show_status()
            elif choice == "2":
                await self._add_account()
            elif choice == "3":
                self._show_status()
                aid = IntPrompt.ask("输入账户 ID")
                await self._login(aid)
            elif choice == "4":
                self._show_status()
                aid = IntPrompt.ask("输入账户 ID")
                await self._generate(aid)
            elif choice == "5":
                self._show_status()
                aid = IntPrompt.ask("输入账户 ID")
                await self._publish_one(aid)
            elif choice == "6":
                await self._start_scheduler()
            elif choice == "7":
                await self._check_proxies()
            elif choice == "8":
                self._show_tasks()
            elif choice == "9":
                self._show_status()
                aid = IntPrompt.ask("输入账户 ID")
                await self._generate_product(aid)

    # ── 账户管理 ──

    async def _add_account(self):
        console.print("\n[bold]添加新账户[/bold]\n")

        nickname = Prompt.ask("本地昵称 (仅标识用)")
        persona = Prompt.ask(
            "AI 人设描述",
            default="一个热爱生活的95后女生, 喜欢分享日常穿搭和美食探店",
        )

        # 分配代理
        available = self.proxy_manager.available
        if available:
            console.print(f"\n可用代理 ({len(available)} 个):")
            for i, p in enumerate(available):
                console.print(f"  {i}: {p}")
            idx = IntPrompt.ask("选择代理编号", default=0)
            proxy = available[min(idx, len(available) - 1)]
        else:
            console.print("[yellow]无可用代理, 将使用直连")
            proxy = ""

        account = Account(
            nickname=nickname,
            proxy=proxy,
            persona=persona,
            status=AccountStatus.OFFLINE,
        )
        aid = self.db.add_account(account)

        if proxy:
            self.proxy_manager.assign(proxy, aid)

        console.print(f"\n[green]账户已添加! ID: {aid}")
        console.print("[yellow]下一步: 执行扫码登录")

    async def _login(self, account_id: int):
        account = self.db.get_account(account_id)
        if not account:
            console.print(f"[red]账户 {account_id} 不存在")
            return

        await self.browser_pool.start()
        success = await self.account_manager.login_by_qrcode(account)
        if success:
            console.print("[green]登录成功, 会话已保存")
        else:
            console.print("[red]登录失败, 请重试")

    # ── 内容生成 ──

    async def _generate(self, account_id: int):
        account = self.db.get_account(account_id)
        if not account:
            console.print(f"[red]账户 {account_id} 不存在")
            return

        topic = Prompt.ask("内容主题", default="日常穿搭分享")
        style = Prompt.ask("风格", default="轻松真实, 像朋友聊天")
        keywords = Prompt.ask("关键词 (逗号分隔)", default="穿搭,日常,分享")
        count = IntPrompt.ask("生成几篇", default=3)

        plan = ContentPlan(
            account_id=account.id,
            topic=topic,
            style=style,
            keywords=[k.strip() for k in keywords.split(",")],
        )

        await self.scheduler.generate_and_queue(account, plan, count)
        console.print(f"\n[green]内容生成完毕, 已加入发布队列")

    # ── 带货内容生成 ──

    async def _generate_product(self, account_id: int):
        """生成带货笔记 (挂商品)"""
        account = self.db.get_account(account_id)
        if not account:
            console.print(f"[red]账户 {account_id} 不存在")
            return

        console.print("\n[bold magenta]═══ 生成带货笔记 ═══[/bold magenta]\n")

        topic = Prompt.ask("内容主题", default="好物推荐")
        style = Prompt.ask("风格", default="真实种草, 像闺蜜分享")
        keywords = Prompt.ask("关键词 (逗号分隔)", default="好物分享,真实测评,推荐")

        # 收集商品信息
        products = []
        console.print("\n[bold]添加要挂载的商品 (输入空关键词结束)[/bold]")

        while True:
            idx = len(products) + 1
            console.print(f"\n[cyan]── 商品 {idx} ──")
            keyword = Prompt.ask("商品搜索关键词 (发布时用于搜索商品)", default="")
            if not keyword:
                if not products:
                    console.print("[yellow]至少需要添加一个商品!")
                    continue
                break

            product_name = Prompt.ask("商品名称 (用于内容生成和校验)", default=keyword)
            product_id = Prompt.ask("商品 ID (可选, 留空自动搜索)", default="")
            product_url = Prompt.ask("商品链接 (可选)", default="")

            products.append(ProductInfo(
                keyword=keyword,
                product_name=product_name,
                product_id=product_id,
                product_url=product_url,
            ))
            console.print(f"[green]  已添加: {product_name}")

        count = IntPrompt.ask("生成几篇", default=3)

        plan = ContentPlan(
            account_id=account.id,
            topic=topic,
            style=style,
            keywords=[k.strip() for k in keywords.split(",")],
            note_type=NoteType.PRODUCT,
            products=products,
        )

        await self.scheduler.generate_and_queue(account, plan, count)

        console.print(f"\n[bold green]带货笔记生成完毕!")
        console.print(f"[green]  商品: {', '.join(p.product_name for p in products)}")
        console.print(f"[green]  已加入发布队列, 发布时会自动挂载商品")

    # ── 发布 ──

    async def _publish_one(self, account_id: int):
        account = self.db.get_account(account_id)
        if not account:
            console.print(f"[red]账户 {account_id} 不存在")
            return

        if account.status != AccountStatus.ONLINE:
            console.print(f"[red]账户 {account.nickname} 不在线, 请先登录")
            return

        tasks = self.db.get_pending_tasks(account_id)
        ready = [t for t in tasks if t.status.value == "ready"]

        if not ready:
            console.print("[yellow]没有待发布的内容, 请先生成")
            return

        console.print(f"\n待发布内容 ({len(ready)} 篇):")
        for t in ready:
            console.print(f"  ID={t.id}: {t.title}")

        task = ready[0]
        console.print(f"\n[bold]将发布: {task.title}")

        await self.browser_pool.start()
        await self.publisher.publish(account, task)

    async def _start_scheduler(self):
        console.print("\n[bold green]启动自动调度模式[/bold green]")
        console.print("[dim]Ctrl+C 退出\n")

        await self.browser_pool.start()
        try:
            await self.scheduler.start()
        except KeyboardInterrupt:
            console.print("\n[yellow]正在停止...")
            await self.scheduler.stop()
            await self.browser_pool.stop()

    # ── 状态查看 ──

    def _show_status(self):
        accounts = self.db.get_all_accounts()

        if not accounts:
            console.print("[yellow]还没有添加任何账户")
            return

        table = Table(title="账户状态")
        table.add_column("ID", style="cyan")
        table.add_column("昵称", style="bold")
        table.add_column("小红书昵称")
        table.add_column("状态")
        table.add_column("代理")
        table.add_column("最后检查")
        table.add_column("今日发布")

        status_colors = {
            AccountStatus.ONLINE: "green",
            AccountStatus.OFFLINE: "dim",
            AccountStatus.LOGGING_IN: "yellow",
            AccountStatus.SESSION_EXPIRED: "red",
            AccountStatus.BANNED: "bold red",
        }

        for a in accounts:
            color = status_colors.get(a.status, "white")
            today_count = self.db.get_today_published_count(a.id)
            last_check = (
                a.last_health_check.strftime("%H:%M:%S")
                if a.last_health_check else "-"
            )
            table.add_row(
                str(a.id),
                a.nickname,
                a.xhs_nickname or "-",
                f"[{color}]{a.status.value}",
                a.proxy[:30] + "..." if len(a.proxy) > 30 else (a.proxy or "直连"),
                last_check,
                str(today_count),
            )

        console.print(table)

    def _show_tasks(self):
        tasks = self.db.get_pending_tasks()

        if not tasks:
            console.print("[yellow]没有待发布的任务")
            return

        table = Table(title="待发布任务")
        table.add_column("ID", style="cyan")
        table.add_column("账户", style="bold")
        table.add_column("类型")
        table.add_column("标题")
        table.add_column("状态")
        table.add_column("商品")
        table.add_column("标签")

        for t in tasks:
            account = self.db.get_account(t.account_id)
            name = account.nickname if account else f"#{t.account_id}"
            note_type = "[magenta]带货" if t.note_type == NoteType.PRODUCT else "普通"
            products_str = ", ".join(
                p.product_name or p.keyword for p in t.products
            )[:30] if t.products else "-"
            table.add_row(
                str(t.id),
                name,
                note_type,
                t.title[:30],
                t.status.value,
                products_str,
                ", ".join(t.tags[:3]),
            )

        console.print(table)

    async def _check_proxies(self):
        console.print("\n[bold]检查所有代理...\n")
        results = await self.proxy_manager.check_all()
        for proxy, (ok, ip) in results.items():
            if ok:
                console.print(f"  [green]OK[/green]  {proxy} -> {ip}")
            else:
                console.print(f"  [red]FAIL[/red] {proxy}")


def main():
    app = App()
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        console.print("\n[yellow]已退出")


if __name__ == "__main__":
    main()
