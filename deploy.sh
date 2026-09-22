#!/bin/bash

#================================================================
# Xiaoyo Bot 一键部署脚本 (Debian 13)
#================================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   Xiaoyo Bot 一键部署脚本${NC}"
echo -e "${GREEN}========================================${NC}"

#----------------------------------------------------------------
# 1. 检查 root 权限
#----------------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}请使用 root 用户运行此脚本:${NC}"
    echo -e "  sudo bash deploy.sh"
    exit 1
fi

#----------------------------------------------------------------
# 2. 更新系统
#----------------------------------------------------------------
echo -e "${YELLOW}[1/8] 更新系统...${NC}"
apt update && apt upgrade -y

#----------------------------------------------------------------
# 3. 安装基础工具
#----------------------------------------------------------------
echo -e "${YELLOW}[2/8] 安装基础工具...${NC}"
apt install -y \
    curl \
    wget \
    git \
    python3 \
    python3-venv \
    python3-pip \
    sudo \
    ca-certificates \
    gnupg \
    lsb-release

#----------------------------------------------------------------
# 4. 安装 Docker (用于运行 NapCat)
#----------------------------------------------------------------
echo -e "${YELLOW}[3/8] 安装 Docker...${NC}"

# 检查 Docker 是否已安装
if ! command -v docker &> /dev/null; then
    # 添加 Docker GPG 密钥
    mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    
    # 添加 Docker 仓库
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # 安装 Docker
    apt update
    apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    
    # 启动 Docker
    systemctl start docker
    systemctl enable docker
    
    echo -e "${GREEN}Docker 安装完成${NC}"
else
    echo -e "${GREEN}Docker 已安装${NC}"
fi

#----------------------------------------------------------------
# 5. 创建用户 (如果不存在)
#----------------------------------------------------------------
echo -e "${YELLOW}[4/8] 创建运行用户...${NC}"
if ! id "xiaoyo" &>/dev/null; then
    useradd -m -s /bin/bash xiaoyo
    usermod -aG docker xiaoyo
    usermod -aG sudo xiaoyo
    echo "xiaoyo:xiaoyo" | chpasswd
    echo -e "${GREEN}用户 xiaoyo 已创建，密码: xiaoyo${NC}"
else
    echo -e "${GREEN}用户 xiaoyo 已存在${NC}"
fi

#----------------------------------------------------------------
# 6. 克隆仓库
#----------------------------------------------------------------
echo -e "${YELLOW}[5/8] 克隆仓库...${NC}"
mkdir -p /home/xiaoyo
cd /home/xiaoyo

# 检查是否已经有仓库
if [ -d "/home/xiaoyo/Xiaoyo-Bot" ]; then
    echo -e "${YELLOW}仓库已存在，是否更新? (y/n)${NC}"
    read -r update
    if [ "$update" = "y" ]; then
        cd /home/xiaoyo/Xiaoyo-Bot
        git pull
    fi
else
    echo -e "${YELLOW}请输入仓库地址（直接回车使用当前目录的文件）:${NC}"
    read -r repo_url
    
    if [ -n "$repo_url" ]; then
        git clone "$repo_url" Xiaoyo-Bot
    else
        echo -e "${RED}请手动上传代码到 /home/xiaoyo/Xiaoyo-Bot 后重新运行此脚本${NC}"
        echo -e "${YELLOW}或者提供 Git 仓库地址${NC}"
        exit 1
    fi
fi

cd /home/xiaoyo/Xiaoyo-Bot
chown -R xiaoyo:xiaoyo /home/xiaoyo

#----------------------------------------------------------------
# 7. 设置 Python 环境
#----------------------------------------------------------------
echo -e "${YELLOW}[6/8] 设置 Python 环境...${NC}"

# 检查 Python 版本
python3_version=$(python3 --version | awk '{print $2}' | cut -d. -f1)
if [ "$python3_version" -lt 3 ] || [ ! -d ".venv" ]; then
    # 创建虚拟环境
    su - xiaoyo -c "cd /home/xiaoyo/Xiaoyo-Bot && python3 -m venv .venv"
    
    # 安装依赖
    su - xiaoyo -c "cd /home/xiaoyo/Xiaoyo-Bot && source .venv/bin/activate && pip install --upgrade pip"
    su - xiaoyo -c "cd /home/xiaoyo/Xiaoyo-Bot && source .venv/bin/activate && pip install -e ."
