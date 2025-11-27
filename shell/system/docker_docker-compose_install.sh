#!/bin/bash

# 检查是否具有root权限
if [ "$EUID" -ne 0 ]; then
  echo "请使用root运行此脚本。"
  exit 1
fi

# 检查curl是否已安装，如果没有，安装它
if ! command -v curl &> /dev/null; then
  echo "安装 curl..."
  apt update
  apt install -y curl
fi

# 更新系统
apt update
apt upgrade -y

# 安装 Docker 依赖
apt install -y apt-transport-https ca-certificates curl software-properties-common

# 添加 Docker GPG 密钥
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# 添加 Docker APT 仓库
echo "deb [signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

# 更新并安装 Docker CE
apt update
apt install -y docker-ce docker-ce-cli containerd.io


# 启动 Docker 服务
systemctl enable docker
systemctl start docker

# 下载并安装 Docker Compose
if ! command -v docker-compose &> /dev/null; then
  echo "下载并安装 Docker Compose..."
  curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
  chmod +x /usr/local/bin/docker-compose
  ln -s /usr/local/bin/docker-compose /usr/bin/docker-compose
fi

# 显示安装信息
echo "Docker已成功安装。"
docker --version
docker-compose --version
