#!/bin/bash

# 检查是否为root用户
if [ "$(id -u)" != "0" ]; then
    echo "错误：此脚本必须以root权限运行。"
    exit 1
fi

# 检查内核版本是否支持BBR（>=4.9）
KERNEL_MAJOR=$(uname -r | cut -d. -f1)
KERNEL_MINOR=$(uname -r | cut -d. -f2)
if [ "$KERNEL_MAJOR" -lt 4 ] || { [ "$KERNEL_MAJOR" -eq 4 ] && [ "$KERNEL_MINOR" -lt 9 ]; }; then
    echo "错误：当前内核版本 $(uname -r) 不支持BBR算法（需要>=4.9）。"
    exit 1
fi

# 检测当前拥塞控制算法
CURRENT_CC=$(sysctl -n net.ipv4.tcp_congestion_control)
if [[ "$CURRENT_CC" == *"bbr"* ]]; then
    echo "BBR 已启用（当前算法：$CURRENT_CC）"
    exit 0
fi

echo "检测到未启用BBR（当前算法：$CURRENT_CC），正在启用..."

# 配置系统参数
{
    echo "# BBR 配置（由脚本自动添加）"
    echo "net.core.default_qdisc = fq"
    echo "net.ipv4.tcp_congestion_control = bbr"
    echo "net.ipv4.tcp_fastopen = 3" 
} >> /etc/sysctl.conf

# 应用配置
sysctl -p >/dev/null 2>&1

# 验证是否启用成功
NEW_CC=$(sysctl -n net.ipv4.tcp_congestion_control)
if [[ "$NEW_CC" == *"bbr"* ]]; then
    echo "成功启用BBR（当前算法：$NEW_CC）"
else
    echo "启用失败！请手动检查："
    echo "1. 确保内核版本 >=4.9"
    echo "2. 尝试执行：sysctl -w net.ipv4.tcp_congestion_control=bbr"
    exit 1
fi
