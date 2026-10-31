"""生产入口（S9）：先迁移、再起服务。测试不走这里（tests/conftest.py 自己建实例）。

`lifespan` 从不建表——库没迁移就起后端，得到的是"插件注册表没同步、/api/plugins 空"这类
极难倒推回启动顺序的现象（S8 真机轮实测踩过）。所以入口把幂等迁移放在 uvicorn 之前：
migrate.py 的三场景（空库 create_all / 有数据无版本 stamp+upgrade / 正常 upgrade）本来就
是设计成可重复跑的。迁移失败直接退出，让 systemd/容器重启策略去处理，绝不带病起服务。
"""

from __future__ import annotations

import sys

import uvicorn

import migrate
from app.config import Settings
from app.main import create_app


def main() -> None:
    settings = Settings.from_env()
    result = migrate.run_migrations(settings.db_path)
    if not result.ok:
        print(f"迁移失败，拒绝启动：{result.error}", file=sys.stderr)
        sys.exit(1)
    uvicorn.run(
        create_app(settings),
        host=settings.bind,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
