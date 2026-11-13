# E07 Agent 验证与交接

> 2026-11-13 版本范围调整：本版以 E07 为交付边界，E08 新闻 RAG、真实 Embedding、Chroma 向量索引/检索及 E09 生成模型/效果融合全部延后。保留 API → RabbitMQ → Python Worker → LangGraph 的统计预测、结果回传、公示/冻结/结算和 E07 mock 调用生命周期、用量/成本接收。契约恢复为 release 1.0.2；历史 E08 计划、测试与服务证据仅作归档，不代表本版功能，也不再自动续跑。

本次回退结果与新验证证据见工作区 `docs/e07-rollback.md`。

以下验收计数与服务结论属于原 E07 运行；本次回退的源码恢复与本地验证另记，不重写历史回执或宣称重跑真实服务。


> 只读汇总。写作期间没有运行 Maven、没有连接 RabbitMQ/MySQL，也没有修改 Agent 源码、测试或已封存证据。

## 最终结论：本地调用生命周期与新闻消费通过，真实 MQ 已在 a4 与 b1 执行

- 最终本地 smoke `e07-smoke-20261106-a4` 的 Agent 全量命令 `python -m unittest discover -s tests -v` 跑出 `Ran 214 tests`、`OK`，exit 0，0 失败、0 错误、0 跳过。见 [agent.log](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/10/final-junit-override/smoke-a4/agent.log)。
- 任务 9 的定向恢复组跑了 46 项，全量 214 项同时通过。计数取自 [verify-task9.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/9/verify-task9.json) 的 `Java 4/334、Python 46/214`。
- 真实 broker 上的消费、confirm 与重投已在 2026-11-06 执行。a4 的六行 `REAL_SERVICE` 由真实 MySQL、真实 RabbitMQ 与真实 Agent CLI 产出；`restart-confirm-replay` 一行先被首轮独立复核撤回，再由 b1 在 `after_effect:2` 做真实 `SIGKILL` 补证。见 [a4 summary](../../f1ap-api/docs/e07-evidence/runs/e07-real-20261106-a4/summary.json) 与 [b1 summary](../../f1ap-api/docs/e07-evidence/runs/e07-replay-20261106-b1/summary.json)。
- F3 第三轮复核判 `confirmed`，`failure_class=none`：`tasks/11/verify-task11-r2.json` 与 `final/f3-qa-r3.json`。首轮的 `needs-fix` 保留为历史，放行范围只到六行真实场景与两项补证。
- 本文所有"通过"仍指 `TEST_FIXTURE` 数据与合成事件。没有任何真实模型、真实 Embedding 或真实新闻供应商被调用；`provider_code` 只有 `MOCK`。正式官方 LIVE 仍是 `NOT_VERIFIED`。
- 工具侧任务 10 的两处 harness 缺陷已修复，第二轮独立核验判 `confirmed`。见 [verify-task10-r2.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/10/verify-task10-r2.json)。
- F2 复审结论是 `APPROVE_WITH_FINDINGS`，两个代码 blocker 已由任务 14 关闭；计划文件 [e07-execution-plan.md](../../.omo/plans/e07-execution-plan.md) 已由 root 把任务 11 与 F3 标为 `[x]`。

## 1. 调用生命周期

生成、Embedding 与新闻 API 共用一套持久化生命周期，实现位于 `f1ap_agent/models/{calls.py,mock.py}` 与 `worker/{engine.py,models.py}`。

| 环节 | 行为 | 证据 |
| --- | --- | --- |
| 身份 | `call_key = uuid5(NAMESPACE_URL, "f1ap-e07:{run_id}:{logical_key}:{attempt_no}")`，每个实际尝试一个键 | `e07-interfaces.md` 的黄金向量；`tasks/1/fixtures.json` |
| 意图 | 先 fsync journal，再发生 provider effect | [tasks/4/calls.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/4/calls.json) |
| 身份时间 | 来自计划 `attempt.call_started_at`，不用注入时钟 | [tasks/9/done-claim.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/9/done-claim.json) 的 `E07_MOCK_IDENTITY_TIME_FROM_CLOCK` |
| 准入 | 每次尝试与退避后重新检查 `expires`、lease、LIVE deadline 与 `call_limit` | `tasks/4/calls.json`、`tasks/9/verify-task9.json` |
| 重试 | 只有确定可重试的失败才发新尝试，最多 2 次；`UNKNOWN` 不自动重试 | `tasks/4/calls.json` |
| 版本 | 每个 `(call_key, usage_revision)` 保存完整快照，支持后续修订与 pending 重发 | `tasks/4/recovery.json` |
| 恢复 | 已完成的实际尝试在六个检查点重启后都不新增 effect；已保存 envelope 只按原字节重发 | `tasks/9/recovery.json` |

