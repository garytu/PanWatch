#!/bin/bash
set -e

# 顏色輸出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 預設值
VERSION=${1:-"latest"}
IMAGE_NAME="sunxiao0721/panwatch"

echo -e "${GREEN}🚀 PanWatch 構建指令碼${NC}"
echo -e "版本: ${YELLOW}${VERSION}${NC}"
echo ""

# 檢查依賴
command -v node >/dev/null 2>&1 || { echo -e "${RED}需要 Node.js${NC}"; exit 1; }
command -v pnpm >/dev/null 2>&1 || { echo -e "${RED}需要 pnpm${NC}"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo -e "${RED}需要 Docker${NC}"; exit 1; }

# Step 1: 構建前端
echo -e "${GREEN}📦 構建前端...${NC}"
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..

# Step 2: 複製前端產物到 static 目錄
echo -e "${GREEN}📁 複製靜態檔案...${NC}"
rm -rf static
mkdir -p static
cp -r frontend/dist/* static/

# Step 3: 構建 Docker 映象（amd64 架構，適配大多數伺服器/NAS）
echo -e "${GREEN}🐳 構建 Docker 映象 (linux/amd64)...${NC}"
FULL_IMAGE="${IMAGE_NAME}:${VERSION}"

docker build --platform linux/amd64 --build-arg VERSION="${VERSION}" -t "${FULL_IMAGE}" .

# 如果版本不是 latest，也打 latest 標籤
if [ "$VERSION" != "latest" ]; then
    docker tag "${FULL_IMAGE}" "${IMAGE_NAME}:latest"
    echo -e "${GREEN}✅ 映象已構建: ${YELLOW}${FULL_IMAGE}${NC} 和 ${YELLOW}${IMAGE_NAME}:latest${NC}"
else
    echo -e "${GREEN}✅ 映象已構建: ${YELLOW}${FULL_IMAGE}${NC}"
fi

# 清理
rm -rf static

echo ""
echo -e "${GREEN}🎉 構建完成！${NC}"
echo ""
echo "執行容器:"
echo -e "  ${YELLOW}docker run -d -p 8000:8000 -v panwatch_data:/app/data ${FULL_IMAGE}${NC}"
echo ""
echo "推送映象:"
echo -e "  ${YELLOW}docker push ${FULL_IMAGE}${NC}"
if [ "$VERSION" != "latest" ]; then
    echo -e "  ${YELLOW}docker push ${IMAGE_NAME}:latest${NC}"
fi
