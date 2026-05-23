#!/bin/bash

# ==================================================
# LVM 自动挂载脚本
# Device : /dev/sdb
# VG     : vg_data
# LV     : lv_data
# Mount  : /data
# FS     : ext4
# ==================================================

set -e

DISK="/dev/sdb"
VG_NAME="vg_data"
LV_NAME="lv_data"
MOUNT_POINT="/data"

echo "========== 开始配置 LVM =========="

# 1. 检查磁盘是否存在
if [ ! -b ${DISK} ]; then
    echo "错误: 磁盘 ${DISK} 不存在"
    exit 1
fi

# 2. 检查是否已经做过 PV
if pvs | grep -q "${DISK}"; then
    echo "${DISK} 已经是 PV"
else
    echo "创建 PV..."
    pvcreate ${DISK}
fi

# 3. 创建 VG
if vgs | grep -q "${VG_NAME}"; then
    echo "VG ${VG_NAME} 已存在"
else
    echo "创建 VG..."
    vgcreate ${VG_NAME} ${DISK}
fi

# 4. 创建 LV
if lvs | grep -q "${LV_NAME}"; then
    echo "LV ${LV_NAME} 已存在"
else
    echo "创建 LV..."
    lvcreate -l 100%FREE -n ${LV_NAME} ${VG_NAME}
fi

# 5. 格式化文件系统
LV_PATH="/dev/${VG_NAME}/${LV_NAME}"

if blkid ${LV_PATH} | grep -q "ext4"; then
    echo "${LV_PATH} 已格式化为 ext4"
else
    echo "格式化 ext4..."
    mkfs.ext4 ${LV_PATH}
fi

# 6. 创建挂载点
if [ ! -d ${MOUNT_POINT} ]; then
    mkdir -p ${MOUNT_POINT}
fi

# 7. 获取 UUID
UUID=$(blkid -s UUID -o value ${LV_PATH})

# 8. 备份 fstab
cp /etc/fstab /etc/fstab.bak_$(date +%F_%H-%M-%S)

# 9. 写入 fstab
if grep -q "${UUID}" /etc/fstab; then
    echo "fstab 已存在配置"
else
    echo "写入 /etc/fstab ..."
    echo "UUID=${UUID} ${MOUNT_POINT} ext4 defaults 0 0" >> /etc/fstab
fi

# 10. 挂载
mount -a

echo "========== 挂载完成 =========="

# 11. 显示结果
lsblk
df -h | grep ${MOUNT_POINT}


echo "=============================="
echo "LVM 配置成功,ext4默认保留5%磁盘空间!"
