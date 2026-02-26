"""浏览器上下文池 — 每个账户一个完全隔离的 BrowserContext"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Playwright,
)
from rich.console import Console

from core.proxy_manager import ProxyManager
from models.schemas import Account

console = Console()

# 常用移动端 UA, 增加多样性
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]


class BrowserPool:
    def __init__(
        self,
        proxy_manager: ProxyManager,
        states_dir: str = "./data/states",
        headless: bool = False,
    ):
        self.proxy_manager = proxy_manager
        self.states_dir = Path(states_dir)
        self.states_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        # account_id -> BrowserContext (每个账户独占一个)
        self._contexts: dict[int, BrowserContext] = {}
        self._lock = asyncio.Lock()

    async def start(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        console.print("[green]浏览器引擎已启动")

    async def stop(self):
        # 先保存所有会话状态
        for account_id, ctx in list(self._contexts.items()):
            await self._save_state(account_id, ctx)
            await ctx.close()
        self._contexts.clear()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        console.print("[yellow]浏览器引擎已关闭")

    async def get_context(self, account: Account) -> BrowserContext:
        """
        获取账户专属的浏览器上下文.
        如果已存在则复用, 否则创建新的.
        每个上下文有独立的代理、Cookie、UA.
        """
        async with self._lock:
            if account.id in self._contexts:
                return self._contexts[account.id]

            context = await self._create_context(account)
            self._contexts[account.id] = context
            return context

    async def close_context(self, account_id: int):
        """关闭并保存指定账户的上下文"""
        async with self._lock:
            ctx = self._contexts.pop(account_id, None)
            if ctx:
                await self._save_state(account_id, ctx)
                await ctx.close()

    async def _create_context(self, account: Account) -> BrowserContext:
        """创建隔离的浏览器上下文"""
        # 代理配置
        proxy_config = None
        if account.proxy:
            proxy_config = self.proxy_manager.to_playwright_proxy(account.proxy)

        # UA 指纹 (根据 account_id 固定分配, 保持一致性)
        ua = USER_AGENTS[account.id % len(USER_AGENTS)]

        # 尝试恢复已保存的会话状态
        state_file = self.states_dir / f"account_{account.id}.json"
        storage_state = None
        if state_file.exists():
            try:
                storage_state = str(state_file)
                console.print(f"[blue]账户 {account.nickname}: 恢复已保存的会话")
            except Exception:
                storage_state = None

        context = await self._browser.new_context(
            proxy=proxy_config,
            user_agent=ua,
            storage_state=storage_state,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        # 反检测: 注入 stealth 脚本
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        """)

        console.print(
            f"[green]账户 {account.nickname}: 浏览器上下文已创建 "
            f"(proxy={'有' if proxy_config else '无'})"
        )
        return context

    async def _save_state(self, account_id: int, context: BrowserContext):
        """持久化浏览器会话状态 (Cookie + localStorage)"""
        state_file = self.states_dir / f"account_{account_id}.json"
        try:
            state = await context.storage_state()
            state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        except Exception as e:
            console.print(f"[red]保存会话状态失败 (account {account_id}): {e}")

    async def save_all_states(self):
        """保存所有活跃上下文的状态"""
        for account_id, ctx in self._contexts.items():
            await self._save_state(account_id, ctx)

    def get_active_account_ids(self) -> list[int]:
        return list(self._contexts.keys())