旧行为保留：原来的单调用 `FakeExecutor`、零调用路径、`E06_SYNTHETIC_V1` 与 `CONTROLLED_INPUT` 研究路径不变；预测结果清单仍然只包含题目结果；旧的 `REGISTERED`、`CALL_STARTED`、`SUCCEEDED`、`FINISHED` 四态日志可以只恢复、不重做。

## 2. 新闻消费

`Task` 对 `PREDICT` 与 `NEWS_REFRESH` 显式分派。新闻任务没有 `definition` 与 `question_ids`，不能塞进十题的假字段，预测属性只在预测任务上访问。

- 目录与批次来自受控同字节目录，Embedding 调用走 MockProvider，不写向量、不采集。
- 完整落盘 usage 与 news 事件后可靠发送，ACK 语义沿用；新闻任务不生成 prediction completed 或 finished。
- CLI 增加显式的 `--mock-plan` 与 `--news-catalog`，只在 E07 fixture profile 下启用；`--existing-topology` 不 declare、不 bind。
- 不带 `--run` 时只做离线配置校验，不建连接；来源缺失或身份漂移按可恢复类型退出，不静默忽略任务。

实际观察（[tasks/5/manual-qa.log](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/5/manual-qa.log)、[tasks/5/news-events.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/5/news-events.json)）：两批新闻加一次 Embedding usage；发送前逐条核对 journal 与 pending 已持久化；确认丢失后重启重发相同 canonical bytes；同一请求重投不产生新调用或新批次；目录缺失或漂移时 provider effect 为 0；同一 Worker 仍能接受 PREDICT 并保留旧结果。

### 2.1 补价入口（任务 14）

任务 14 补上 F2 指出的两个缺口：生产 CLI 没有补价入口，修订身份泄漏夹具 run_id。

- 修订身份改为 `sha256([plan.sha256, logical_key, attempt_no]) + usage_revision`。登记侧刻意不含 run；使用时按实际 run 重建 payload 的 call_key 与 event_id。同一 rev3 对 7001、8001、9137 得到三个互不相同的身份，独立核验用 uuid5 重算一致。
- 新闻 reload 保留修订脚本绑定，不再因重载 plan 丢失 revisions。
- CLI 新增 `--mock-revisions`、`--mock-scenario` 与 `--reconcile-usage RUN_ID:CALL_KEY:REVISION`。该命令要求同时给 `--run`、`--mock-plan`、`--mock-revisions`、`--mock-scenario` 与计划绑定的 `--news-catalog`；只做一次补价，发送前先落 pending envelope，不重跑任务，不碰 finished；缺 `--run` 返回 `RECONCILE_RUN_REQUIRED` 且不建连接。
- 独立核验 `confirmed`：补价期间 provider 调用为零，finished 与既存 envelope 字节不变，定向 54 项加旧 Worker 24 项、全量 222 项均通过。见 [verify-task14.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/14/verify-task14.json)。

限制：成功路径只跑假 broker，真实 RabbitMQ 的 confirm/return 与 ACK 后补价没有观察；旧 journal 若没有装载同一登记脚本，不能假定可直接补价。
## 3. 测试与命令

| 命令 | 计数 | 结果 | 证据 |
| --- | --- | --- | --- |
| `TMPDIR=/private/tmp .venv/bin/python -m unittest discover -s tests -v` | 214 | `OK`，0 失败/错误/跳过 | [agent.log](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/10/final-junit-override/smoke-a4/agent.log) |
| 任务 9 定向恢复组 | 46 | 通过 | [verify-task9.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/9/verify-task9.json) |
| `tests.worker.test_news` | 10 | 通过 | [tasks/5/done-claim.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/5/done-claim.json) |
| `tests.messaging.test_rabbit_unit` | 13 | 通过 | 同上 |
| 任务 14 定向 54 + 旧 Worker 24 | 78 | 均通过 | [verify-task14.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/14/verify-task14.json) |
| 任务 14 全量 discovery | 222 | `OK`；`green.log` 含一次失败启动与 ATTEMPT 2，计数以独立核验为准 | [verify-task14.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/14/verify-task14.json) |