fi

#----------------------------------------------------------------
# 8. 配置 .env 文件
#----------------------------------------------------------------
echo -e "${YELLOW}[7/8] 配置环境变量...${NC}"

if [ ! -f "/home/xiaoyo/Xiaoyo-Bot/.env" ]; then
    # 创建完整的 .env 配置
    cat > /home/xiaoyo/Xiaoyo-Bot/.env << 'EOF'
# 基础环境配置
ENVIRONMENT=prod
DRIVER=~fastapi

# OneBot 配置 (NapCat 反向 WebSocket)
ONEBOT_ACCESS_TOKEN=your_access_token_here
ONEBOT_REVERSE_WS_ENABLED=true
ONEBOT_REVERSE_WS_HOST=127.0.0.1
ONEBOT_REVERSE_WS_PORT=3001
ONEBOT_REVERSE_WS_PATH=/onebot/v11/ws/

# 命令前缀
COMMAND_START=["/", ""]

# AI接口配置 (请修改为你的 API)
AI_API_KEY=your_api_key_here
AI_API_BASE="https://open.bigmodel.cn/api/paas/v4/"
AI_MODEL="GLM-4-Flash"
EOF
    
    echo -e "${YELLOW}请编辑 .env 文件配置你的 QQ 号、Token 和 API Key:${NC}"
    echo -e "  nano /home/xiaoyo/Xiaoyo-Bot/.env"
fi



#----------------------------------------------------------------
# 9. 配置 NapCat Docker
#----------------------------------------------------------------
echo -e "${YELLOW}[8/8] 配置 NapCat Docker...${NC}"

# 创建 NapCat 配置目录
mkdir -p /home/xiaoyo/napcat/config
mkdir -p /home/xiaoyo/napcat/data

# 创建 NapCat 配置
cat > /home/xiaoyo/napcat/config/napcat.json << 'EOF'
{
  "http": {
    "enable": true,
    "host": "0.0.0.0",
    "port": 3000
  },
  "ws": {
    "enable": true,
    "host": "0.0.0.0",
    "port": 3001
  },
  "reverse_ws": {
    "enable": true,
    "urls": [
      "ws://127.0.0.1:3001/onebot/v11/ws/"
    ]
  }
}
EOF

# 创建 NapCat 启动脚本
cat > /home/xiaoyo/napcat/docker-compose.yml << 'EOF'
version: '3.8'

services:
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: napcat
    restart: unless-stopped
    ports:
      - "3000:3000"
      - "3001:3001"
    volumes:
      - ./config:/app/config
      - ./data:/app/data
    environment:
      - ACCOUNT_UIN=0
      - ACCOUNT_MODE=1
      - NapCat_Enable_WebUi=true
EOF

chown -R xiaoyo:xiaoyo /home/xiaoyo/napcat

#----------------------------------------------------------------
# 10. 创建 systemd 服务
#----------------------------------------------------------------
echo -e "${YELLOW}创建 systemd 服务...${NC}"

cat > /etc/systemd/system/xiaoyo-bot.service << 'EOF'
[Unit]
Description=Xiaoyo Bot
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=xiaoyo
WorkingDirectory=/home/xiaoyo/Xiaoyo-Bot
ExecStart=/home/xiaoyo/Xiaoyo-Bot/.venv/bin/python -m nonebot run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable xiaoyo-bot.service

#----------------------------------------------------------------
# 完成
#----------------------------------------------------------------
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   部署完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}后续步骤:${NC}"
echo -e "  1. 编辑配置文件: nano /home/xiaoyo/Xiaoyo-Bot/.env"
echo -e "  2. 启动 NapCat: cd /home/xiaoyo/napcat && docker-compose up -d"
echo -e "  3. 启动 Bot: systemctl start xiaoyo-bot"
echo -e "  4. 查看日志: journalctl -u xiaoyo-bot -f"
echo ""
echo -e "${YELLOW}NapCat WebUI 地址:${NC}"
echo -e "  http://你的服务器IP:3000"
echo ""
