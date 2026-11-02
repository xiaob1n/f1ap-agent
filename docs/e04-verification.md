# E04 Agent 验证与交接

日期：2026-11-01。范围：Agent B2 OpenF1 受限准备、冻结输入、领奖台统计基线和 B4 研究结果适配最小部分。用户本次选择“先完成本地”，真实 OpenF1、RabbitMQ、MySQL、Redis 和 G2 规则门保持 BLOCKED。真实编排入口、实际 Agent CLI 路径和本 run 资源边界已交付，但本次没有启动真实服务。

对端交接：[API E04 验证与交接](../../f1ap-api/docs/e04-verification.md)。本地范围交付，不代表原计划的真实联合编排和 G2 已完成。

## 1. 当前结论

- 受控 mapping 信封为 `OPENF1_MAPPING_V1`，按 `round_id + definition_sha256` 选择；重复 round、重复 driver、非法 endpoint、时间倒置、路径逃逸、符号链接和 hash 篡改均拒绝。
- HTTPX `0.28.1` 已锁定。客户端固定 host、七类 GET endpoint、session 参数、单连接、无重定向、无环境代理和响应大小上限；真实网络本次未调用，MockTransport 已覆盖请求边界。
- 冻结 manifest 记录原始响应字节 hash、行数、字段集合、单位、首次观测时间和限制说明；消费任务只读取冻结文件，不重新请求 OpenF1。
- 特征清洗把圈时转换为整数微秒，拒绝 cutoff 后事件、重复事件、缺字段、错误单位、非 GREEN/干地、pit-out、缺 stint 和候选样本不足。
- `PODIUM_PACE_BOOTSTRAP_V1` 固定 `seed=0`、`samples=10000`、`laps_per_sample=5`，候选排序和抽样可重放；独立排列 oracle 验证 `EXPECTED_POINTS_V1` 的合成积分适配。
- 研究任务只接受 `RESEARCH + CONTROLLED_INPUT`，强制 `OPENF1_READ`、`NO_MODEL`、`NOT_APPLICABLE` 和 `EXPECTED_POINTS_V1`；不调用 executor，不产生 usage 事件，输出十个 result 和一个 finished，`last_usage_revision=0`。
- 规则未知时 Q1 为 `FAILED/RULE_BLOCKED`，Q2–Q10 为 `FAILED/UNSUPPORTED_E04`；数据不足为 `DATA_INSUFFICIENT`，非 `VALID` 结果的概率和推荐为空。
- `service_run.py` 仅在批准环境门全部通过后启动 API `e04-integration` profile；真实路径通过 `--existing-topology` 被动复用已登记拓扑，不声明或绑定共享拓扑，本 run 清理由 owner guard 限定。

## 2. 当前本地验证

| 层级 | 命令/证据 | 结果 |
| --- | --- | --- |
| `OFFLINE_STATIC` | E01 独立回归 | PASS；release `1.0.2`、manifest 未漂移 |
| `LOCAL_TEST` | `TMPDIR=/private/tmp .venv/bin/python -m unittest discover -s tests -q` | PASS；87/87 |
| `LOCAL_TEST` | Agent 定向 basedpyright | 0 errors，5 warnings；warnings 来自既有 JSON/契约边界的 Unknown 传播，无新增 suppressions |
| `LOCAL_TEST` | `python -m f1ap_agent.e04_fixture --output f1ap-agent/docs/e04-evidence/runs/e04-local-sealed-20261101T153316-e07b95/agent-fixture` | PASS；external services `NOT_RUN` |
| `LOCAL_TEST` | `VerifyEvents` + `VerifyResearch` | PASS；10 results、11 unique events、0 calls、7 frozen response endpoints |
| `LOCAL_TEST` | 重复夹具/研究产物对账 | PASS；mapping、manifest、request、events 和研究计数/身份一致 |

独立关联验证输出：

```text
release=1.0.2
contract_manifest_sha256=6c9e34dfd002cb1f3309dc3d98871cd1cd93135f77c46810711674158124cd37
mapping_sha256=3a3407da26e3c337a4ddf17bcba3b05409025b9495e4df95b9f28ac274e36aa9
openf1_manifest_sha256=0a1bb862a9018d10903c30bbe905adcf7f1598a26ed5a39308b043acfc471bfc
results=10
calls=0
response_count=7
scope=OFFLINE_RESEARCH_BINDINGS
```