RED 记录可读：任务 5 的新闻测试在执行到 `assertIsNone` 时得到 `B1_REQUIRES_PREDICTION_REQUEST`，而同一个冻结请求此前已被 verifier 接受；任务 4 的 RED 是 `LOG_EVENT_SET` 拒绝第二个独立调用用量，以及意图 fsync 后租约过期仍产生 provider effect。两者都不是导入或编译失败。

## 4. 重启与确认行为

六个检查点分别位于意图落盘前、意图后、effect 后、结果持久化后、pending 后、confirm 后。独立核验的重启计数：

- `before_intent`：0 到 1，首次执行。
- `after_intent`：0 到 0，结果记 `UNKNOWN`。
- `after_effect`：1 到 1，结果仍为 `UNKNOWN`，因为恢复时不知道 provider 是否成功。
- `after_result`、`after_pending`、`after_confirm`：1 到 1。

`after_pending` 与 `after_confirm` 各比较 12 个已持久 envelope，字节全部相同。前四个检查点在中断前没有已发送或 pending 的 envelope，因此不能说它们存在"跨中断原字节"，只能说恢复后的两次发送字节相同。这个区分写在 [tasks/9/verify-task9.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/9/verify-task9.json) 的 `qualification` 里。

### 4.1 真实 SIGKILL 补证（b1，2026-11-06T13:35Z）

上面六点是本地集合。b1 在真实 MySQL 与 RabbitMQ 上补了一个真实进程崩溃点：

- 探针包住真实 Agent CLI，在 `after_effect:2` 检查点由 `/bin/kill -KILL 69034` 杀掉，`exit_code=137`。见 [commands.json](../../f1ap-api/docs/e07-evidence/runs/e07-replay-20261106-b1/real-service/commands.json) 的 `effect-sigkill`。
- 崩溃时 journal 的 embedding 调用停在 `INTENT`，`result=null`，没有 envelope；generation 调用已是 `SUCCEEDED`。见 [effect-checkpoint.json](../../f1ap-api/docs/e07-evidence/runs/e07-replay-20261106-b1/real-service/effect-checkpoint.json)。
- 重启（exit 0）后，同一 runtime 的 journal 变为 `FINISHED`：generation 仍为 `SUCCEEDED`，embedding 为 `UNKNOWN` + `CRASH_UNCERTAIN`，三金额全 null，`provider_request_id` 为 null。没有重做该调用，也没有把未知写成成功。见 [crash-inbox.json](../../f1ap-api/docs/e07-evidence/runs/e07-replay-20261106-b1/real-service/crash-inbox.json) 的 `recovered_journal`。
- effect 计数崩溃前后都是 1 到 1，两个 call_key 各 1 次。这个数只能说明本次没有观察到重复 effect。
- 重启后 13 条事件经真实 broker 重投并进入真实 Inbox：13 条初始 `basicAck`、13 条重复 `basicAck`、13 条同字节 mandatory confirm；DB 行数（`calls 2`、`messages 14`、`results 10`、`publications 10`）前后一致。
- 同 `event_id`、不同 `input_tokens`（10 改 11）的冲突消息被 `basicReject` 拒收，隔离码 `EVENT_HASH_CONFLICT`，调用投影不变。
- 清理删除 60 行业务数据，哨兵 130 前后相等，拓扑未变。
- 第三轮独立复核判 `confirmed`：见 [verify-task11-r2.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/11/verify-task11-r2.json) 与 [f3-qa-r3.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/final/f3-qa-r3.json)。

## 5. 限制

