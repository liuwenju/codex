#!/bin/bash

# IP 列表
IP_LIST=(
  "8.8.8.8"
  "114.114.114.114"
  "1.1.1.1"
)

# Server酱 SCKEY
SCKEY="SCTxxxxxxxxxxxxxx"

# 结果临时变量
RESULT=""

for IP in "${IP_LIST[@]}"; do
    if ping -c 2 -W 1 $IP >/dev/null 2>&1; then
        RESULT+="[OK] $IP 在线\n"
    else
        RESULT+="[FAIL] $IP 不可达 ❌\n"
    fi
done

# 发送到 ServerChan
curl -s \
  -X POST \
  "https://sctapi.ftqq.com/${SCKEY}.send" \
  -d "title=Ping 结果通知" \
  -d "desp=${RESULT}"

