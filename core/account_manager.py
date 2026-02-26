"""账户管理 — 扫码登录 + 会话保活 + 身份校验"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from playwright.async_api import BrowserContext, Page
from rich.console import Console

from core.browser_pool import BrowserPool
from core.proxy_manager import ProxyManager
from models.schemas import Account, AccountStatus
from storage.database import Database

console = Console()

XHS_CREATOR_URL = "https://creator.xiaohongshu.com"
XHS_LOGIN_URL = "https://creator.xiaohongshu.com/login"
XHS_HOME_URL = "https://creator.xiaohongshu.com/new/home"

# 登录后用于校验身份的接口
XHS_USER_INFO_URL = "https://creator.xiaohongshu.com/api/galaxy/user/myself"


class AccountManager:
    def __init__(
        self,
        db: Database,
        browser_pool: BrowserPool,
        proxy_manager: ProxyManager,
    ):
        self.db = db
        self.browser_pool = browser_pool
        self.proxy_manager = proxy_manager

    # ── 扫码登录 ──

    async def login_by_qrcode(self, account: Account) -> bool:
        """
        打开登录页, 等待用户扫码.
        扫码成功后自动保存会话状态和用户信息.
        """
        account.status = AccountStatus.LOGGING_IN
        self.db.update_account(account)

        ctx = await self.browser_pool.get_context(account)
        page = await ctx.new_page()

        try:
            console.print(f"\n[bold yellow]请用小红书 APP 扫码登录: {account.nickname}")
            console.print("[yellow]等待扫码中... (最长等待 180 秒)\n")

            await page.goto(XHS_LOGIN_URL, wait_until="networkidle", timeout=30000)

            # 等待扫码完成 — 检测 URL 离开 /login 页面
            # 小红书可能跳转到 /home, /new/home, 或其他页面
            try:
                await page.wait_for_url(
                    lambda url: "/login" not in url,
                    timeout=180000,  # 3 分钟等待扫码
                )
            except Exception:
                if "/login" in page.url:
                    console.print(f"[red]账户 {account.nickname}: 扫码超时或失败")
                    account.status = AccountStatus.OFFLINE
                    self.db.update_account(account)
                    return False

            # 登录成功, 获取用户信息
            await asyncio.sleep(2)  # 等页面完全加载
            user_info = await self._fetch_user_info(page)

            if user_info:
                account.xhs_user_id = user_info.get("user_id", "")
                account.xhs_nickname = user_info.get("nickname", "")
                console.print(
                    f"[green]登录成功! 小红书昵称: {account.xhs_nickname} "
                    f"(ID: {account.xhs_user_id})"
                )
            else:
                console.print("[yellow]登录成功, 但未能获取用户信息 (不影响使用)")

            account.status = AccountStatus.ONLINE
            account.last_health_check = datetime.now()
            account.consecutive_failures = 0
            self.db.update_account(account)

            # 保存会话状态
            await self.browser_pool.save_all_states()
            return True

        except Exception as e:
            console.print(f"[red]登录异常: {e}")
            account.status = AccountStatus.OFFLINE
            self.db.update_account(account)
            return False
        finally:
            await page.close()

    # ── 会话健康检查 ──

    async def check_session(self, account: Account) -> bool:
        """
        检查账户会话是否还活着.
        通过访问用户信息接口判断.
        """
        if account.status not in (AccountStatus.ONLINE, AccountStatus.SESSION_EXPIRED):
            return False

        ctx = await self.browser_pool.get_context(account)
        page = await ctx.new_page()

        try:
            user_info = await self._fetch_user_info(page)
            account.last_health_check = datetime.now()

            if user_info and user_info.get("user_id"):
                # 会话有效
                account.status = AccountStatus.ONLINE
                account.consecutive_failures = 0
                self.db.update_account(account)
                return True
            else:
                # 会话过期
                account.consecutive_failures += 1
                if account.consecutive_failures >= 3:
                    account.status = AccountStatus.SESSION_EXPIRED
                    console.print(
                        f"[red]账户 {account.nickname}: 会话已过期 "
                        f"(连续 {account.consecutive_failures} 次检查失败)"
                    )
                self.db.update_account(account)
                return False

        except Exception as e:
            account.consecutive_failures += 1
            account.last_health_check = datetime.now()
            self.db.update_account(account)
            console.print(f"[red]健康检查异常 ({account.nickname}): {e}")
            return False
        finally:
            await page.close()

    async def check_all_sessions(self) -> dict[int, bool]:
        """批量检查所有在线账户"""
        accounts = self.db.get_online_accounts()
        results = {}
        for account in accounts:
            results[account.id] = await self.check_session(account)
            await asyncio.sleep(1)  # 避免请求过快
        return results

    # ── 身份校验 (发布前必调) ──

    async def verify_identity(self, account: Account, page: Page) -> Optional[str]:
        """
        发布前的身份校验.
        返回当前登录的 user_id, 如果和预期不匹配则返回 None.
        这是防止发错账户的核心机制.
        """
        user_info = await self._fetch_user_info(page)
        if not user_info:
            console.print(f"[red]身份校验失败: 无法获取用户信息 ({account.nickname})")
            return None

        current_user_id = user_info.get("user_id", "")

        if account.xhs_user_id and current_user_id != account.xhs_user_id:
            console.print(
                f"[bold red]!!! 身份不匹配 !!! "
                f"预期: {account.xhs_user_id}, 实际: {current_user_id}"
            )
            return None

        return current_user_id

    # ── 内部方法 ──

    async def _fetch_user_info(self, page: Page) -> Optional[dict]:
        """通过页面 JS 调用获取当前登录用户信息"""
        try:
            # 方法1: 直接请求 API
            response = await page.goto(XHS_USER_INFO_URL, wait_until="load", timeout=10000)
            if response and response.ok:
                data = await response.json()
                if data.get("success") or data.get("code") == 0:
                    return data.get("data", {})

            # 方法2: 从页面 cookie 中提取 user_id
            cookies = await page.context.cookies()
            for cookie in cookies:
                if cookie["name"] in ("userId", "user_id", "customerUserId"):
                    return {"user_id": cookie["value"], "nickname": ""}

            # 方法3: 从首页 DOM 中提取 (备选)
            await page.goto(XHS_HOME_URL, wait_until="networkidle", timeout=15000)
            user_info = await page.evaluate("""
                () => {
                    try {
                        if (window.__INITIAL_STATE__) {
                            const state = window.__INITIAL_STATE__;
                            return {
                                user_id: state.user?.userId || state.user?.user_id || '',
                                nickname: state.user?.nickname || state.user?.nick_name || ''
                            };
                        }
                    } catch(e) {}
                    return null;
                }
            """)
            return user_info

        except Exception as e:
            console.print(f"[dim]获取用户信息失败: {e}")
            return None