## 3. 本地场景证据

本端归档：[机器摘要](e04-evidence/runs/e04-local-sealed-20261101T153316-e07b95/summary.json)、[14 场景明细](e04-evidence/runs/e04-local-sealed-20261101T153316-e07b95/scenarios.json)、[独立负例](e04-evidence/runs/e04-local-sealed-20261101T153316-e07b95/negative-verifier/summary.json)。[API 端独立归档](../../f1ap-api/docs/e04-evidence/runs/e04-local-sealed-20261101T153316-e07b95/summary.json) 使用相同 run ID 和 source 指纹，生成时间独立记录。

本端时间：`2026-11-01T15:33:16.768Z` 至 `2026-11-01T15:33:21.713Z`；source SHA-256：`59dd3b9d0cf9397947a80b2de2bc459c90f45ec5bf3c44c9e6679dbb44766113`。工程矩阵为 3 项本地 PASS、11 项真实服务 BLOCKED，0 项失败；4 项 `local_checks` PASS 不代表 4 项真实工程通过。`summary.json` 明确记录 14 项计数和同一 source 指纹。

- `openf1-data-insufficient`：独立坏输入使用已核验合成规则和空圈响应，Q1 为 `DATA_INSUFFICIENT`，`data_status=INSUFFICIENT`，概率/推荐为空。
- `podium-baseline`：本地 `research.json` 复算通过，固定 10000 样本、4 个候选、每车 3 个名次计数列，每列跨候选总和为 10000，mapping/manifest/cutoff 身份一致；该结果仅进入 `local_checks`，不升级为 `REAL_SERVICE` 工程通过。
- `rule-blocked`：正常合成夹具十题均非 `VALID`，Q1 原因为 `RULE_BLOCKED`，其余为 `UNSUPPORTED_E04`，没有概率或推荐。
- `negative-verifier`：破坏 payload/result hash、run/question 关联、缺题、finished、非 VALID 概率/推荐、mapping/manifest/response hash 和快照绑定的副本均被非零拒绝。

## 4. 真实验收解除条件

需要新的当前运行登记：固定 OpenF1 meeting/session ID、mapping 原始 hash、白名单 endpoint/字段、允许的 cutoff、可达的只读 OpenF1 地址和独立证据目录。没有这些输入时，不把本地合成值写成真实场次，也不运行真实采集。

R01–R05/R10 当前仍未达到 `VERIFIED`。即使真实圈速数据可用，规则门未解除前也不得把内部 baseline 候选写成合法概率或推荐。

统一真实编排代码已接通：`run-g2.py --run` 通过所有门禁后调用 API `E04ServicesIT`，由 Java 流程启动实际 OpenF1 准备 CLI 和 Agent `--run --existing-topology`，收集实际 MQ 事件并执行 owner-guarded 清理。本次只运行了门禁和本地 fixture，未提供真实环境，因此真实工程矩阵仍全部 BLOCKED；下一次需在当前运行登记中提供固定 ID、mapping、隔离服务和清理授权。

固定输入和权限规则：本轮未登记真实 meeting/session ID，`config/openf1/round-mappings.json` 保持空配置。真实采集需要显式 `--run`、批准的只读地址、固定 mapping、白名单七端点和确定的 cutoff；响应必须冻结到本 run 的证据目录。不得推断赛事、访问未列端点或把合成场次记录为真实输入。

## 5. E05 交接

E04 Agent 侧交付真实适配所需的受限客户端、冻结输入、可解释领奖台基线、研究任务分流、十题终态和可靠重投边界。完整 Q2–Q10 统计、正式规则、新闻、Embedding、生成模型、供应商调用、费用和正式推荐仍属于后续阶段。

本轮未执行 DDL、部署、共享拓扑修改、真实 OpenF1/API/MQ/DB/Redis 连接、付费调用或 Git 提交；测试及证据按仓库规则仅留本地。

E05 的未满足条件：Q2–Q10 仍需逐题输入、规则和支持矩阵，特别是 Q6/Q8 口径及 Q10 联合极端场景。当前 `UNSUPPORTED_E04` 不得用 baseline、LLM、新闻或 Embedding 猜测填满；规则未达到 `VERIFIED` 前不得输出正式推荐。
