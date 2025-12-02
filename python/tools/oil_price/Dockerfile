# 使用 2025 年企业最常用的轻量 Python 基础镜像
FROM python:3.11-slim

WORKDIR /app

# 加速国内 pip & 安装依赖
COPY requirements.txt .
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip install --no-cache-dir -r requirements.txt

COPY get_price.py .

# 设置环境变量默认值
ENV SERVERCHAN_SENDKEY="your_key"

CMD ["python", "get_price.py"]

