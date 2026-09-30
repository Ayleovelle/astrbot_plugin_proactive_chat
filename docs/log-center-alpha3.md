# 日志中心 alpha3：嵌套补发回执修复

版本 `v1.2.6+logcenter.alpha3`。基于用户 fork 已合入 alpha2 的 `dfc8d89f2a5f64684cb46b0efd3d8a698436377d`，其文件树与原 alpha2 `41b0aa04482e0524301cc768977db1fb91fd39aa` 相同。专用分支 `fix/log-center-nested-send-20261001`，目标仍是 fork 内 `log-center-base-20261001` 的 draft PR。此次不修改 main、不向上游发 PR、不合并、不部署；继续 AGPL。

## 缺陷与修复范围

旧实现中，发送后 hook 的独立 `event.send()` 复用主段 operation/segment，后一次回执覆盖前一次：True→None 汇总成全未知；None→True 汇总成全接受。永久测试在真实 `check_and_chat → _send_proactive_message → ProactiveMessageEvent.send → after-hook` 上分别复现两个失败，外部框架/平台为假对象，无网络调用。

修复只涉及日志身份与汇总：

- 默认进入独立发送入口建立 operation；嵌套补发或工具发送建立子 operation，通过 `parent_operation_id` 和 `parent_span_id` 关联发送批次。
- `delegated_send(callback, ...)` 是日志内部的一次性、绑定目标的 continuation。主段内部 wrapper 与 event→platform/core 委托共享逻辑 segment；标记在被委托入口消费，不能被无关补发继承。
- `observed_send` 继续观察实际 API 边界，attempt 明细保持逐条追加。明确同一逻辑消息的重试保持 segment 并递增 attempt；两次失败只算一个失败段，明确失败后成功只算一个接受段。没有增加业务重试调用；已发生未知的段仍不会自动回退重发。
- run 的 receipt collector 在复制的 asyncio task context 间共享，仅共享回执/计划/父关联；执行阶段与业务状态仍为每个上下文独立的不可变值。发送批次按 operation 子树汇总，run 汇总本轮事实，不合并其他 run。

True→None 和 None→True 现在均为 `partial_success`，accepted=1、unknown=1；True→False 为 accepted=1、failed=1；True→True 为 accepted=2。False/None 不转换成成功回执。实际发送路径、历史存档和未回复业务计数保持原行为，因而日志接口接受与业务流程返回仍须分别理解。

## 子任务与唯一终态

collector 归当前 run 所有，生命周期终止于 `run.completed` 快照：

1. 子任务在终态前完成的实际回执参与本轮汇总。
2. 已开始但仍未返回的 API 在快照中按 unknown 处理，记录 `pending_attempts`，并令 `incomplete=true`。
3. 终态关闭后才返回或才开始的发送仍记录原 run/父 operation，attempt 带 `after_run_terminal=true`。关闭后的 collector 不改写已保存终态，也不把迟到事实移入下一轮。
4. 不等待、取消或重发 hook 自行创建的任务，不改变业务调度策略。因此终态不是对未来 hook 行为的预测；分享诊断时应同时检查终态后的 attempt。

`send.completed` 表示发送批次结束时的观测快照，`run.completed` 表示本轮结束快照，二者时间边界不同；迟到事实可出现在其后。每轮仍只有一个 run.completed。

## 存储、隐私与安装

SQLite schema 仍为 2，无列或迁移变化。新增字段仅位于已脱敏 JSON details；已有 alpha2 行不改写、不补算成新语义。稳定会话别名、密码/no-auth 修复、导出权限与默认正文/凭据/异常消息脱敏均沿用 alpha2；插件运行依赖不变。

沿用 [alpha2 安装与范围说明](log-center-alpha2.md)，备份后在测试实例更新 ZIP，确认版本为 alpha3。ZIP 包含预编译 admin/index.html 与 JS，不需要重新编译 JSX。回退到 alpha2 会恢复其嵌套回执缺陷；保留会话数据、日志数据库与别名密钥。原 alpha1 输入的逐文件验证清单仍随包保留，本修复没有重新下载或改写原输入。

## 验证与边界

本地 Python 3.12：原 56 项加 13 项嵌套发送永久测试，共 **69 tests / OK**。覆盖两种 True/None 顺序、True/False/True、异常/取消、内部委托单次计数、调用前 core 回退、明确失败的真实边界重试、独立工具发送、多段与多补发、递归发送、joined 子任务、迟到开始/回执、并发 run 隔离、唯一终态及脱敏。测试真实模块，模型和平台为隔离假对象。

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

上述检查通过，mypy 仅检查日志核心模块。已有永久 Playwright 回归复跑 **13 checks PASS**（1440×1000、390×844），仅预期注入的 401 网络错误。GitHub Actions 在 PR 的准确 head 上运行 Python 3.10/3.12，以该 head 的实际 CI 状态为准。测试包还应核对跟踪文件字节、解压测试、首页/17 个 JS 静态资源、AGPL LICENSE 与不含运行时 DB/key/env/cache。

未验证真实 AstrBot SDK、真实模型/平台、生产 CDN、持续高吞吐、多进程共享数据目录或真实磁盘填满/强杀。没有实际消息发送、付费调用或部署。本次没有扩大模块化重构范围。
