#!/usr/bin/env bash

read -p "请输入自定义的端口:" port
path="/root/hysteriax"

if [ ! -d "$path" ]; then
  mkdir $path && cd $path
else
    echo "文件夹已经存在,10s后自动覆盖文件夹内配置,按CTRL+C停止"
    sleep 10
    cd $path
fi

domain=$(openssl rand -hex 8)
password=$(openssl rand -hex 16)
obfs=$(openssl rand -hex 6)
ip=$(curl ifconfig.io)


# 生成CAkey
echo "生成CAkey"
openssl genrsa -out hysteria.ca.key 2048
# 生成CA证书
echo "生成CA证书"
openssl req -new -x509 -days 3650 -key hysteria.ca.key -subj "/C=CN/ST=GD/L=SZ/O=Hysteria, Inc./CN=Hysteria Root CA" -out hysteria.ca.crt

openssl req -newkey rsa:2048 -nodes -keyout hysteria.server.key -subj "/C=CN/ST=GD/L=SZ/O=Hysteria, Inc./CN=*.${domain}.com" -out hysteria.server.csr
# 签发服务端用的证书
echo "签发服务端用的证书"
openssl x509 -req -extfile <(printf "subjectAltName=DNS:${domain}.com,DNS:www.${domain}.com") -days 3650 -in hysteria.server.csr -CA hysteria.ca.crt -CAkey hysteria.ca.key -CAcreateserial -out hysteria.server.crt

echo "获取指纹"

finger=$(openssl x509 -noout -fingerprint -sha256 -in ${path}/hysteria.server.crt|cut -d "=" -f2)

cat > ./client.yaml <<EOF
server: ${ip}:${port}

auth: ${password}

bandwidth:
  up: 60 mbps
  down: 360 mbps

socks5:
  listen: 127.0.0.1:7890

tls:
  insecure: true
  pinSHA256: ${finger}
EOF

echo "========================================================================"
echo "=                                                                      ="
echo "= client.yaml文件生成成功,默认忽略证书安全加指纹验证,务必导入到客户端! ="
echo "=                                                                      ="
echo "========================================================================"

cat > ./hysteria.yaml <<EOF
listen: :${port} #监听端口

#使用CA证书
#acme:
#  domains:
#    - www.xxx.com #你的域名，需要先解析到服务器ip
#  email: test@sharklasers.com

#使用自签证书
tls:
  cert: ${path}/hysteria.server.crt 
  key: ${path}/hysteria.server.key

auth:
  type: password
  password: ${password}

masquerade:
  type: proxy
  proxy:
    url: https://www.coursera.org #伪装网址
    rewriteHost: true
EOF

echo "hysteria.yaml文件生成成功"


cat > ./docker-compose.yaml <<EOF
version: '3.9'
services:
  hysteria:
    image: tobyxdd/hysteria:latest
    container_name: hysteria-v2
    environment:
      - TZ=Asia/Shanghai
    restart: always
    network_mode: "host"
    volumes:
      - ${path}/hysteria.yaml:/etc/hysteria.yaml
      - ${path}/hysteria.server.crt:${path}/hysteria.server.crt
      - ${path}/hysteria.server.key:${path}/hysteria.server.key
    command: ["server", "--config", "/etc/hysteria.yaml"]
EOF

echo "docker-compse.yaml文件生成成功,别忘记启动"
