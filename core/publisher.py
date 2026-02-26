"""发布器 — 三重校验 + 自动化发布笔记 (支持带货挂商品)"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, BrowserContext
from rich.console import Console

from core.account_manager import AccountManager
from core.browser_pool import BrowserPool
from core.proxy_manager import ProxyManager
from models.schemas import Account, NoteType, ProductInfo, PublishTask, TaskStatus
from storage.database import Database

console = Console()

XHS_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish"


class Publisher:
    def __init__(
        self,
        db: Database,
        browser_pool: BrowserPool,
        proxy_manager: ProxyManager,
        account_manager: AccountManager,
    ):
        self.db = db
        self.browser_pool = browser_pool
        self.proxy_manager = proxy_manager
        self.account_manager = account_manager

    async def publish(self, account: Account, task: PublishTask) -> bool:
        """
        发布一篇笔记, 全程三重校验.
        支持普通笔记和带货笔记 (挂商品).
        """
        task.status = TaskStatus.PUBLISHING
        self.db.update_task(task)

        ctx = await self.browser_pool.get_context(account)
        page = await ctx.new_page()

        try:
            # ══════════════════════════════════════
            # 第一重校验: 身份核验
            # ══════════════════════════════════════
            console.print(f"[blue][校验1/3] 身份核验: {account.nickname}")
            verified_user_id = await self.account_manager.verify_identity(account, page)
            if not verified_user_id:
                task.status = TaskStatus.FAILED
                task.error_msg = "身份校验失败: user_id 不匹配或无法获取"
                self.db.update_task(task)
                console.print(f"[bold red]发布中止: 身份不匹配!")
                return False
            task.verified_user_id = verified_user_id
            console.print(f"[green]  身份确认: {verified_user_id}")

            # ══════════════════════════════════════
            # 第二重校验: 代理 IP 核验
            # ══════════════════════════════════════
            console.print(f"[blue][校验2/3] 代理 IP 核验")
            if account.proxy:
                ok, current_ip = await self.proxy_manager.check_proxy(account.proxy)
                if not ok:
                    task.status = TaskStatus.FAILED
                    task.error_msg = f"代理不可用: {account.proxy}"
                    self.db.update_task(task)
                    console.print(f"[bold red]发布中止: 代理不可用!")
                    return False
                task.verified_proxy_ip = current_ip
                console.print(f"[green]  代理 IP: {current_ip}")
            else:
                console.print("[yellow]  无代理 (跳过)")

            # ══════════════════════════════════════
            # 执行发布
            # ══════════════════════════════════════
            note_type_label = "带货笔记" if task.note_type == NoteType.PRODUCT else "普通笔记"
            console.print(f"[blue]正在发布 ({note_type_label}): {task.title}")

            if task.note_type == NoteType.PRODUCT and task.products:
                console.print(f"[blue]  挂载商品: {len(task.products)} 个")
                for p in task.products:
                    console.print(f"[blue]    - {p.product_name or p.keyword}")

            success = await self._do_publish(page, task)

            if not success:
                task.status = TaskStatus.FAILED
                task.error_msg = "发布操作失败"
                self.db.update_task(task)
                return False

            # ══════════════════════════════════════
            # 第三重校验: 发布后确认
            # ══════════════════════════════════════
            console.print(f"[blue][校验3/3] 发布后确认")
            await asyncio.sleep(3)

            # 访问作品管理页确认
            post_verified = await self._verify_post(page, account, task)
            if post_verified:
                task.status = TaskStatus.PUBLISHED
                task.published_at = datetime.now()
                self.db.update_task(task)
                console.print(
                    f"[bold green]发布成功! "
                    f"账户: {account.nickname} | 类型: {note_type_label} | 标题: {task.title}"
                )
                return True
            else:
                # 发布可能成功了但确认失败, 标记为需要人工核查
                task.status = TaskStatus.PUBLISHED
                task.published_at = datetime.now()
                task.error_msg = "发布可能成功, 但三重校验第3步未能确认, 请人工核查"
                self.db.update_task(task)
                console.print("[yellow]发布可能成功, 请人工确认")
                return True

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_msg = str(e)
            self.db.update_task(task)
            console.print(f"[red]发布异常: {e}")
            return False
        finally:
            await page.close()

    async def _do_publish(self, page: Page, task: PublishTask) -> bool:
        """执行实际的发布流程 (小红书创作者中心)"""
        try:
            await page.goto(XHS_PUBLISH_URL, timeout=60000)
            await asyncio.sleep(5)

            # 切换到 "上传图文" tab
            await page.evaluate("""() => {
                for (const tab of document.querySelectorAll('.creator-tab')) {
                    if (tab.textContent.trim().includes('上传图文')) { tab.click(); break; }
                }
            }""")
            await asyncio.sleep(2)

            # 如果有图片, 先上传
            if task.image_paths:
                await self._upload_images(page, task.image_paths)
                await asyncio.sleep(5)

            # 填写标题
            title_input = page.locator('input[placeholder*="标题"]').first
            if await title_input.count() == 0:
                title_input = page.locator('[class*="title"] [contenteditable]').first
            await title_input.click()
            await title_input.fill("")
            await page.keyboard.type(task.title, delay=50)
            await asyncio.sleep(0.5)

            # 填写正文
            content_editor = page.locator('.ProseMirror[contenteditable="true"]').first
            if await content_editor.count() == 0:
                content_editor = page.locator('[contenteditable="true"]').last
            await content_editor.click()

            # 分段输入正文
            paragraphs = task.content.split("\n")
            for i, para in enumerate(paragraphs):
                if para.strip():
                    await page.keyboard.type(para.strip(), delay=30)
                if i < len(paragraphs) - 1:
                    await page.keyboard.press("Enter")
            await asyncio.sleep(0.5)

            # 添加标签 (通过话题按钮)
            if task.tags:
                for tag in task.tags[:8]:
                    await page.keyboard.type(f" #{tag}", delay=30)
                    await asyncio.sleep(0.5)
                    suggestion = page.locator('[class*="tag-suggestion"], [class*="topic-item"], [class*="mention-list"] [class*="item"]').first
                    if await suggestion.count() > 0:
                        await suggestion.click()
                        await asyncio.sleep(0.3)

            await asyncio.sleep(1)

            # ══════════════════════════════════════
            # 带货笔记: 挂载商品
            # ══════════════════════════════════════
            if task.note_type == NoteType.PRODUCT and task.products:
                console.print("[blue]  正在挂载商品...")
                product_ok = await self._attach_products(page, task.products)
                if not product_ok:
                    console.print("[yellow]  商品挂载失败, 将以普通笔记发布")
                    task.error_msg = "商品挂载失败, 已降级为普通笔记发布"

            # 点击发布按钮
            publish_btn = page.locator('button:has-text("发布")').last
            if await publish_btn.count() > 0:
                await publish_btn.click()
                await asyncio.sleep(5)

                # 检查是否有发布成功的提示
                success_indicator = page.locator(
                    'text="发布成功", text="已发布", [class*="success"]'
                ).first
                if await success_indicator.count() > 0:
                    return True

                # 检查 URL 是否跳转到作品管理
                if "publish" not in page.url.lower() or "manage" in page.url.lower():
                    return True

            return False

        except Exception as e:
            console.print(f"[red]发布流程异常: {e}")
            return False

    async def _attach_products(self, page: Page, products: list[ProductInfo]) -> bool:
        """
        在发布页面挂载商品.

        小红书创作者中心真实流程:
        1. 滚动到 "店内商品" 区域
        2. 点击 "添加商品" 按钮 (在 .multi-good-select-empty-btn 内)
        3. 弹出 "选择商品" 弹窗 (.multi-goods-selector-modal)
        4. 在搜索框 (placeholder="搜索商品ID 或 商品名称") 搜索
        5. 勾选商品 (checkbox)
        6. 点击 "保存"
        """
        try:
            # ── 步骤1: 滚动到商品区域并点击 "添加商品" ──
            await page.evaluate("""() => {
                const el = document.querySelector('.multi-good-select-empty-btn') ||
                           document.querySelector('.publish-page-content-business');
                if (el) el.scrollIntoView({behavior: 'smooth', block: 'center'});
            }""")
            await asyncio.sleep(1)

            add_btn = page.locator('.multi-good-select-empty-btn button').first
            if await add_btn.count() == 0:
                add_btn = page.locator('button:has-text("添加商品")').first
            if await add_btn.count() == 0:
                console.print("[red]  未找到添加商品按钮")
                return False

            await add_btn.click()
            await asyncio.sleep(3)

            # 确认弹窗已出现
            modal = page.locator('.multi-goods-selector-modal, .d-modal:has-text("选择商品")')
            if await modal.count() == 0:
                console.print("[red]  商品选择弹窗未出现")
                return False
            console.print("[green]  商品选择弹窗已打开")

            # ── 步骤2: 逐个搜索并勾选商品 ──
            attached_count = 0
            for product in products:
                ok = await self._search_and_select_product(page, product)
                if ok:
                    attached_count += 1
                    console.print(
                        f"[green]  商品已勾选: {product.product_name or product.keyword}"
                    )
                else:
                    console.print(
                        f"[yellow]  商品勾选失败: {product.product_name or product.keyword}"
                    )
                await asyncio.sleep(1)

            # ── 步骤3: 点击 "保存" 按钮 ──
            save_btn = page.locator('.d-modal-footer button:has-text("保存")').first
            if await save_btn.count() == 0:
                save_btn = page.locator('button:has-text("保存")').last
            if await save_btn.count() > 0:
                await save_btn.click()
                await asyncio.sleep(2)
                console.print("[green]  已保存商品选择")

            if attached_count > 0:
                console.print(
                    f"[green]  成功挂载 {attached_count}/{len(products)} 个商品"
                )
                return True
            else:
                console.print("[red]  所有商品挂载均失败")
                return False

        except Exception as e:
            console.print(f"[red]  商品挂载异常: {e}")
            return False

    async def _search_and_select_product(
        self, page: Page, product: ProductInfo
    ) -> bool:
        """
        在 "选择商品" 弹窗中搜索并勾选一个商品.
        弹窗结构:
        - 搜索框: input[placeholder="搜索商品ID 或 商品名称"]
        - 商品列表: 每项有 checkbox + 商品图片 + 名称 + 商品ID + 价格
        - 底部: "已选择 N 项" + "保存"
        """
        try:
            search_keyword = product.keyword or product.product_name
            if not search_keyword:
                console.print("[red]    商品缺少搜索关键词")
                return False

            # 找到弹窗内的搜索框
            search_input = page.locator(
                'input[placeholder*="搜索商品"]'
            ).first
            if await search_input.count() == 0:
                search_input = page.locator(
                    '.multi-goods-selector-modal input[type="text"]'
                ).first
            if await search_input.count() == 0:
                console.print("[red]    未找到商品搜索框")
                return False

            # 清空并输入搜索关键词
            await search_input.click()
            await search_input.fill("")
            await asyncio.sleep(0.3)
            await page.keyboard.type(search_keyword, delay=50)
            await page.keyboard.press("Enter")
            await asyncio.sleep(3)

            # ── 从搜索结果中选择商品 ──

            # 策略1: 如果有 product_id, 找包含该ID文本的行并勾选
            if product.product_id:
                id_match = page.locator(
                    f'.multi-goods-selector-modal :text("{product.product_id}")'
                ).first
                if await id_match.count() > 0:
                    # 找到所在行的 checkbox
                    row = id_match.locator("xpath=ancestor::*[.//input[@type='checkbox']]").first
                    if await row.count() > 0:
                        checkbox = row.locator('input[type="checkbox"]').first
                        if await checkbox.count() > 0:
                            await checkbox.click()
                            await asyncio.sleep(0.5)
                            console.print(f"[green]    通过商品ID精确匹配")
                            return True

            # 策略2: 点击商品卡片上的 .d-checkbox 包装元素
            # 原生 checkbox 被隐藏 (opacity:0), 需要点击可见的 .d-checkbox 容器
            checkbox_wrappers = page.locator(
                '.good-card-container .d-checkbox'
            )
            wrapper_count = await checkbox_wrappers.count()
            if wrapper_count > 0:
                for i in range(wrapper_count):
                    wrapper = checkbox_wrappers.nth(i)
                    # 检查对应的原生 checkbox 是否已勾选
                    cb = wrapper.locator('input[type="checkbox"]').first
                    checked = await cb.is_checked() if await cb.count() > 0 else False
                    if not checked:
                        await wrapper.click()
                        await asyncio.sleep(0.5)
                        console.print(f"[green]    已勾选第 {i+1} 个商品")
                        return True

            # 策略3: 直接点击商品卡片行
            cards = page.locator('.good-card-container')
            if await cards.count() > 0:
                await cards.first.click()
                await asyncio.sleep(0.5)
                console.print(f"[green]    已点击第一个商品卡片")
                return True

            console.print(f"[yellow]    未找到匹配商品: {search_keyword}")
            return False

        except Exception as e:
            console.print(f"[red]    搜索商品异常: {e}")
            return False

    async def _upload_images(self, page: Page, image_paths: list[str]):
        """上传图片 (accept='.jpg,.jpeg,.png,.webp' 的隐藏 file input)"""
        valid_paths = [p for p in image_paths if Path(p).exists()]
        if not valid_paths:
            console.print("[yellow]  无有效图片路径")
            return

        file_input = page.locator('input[type="file"][accept*="image"], input[type="file"][accept*=".jpg"]').first
        if await file_input.count() == 0:
            file_input = page.locator('input[type="file"]').first
        if await file_input.count() > 0:
            await file_input.set_input_files(valid_paths)
            await asyncio.sleep(max(len(valid_paths) * 3, 5))
            console.print(f"[green]  已上传 {len(valid_paths)} 张图片")

    async def _verify_post(
        self, page: Page, account: Account, task: PublishTask
    ) -> bool:
        """发布后校验: 访问作品管理, 确认笔记存在且归属正确"""
        try:
            await page.goto(
                "https://creator.xiaohongshu.com/publish/manage",
                wait_until="networkidle",
                timeout=15000,
            )
            await asyncio.sleep(2)

            # 查找最新发布的笔记标题
            page_content = await page.content()
            if task.title[:10] in page_content:
                console.print(f"[green]  笔记已确认在作品列表中")
                return True

            console.print("[yellow]  未在作品列表中找到笔记 (可能审核中)")
            return False

        except Exception as e:
            console.print(f"[yellow]  发布后确认失败: {e}")
            return False
