# RF Track 容器镜像（S9 部署物，次形态；主形态是 venv+systemd，见 README.md）。
# 构建上下文必须是仓库根：docker build -f rftrack/deploy/Dockerfile .
FROM python:3.13-slim

WORKDIR /app/rftrack/backend
COPY rftrack/requirements.txt /app/rftrack/requirements.txt
RUN pip install --no-cache-dir -r /app/rftrack/requirements.txt

COPY rftrack/backend /app/rftrack/backend
COPY rftrack/frontend /app/rftrack/frontend

# 数据只进卷：镜像层永远无状态。compose 里挂 /data。
ENV RFTRACK_DB_PATH=/data/rftrack.sqlite3 \
    RFTRACK_BIND=0.0.0.0 \
    RFTRACK_PORT=9000
VOLUME /data
EXPOSE 9000

CMD ["python", "serve.py"]
