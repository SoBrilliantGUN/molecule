# ===== Molecules 后端镜像 =====
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 先装依赖，利用 Docker 层缓存；国内 ECS 用阿里云 pypi 源加速
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 拷贝后端源码
COPY . .

EXPOSE 8000

# 启动前先跑数据库迁移，再起 uvicorn
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
