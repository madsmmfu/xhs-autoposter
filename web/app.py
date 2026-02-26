"""Web 前端 — FastAPI + WebSocket 实时日志"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from core.account_manager import AccountManager
from core.browser_pool import BrowserPool
from core.content_generator import ContentGenerator
from core.proxy_manager import ProxyManager
from core.publisher import Publisher
from models.schemas import (
    Account, AccountStatus, ContentPlan,
    NoteType, ProductInfo, PublishTask, TaskStatus,
)
from scheduler.task_scheduler import TaskScheduler
from storage.database import Database

# ── 全局日志广播 ──

class LogBroadcaster:
    """将日志通过 WebSocket 广播给所有连接的前端"""
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self.connections.remove(ws)

    async def broadcast(self, msg: str, level: str = "info"):
        data = json.dumps({
            "type": "log",
            "level": level,
            "message": msg,
            "time": datetime.now().strftime("%H:%M:%S"),
        })
        dead = []
        for ws in self.connections:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.remove(ws)

    async def send_event(self, event_type: str, data: dict):
        payload = json.dumps({"type": event_type, **data})
        dead = []
        for ws in self.connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.remove(ws)

log_broadcaster = LogBroadcaster()


# ── 猴子补丁 rich console，转发日志到 WebSocket ──

import rich.console
_original_print = rich.console.Console.print

def _patched_print(self, *args, **kwargs):
    _original_print(self, *args, **kwargs)
    # 提取纯文本
    text = " ".join(str(a) for a in args)
    # 去掉 rich markup
    import re
    clean = re.sub(r'\[/?[^\]]*\]', '', text)
    if clean.strip():
        level = "info"
        if "[red]" in text or "失败" in text or "异常" in text:
            level = "error"
        elif "[yellow]" in text or "警告" in text:
            level = "warn"
        elif "[green]" in text or "成功" in text:
            level = "success"
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(log_broadcaster.broadcast(clean.strip(), level))
        except RuntimeError:
            pass  # 没有运行中的 event loop 时忽略

rich.console.Console.print = _patched_print


# ── 加载配置 ──

def load_config() -> dict:
    config_path = PROJECT_ROOT / "config" / "settings.yaml"
    if not config_path.exists():
        return {
            "llm": {"api_key": "", "model": "claude-sonnet-4-20250514", "base_url": ""},
            "proxies": [],
            "schedule": {"max_posts_per_day": 3, "min_interval_minutes": 60, "active_hours": [8, 23]},
            "browser": {"headless": False, "states_dir": "./data/states"},
            "database": {"path": "./data/xhs.db"},
        }
    with open(config_path) as f:
        return yaml.safe_load(f)


# ── 初始化应用组件 ──

config = load_config()
db = Database(config.get("database", {}).get("path", "./data/xhs.db"))
proxy_manager = ProxyManager(config.get("proxies", []))
browser_pool = BrowserPool(
    proxy_manager=proxy_manager,
    states_dir=config.get("browser", {}).get("states_dir", "./data/states"),
    headless=config.get("browser", {}).get("headless", False),
)
account_manager = AccountManager(db=db, browser_pool=browser_pool, proxy_manager=proxy_manager)
llm_cfg = config.get("llm", {})
content_generator = ContentGenerator(
    base_url=llm_cfg.get("base_url", ""),
    api_key=llm_cfg.get("api_key", ""),
    model=llm_cfg.get("model", ""),
)
publisher = Publisher(db=db, browser_pool=browser_pool, proxy_manager=proxy_manager, account_manager=account_manager)
scheduler = TaskScheduler(
    db=db, account_manager=account_manager, content_generator=content_generator,
    publisher=publisher, browser_pool=browser_pool, config=config,
)

# 浏览器是否已启动
browser_started = False

async def ensure_browser():
    global browser_started
    if not browser_started:
        await browser_pool.start()
        browser_started = True


# ── FastAPI 应用 ──

app = FastAPI(title="小红书自动发布系统")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# ── WebSocket 日志 ──

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await log_broadcaster.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        log_broadcaster.disconnect(ws)


# ── 页面路由 ──

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    return html_path.read_text(encoding="utf-8")


# ── API: 账户管理 ──

@app.get("/api/accounts")
async def get_accounts():
    accounts = db.get_all_accounts()
    result = []
    for a in accounts:
        today_count = db.get_today_published_count(a.id)
        result.append({
            "id": a.id,
            "nickname": a.nickname,
            "xhs_user_id": a.xhs_user_id,
            "xhs_nickname": a.xhs_nickname,
            "proxy": a.proxy,
            "persona": a.persona,
            "status": a.status.value,
            "last_health_check": a.last_health_check.strftime("%H:%M:%S") if a.last_health_check else None,
            "consecutive_failures": a.consecutive_failures,
            "today_published": today_count,
        })
    return result


@app.post("/api/accounts")
async def add_account(data: dict):
    nickname = data.get("nickname", "")
    persona = data.get("persona", "一个热爱生活的95后女生, 喜欢分享日常穿搭和美食探店")
    proxy = data.get("proxy", "")

    if not nickname:
        return JSONResponse({"error": "昵称不能为空"}, status_code=400)

    account = Account(
        nickname=nickname,
        proxy=proxy,
        persona=persona,
        status=AccountStatus.OFFLINE,
    )
    aid = db.add_account(account)

    if proxy:
        try:
            proxy_manager.assign(proxy, aid)
        except ValueError:
            pass

    await log_broadcaster.broadcast(f"账户已添加: {nickname} (ID: {aid})", "success")
    return {"id": aid, "nickname": nickname}


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int):
    account = db.get_account(account_id)
    if not account:
        return JSONResponse({"error": "账户不存在"}, status_code=404)
    db.delete_account(account_id)
    await log_broadcaster.broadcast(f"账户已删除: {account.nickname}", "warn")
    return {"ok": True}


# ── API: 扫码登录 ──

@app.post("/api/accounts/{account_id}/login")
async def login_account(account_id: int):
    account = db.get_account(account_id)
    if not account:
        return JSONResponse({"error": "账户不存在"}, status_code=404)

    await ensure_browser()
    await log_broadcaster.broadcast(f"开始扫码登录: {account.nickname}", "info")

    # 异步执行登录
    async def do_login():
        success = await account_manager.login_by_qrcode(account)
        await log_broadcaster.send_event("login_result", {
            "account_id": account_id,
            "success": success,
        })

    asyncio.create_task(do_login())
    return {"message": "请用小红书 APP 扫码 (等待中...)"}


# ── API: 会话检查 ──

@app.post("/api/accounts/{account_id}/check")
async def check_session(account_id: int):
    account = db.get_account(account_id)
    if not account:
        return JSONResponse({"error": "账户不存在"}, status_code=404)

    await ensure_browser()
    ok = await account_manager.check_session(account)
    return {"account_id": account_id, "online": ok}


# ── API: 内容生成 ──

@app.post("/api/generate")
async def generate_content(data: dict):
    account_id = data.get("account_id")
    account = db.get_account(account_id)
    if not account:
        return JSONResponse({"error": "账户不存在"}, status_code=404)

    topic = data.get("topic", "日常穿搭分享")
    style = data.get("style", "轻松真实")
    keywords = data.get("keywords", "穿搭,日常,分享")
    count = data.get("count", 1)
    note_type = NoteType.PRODUCT if data.get("note_type") == "product" else NoteType.NORMAL

    # 解析商品
    products = []
    for p in data.get("products", []):
        products.append(ProductInfo(
            keyword=p.get("keyword", ""),
            product_name=p.get("product_name", ""),
            product_id=p.get("product_id", ""),
            product_url=p.get("product_url", ""),
        ))

    plan = ContentPlan(
        account_id=account.id,
        topic=topic,
        style=style,
        keywords=[k.strip() for k in keywords.split(",")],
        note_type=note_type,
        products=products,
    )

    await log_broadcaster.broadcast(
        f"开始生成内容: {account.nickname} | 主题: {topic} | 类型: {'带货' if note_type == NoteType.PRODUCT else '普通'}",
        "info"
    )

    async def do_generate():
        await scheduler.generate_and_queue(account, plan, count)
        await log_broadcaster.send_event("generate_done", {"account_id": account_id})

    asyncio.create_task(do_generate())
    return {"message": f"正在生成 {count} 篇内容..."}


# ── API: 任务管理 ──

@app.get("/api/tasks")
async def get_tasks():
    tasks = db.get_pending_tasks()
    result = []
    for t in tasks:
        account = db.get_account(t.account_id)
        result.append({
            "id": t.id,
            "account_id": t.account_id,
            "account_name": account.nickname if account else f"#{t.account_id}",
            "title": t.title,
            "content": t.content,
            "tags": t.tags,
            "status": t.status.value,
            "note_type": t.note_type.value,
            "products": [
                {"keyword": p.keyword, "product_name": p.product_name,
                 "product_id": p.product_id}
                for p in t.products
            ],
            "created_at": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "",
            "error_msg": t.error_msg,
        })
    return result


@app.get("/api/tasks/all")
async def get_all_tasks():
    """获取所有任务 (含已发布和失败的)"""
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 50").fetchall()
        result = []
        for row in rows:
            t = db._row_to_task(row)
            account = db.get_account(t.account_id)
            result.append({
                "id": t.id,
                "account_id": t.account_id,
                "account_name": account.nickname if account else f"#{t.account_id}",
                "title": t.title,
                "content": t.content,
                "tags": t.tags,
                "status": t.status.value,
                "note_type": t.note_type.value,
                "products": [
                    {"keyword": p.keyword, "product_name": p.product_name}
                    for p in t.products
                ],
                "created_at": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "",
                "published_at": t.published_at.strftime("%Y-%m-%d %H:%M") if t.published_at else "",
                "error_msg": t.error_msg,
            })
        return result


# ── API: 发布 ──

@app.post("/api/publish/{task_id}")
async def publish_task(task_id: int):
    # 从所有任务中找到指定任务
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return JSONResponse({"error": "任务不存在"}, status_code=404)
        task = db._row_to_task(row)

    account = db.get_account(task.account_id)
    if not account:
        return JSONResponse({"error": "账户不存在"}, status_code=404)

    if account.status != AccountStatus.ONLINE:
        return JSONResponse({"error": f"账户 {account.nickname} 不在线, 请先登录"}, status_code=400)

    await ensure_browser()
    await log_broadcaster.broadcast(f"开始发布: {task.title}", "info")

    async def do_publish():
        success = await publisher.publish(account, task)
        await log_broadcaster.send_event("publish_done", {
            "task_id": task_id,
            "success": success,
        })

    asyncio.create_task(do_publish())
    return {"message": f"正在发布: {task.title}"}


@app.post("/api/publish-account/{account_id}")
async def publish_account(account_id: int):
    """发布指定账户的第一个待发布任务"""
    account = db.get_account(account_id)
    if not account:
        return JSONResponse({"error": "账户不存在"}, status_code=404)

    tasks = db.get_pending_tasks(account_id)
    ready = [t for t in tasks if t.status == TaskStatus.READY]
    if not ready:
        return JSONResponse({"error": "没有待发布的内容"}, status_code=400)

    task = ready[0]
    await ensure_browser()
    await log_broadcaster.broadcast(f"开始发布: {account.nickname} -> {task.title}", "info")

    async def do_publish():
        success = await publisher.publish(account, task)
        await log_broadcaster.send_event("publish_done", {
            "task_id": task.id,
            "success": success,
        })

    asyncio.create_task(do_publish())
    return {"message": f"正在发布: {task.title}"}


# ── API: 代理检查 ──

@app.get("/api/proxies")
async def get_proxies():
    return {
        "proxies": proxy_manager.proxies,
        "assigned": {p: aid for p, aid in proxy_manager._assigned.items()},
    }


@app.post("/api/proxies/check")
async def check_proxies():
    await log_broadcaster.broadcast("开始检查所有代理...", "info")
    results = await proxy_manager.check_all()
    formatted = {}
    for proxy, (ok, ip) in results.items():
        formatted[proxy] = {"ok": ok, "ip": ip}
    return formatted


# ── API: 调度器 ──

scheduler_task = None

@app.post("/api/scheduler/start")
async def start_scheduler():
    global scheduler_task
    if scheduler_task and not scheduler_task.done():
        return {"message": "调度器已在运行"}

    await ensure_browser()
    await log_broadcaster.broadcast("启动自动调度模式", "success")

    scheduler_task = asyncio.create_task(scheduler.start())
    return {"message": "调度器已启动"}


@app.post("/api/scheduler/stop")
async def stop_scheduler():
    global scheduler_task
    await scheduler.stop()
    if scheduler_task:
        scheduler_task.cancel()
        scheduler_task = None
    await log_broadcaster.broadcast("调度器已停止", "warn")
    return {"message": "调度器已停止"}


@app.get("/api/scheduler/status")
async def scheduler_status():
    running = scheduler_task is not None and not scheduler_task.done()
    return {"running": running}


# ── API: 图片上传 ──

UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@app.post("/api/upload")
async def upload_images(files: list[UploadFile] = File(...)):
    paths = []
    for f in files:
        ext = Path(f.filename).suffix or ".jpg"
        name = f"{uuid.uuid4().hex[:12]}{ext}"
        save_path = UPLOAD_DIR / name
        content = await f.read()
        save_path.write_bytes(content)
        paths.append(str(save_path))
    return {"paths": paths, "count": len(paths)}


# ── 启动入口 ──

def start():
    import uvicorn
    os.chdir(str(PROJECT_ROOT))
    print("\n  小红书自动发布系统 — Web 版")
    print(f"  打开浏览器访问: http://localhost:8000\n")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    start()
