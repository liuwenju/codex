#!/bin/bash

# ==================================================
# LVM 严格初始化挂载脚本 (支持 ext4 / xfs 选择)
# 环境严格校验 -> 确认 -> 线性执行
# ==================================================

set -e

DISK="/dev/sdb"
VG_NAME="vg_data"
LV_NAME="lv_data"
MOUNT_POINT="/data"
LV_PATH="/dev/${VG_NAME}/${LV_NAME}"

# 1. 是否 root？ -> 否→退出
if [ "$EUID" -ne 0 ]; then
    echo "错误: 请以 root 用户或使用 sudo 运行此脚本！"
    exit 1
fi

# 2. /dev/sdb 是否存在？ -> 否→退出
if [ ! -b "${DISK}" ]; then
    echo "错误: 磁盘 ${DISK} 不存在，脚本退出。"
    exit 1
fi

# 3. /dev/sdb 是否已有文件系统或分区？ -> 是→退出
# blkid 如果能检测到内容（如 ext4, xfs, 甚至 lvm2_member），说明这块盘不是“干净”的
if blkid "${DISK}" >/dev/null 2>&1; then
    echo "错误: 检测到磁盘 ${DISK} 上已存在文件系统、分区或数据签名！"
    echo "为了保护数据，不允许覆盖，脚本直接退出。"
    exit 1
fi

# 4. /dev/sdb 是否已经是 PV？ -> 是→退出
if pvs "${DISK}" >/dev/null 2>&1; then
    echo "错误: 检测到磁盘 ${DISK} 已经是 PV (物理卷)！"
    echo "为了保护现有 LVM 配置，不允许覆盖，脚本直接退出。"
    exit 1
fi

# 额外防呆：检查 VG 或挂载点是否冲突（确保后续创建不会因重名报错）
if vgs "${VG_NAME}" >/dev/null 2>&1; then
    echo "错误: VG 组名 [${VG_NAME}] 在系统中已存在，请修改脚本中的 VG_NAME！"
    exit 1
fi
if mount | grep -q "[[:space:]]${MOUNT_POINT}[[:space:]]"; then
    echo "错误: 挂载点 ${MOUNT_POINT} 当前已有设备挂载，脚本退出！"
    exit 1
fi

# 5. 文件系统选择 (保留原有的完整提示信息)
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

read -p "请输入序号 [1 或 2，按 Ctrl+C 取消]: " FS_CHOICE
case $FS_CHOICE in
    1)
        FS_TYPE="xfs"
        MKFS_CMD="mkfs.xfs -f"
        ;;
    2)
        FS_TYPE="ext4"
        MKFS_CMD="mkfs.ext4 -F"
        ;;
    *)
        echo "错误: 无效输入，脚本退出。"
        exit 1
        ;;
esac

# 6. 用户确认(YES)
echo "=================================================="
echo "危险警告: 即将对全新磁盘 ${DISK} 创建 LVM 并格式化为 ${FS_TYPE}！"
echo "在此之后，操作将无法撤销。"
read -p "请输入大写的 YES 以确认执行 (其他输入将取消): " CONFIRM
if [ "${CONFIRM}" != "YES" ]; then
    echo "操作已取消，脚本退出。"
    exit 0
fi
echo "=================================================="

# 7. 开始线性执行 (pvcreate -> vgcreate -> lvcreate -> mkfs -> mkdir -> fstab -> mount)

echo "[1/7] 创建 PV..."
pvcreate "${DISK}"

echo "[2/7] 创建 VG..."
vgcreate "${VG_NAME}" "${DISK}"

echo "[3/7] 创建 LV..."
lvcreate -l 100%FREE -n "${LV_NAME}" "${VG_NAME}"

echo "[4/7] 格式化为 ${FS_TYPE}..."
${MKFS_CMD} "${LV_PATH}"

echo "[5/7] 创建挂载点..."
mkdir -p "${MOUNT_POINT}"

echo "[6/7] 写入 /etc/fstab..."
UUID=$(blkid -s UUID -o value "${LV_PATH}")
if [ -z "${UUID}" ]; then
    echo "严重错误: 无法获取新格式化分区的 UUID，已中止修改 fstab！"
    exit 1
fi

FSTAB_BAK="/etc/fstab.bak_$(date +%F_%H-%M-%S)"
cp /etc/fstab "${FSTAB_BAK}"
echo "UUID=${UUID} ${MOUNT_POINT} ${FS_TYPE} defaults 0 0" >> /etc/fstab
echo "      (已备份 fstab 至 ${FSTAB_BAK})"

echo "[7/7] 执行挂载..."
mount -a

# 8. 完成
echo "=================================================="
echo "               LVM 配置及挂载完成                 "
echo "=================================================="
lsblk "${DISK}"
echo "--------------------------------------------------"
df -hT | grep -E "${MOUNT_POINT}|Filesystem" || true
