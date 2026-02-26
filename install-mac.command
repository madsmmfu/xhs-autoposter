#!/bin/bash
# ═══════════════════════════════════════════════════
#  小红书自动发布系统 — macOS 一键安装
#  双击此文件即可自动安装所有依赖
# ═══════════════════════════════════════════════════

set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

print_banner() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}   小红书多账户自动发布系统 — macOS 一键安装${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
    echo ""
}

print_step() {
    echo -e "\n${CYAN}[步骤 $1/$TOTAL_STEPS]${NC} ${BOLD}$2${NC}"
    echo "─────────────────────────────────────────"
}

print_ok() {
    echo -e "  ${GREEN}✓${NC} $1"
}

print_warn() {
    echo -e "  ${YELLOW}⚠${NC} $1"
}

print_err() {
    echo -e "  ${RED}✗${NC} $1"
}

TOTAL_STEPS=6

# 切换到脚本所在目录 (双击 .command 时 cwd 是 HOME)
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

print_banner
echo -e "项目目录: ${BOLD}${PROJECT_DIR}${NC}"

# ── 步骤 1: 检查 Homebrew ──
print_step 1 "检查 Homebrew"

if command -v brew &>/dev/null; then
    print_ok "Homebrew 已安装: $(brew --prefix)"
else
    print_warn "未检测到 Homebrew, 正在安装..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Apple Silicon 路径
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    print_ok "Homebrew 安装完成"
fi

# ── 步骤 2: 检查 Python ──
print_step 2 "检查 Python 3"

PYTHON=""

# 优先查找 python3
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

    if [[ "$PY_MAJOR" -ge 3 ]] && [[ "$PY_MINOR" -ge 10 ]]; then
        PYTHON="python3"
        print_ok "Python ${PY_VER} 已安装: $(which python3)"
    else
        print_warn "Python ${PY_VER} 版本过低 (需要 >= 3.10)"
    fi
fi

if [[ -z "$PYTHON" ]]; then
    print_warn "正在通过 Homebrew 安装 Python 3..."
    brew install python@3.12
    PYTHON="python3"
    print_ok "Python 3 安装完成"
fi

# ── 步骤 3: 创建虚拟环境 ──
print_step 3 "创建 Python 虚拟环境"

VENV_DIR="${PROJECT_DIR}/venv"

if [[ -d "$VENV_DIR" ]]; then
    print_ok "虚拟环境已存在: ${VENV_DIR}"
else
    $PYTHON -m venv "$VENV_DIR"
    print_ok "虚拟环境已创建: ${VENV_DIR}"
fi

# 激活虚拟环境
source "${VENV_DIR}/bin/activate"
print_ok "已激活虚拟环境"

# ── 步骤 4: 安装 Python 依赖 ──
print_step 4 "安装 Python 依赖"

pip install --upgrade pip -q
pip install -r "${PROJECT_DIR}/requirements.txt" -q
print_ok "所有 Python 依赖已安装"

# ── 步骤 5: 安装 Playwright 浏览器 ──
print_step 5 "安装 Playwright Chromium 浏览器"

if python -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
    print_ok "Playwright 已安装"
else
    print_err "Playwright 导入失败, 请检查"
fi

echo "  正在下载 Chromium (首次可能需要几分钟)..."
playwright install chromium 2>&1 | tail -1
print_ok "Chromium 浏览器已就绪"

# ── 步骤 6: 初始化配置 ──
print_step 6 "初始化配置文件"

CONFIG_FILE="${PROJECT_DIR}/config/settings.yaml"

if [[ -f "$CONFIG_FILE" ]]; then
    print_ok "配置文件已存在: config/settings.yaml"
else
    cp "${PROJECT_DIR}/config/settings.example.yaml" "$CONFIG_FILE"
    print_ok "已从模板创建: config/settings.yaml"
    print_warn "请编辑 config/settings.yaml 填写 API Key 和代理"
fi

# 创建数据目录
mkdir -p "${PROJECT_DIR}/data/states"
print_ok "数据目录已就绪"

# ── 完成 ──
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   安装完成!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}下一步:${NC}"
echo ""
echo -e "  1. 编辑配置文件:"
echo -e "     ${CYAN}nano config/settings.yaml${NC}"
echo ""
echo -e "  2. 双击 ${BOLD}start-mac.command${NC} 启动程序"
echo -e "     或在终端运行:"
echo -e "     ${CYAN}cd ${PROJECT_DIR} && source venv/bin/activate && python main.py${NC}"
echo ""

read -n 1 -s -r -p "按任意键关闭..."
echo ""