1. **真实 MQ 已执行，但覆盖有限。** a4 与 b1 跑在真实 RabbitMQ 与真实 MySQL 上，b1 补了 effect 后的真实 SIGKILL 与真实 broker 重投。仍未覆盖 confirm 前崩溃、物理断网重连，也不能把重投证据读成 broker 自动 redeliver 或消费者线程调度已验证；confirm 丢失仍靠 harness 注入。
2. **Journal 是恢复依据，不是 effect 的证明。** 计数型 MockProvider 只能说明本次没有观察到重复 effect，不能证明分布式 exactly-once。
3. **修复前的 clock 身份日志不可恢复。** 这类 journal 会被 `LOG_CALL_IDENTITY` 拒绝，没有迁移路径；处置见 [API 接口文档](../../f1ap-api/docs/e07-interfaces.md) 新增章节。
4. **旧四态捕获文件多一个尾部 LF。** 语义与校验和不受影响，但"未经处理的原始字节"不成立。
5. **旧 1.0 兼容不等于修复前 1.1 兼容。** 前者只恢复、不重做调用；后者直接拒绝。
6. **类型检查没有作为门槛。** 任务 4 记录 Agent 侧 `basedpyright` 为 0 error / 388 warning，主要是旧契约未注解接口与 unittest 风格，没有压制；API 侧 `tools/e07` 的最新独立回执是 461 errors / 674 warnings，覆盖 11 个 E07 工具文件，检查器配置未改、未加 ignore，那一项在 API 验证报告里单列。
7. **Agent 侧全量测试通过不等于阶段完成。** a4 的 214 项与任务 14 的 222 项全绿都是本地事实；真实层已执行，F3 第三轮判 `confirmed`，计划文件也已把任务 11 与 F3 标为 `[x]`。正式 LIVE 仍未验证。
8. **补价的成功路径只证明到假 broker。** 任务 14 已由 `verify-task14.json` 判 `confirmed`（7001/8001/9137 身份互异、零 provider 调用、finished 与既存 envelope 不变），但没有真实 RabbitMQ 下的 ACK 后补价观察。
9. **证据里的临时目录是环境本地物。** `reconcile.json.cleanup_receipts` 等记录了 `/private/tmp/e07-task14-qa-...` 这类主机路径，不能当作可移植清理清单；对外引用以 run 内相对路径为准。
10. **没有真实供应商授权。** 任何 E08 的云端 Embedding 或真实新闻调用都要重新取得授权，不能引用本次结论。

## 6. 门状态

| 门 | 状态 | 依据与边界 |
| --- | --- | --- |
| F1 计划合规审计 | REJECT（待新一轮） | [f1-compliance.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/final/f1-compliance.json)：拒于真实层未执行、任务 1 裁决、canonical 构建与文档同步四项。真实层已执行、任务 1 已关闭、本轮文档已同步；canonical `clean verify` 仍缺，需审计方重新裁决 |
| F2 代码质量审查 | APPROVE_WITH_FINDINGS（R2；blockers 已关闭） | [f2-review.md](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/final/f2-review.md)：F2-01/F2-02 由任务 14 修复并 `confirmed`；F2-03/F2-04 文档项已更正 |
| F3 真实手工 QA | **CONFIRMED**（范围受限） | [verify-task11-r2.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/tasks/11/verify-task11-r2.json) 与 [f3-qa-r3.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/final/f3-qa-r3.json) 均判 `confirmed`、`failure_class=none`；首轮 `needs-fix` 保留为历史。放行只到六行真实场景与两项补证，不含正式 LIVE |
| F4 理想态核验 | APPROVE_WITH_FINDINGS | [f4-fidelity.json](../../f1ap-api/docs/e07-evidence/runs/e07-20261105T131858Z-a1/final/f4-fidelity.json)：仅本地 mock 范围，线上行按真实层条件读 |
| 任务 11 | `[x]`（计划已同步）；执行 a4 与 b1 | 执行状态与验收状态分开读；正式 LIVE 不在放行范围；旧 `BLOCKED_NOT_RUN` 仅作历史 |
| 任务 10 | 本地工具验收通过，独立复核 `confirmed` | 仅工具范围，不含真实层与类型检查 |
| 任务 14 | 独立核验 `confirmed` | 补价命令、修订身份与新闻重载绑定；仅假 broker，不含真实 MQ |
| 类型检查 | 未通过，461 errors / 674 warnings | E07 工具文件，未抑制 |
| 主应用打包 | BLOCKED_ENV | Spring Boot repackage 制品在全部磁盘缓存中缺失；`clean verify` 未完成，基线为 `clean test` 真实计数 |

## 7. 交接给 E08

Agent 侧 E08 要补的是：真实新闻来源与许可、真实 Embedding 调用边界、Chroma 单写进程与持久卷恢复、真实零增量与完成协议。E07 只提供了公共调用入口、修订与恢复语义，没有实现上述任何一项。证据与顺序见 API 侧 [E07 验证报告](../../f1ap-api/docs/e07-verification.md) 第 11 节与 [执行顺序](../../docs/execution-order.md)。
