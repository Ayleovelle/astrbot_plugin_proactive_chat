# 详细日志中心 alpha2：范围与验证

> 本文保留 alpha2 的历史范围。嵌套补发回执覆盖的 alpha3 修复与异步终态边界见 [alpha3 说明](log-center-alpha3.md)。

版本：`v1.2.6+logcenter.alpha2`。这是个人 fork 内的测试版本，继续沿用 AGPL-3.0。插件基础为上游 `635fc18eeb84e3c7b58661425359244552286835`；本次不更新 fork 的原有 main，也不合并或部署。

## 输入与来源

- 设计：Library 的《主动聊天插件详细日志增量设计.docx》，`libfile_a98ed97e4b588191b580176cb7df14ba`，完整读取 10 页。
- 原 alpha1 ZIP：`libfile_4ed104e2b4e08191a251f9a413d76ca9`，声明 SHA256 为 `0022607bffbe72b7bbf803641fcd1196c34e57bb887838744c5726df79511394`。本环境的 Library ZIP 下载失败，没有拿到 ZIP 字节，因此没有验证该整包哈希。
- 使用获授权的精确源文本 `proactive-chat-alpha1-exact-source-transfer.txt`，`libfile_8f0f1860dd68819187c6bf3f8eb03f44`，完整分页读取。依据每个记录的原始 bytes/SHA256 重构，不依据概要重写代码。42 个记录对应 41 个不同路径：测试文件的 ZIP 记录与保留工作区记录相同。所有记录的字节数和 SHA256 均匹配，记录清单见 [alpha1-source-verification.json](alpha1-source-verification.json)。`admin/index.html` 使用包内版本。
- 恢复结果独立提交于 `6e474aaee1a11de04712b6e0bffe682db810b2f3`。清单描述的是此提交按仓库 checkout 规则得到的 alpha1 原始字节：`run_ruff.bat` 沿用原 `.gitattributes` 的 CRLF，Git blob 正常存为 LF；其余记录直接匹配 blob。随后 alpha2 修改不应再与 alpha1 哈希相等。

## 本次实现

沿用 alpha1 的日志门面、关联上下文、有界队列、单 SQLite 写入线程和已有 Web UI，未另造日志系统，也没有新增 Python 运行依赖。

每轮任务以 `run.completed` 为唯一终态。记录执行结果、发送结果、已接受/明确失败/未知/尚未尝试的分段数，及存档、计数、调度、取消、错误关联和完整性。`True` 只表示接口明确接受，不表示终端收到或已读。纯模型耗时来自真正的 `provider.text_chat` 调用边界，`context.prepared` 单独记录准备耗时；旧 `generation_finished` 仍表示整体生成流程，不能拿它当纯模型耗时。

发送日志保留 operation、attempt、segment 和实际调用证据：

| 真实证据 | 日志结论与行为 |
| --- | --- |
| API 返回 True | accepted；不声称已送达或已读 |
| API 返回 False | explicit_failure；不凭外层流程 True 改写为接受 |
| API 返回 None，或开始调用后异常/超时 | delivery_unknown；不自动回退重发该段 |
| 会话对象构造失败、路由不可用，且尚未开始 API 调用 | 保留原有合理 core 回退；不伪造一次平台发送 |
| 部分段接受，另有失败、未知或尚未尝试段 | partial_success；未知数另列 |
| 在调用中取消 | 当前段未知，已接受段保留，CancelledError 继续传播 |
| 在两个分段之间取消 | 保留已接受段，剩余计划段标记尚未尝试，不伪造未知回执 |

上游某些发送 helper 即使底层返回 False 仍返回流程 True。本次保留该既有业务记账路径，同时明确记录底层 explicit_failure，测试同时检查接口证据与原计数行为。异常补偿调度表示下一轮任务；不会把它写成同一次发送的自动重试成功。观察到的取消与取消请求分别记录。异常使用安全类别、错误 ID、因果链和受控调用位置，重复捕获关联同一错误，不重复暴露原始堆栈消息。

INFO 继续记录触发条件、阈值、免打扰判断、状态前后和下次调度；DEBUG 增加诊断。会话使用安装内稳定 HMAC 别名，密钥为本地 `log_center.key`（新建权限 0600），重启后仍能关联。默认不保存凭据、prompt、用户正文、生成正文、模型完整响应、原始异常消息、动态日志参数或局部变量；本插件 Web 日志与 AstrBot 控制台输出共用脱敏门面，不接管其他插件或 AstrBot 全局日志。

队列同时限制 1000 条与 8 MiB；终态、错误和状态优先，有压力时明确计入丢失。健康信息包含写入线程、排队字节、当前启动丢弃、持久累计丢弃、丢失时间窗、保留清理数、最近提交和退出 drain 超时。存储故障不阻断聊天。累计丢失在重启后仍可见，但磁盘无法写入时尚未落盘的健康计数仍可能因进程崩溃丢失。

查询使用固定 `snapshot_max_id`，新增精确事件、执行结果、错误、operation、提供者与模型筛选。任务链路读取全部当前保留事件，按时间顺序显示，不限于当前 50 条列表。导出由服务端执行，固定快照、最多 1000 条，明确标注截断、范围与保留变化；会话别名在每个导出文件内重新随机化，不附映射。日志 API 全部使用原密码鉴权并返回 no-store，保留 alpha1 的 no-auth 哨兵修复。

