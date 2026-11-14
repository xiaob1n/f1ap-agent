# E10 Agent 本地优先验证报告

日期：2026-11-14。交接状态：**LOCAL_DELIVERY_WITH_VERIFICATION_LIMITS / REMOTE_DEFERRED**。同一次综合审查批准限定交接，不批准完整验收通过；最终复合验证 INCOMPLETE，远端 REMOTE_NOT_RUN，G5 未通过。

## 实际结果

本轮遵循[计划顶部覆盖](../../.omo/plans/e10-execution-plan.md)，采用本地优先与收敛验收。基线仍为 E07、契约 `1.0.2`、`MOCK`、合成 `TEST_FIXTURE`；E08/E09 延后，正式官方 LIVE 为 `NOT_VERIFIED`。UNKNOWN 成本不记零，也不触发预算停机。

- F 最终运行 `e10-local-final-20261114-consolidated` 的 Agent [原始命令](../../f1ap-api/docs/e10-evidence/runs/e10-local-final-20261114-consolidated/local/agent/command.json) exit 0；[日志](../../f1ap-api/docs/e10-evidence/runs/e10-local-final-20261114-consolidated/local/agent/stdout.log) 记录 `Ran 246 tests`、`OK`。命令为隔离环境中 `python -m unittest discover -s tests -v`，不是容器或真实 MQ 验收。
- 同一 F 的 API 44 份 XML 合计 381 tests，零失败、错误、跳过。Dockerfile/Compose/既有文档静态审计通过。修正 JAR 暂存摘要为 `d103eb99e7be131cbd9cb5e12db4e746ab8ca6550ccb6c9c53f00fd46b41d8bd`，manifest 620 文件。原件链接及操作入口集中在 [API 验证报告](../../f1ap-api/docs/e10-verification.md)。
- F E01 [Java 原日志](../../f1ap-api/docs/e10-evidence/runs/e10-local-final-20261114-consolidated/e01-contract-gate/runs/559a8a471d584cfd8d80005b24ddcb4e/java-tests-build.log) 记录 245 tests、零失败/错误/跳过及 BUILD SUCCESS；[Python 原日志](../../f1ap-api/docs/e10-evidence/runs/e10-local-final-20261114-consolidated/e01-contract-gate/runs/559a8a471d584cfd8d80005b24ddcb4e/python-unit-tests.log) 为 37 tests、OK，是 Agent 子集，不另累加覆盖。
- R4 恢复 `e10-local-recovery4-20261114-consolidated` 的 [tools 日志](../../f1ap-api/docs/e10-evidence/runs/e10-local-recovery4-20261114-consolidated/contract-recovery/tools-unit-tests/stdout.log) 记录 291 tests、OK；tools 与剩余 E01 的 [50 份原始回执](../../f1ap-api/docs/e10-evidence/runs/e10-local-recovery4-20261114-consolidated/contract-recovery/) 全部结束，10 条正例为零、40 条预期拒绝为非零，日志摘要逐项匹配。这是命令数，不是 50 个测试。

## 未完成项与时间线

F contract 原回执 exit 1，旧失败保留。R4 根目录缺 `ledger.json`、`recovery-finish.json` 和全局后置回执，driver 停在 `RESUME_POPULATION`；严格完整 primary 与实际本地六负例为 `NOT_RUN / NOT_PROVEN`。最终 summary 不存在，不制造综合 PASS。

[finalization-drift.json](../../f1ap-api/docs/e10-evidence/runs/e10-20261113T131700Z-a1/tasks/10/resume-driver/resumed-20261114T0939Z/finalization-drift.json) 明确 50 条命令于 09:28:22.769679Z 前全部结束，共享编辑首次下界为 09:30:48.763Z。执行 ISO 中 driver/test 摘要未变，因此 50 条结果有效。共享 driver/test 是执行后、失败收尾前的改动，未被这些结果覆盖，不是执行窗口内漂移。停止请求前排队测试的 exit 1 保留，当前共享远端改动未验证。

历史 synthetic 的 1 控制 + 12 拒绝只证明合成夹具分支，不代表 F 实际运行。本轮最终 E10 synthetic 12、actual-local 6 均 `NOT_RUN`，40 个 E01 预期拒绝不能替代它们。[同一次最终综合评估](../../f1ap-api/docs/e10-evidence/runs/e10-20261113T131700Z-a1/tasks/10/local-delivery-consolidated-final-assessment.json) 已 APPROVE 限定交接，状态为 `LOCAL_DELIVERY_WITH_VERIFICATION_LIMITS / REMOTE_DEFERRED`，不批准严格全链或全局源码不变证明。远端 driver 仍部分失败关闭，远端双构建、十场景、真实服务、容器排空与卷恢复全部延后。

后续共享远端增量的[真实失败](../../f1ap-api/docs/e10-evidence/runs/e10-20261113T131700Z-a1/tasks/10/resume-driver/resumed-20261114T0939Z/expiry-pending-identity-red.log) 是 `test_expired_prediction_lifecycle_retains_identity_and_protected_projection`：`after_pending` 未传 delivery，None 未通过 event_id 身份断言，1 test、1 failure、exit 1。标 `KNOWN_FAIL / REMOTE_DEFERRED`，排除出已测 ISO 与已暂存产品的通过范围；不是当前工作区全绿。原 F tools 的 290 tests、4 errors 也保留，R4 的 291 OK 不改写历史。后续 `RESUME_POPULATION` 不能确定首次完成 50 命令总进程的退出码或缺封存原因，本次不修复或重跑。

## 交接、残留和清理

[runbook](runbook.md)、[API 发布清单](../../f1ap-api/docs/e10-release-checklist.md)、[Compose](../../f1ap-api/deploy/compose.yml) 和双端暂存定义可用。手册内 `PENDING_TASK11` 按本轮覆盖解读为 `REMOTE_NOT_RUN`，不是待自动执行。后续操作须遵守单次授权，Agent 不挂 API 导入/答案卷，不接收 MySQL/Nacos/Redis 凭据。原始证据仅本地受控留存，不公开凭据、私有路径或完整私有 argv。

DatabaseGuard 注册顺序、既有类型错误/警告和两个旧 phase-plan 链接保留未修，不扩大本任务范围。Task12 无测试、Maven、网络、服务、Git 或委派操作，无自有运行进程需要清理；ISO、制品及所有旧失败保留。恢复执行者记录 owned processes 为空，不等于远端清理已验收。

本次静态报告审计 `NOT_RUN`，原因是最终 summary 缺失且本任务不额外运行门禁。仅读取原件并检查新增报告的链接与摘要，记录见 [Task12 证据](../../f1ap-api/docs/e10-evidence/runs/e10-20261113T131700Z-a1/tasks/12/docs-localfirst/observed-evidence.json)。
