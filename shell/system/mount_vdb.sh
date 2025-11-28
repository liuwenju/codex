#!/bin/bash
# 自动检测 /dev/vdb 是否已格式化，没有则自动格式化并挂载到 /opt/app

DISK="/dev/vdb"
MOUNT_POINT="/opt/app"

echo ">>> 检查磁盘是否存在: $DISK"
if [ ! -b $DISK ]; then
    echo "错误：磁盘 $DISK 不存在！"
    exit 1
fi

echo ">>> 检查磁盘是否已格式化..."
FS_TYPE=$(blkid -o value -s TYPE $DISK)

if [ -n "$FS_TYPE" ]; then
    echo ">>> 检测到已有文件系统: $FS_TYPE"
    echo ">>> 不进行格式化操作。"
else
    echo ">>> 未检测到文件系统，准备格式化为 ext4 ..."
    mkfs.ext4 -F $DISK
fi

echo ">>> 创建挂载目录: $MOUNT_POINT"
mkdir -p $MOUNT_POINT

echo ">>> 获取 UUID ..."
UUID=$(blkid -s UUID -o value $DISK)

# 确保 fstab 中不存在重复条目
grep -q "$UUID" /etc/fstab
if [ $? -ne 0 ]; then
    echo ">>> 写入 /etc/fstab ..."
    echo "UUID=$UUID  $MOUNT_POINT  ext4  defaults  0 0" >> /etc/fstab
else
    echo ">>> fstab 中已存在该磁盘 UUID，跳过写入。"
fi

echo ">>> 挂载所有 fstab 项 ..."
mount -a

echo ">>> 完成！当前挂载情况："
df -h | grep $MOUNT_POINT

