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
cd web && npm run build && cd -          # 控制台产物不在 git 里，先在本机出包
RFTRACK_HOST_PORT=19000 \
  docker compose -f rftrack/deploy/docker-compose.yml up -d --build
curl -s http://127.0.0.1:19000/api/health | tr ',' '\n' | grep -E '"(console|workbench)"'
```

数据在 named volume `rftrack-data`；容器随删随起数据不动。

一套实例发**两个工作区**，都从同一个 origin 出来，根上 `/` 是一张入口页：

| 入口 | 谁发的 | 产物从哪来 |
| --- | --- | --- |
| `/app/` 单机控制台 | `mount_console`，目录默认 `web/dist` | **宿主机绑卷只读挂进来**（compose 里那条 `- ../../web/dist:/app/web/dist:ro`） |
| `/workbench/` 站点监测 | `mount_workbench` | `COPY rftrack/frontend` 进镜像（Vue3 CDN 无构建，跟着代码走） |

分工的理由：`web/dist` 是 node 构建产物，既不进 git，也不值得为它在镜像里塞一套 node
工具链（主形态的 Pi 更跑不动）；工作台那一边压根不需要构建，所以它跟代码同生共死。

**更新一版** = `cd web && npm run build`，然后：

- 只改了控制台 → `docker compose -f rftrack/deploy/docker-compose.yml restart`；
- 改了后端或工作台 → 同上再加 `--build`。

`restart` 那一步不能省：挂没挂上 `/app` 是**启动那一刻**看的，运行中往目录里补文件不会让
`/api/health` 的 `console` 那一节自己活过来。忘了 restart 的现象是"页面还是旧的"，
而 `/api/health` 会一直说 `mounted: true`——它只保证目录在，不保证内容新。

`web/dist` 不存在也能起：compose 的绑卷会留一个空目录，入口页与 `console.reason` 都写成
"配了目录但里面没有 index.html（前端产物还没构建进来）"，不算故障、不进 `problems`。

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
- **同源之后，控制台那两条"伴生服务"探测会打到后端的同类路由上**。`web/` 按同源约定探
  `serve.mjs` 的两条接口，而这两条路在 `/app/` 下由 rftrack 后端接走：
  - `GET /api/endpoints`（`web/src/model/devices.ts:92`，`main.ts:939` 那份 `DeviceManager(2000)`
    每 2s 一次）撞上后端的桥接路由（`app/routers/bridge.py:43`，`gateway` 必填）→ **422**；
  - `GET /api/mode` 后端没这条路由 → **404**。
  两边都只认"非 2xx 就当没有伴生服务"（`if (!r.ok) return`），所以行为是设计内的回落，不是故障：
  控制台退回 Web Serial 直连 / 模拟器，不编造设备卡。两处要说清的代价——
  ①**只要有一个控制台标签页开着**，后端日志每 2s 一条 422；看到这批 422 不要去找"哪个网关没配"。
  ②`/api/mode` 那条 404 让"桥接板子"那一整段（含端点下拉，`main.ts:641-644` 在 `r.ok` 之后才装配）
  在这套同源部署里**根本不出现**——要按网关视角看桥接的板子，走 `/workbench/` 那一边，
  或者照主形态让 `serve.mjs` 自己发 `/app/`。
- **容器里拨宿主机上的 `serve.mjs`，只能用 `host.docker.internal`，不能用宿主机的局域网 IP**。
  本机实测：网关 `base_url` 写 `http://192.168.8.156:8011` 时 `POST /api/gateways/{id}/probe`
  回 `ok:false reason:"timeout"`，换成 `http://host.docker.internal:8011` 当场
  `ok:true dial_state:"online"`（`172.17.0.1:8011` 则是 `refused`）。现象是"超时"而不是"拒绝"，
  很容易被读成"桥没起来"——桥其实活着，只是容器到不了那个地址。
  Linux 上这个域名不存在，需要显式补 `extra_hosts: ["host.docker.internal:host-gateway"]`；
  macOS/Windows 的 Docker Desktop 自带。
