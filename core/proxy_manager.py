"""代理管理 — 每个账户绑定固定代理, IP 不串"""

from __future__ import annotations

import asyncio
import aiohttp
from rich.console import Console

console = Console()

# 查询出口 IP 的公共服务
IP_CHECK_URL = "https://httpbin.org/ip"


class ProxyManager:
    def __init__(self, proxies: list[str]):
        # proxy 地址列表
        self.proxies = list(proxies)
        # 已分配: proxy -> account_id
        self._assigned: dict[str, int] = {}

    @property
    def available(self) -> list[str]:
        """未被分配的代理"""
        return [p for p in self.proxies if p not in self._assigned]

    def assign(self, proxy: str, account_id: int):
        """绑定代理到账户"""
        if proxy in self._assigned and self._assigned[proxy] != account_id:
            raise ValueError(
                f"代理 {proxy} 已绑定到账户 {self._assigned[proxy]}, "
                f"不能再绑到 {account_id}"
            )
        self._assigned[proxy] = account_id

    def release(self, proxy: str):
        self._assigned.pop(proxy, None)

    def get_account_proxy(self, account_id: int) -> str | None:
        for proxy, aid in self._assigned.items():
            if aid == account_id:
                return proxy
        return None

    async def check_proxy(self, proxy: str, timeout: int = 10) -> tuple[bool, str]:
        """
        检查代理是否可用, 返回 (是否可用, 出口IP).
        用于发布前验证 IP 是否正确.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    IP_CHECK_URL,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    data = await resp.json()
                    ip = data.get("origin", "")
                    return True, ip
        except Exception as e:
            console.print(f"[red]代理 {proxy} 不可用: {e}")
            return False, ""

    async def check_all(self) -> dict[str, tuple[bool, str]]:
        """批量检查所有代理"""
        results = {}
        tasks = [self.check_proxy(p) for p in self.proxies]
        checks = await asyncio.gather(*tasks, return_exceptions=True)
        for proxy, result in zip(self.proxies, checks):
            if isinstance(result, Exception):
                results[proxy] = (False, str(result))
            else:
                results[proxy] = result
        return results

    def to_playwright_proxy(self, proxy: str) -> dict:
        """
        将 http://user:pass@host:port 转为 Playwright 代理配置.
        """
        from urllib.parse import urlparse
        parsed = urlparse(proxy)
        config = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
        }
        if parsed.username:
            config["username"] = parsed.username
        if parsed.password:
            config["password"] = parsed.password
        return config
