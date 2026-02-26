#!/bin/bash
# ═══════════════════════════════════════════════════
#  小红书自动发布系统 — macOS 一键启动
#  双击此文件即可启动程序
# ═══════════════════════════════════════════════════

# 切换到脚本所在目录
cd "$(dirname "$0")"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${BOLD}═══ 小红书多账户自动发布系统 ═══${NC}"
echo ""

# 检查虚拟环境
if [[ ! -d "venv" ]]; then
    echo -e "${RED}错误: 未找到虚拟环境${NC}"
    echo -e "请先双击 ${BOLD}install-mac.command${NC} 进行安装"
    echo ""
    read -n 1 -s -r -p "按任意键关闭..."
    exit 1
fi

# 检查配置文件
if [[ ! -f "config/settings.yaml" ]]; then
    echo -e "${RED}错误: 未找到配置文件${NC}"
    echo -e "请先复制 config/settings.example.yaml 为 config/settings.yaml 并填写配置"
    echo ""
    read -n 1 -s -r -p "按任意键关闭..."
    exit 1
fi

# 激活虚拟环境
source venv/bin/activate

echo -e "${GREEN}环境已就绪, 启动中...${NC}"
echo ""

# 启动程序
python main.py

echo ""
echo -e "${YELLOW}程序已退出${NC}"
read -n 1 -s -r -p "按任意键关闭..."
echo ""
