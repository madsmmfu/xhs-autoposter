#!/bin/bash
# ═══════════════════════════════════════════════════════
#  构建 macOS .app 和 .dmg 安装包
#  用法: bash build-mac-app.sh
# ═══════════════════════════════════════════════════════

set -e

APP_NAME="小红书自动发布"
BUNDLE_ID="com.xhs.autoposter"
VERSION="1.0.0"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
APP_DIR="${BUILD_DIR}/${APP_NAME}.app"
CONTENTS="${APP_DIR}/Contents"
MACOS="${CONTENTS}/MacOS"
RESOURCES="${CONTENTS}/Resources"
DMG_DIR="${BUILD_DIR}/dmg"

echo "═══ 构建 ${APP_NAME}.app ═══"
echo ""

# ── 清理旧构建 ──
rm -rf "${BUILD_DIR}"
mkdir -p "${MACOS}" "${RESOURCES}"

# ── 1. 复制项目源码到 Resources ──
echo "[1/5] 打包项目源码..."

# Python 源码
cp "${SCRIPT_DIR}/main.py" "${RESOURCES}/"
cp "${SCRIPT_DIR}/requirements.txt" "${RESOURCES}/"

for dir in core models scheduler storage; do
    mkdir -p "${RESOURCES}/${dir}"
    cp "${SCRIPT_DIR}/${dir}"/*.py "${RESOURCES}/${dir}/"
done

# 配置模板
mkdir -p "${RESOURCES}/config"
cp "${SCRIPT_DIR}/config/settings.example.yaml" "${RESOURCES}/config/"

# 安装脚本 (复用)
cp "${SCRIPT_DIR}/install-mac.command" "${RESOURCES}/"

echo "  ✓ 源码已打包"

# ── 2. 创建应用图标 ──
echo "[2/5] 生成应用图标..."

ICONSET="${BUILD_DIR}/AppIcon.iconset"
mkdir -p "${ICONSET}"

# 用 Python 生成简单图标 (红色背景 + 白色文字 "小红书")
python3 -c "
from PIL import Image, ImageDraw, ImageFont
import sys

sizes = [16, 32, 64, 128, 256, 512, 1024]

for sz in sizes:
    img = Image.new('RGBA', (sz, sz), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 圆角矩形背景
    margin = int(sz * 0.08)
    radius = int(sz * 0.18)
    bg_rect = [margin, margin, sz - margin, sz - margin]

    # 红色背景
    draw.rounded_rectangle(bg_rect, radius=radius, fill=(255, 48, 48, 255))

    # 白色文字 '书'
    font_size = int(sz * 0.55)
    try:
        font = ImageFont.truetype('/System/Library/Fonts/PingFang.ttc', font_size)
    except Exception:
        try:
            font = ImageFont.truetype('/System/Library/Fonts/STHeiti Medium.ttc', font_size)
        except Exception:
            font = ImageFont.load_default()

    text = '书'
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (sz - tw) / 2 - bbox[0]
    ty = (sz - th) / 2 - bbox[1] - sz * 0.02
    draw.text((tx, ty), text, fill=(255, 255, 255, 255), font=font)

    # 保存各尺寸
    if sz == 16:
        img.save('${ICONSET}/icon_16x16.png')
    elif sz == 32:
        img.save('${ICONSET}/icon_16x16@2x.png')
        img.save('${ICONSET}/icon_32x32.png')
    elif sz == 64:
        img.save('${ICONSET}/icon_32x32@2x.png')
    elif sz == 128:
        img.save('${ICONSET}/icon_128x128.png')
    elif sz == 256:
        img.save('${ICONSET}/icon_128x128@2x.png')
        img.save('${ICONSET}/icon_256x256.png')
    elif sz == 512:
        img.save('${ICONSET}/icon_256x256@2x.png')
        img.save('${ICONSET}/icon_512x512.png')
    elif sz == 1024:
        img.save('${ICONSET}/icon_512x512@2x.png')
" 2>/dev/null && {
    iconutil -c icns "${ICONSET}" -o "${RESOURCES}/AppIcon.icns" 2>/dev/null
    echo "  ✓ 图标已生成"
} || {
    echo "  ⚠ 图标生成跳过 (缺少 Pillow, 使用默认图标)"
}

# ── 3. 创建启动器脚本 ──
echo "[3/5] 创建启动器..."

cat > "${MACOS}/launcher" << 'LAUNCHER_SCRIPT'
#!/bin/bash
# ═══ 小红书自动发布系统 — macOS App 启动器 ═══

# 定位 Resources 目录
RESOURCES_DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
# 用户数据目录 (放在用户 home, 不在 app 包内)
USER_DATA_DIR="$HOME/.xhs-autoposter"

# ── 首次启动: 初始化用户数据目录 ──
init_user_data() {
    mkdir -p "${USER_DATA_DIR}/config"
    mkdir -p "${USER_DATA_DIR}/data/states"

    # 复制源码 (后续更新也会覆盖)
    cp -r "${RESOURCES_DIR}/main.py" "${USER_DATA_DIR}/"
    cp -r "${RESOURCES_DIR}/requirements.txt" "${USER_DATA_DIR}/"
    for dir in core models scheduler storage; do
        rm -rf "${USER_DATA_DIR}/${dir}"
        cp -r "${RESOURCES_DIR}/${dir}" "${USER_DATA_DIR}/"
    done

    # 配置文件模板 (仅首次复制)
    if [[ ! -f "${USER_DATA_DIR}/config/settings.yaml" ]]; then
        cp "${RESOURCES_DIR}/config/settings.example.yaml" "${USER_DATA_DIR}/config/settings.example.yaml"
    fi
}

# ── 检查环境是否就绪 ──
check_env() {
    [[ -d "${USER_DATA_DIR}/venv" ]] && \
    [[ -f "${USER_DATA_DIR}/venv/bin/python" ]] && \
    "${USER_DATA_DIR}/venv/bin/python" -c "import playwright, anthropic, rich, yaml" 2>/dev/null
}

# ── 创建安装脚本 (在终端中运行) ──
create_setup_script() {
    cat > "/tmp/xhs-setup.sh" << 'SETUP_EOF'
#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

USER_DATA_DIR="$HOME/.xhs-autoposter"

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${BOLD}   小红书自动发布系统 — 首次安装${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""

# 检查 Python
PYTHON=""
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [[ "$PY_MAJOR" -ge 3 ]] && [[ "$PY_MINOR" -ge 10 ]]; then
        PYTHON="python3"
        echo -e "  ${GREEN}✓${NC} Python ${PY_VER}"
    fi
fi

if [[ -z "$PYTHON" ]]; then
    echo -e "  ${YELLOW}⚠${NC} 需要 Python >= 3.10"
    if command -v brew &>/dev/null; then
        echo -e "  ${CYAN}→${NC} 正在通过 Homebrew 安装..."
        brew install python@3.12
        PYTHON="python3"
    else
        echo -e "  ${CYAN}→${NC} 正在安装 Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        if [[ -f /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
        brew install python@3.12
        PYTHON="python3"
    fi
    echo -e "  ${GREEN}✓${NC} Python 已安装"
fi

# 创建虚拟环境
echo -e "\n  ${CYAN}→${NC} 创建虚拟环境..."
$PYTHON -m venv "${USER_DATA_DIR}/venv"
source "${USER_DATA_DIR}/venv/bin/activate"
echo -e "  ${GREEN}✓${NC} 虚拟环境已创建"

# 安装依赖
echo -e "\n  ${CYAN}→${NC} 安装 Python 依赖..."
pip install --upgrade pip -q
pip install -r "${USER_DATA_DIR}/requirements.txt" -q
echo -e "  ${GREEN}✓${NC} 依赖已安装"

# 安装 Playwright
echo -e "\n  ${CYAN}→${NC} 下载 Chromium 浏览器 (可能需要几分钟)..."
playwright install chromium
echo -e "  ${GREEN}✓${NC} Chromium 已就绪"

# 配置文件
if [[ ! -f "${USER_DATA_DIR}/config/settings.yaml" ]]; then
    cp "${USER_DATA_DIR}/config/settings.example.yaml" "${USER_DATA_DIR}/config/settings.yaml"
    echo -e "\n  ${YELLOW}⚠${NC} 已创建配置文件: ${USER_DATA_DIR}/config/settings.yaml"
    echo -e "  ${YELLOW}⚠${NC} 请编辑填写 API Key 和代理地址"
fi

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   安装完成! 请重新打开 App 启动程序${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  配置文件: ${BOLD}${USER_DATA_DIR}/config/settings.yaml${NC}"
echo -e "  数据目录: ${BOLD}${USER_DATA_DIR}/data/${NC}"
echo ""
read -n 1 -s -r -p "按任意键关闭..."
SETUP_EOF
    chmod +x /tmp/xhs-setup.sh
}

# ── 创建运行脚本 ──
create_run_script() {
    cat > "/tmp/xhs-run.sh" << 'RUN_EOF'
#!/bin/bash
USER_DATA_DIR="$HOME/.xhs-autoposter"

echo ""
echo -e "\033[1m═══ 小红书多账户自动发布系统 ═══\033[0m"
echo -e "\033[2m数据目录: ${USER_DATA_DIR}\033[0m"
echo ""

cd "${USER_DATA_DIR}"
source venv/bin/activate

# 检查配置
if [[ ! -f "config/settings.yaml" ]]; then
    echo -e "\033[1;31m错误: 请先编辑配置文件\033[0m"
    echo -e "路径: ${USER_DATA_DIR}/config/settings.yaml"
    echo ""
    if [[ -f "config/settings.example.yaml" ]]; then
        cp "config/settings.example.yaml" "config/settings.yaml"
        echo "已从模板创建, 请填写 API Key 和代理地址后重新启动"
        open "config/settings.yaml"
    fi
    echo ""
    read -n 1 -s -r -p "按任意键关闭..."
    exit 1
fi

python main.py

echo ""
echo -e "\033[1;33m程序已退出\033[0m"
read -n 1 -s -r -p "按任意键关闭..."
RUN_EOF
    chmod +x /tmp/xhs-run.sh
}

# ═══ 主逻辑 ═══

# 1. 初始化用户数据目录
init_user_data

# 2. 检查是否需要安装
if ! check_env; then
    # 首次运行 — 打开终端执行安装
    create_setup_script
    open -a Terminal /tmp/xhs-setup.sh
    exit 0
fi

# 3. 同步最新代码到用户目录
init_user_data

# 4. 正常启动 — 打开终端运行程序
create_run_script
open -a Terminal /tmp/xhs-run.sh
LAUNCHER_SCRIPT

chmod +x "${MACOS}/launcher"
echo "  ✓ 启动器已创建"

# ── 4. 创建 Info.plist ──
echo "[4/5] 创建 Info.plist..."

cat > "${CONTENTS}/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>${BUNDLE_ID}</string>
    <key>CFBundleVersion</key>
    <string>${VERSION}</string>
    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
    <key>NSHumanReadableCopyright</key>
    <string>小红书自动发布系统 v${VERSION}</string>
</dict>
</plist>
PLIST

echo "  ✓ Info.plist 已创建"

# ── 5. 打包 DMG ──
echo "[5/5] 打包 DMG 安装镜像..."

DMG_NAME="${APP_NAME}-v${VERSION}.dmg"
DMG_PATH="${BUILD_DIR}/${DMG_NAME}"

mkdir -p "${DMG_DIR}"
cp -r "${APP_DIR}" "${DMG_DIR}/"

# 创建 Applications 快捷方式
ln -sf /Applications "${DMG_DIR}/Applications"

# 添加说明文件
cat > "${DMG_DIR}/使用说明.txt" << 'GUIDE'
═══ 小红书自动发布系统 — 安装说明 ═══

1. 将「小红书自动发布.app」拖入「Applications」文件夹
2. 首次打开: 右键 → 打开 (绕过 macOS 安全限制)
3. 首次启动会自动安装 Python 环境和浏览器
4. 安装完成后, 编辑配置文件填写 API Key
5. 再次打开 App 即可使用

配置文件位置: ~/.xhs-autoposter/config/settings.yaml
数据存储位置: ~/.xhs-autoposter/data/

如遇问题, 请加微信群交流。
GUIDE

# 创建 DMG
hdiutil create -volname "${APP_NAME}" \
    -srcfolder "${DMG_DIR}" \
    -ov -format UDZO \
    "${DMG_PATH}" 2>/dev/null

echo "  ✓ DMG 已生成"

# ── 完成 ──
echo ""
echo "═══════════════════════════════════════════════════"
echo "  构建完成!"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  .app 路径: ${APP_DIR}"
echo "  .dmg 路径: ${DMG_PATH}"
echo ""
echo "  大小: $(du -sh "${DMG_PATH}" | awk '{print $1}')"
echo ""

# 打开构建目录
open "${BUILD_DIR}"
