#!/bin/bash

# ==================================================
# LVM 自动挂载脚本 (支持 ext4 / xfs 选择)
# Device : /dev/sdb
# VG     : vg_data
# LV     : lv_data
# Mount  : /data
# ==================================================

# set -e 确保任何一步出错立即停止运行
set -e

DISK="/dev/sdb"
VG_NAME="vg_data"
LV_NAME="lv_data"
MOUNT_POINT="/data"
LV_PATH="/dev/${VG_NAME}/${LV_NAME}"

# 1. 检查是否为 root 用户运行
if [ "$EUID" -ne 0 ]; then
    echo "错误: 请以 root 用户或使用 sudo 运行此脚本！"
    exit 1
fi

echo "=================================================="
echo "          请选择要格式化的文件系统类型            "
echo "=================================================="
echo "【1】 XFS  (推荐)"
echo "    优点: 高性能，特别适合大文件、高并发和海量数据；"
echo "          默认不保留管理员空间 (100% 可用)。"
echo "    缺点: 仅支持扩容，不支持缩小 (Shrink)。"
echo "--------------------------------------------------"
echo "【2】 EXT4 (传统)"
echo "    优点: 极其成熟稳定，兼容性好；"
echo "          支持扩容，也支持缩小文件系统。"
echo "    缺点: 默认会保留 5% 的空间给 root 用户以防占满系统。"
echo "=================================================="

# 读取用户输入
read -p "请输入序号 [1 或 2，按 Ctrl+C 取消]: " FS_CHOICE

case $FS_CHOICE in
    1)
        FS_TYPE="xfs"
        MKFS_CMD="mkfs.xfs -f" # -f 强制覆盖旧分区表
        ;;
    2)
        FS_TYPE="ext4"
        MKFS_CMD="mkfs.ext4 -F" # -F 强制格式化
        ;;
    *)
        echo "错误: 无效输入，脚本退出。"
        exit 1
        ;;
esac

echo ""
echo "========== 开始配置 LVM，文件系统: ${FS_TYPE} =========="

# 2. 检查磁盘是否存在
if [ ! -b "${DISK}" ]; then
    echo "错误: 磁盘 ${DISK} 不存在，请检查盘符"
    exit 1
fi

# 3. 检查并创建 PV (精确检查)
if pvs "${DISK}" >/dev/null 2>&1; then
    echo "[INFO] ${DISK} 已经是 PV"
else
    echo "创建 PV..."
    pvcreate "${DISK}"
fi

# 4. 检查并创建 VG (精确检查)
if vgs "${VG_NAME}" >/dev/null 2>&1; then
    echo "[INFO] VG ${VG_NAME} 已存在"
else
    echo "创建 VG..."
    vgcreate "${VG_NAME}" "${DISK}"
fi

# 5. 检查并创建 LV (精确检查)
if lvs "${LV_PATH}" >/dev/null 2>&1; then
    echo "[INFO] LV ${LV_NAME} 已存在"
else
    echo "创建 LV..."
    lvcreate -l 100%FREE -n "${LV_NAME}" "${VG_NAME}"
fi

# 6. 格式化文件系统
if blkid "${LV_PATH}" | grep -q "${FS_TYPE}"; then
    echo "[INFO] ${LV_PATH} 已是 ${FS_TYPE} 格式，跳过格式化"
else
    echo "正在格式化为 ${FS_TYPE} ..."
    ${MKFS_CMD} "${LV_PATH}"
fi

# 7. 创建挂载点
if [ ! -d "${MOUNT_POINT}" ]; then
    mkdir -p "${MOUNT_POINT}"
fi

# 8. 获取 UUID 并进行空值校验
UUID=$(blkid -s UUID -o value "${LV_PATH}")
if [ -z "${UUID}" ]; then
    echo "错误: 无法获取 ${LV_PATH} 的 UUID！"
    exit 1
fi

# 9. 备份 fstab
FSTAB_BAK="/etc/fstab.bak_$(date +%F_%H-%M-%S)"
cp /etc/fstab "${FSTAB_BAK}"
echo "[INFO] 已备份 /etc/fstab 到 ${FSTAB_BAK}"

# 10. 安全写入 fstab
if grep -q "${UUID}" /etc/fstab; then
    echo "[INFO] fstab 中已存在该设备的配置"
elif grep -q "[[:space:]]${MOUNT_POINT}[[:space:]]" /etc/fstab; then
    echo "错误: fstab 中已存在挂载点 ${MOUNT_POINT} 的其他设备配置，请手动检查！"
    exit 1
else
    echo "写入 /etc/fstab ..."
    echo "UUID=${UUID} ${MOUNT_POINT} ${FS_TYPE} defaults 0 0" >> /etc/fstab
fi

# 11. 挂载
echo "执行挂载..."
mount -a

echo "========== 挂载完成 =========="

# 12. 显示结果 (结尾加 || true 防止 grep 找不到导致 set -e 退出)
lsblk "${DISK}"
echo "--------------------------------"
df -hT | grep -E "${MOUNT_POINT}|Filesystem" || true
