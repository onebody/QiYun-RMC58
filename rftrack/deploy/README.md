# RF Track 部署（S9）

两种形态，**venv + systemd 为主**（Pi 上的现实），容器为辅。共同前提：

- Python ≥3.13（Mac 上 3.14 验过），依赖钉版在 `rftrack/requirements.txt`。
- 数据库先迁移再启动：**入口统一走 `backend/serve.py`**，它先跑幂等迁移再起 uvicorn。
  直接 `uvicorn app.main:create_app` 起出来的实例没有建表，插件注册表不会同步——
  这是 S8 真机轮实测踩过的坑，别绕开入口。
- 每个场地一套实例（`RFTRACK_INSTANCE_ROLE=site`），汇聚机另起一套只读实例
  （`aggregator`，写侧一律 405）。层级与拓扑见 `docs/rf-track/` 落地方案 §5。

## 形态一：venv + systemd（主）

```bash
sudo useradd -r -d /opt/rftrack -s /usr/sbin/nologin rftrack
sudo mkdir -p /opt/rftrack/{repo,venv,data}
sudo chown -R rftrack:rftrack /opt/rftrack

# 代码（rf-track 分支）放进 /opt/rftrack/repo，然后：
cd /opt/rftrack/repo
python3 -m venv /opt/rftrack/venv
/opt/rftrack/venv/bin/pip install -r rftrack/requirements.txt

sudo cp rftrack/deploy/rftrack.env.example /opt/rftrack/rftrack.env
sudoedit /opt/rftrack/rftrack.env        # 至少确认 RFTRACK_DB_PATH
sudo cp rftrack/deploy/rftrack.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rftrack
curl -s http://127.0.0.1:9000/api/health | head
```

升级 = 换代码 + 重启：`systemctl restart rftrack`（serve.py 里的迁移是幂等的，
会按 alembic 版位自动 upgrade）。

## 形态二：Docker

```bash
docker compose -f rftrack/deploy/docker-compose.yml up -d --build
```

数据在 named volume `rftrack-data`；容器随删随起数据不动。

## 数据搬家（归档）

导出：`curl -o site-home.zip http://<host>:9000/api/export/site/1`（读侧，汇聚实例也能导）。
导入：表单字段名是 `file`，写别的会 422 `body.file` 缺字段（不是权限问题）：

```bash
curl -F "file=@site-home.zip" http://<host>:9000/api/import/site-package
```

**只写新库文件**（`<库>.imports/` 下），按 R18
不自动切换现网库——导入报告里的 `next_step` 就是人工改 `RFTRACK_DB_PATH` 重启这一步。

## 坑

- compose 项目名要固定（本文件已加 `name: rftrack`）：同一目录多份 compose 共享默认
  项目名时，`down` 会把别的环境的容器一起带走。
- 宿主机 9000 常被占（MinIO 默认就吃 9000/9001）：`RFTRACK_HOST_PORT=19000 docker compose up -d`
  换端口。别指望第二个 `-f` 文件——compose 对 `ports` 列表做**并集合并**，加一条变成同时
  发布两个端口，冲突那个照样绑失败。
- `python-multipart` 必须在 `rftrack/requirements.txt` 里：缺了它不是导入报错，而是
  **路由注册那一刻整个进程起不来**，本机装了别的项目的同名包所以测试全绿（Docker 首跑踩实）。
- systemd 单元的 `ReadWritePaths=/opt/rftrack/data` 必须真的覆盖 `RFTRACK_DB_PATH`
  所在目录，否则 ProtectSystem=strict 下写库直接 EACCES，现象是"迁移失败，拒绝启动"。
- Pi 上插着板子的 USB 串口属于 `dialout` 组：`sudo usermod -aG dialout rftrack`。
