#!/bin/bash
#==============================================================
#  CentOS 7 系统初始化脚本
#==============================================================
set -e

echo "========== CentOS 7 系统初始化开始 =========="

##############################################################
# 1. 关闭 SELinux
##############################################################
echo "[1/15] 关闭 SELinux..."
sed -i 's/^SELINUX=.*/SELINUX=disabled/' /etc/selinux/config
setenforce 0 || true


##############################################################
# 2. 关闭 firewalld（按需）
##############################################################
echo "[2/15] 关闭 firewalld..."
systemctl stop firewalld || true
systemctl disable firewalld || true


##############################################################
# 3. 配置阿里云 yum 源
##############################################################
echo "[3/15] 替换为阿里云 yum 源..."
yum install -y wget epel-release
mkdir -p /etc/yum.repos.d/backup
mv /etc/yum.repos.d/*.repo /etc/yum.repos.d/backup/ 2>/dev/null

wget -O /etc/yum.repos.d/CentOS-Base.repo \
  http://mirrors.aliyun.com/repo/Centos-7.repo

wget -O /etc/yum.repos.d/epel.repo \
  http://mirrors.aliyun.com/repo/epel-7.repo

yum clean all && yum makecache


##############################################################
# 4. 安装常用工具
##############################################################
echo "[4/15] 安装常用工具..."
yum install -y vim wget curl lrzsz tree net-tools telnet \
  htop iftop iotop unzip zip git ntp chrony bash-completion


##############################################################
# 5. 设置系统文件描述符
##############################################################
echo "[5/15] 优化文件描述符..."
cat > /etc/security/limits.conf <<EOF
* soft nofile 655350
* hard nofile 655350
root soft nofile 655350
root hard nofile 655350
EOF

echo "ulimit -SHn 655350" >> /etc/profile


##############################################################
# 6. 内核参数优化 sysctl（企业级网络优化）
##############################################################
echo "[6/15] 优化 sysctl..."
cat > /etc/sysctl.d/99-sysctl.conf <<EOF
fs.file-max = 655350

# 网络连接优化
net.core.somaxconn = 65535
net.core.netdev_max_backlog = 65535
net.ipv4.tcp_max_syn_backlog = 8192

# TCP 参数
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 10
net.ipv4.tcp_keepalive_time = 1200

# TIME_WAIT
net.ipv4.tcp_tw_recycle = 0

# 端口范围
net.ipv4.ip_local_port_range = 1024 65000

# rmem / wmem
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
EOF

sysctl --system


##############################################################
# 7. 禁用 IPv6
##############################################################
echo "[7/15] 禁用 IPv6..."
cat >> /etc/sysctl.conf <<EOF
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
EOF
sysctl -p


##############################################################
# 8. SSH 优化
##############################################################
echo "[8/15] SSH 优化..."
sed -i 's/#UseDNS yes/UseDNS no/' /etc/ssh/sshd_config
sed -i 's/GSSAPIAuthentication yes/GSSAPIAuthentication no/' /etc/ssh/sshd_config
systemctl restart sshd


##############################################################
# 9. 时区、时间同步
##############################################################
echo "[9/15] 设置时区为 Asia/Shanghai..."
timedatectl set-timezone Asia/Shanghai

echo "启用 chronyd 时间同步..."
systemctl enable chronyd
systemctl start chronyd


##############################################################
# 10. swap 优化
##############################################################
echo "[10/15] 调整 swap..."
cat >> /etc/sysctl.conf <<EOF
vm.swappiness = 10
EOF
sysctl -p


##############################################################
# 11. 关闭不必要服务
##############################################################
echo "[11/15] 禁用无用服务..."
systemctl disable postfix --now 2>/dev/null
systemctl disable avahi-daemon --now 2>/dev/null
systemctl disable cups --now 2>/dev/null


##############################################################
# 12. 登录欢迎信息 Banner
##############################################################
echo "[12/15] 设置登录 Banner..."
cat > /etc/motd <<EOF
欢迎使用 CentOS7 服务器
未经授权禁止访问
EOF


##############################################################
# 13. Docker 机器优化（可选）
##############################################################
echo "[13/15] Docker 优化配置（跳过）..."
#mkdir -p /etc/docker
#cat > /etc/docker/daemon.json <<EOF
#{
#  "log-driver": "json-file",
#  "log-opts": {
#    "max-size": "100m",
#    "max-file": "3"
#  },
#  "exec-opts": ["native.cgroupdriver=systemd"]
#}
#EOF


##############################################################
# 14. K8s 节点内核优化（可选）
##############################################################
echo "[14/15] K8s 节点额外优化 (跳过)..."
#cat > /etc/sysctl.d/k8s.conf <<EOF
#net.ipv4.ip_forward = 1
#net.bridge.bridge-nf-call-iptables = 1
#net.bridge.bridge-nf-call-ip6tables = 1
#EOF
#sysctl --system


##############################################################
# 15. 清理系统
##############################################################
echo "[15/15] 清理系统..."
yum autoremove -y
yum clean all

echo "========== 系统优化完成，请重启系统 =========="