## 数据升级与回退

SQLite schema 2 在旧列上新增版本、事件、观察时间和启动 ID；不删除 `session_data.json`，旧条目标为 legacy/incomplete，读取与导出时隐藏旧会话标识。旧库中已经存在的原始会话 ID 不会被本次迁移物理擦除，随原保留策略清理。新事件落盘使用别名。发现更高版本 schema 时停止采集并报健康错误，不重建数据库。

恢复时，对存在启动记录而无终态的旧 schema 2 任务写入 `interrupted_or_unknown`，不会推断为成功或已取消。它表示中断或日志缺失，不能证明实际发送情况。

安装前备份插件、配置和数据目录，沿用 alpha1 的 ZIP 安装方式；版本应显示 alpha2。回退先停用插件，再恢复备份代码。alpha1 可读取保留的旧列，但不能提供 alpha2 的回执与终态语义；不要删除原会话数据或稳定别名密钥。新日志库可保留供后续诊断。

## 永久测试与执行方法

Python 测试使用真实聊天执行流、模型调用封装、发送 helper、分段循环和 WebAdminServer，框架与外部模型/平台是隔离假对象。请在独立测试进程运行，勿在运行中的 AstrBot 进程 import 测试文件。

```sh
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
mypy --python-version 3.10 --ignore-missing-imports --follow-imports skip --check-untyped-defs core/log_center.py
python -m compileall -q .
node --check admin/js/views/LogsView.js
node --check tests/log_center.browser.cjs
git diff --check
```

本环境 Python 3.12.14 实跑 **56 项测试通过**。Ruff 0.14.2 的 lint/格式、日志核心模块的 mypy 1.18.2 检查、Python 编译、上述 JS 语法和 diff 检查通过。类型检查仅覆盖日志核心模块，不声称完成整个 AstrBot 插件的类型检查。另有 GitHub Actions 在 Python 3.10/3.12 运行永久回归，结果以 PR 对应准确提交的 Actions 状态为准。

主要覆盖：完整成功/全失败/部分成功/未知、模型纯耗时、401/429/超时/空响应、前后钩子 stop/error、缺少 provider、并发关联、调用前回退与调用后不重发、三种取消时点、存档与计数、补偿调度、队列字节/优先级/丢弃、保留上限、持久丢失、同进程 writer 冲突、模拟 drain 超时、原 alpha1 数据升级、未来 schema 拒绝、中断恢复、真实 SQLite 排他锁和恢复、SQLITE_FULL 写入故障注入、磁盘字节及控制台/查询/导出隐私、鉴权与 no-auth/过期 token、固定快照及跨页完整链路。

原测试中的四项旧断言逐条调整而未删除覆盖：终态名称按唯一 run 契约更新；并发会话断言改用稳定别名；平台未知返回和调用后超时不再期望 core 重发。新增明确未开始调用仍可回退的正例，以及 False/None/异常矩阵和未泄露正文的反例。

浏览器测试使用永久 `tests/browser_fixture.py`，加载真正的 LogsView、CSS 和日志 API，仅数据与 React/MUI 静态依赖为本地测试夹具。需准备 React/ReactDOM 18.3.1、MUI 5.16.14 的 UMD 文件，以及 Playwright/Chromium：

```sh
PROACTIVE_QA_NODE_MODULES=/path/to/node_modules python tests/browser_fixture.py
# 第二个终端；Playwright 需能被 Node require 找到
PROACTIVE_QA_OUTPUT=/tmp/proactive-log-qa node tests/log_center.browser.cjs
```

本环境 `/usr/bin/chromium` 实跑 **13 项浏览器检查通过**，视口 1440×1000 与 390×844：首屏与截图、无框架错误、分页、详情关闭重开、161 条完整链路、快照导出、精确筛选、空结果、401 清除旧数据与恢复、移动端无水平溢出。刻意注入 401 会产生一条预期网络错误，其余控制台与 pageerror 检查通过。截图是局部 UI 验证，不是整个 AstrBot 管理端的生产验收。

## 未验证与后续范围

没有调用真实模型、消息平台、群发或部署；未实机验证 AstrBot SDK/适配器、生产 CDN 与真实终端回执。没有执行 10 分钟 100 events/s、500 events/s 突发、50000 条性能基准；没有真实填满物理磁盘或强杀进程测试，磁盘满使用 SQL 写入故障注入，中断恢复使用缺失终态数据夹具。drain 超时测试是控制对象模拟，并非真实卡死线程。

本次 P0 未扩展重启后的完整调度生命周期与持久 previous-run 关系，也未为其他插件、遥测、邮件/平台通知添加采集或导出。原有业务遥测配置与代码未改动；新增日志不自动上传第三方。多进程同时使用同一插件数据目录不在本次支持范围内，同进程重复 writer 已阻止。保留、关闭 DEBUG、采集失败、丢弃都可造成证据缺失，不能把缺失当作业务动作未发生。

安装与手动测试会真实发消息的动作仍需由使用者在测试 AstrBot 中自行执行；本次开发只提供测试包和 fork 内 draft PR。
