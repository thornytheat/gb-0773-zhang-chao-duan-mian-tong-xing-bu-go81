# 涨潮断面通行簿深度登记

- 中型。用精确阈值交点及航行偏移求全程潮窗。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 潮尺读数采信 | 查重复刊头和隔离行 | `/api/stage/readings` | `app/modules/intake/readings.py` `app/modules/intake/service.py` | `tests/test_readings.py` |
| M2 站点基准统一 | 复核孪站裁决 | `/api/stage/datum` | `app/modules/datum/resolve.py` | `tests/test_datum.py` |
| M3 水位阈值交点 | 查看来源点和交点 | `/api/stage/crossings` | `app/modules/crossings/interpolate.py` | `tests/test_crossings.py` |
| M4 全程可行潮窗 | 查断面偏移后的区间 | `/api/stage/windows` | `app/modules/passage/service.py` `app/modules/passage/state.py` | `tests/test_windows.py` |
| M5 船期覆盖勾稽 | 回查申请时点和时长差额 | `/api/stage/coverage` | `app/modules/schedule/service.py` | `tests/test_coverage.py` |
| M6 批次留档复跑 | 同源复算、补正归档及归档查阅 | `/api/journal` | `app/modules/journal/service.py` `app/modules/journal/store.py` | `tests/test_journal.py` `tests/test_permit.py` |
| M7 放行凭据签发 | 按取舍只签已核清潮窗、追溯基准与潮窗 | `/api/journal/<id>/permit` `/api/permit/<id>` | `app/modules/permit/service.py` `app/modules/proof/service.py` | `tests/test_proof.py` `tests/test_permit.py` |

| TRAP | 现象 | 朴素处理后果 | 处理 | 位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-02 | 潮尺 BOM | 站号列错认 | 消除开头 BOM | `app/modules/intake/readings.py` | `tests/test_readings.py::test_bom` |
| TRAP-04 | 分段重复表头 | 表头当读数 | 记录分段并跳过 | `app/modules/intake/readings.py` | `tests/test_readings.py::test_repeated_header` |
| TRAP-05 | 多余列 | 原件错位 | 隔离留原列数组 | `app/modules/intake/readings.py` | `tests/test_readings.py::test_wrong_width` |
| TRAP-07 | 潮尺 N/A | 以零插值 | 隔离并保留原值 | `app/modules/intake/service.py` | `tests/test_readings.py::test_sentinel` |
| TRAP-10 | 三分之一秒交点 | 临界比较漂移 | 有理数插值 | `app/modules/crossings/interpolate.py` | `tests/test_crossings.py::test_exact_fraction` |
| TRAP-11 | Z 与东八区同刻 | 重复时间点 | 统一 UTC 秒 | `app/clock.py` | `tests/test_datum.py::test_timezone_repeat` |
| TRAP-14 | 同时潮尺矛盾 | 后报覆盖 | 同键冲突全部隔离 | `app/modules/datum/resolve.py` | `tests/test_datum.py::test_conflicting_station` |
| TRAP-15 | 厘米、米混用 | 水深错档 | 按单位换算后加基准 | `app/modules/datum/resolve.py` | `tests/test_datum.py::test_normalize_unit` |
| TRAP-19 | 孪站同刻 | 顺序决定选站 | 显式优先级 | `app/modules/datum/resolve.py` | `tests/test_datum.py::test_station_priority` |
| TRAP-23 | 船期右端 | 重复归属 | 排除右端 | `app/modules/passage/service.py` | `tests/test_windows.py::test_half_open_end` |
| TRAP-31 | 只核清部分潮窗，整本签 | 缺证时段写成可行 | 只把已核清潮窗写入凭据，缺证与否单列并不写成可行；全程策略下仍有缺证即拒签 | `app/modules/permit/service.py` | `tests/test_permit.py::test_partial_clearance_signs_only_cleared_windows` `::test_whole_route_refuses_uncleared` |
| TRAP-32 | 签发页没说清取舍 | 复核者不知凭据覆盖到哪 | 凭据写入 policy、policy_text 与 decision.statement，逐窗口列证据 | `app/modules/permit/service.py` | `tests/test_permit.py::test_permit_traces_datum_and_windows` |
| TRAP-33 | 测站基准更新后沿用旧潮窗 | 失效潮窗继续放行 | 每个断面记基准版本；旧凭据转 `stale` 并拒绝下载，须按当前资料重算 | `app/modules/journal/store.py` `app/modules/permit/service.py` | `tests/test_permit.py::test_datum_update_invalidates_only_affected` |
| TRAP-34 | 重试同一补正重复留档 | 归档膨胀、证据歧义 | 补正按输入定址，重试返回原簿；输入与旧簿相同直接返回旧簿 | `app/modules/journal/service.py` | `tests/test_permit.py::test_correction_links_without_new_retry_archive` `::test_same_inputs_correction_keeps_original` |
| TRAP-35 | 补正后旧凭据仍能签 | 两份互相矛盾的放行凭据 | 补正链定位末端；下游结论变化的旧凭据立即失效 | `app/modules/permit/service.py` | `tests/test_permit.py::test_correction_supersedes_old_permit` |
| TRAP-36 | 证据追不到基准和潮窗 | 无法复核凭据来自哪版基准 | 凭据和运行证据均附逐断面指纹、版本号与潮窗区间 | `app/modules/proof/service.py` | `tests/test_permit.py::test_export_run_carries_datum_trace` |
| TRAP-37 | 补正或换基准连带停掉无关船期 | 可以照签的船期一起被卡 | 按船期核潮窗结论指纹，只失效实际受影响的船期；其余标 `partial` 照常可签 | `app/modules/permit/service.py` | `tests/test_permit.py::test_unaffected_vessel_still_signable` |
| TRAP-38 | 补正后只剩缺证仍按旧策略签 | 把缺证当已核清 | 补正后无核清达标潮窗即拒签（`NO_CLEARED_WINDOW`） | `app/modules/permit/service.py` | `tests/test_permit.py::test_unaffected_vessel_still_signable` |

海棠17窗口 3000 秒 = 1500 可行 + 900 不可行 + 600 缺证。潮尺接收 20 条数据，18 条格式采信，11 条进入断面判定，2 条格式隔离、7 条业务隔离。重复表头及空行单计。以分钟级实测样例演示，不覆盖未带偏移的当地时刻、曲线外推和实时设备连接。
