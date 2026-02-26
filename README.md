# xhs-autoposter

小红书多账户自动发布系统，支持 **普通笔记** 和 **带货笔记（挂商品）**。

基于 Playwright 浏览器自动化 + Claude AI 内容生成，实现从内容生产到发布的全流程自动化。

## 功能

- **多账户管理** — 扫码登录、会话持久化、独立代理隔离
- **AI 内容生成** — Claude 驱动，支持人设定制，生成小红书风格种草笔记
- **带货笔记** — 自动挂载店铺商品，支持按关键词/商品ID搜索选品
- **三重校验发布** — 身份核验 → 代理IP核验 → 发布后确认
- **自动调度** — 定时发布、随机间隔、活跃时段控制
- **会话保活** — 定期健康检查，自动检测会话过期

## 架构

```
xhs-autoposter/
├── main.py                  # 入口 (交互菜单 + CLI)
├── config/
│   ├── settings.example.yaml  # 配置模板
│   └── settings.yaml          # 真实配置 (gitignore)
├── core/
│   ├── account_manager.py   # 扫码登录 / 身份校验 / 会话保活
│   ├── browser_pool.py      # Playwright 浏览器上下文池
│   ├── content_generator.py # Claude AI 内容生成
│   ├── proxy_manager.py     # 代理分配与检测
│   └── publisher.py         # 发布器 (三重校验 + 商品挂载)
├── models/
│   └── schemas.py           # 数据模型 (Account / PublishTask / ProductInfo)
├── scheduler/
│   └── task_scheduler.py    # 定时调度 (保活 / 发布 / 状态持久化)
├── storage/
│   └── database.py          # SQLite 持久化
└── data/                    # 运行时数据 (gitignore)
    ├── xhs.db               # SQLite 数据库
    └── states/              # 浏览器会话状态
```

## 快速开始

### macOS 一键安装 (推荐)

1. 双击 `install-mac.command` — 自动安装 Python、依赖、Playwright 浏览器
2. 编辑 `config/settings.yaml`，填写 API Key 和代理
3. 双击 `start-mac.command` — 启动程序

> 安装脚本会自动: 检查/安装 Homebrew → 检查/安装 Python 3 → 创建虚拟环境 → 安装所有依赖 → 下载 Chromium → 初始化配置文件

### 手动安装

#### 1. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

#### 2. 配置

```bash
cp config/settings.example.yaml config/settings.yaml
```

编辑 `config/settings.yaml`，填写：

- `llm.api_key` — Anthropic API Key
- `proxies` — 代理地址列表（每个账户绑定一个独立代理）

#### 3. 运行

```bash
python main.py
```

进入交互菜单：

```
═══ 小红书多账户自动发布系统 ═══

  1. 查看账户状态
  2. 添加账户
  3. 扫码登录
  4. 生成内容 (普通笔记)
  5. 立即发布
  6. 启动自动调度
  7. 检查所有代理
  8. 查看待发布任务
  9. 生成带货笔记 (挂商品)
  0. 退出
```

### CLI 模式

```bash
python main.py add-account          # 添加账户
python main.py login 1              # 扫码登录账户 1
python main.py generate 1           # 为账户 1 生成普通笔记
python main.py generate-product 1   # 为账户 1 生成带货笔记
python main.py publish 1            # 发布账户 1 的待发布内容
python main.py start                # 启动自动调度
python main.py status               # 查看状态
```

## 带货笔记流程

### 生成带货内容

选择菜单 `9` 或运行 `python main.py generate-product <account_id>`：

1. 输入内容主题、风格、关键词
2. 逐个添加要挂载的商品（关键词 + 名称 + 可选商品ID）
3. AI 自动生成种草风格的带货笔记，自然融入商品推荐

### 发布时自动挂商品

发布带货笔记时，系统自动执行：

1. 上传图片 → 填写标题和正文 → 添加话题标签
2. 滚动到「店内商品」区域 → 点击「添加商品」
3. 在弹窗中搜索商品 → 勾选 → 保存
4. 点击发布

商品匹配策略（按优先级）：

1. **商品 ID 精确匹配** — 如果提供了 `product_id`
2. **关键词搜索 + 自动勾选** — 搜索结果中勾选第一个匹配商品
3. **降级** — 挂载失败时自动降级为普通笔记发布

## 三重校验机制

每次发布前自动执行：

| 校验 | 内容 | 目的 |
|------|------|------|
| 1/3 身份核验 | 对比当前登录 user_id 与预期账户 | 防止发错账户 |
| 2/3 代理 IP 核验 | 检测代理可用性和出口 IP | 防止 IP 串号 |
| 3/3 发布后确认 | 访问作品管理页确认笔记存在 | 确认发布成功 |

## 配置说明

```yaml
# AI 内容生成
llm:
  api_key: "sk-ant-xxxxx"        # Anthropic API Key
  model: "claude-sonnet-4-20250514"  # 模型

# 代理 (每账户独占一个)
proxies:
  - "http://user:pass@host:port"

# 发布调度
schedule:
  max_posts_per_day: 3           # 每账户每天最多发几篇
  min_interval_minutes: 60       # 两篇之间最小间隔
  active_hours: [8, 23]          # 活跃时段

# 浏览器
browser:
  headless: false                # 扫码登录需要 false
  states_dir: "./data/states"    # 会话持久化目录
```

## 技术栈

- **浏览器自动化**: Playwright (Chromium)
- **AI 内容生成**: Anthropic Claude API
- **数据库**: SQLite
- **UI**: Rich (终端交互)
- **代理**: 每账户独立 HTTP 代理，aiohttp 检测

## 注意事项

- 首次登录需要 `headless: false`，手机 APP 扫码
- 每个账户建议绑定独立代理，避免 IP 关联
- 带货功能要求账户已开通小红书店铺且有上架商品
- 调度模式下自动模拟人类行为（随机间隔、活跃时段限制）

## 交流群

<img src="wechat-group.png" width="300" alt="微信交流群">
