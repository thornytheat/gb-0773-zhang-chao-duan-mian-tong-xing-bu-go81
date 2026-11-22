# 涨潮断面通行簿

内河调度员用潮尺、站点基准和船期表核对放行时间。工作台默认打开断测场次，左侧可依次查看采信、测站归一、阈值交点、全程潮窗、船期覆盖、运行留档和证据签发。

## 启动

```sh
docker compose up --build
```

访问 `http://localhost:18472`。本机也可安装 `requirements.txt` 后执行 `python -m app.main`。留档存入 `TIDE_DB` 指定的 SQLite 库，默认 `var/tide.sqlite3`。清空演示归档时先停止应用，再移除该库或 Compose 命名卷。

## 复核

1. 首页选海棠17，核对 3000 秒窗口中的 1500 秒可行、900 秒不可行、600 秒缺证。
2. 打开全程可行潮窗，查看放行起止、临界点和断面潮尺行。
3. 打开运行留档，复算并保留原归档号，再到潮窗证据签发选择取舍并签发放行凭据。
4. 只核清部分潮窗时选“先签已核清的潮窗”：凭据只写入已核清区间，缺证时段单列为未覆盖并写明“不写成可行”；等全程核完再签则选“等全程核完再签”，仍有缺证时拒绝签发。
5. 切换连续测站样例，海棠17的全部 3000 秒均可行。

`GET /api/stage/{readings,datum,crossings,windows,coverage}` 可查看样例阶段结果。对同路径执行 POST 可提交 `{"sources":{"observations":"...","stations":"...","vessels":"..."}}`。`POST /api/journal` 接受同一输入或 `{"sample":"dirty"}`。`GET /api/journal/<id>` 查归档、补正链与凭据状态，`POST /api/journal/<id>/rerun` 复算，`POST /api/journal/<id>/correct` 补正归档，`GET /api/export/<id>` 下载运行证据。

`POST /api/journal/<id>/permit` 签发放行凭据，接受 `{"vessel":"海棠17","policy":"cleared_only"|"whole_route"}`；同一补正重复签发返回原凭据（`reused: true`）。`GET /api/permit/<permit_id>` 查凭据与有效性，`GET /api/permit/<permit_id>/export` 下载凭据证据。补正或基准更新后旧凭据转为 `superseded`/`stale`，下载返回 409，旧簿与旧凭据仍可按号查阅。

字段见 [数据字典](docs/DATA-DICTIONARY.md)，规则与回归见 [深度登记](docs/DEPTH.md)。本机运行 `python -m pytest -q`。
