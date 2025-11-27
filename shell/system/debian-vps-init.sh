#!/bin/bash

# Debian 12 VPS初始化配置脚本
# 功能：更新系统、设置时区、安装常用工具、安装oh-my-zsh、配置fail2ban

# 检查是否为root用户
if [ "$(id -u)" -ne 0 ]; then
    echo "错误：此脚本必须以root权限运行。"
    exit 1
fi

# 更新系统
echo "正在更新系统..."
apt update -y
apt upgrade -y
apt autoremove -y
echo "系统更新完成！"

# 设置时区为上海
echo "正在设置时区为Asia/Shanghai..."
timedatectl set-timezone Asia/Shanghai
echo "当前时间: $(date)"
echo "时区设置完成！"

# 安装常用工具
echo "正在安装必备软件包..."
apt install -y net-tools git curl ncdu zsh vim htop btop vnstat fail2ban

# 验证安装
for pkg in net-tools git curl ncdu zsh vim htop btop vnstat fail2ban; do
    if ! dpkg -l | grep -q $pkg; then
        echo "警告: $pkg 未成功安装"
    fi
done
echo "软件包安装完成！"

# 配置fail2ban
echo "正在配置fail2ban..."
cat > /etc/fail2ban/jail.local << 'EOL'
[sshd]
enabled = true
port = ssh
filter = sshd
# Debian 12使用systemd日志
backend = systemd
# 永久封禁设置
maxretry = 3
bantime = -1
findtime = 600
ignoreip = 127.0.0.1/8 ::1

EOL

systemctl restart fail2ban
systemctl enable fail2ban

# 检查fail2ban状态
if fail2ban-client status sshd | grep -q "Currently banned"; then
    echo "fail2ban配置成功！当前封禁IP数: $(fail2ban-client status sshd | grep 'Currently banned' | awk '{print $4}')"
else
    echo "fail2ban配置完成，但未检测到封禁信息。"
fi


# 设置zsh为默认shell（确保在安装脚本后执行）
#echo "正在将zsh设置为默认shell..."
#chsh -s $(which zsh) $USER

echo "----------------------------------------"
echo "所有配置完成！"
echo "----------------------------------------"
echo "已安装工具:"
echo "  - 网络工具: net-tools, vnstat"
echo "  - 开发工具: git, curl, vim"
echo "  - 系统监控: htop, btop"
echo "  - 安全工具: fail2ban（SSH 3次失败封锁）"
echo ""
echo "重要提示:"
echo "1. 请重新登录VPS以使zsh生效"
echo "2. 使用命令 'fail2ban-client status sshd' 查看SSH封锁状态"
echo "3. 使用 'vnstat' 查看网络流量统计"
echo "4. 使用 'htop' 或 'btop' 监控系统资源"
echo ""
echo "当前时区: $(timedatectl | grep "Time zone")"
echo "系统版本: $(lsb_release -ds)"
echo "内核版本: $(uname -r)"
echo "fail2ban状态: $(systemctl is-active fail2ban)"


# 安装oh-my-zsh
echo "正在安装oh-my-zsh..."
# 安装oh-my-zsh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
