# E06 Agent 验证与交接

> 后续项目门更新（2026-11-05）：已补摩纳哥实际题型及历史来源提取；用户采用项目规则后Worker run6002十题VALID、每题两策略、11事件，项目G3联合验收PASS，见[最终记录](../../f1ap-api/docs/g3-project-verification.md)。下方合成工程/旧官方规则BLOCKED结论保留历史含义，不宣称正式LIVE或严格赛前回测通过。

## 最终结论：真实 MQ 消费通过（工程层），正式 G3 仍 BLOCKED

E06 的真实链路里，Agent 以真实 RabbitMQ 消费者身份消费了 API 发出的 `prediction.requested.v1`，对同一 run 的十题输出两条策略推荐和一条 run 结束事件，共 11 个事件。Java 侧按冻结契约 1.0.2 逐个校验后投影进 10 个结果槽。证据是 run `e06-real-20261105-prescan-r31`：

- 消费命令（`real-service/commands.json` 原文）：`/Users/lubinze/Desktop/f1ap/f1ap-agent/.venv/bin/python -m f1ap_agent.messaging --run --init-runtime --max-messages 1 --existing-topology --runtime-dir .../real-service/agent-runtime --output .../real-service/agent-output`，exit 0。
- 输出：`real-service/agent-output/events.json` 共 11 个事件，10 条 `prediction.question.completed.v1` 加 1 条 `agent.run.finished.v1`，同一 `trace_id`。
- Java 侧断言见 `real-service/service-scenarios.json` 的 `mq-live-freeze-settle`：「ten VALID slots with two strategies from the real Agent」「eleven contract-validated events」「Actual OUT, real Agent」全部 `passed=true`。

这是工程链路通过，不是算法能力通过。Agent 只消费合成 profile，没有真实统计推断；完整规则仍为 0/10 VERIFIED，正式 G3 保持 BLOCKED。

对端记录：[API E06 验证](../../f1ap-api/docs/e06-verification.md)、[E06 支持矩阵](../../f1ap-api/docs/e06-support-matrix.md)、[固定接口](../../f1ap-api/docs/e06-interfaces.md)。

## 1. Agent 在真实 run 里做了什么

| 项目 | 实际内容 | 证据 |
| --- | --- | --- |
| 队列与拓扑 | 消费 `f1ap.agent.tasks.v1`；`--existing-topology` 只 `get_queue`/`get_exchange` 取既有 `f1ap.v1` 交换机，不声明、不修改共享拓扑 | `f1ap-agent/src/f1ap_agent/messaging/consumer.py` |
| 并发与确认 | `prefetch_count=1`；`publisher_confirms=True`、`on_return_raises=True`；状态落盘并确认全部回包后才 ACK；非法消息 `reject(requeue=False)` | 同上 |
| 持久状态 | `--init-runtime` 初始化 runtime；`sources.json` 记录 `payload_sha256` 与 `run_id`，`runs.json` 是 run id 到 `payload_sha256` 的映射 | `real-service/agent-runtime/sources.json`、`runs.json` |
| 输入冻结 | 消费到的请求字节与哈希存于 `real-service/agent-output/request.json`，`payload_sha256=0b8eb31762611230c5a8d76cc601daf800c69302405771999e142ef76f06914a`，`run_id=83` | `real-service/agent-output/request.json`、`real-service/agent-runtime/sources.json` |
| 输出 | 11 个事件按序写入 `real-service/agent-output/events.json` | 同上 |

`agent.log` 为空文件（sha `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`，即零字节），进程本身 exit 0；运行结果不在标准输出，而在上述结构化产物里。

## 2. Agent 实际计算了什么

十题使用 `algorithm_version=E06_SYNTHETIC_V1`、`call_limit=0`、`model_config_ref=MOCK_ONLY`、`tools=[]` 的固定 profile；Worker 在零调用分支前同时核对 `fixture_class=TEST_FIXTURE` 与 `algorithm_version`，命中后走固定输出，不执行 executor。

逐题产出（以 Q1 为例，其余九题同构，见 `real-service/agent-output/events.json`）：

- `status=VALID`、`data_status=SUFFICIENT`、`reason=E06_SYNTHETIC_V1`。
- `probabilities` 只有一条 `{event_key: B1_SYNTHETIC, family: JOINT, probability: "1"}`，这是夹具标记，不是概率模型输出。
- `recommendations` 两条：`EXPECTED_POINTS/EXPECTED_POINTS_V1` 取定义前 `min_picks` 个候选（Q1 为 131、11161、124）；`HIT_RATE/HIT_RATE_V1` 取定义末尾 `min_picks` 个候选并倒序（Q1 为 13、121、129）。两条的 `expected_score` 都是 `"0"`。
- `result_sha256` 逐题不同（Q1 `6082da949740c369999d446b8574ec3cdd81f3d1354671f729eb1247a5a2cb60`），供 Inbox 与后续结算复核。
- `agent.run.finished.v1` 汇总十题结果哈希，`last_usage_revision=0`，`expected_question_ids` 覆盖 1031 至 1040。

选法直接来自定义选项顺序，不读官方答案、不读特征、不读新闻；`E06SyntheticProfile` 只在 `round.source_system=TEST_FIXTURE` 且受控 import `source_id=E06_SYNTHETIC_V1` 时生效（见 [固定接口](../../f1ap-api/docs/e06-interfaces.md)）。

## 3. Agent 明确不做的事

- 不连接 MySQL、Nacos、Redis；不写业务库；结算与公示由 API 负责。
- 不调用任何模型、新闻或 Embedding 服务；`call_limit=0`、`tools=[]`，`usage` 不增长。
- 不读取官方答案表或答案消息，不存在 answers 到 Agent 的生产引用（审查结论见 [f2-review](../../f1ap-api/docs/e06-evidence/final/f2-review.md) 的「答案隔离」项）。
- 不做真实统计推断、概率校准或真实规则求解，不产生正式 VALID/LIVE 结论。
- 不声明或删除共享拓扑；清理由 API 的 owner-guarded 流程完成，三条队列清理后为 0 消息、0 消费者（`real-service/cleanup.json`）。

## 4. 测试与命令

- 全量回归：`cd f1ap-agent && TMPDIR=/private/tmp .venv/bin/python -m unittest discover -s tests -v`，exit 0，153 项，0 失败/0 错误/0 跳过（run `e06-local-20261105-final31` 的 `commands.json`）。该 run 的源码指纹是 `5e0abc12…`，与 r31 聚合的 recorded/current 及当前工作区相同，因此 153 是对最终源码的实际计数。E05 最终记录为 150 项，本阶段新增的三个 E06 用例在 `f1ap-agent/tests/worker/test_worker.py`：`test_e06_zero_call_produces_two_legal_different_strategies`、`test_e06_pending_restart_resends_identical_events_without_usage`、`test_e06_malformed_profile_never_reaches_executor`。
- 消费入口的离线自检：私有环境齐全但不加 `--run` 时，只打印 `CONFIG_VALID; network disabled without --run` 并退出 0，不建立连接；缺环境时先报 `MISSING_ENV` 并退出 2。`--run` 只在私有环境齐全、vhost 非默认时才连接。
- 真实 run 不跑 Agent 单元测试，跑的是实际消费进程；它由 API 侧 `tools/e06/service_run.py` 编排并记录到 `real-service/commands.json`。

## 5. 环境要求（不满足即真实层不可执行）

- **解释器**：工具不解析 `python` 或 `PYTHON`，固定使用工作区 `f1ap-agent/.venv/bin/python` 的绝对路径，并以 `F1AP_E06_AGENT_PYTHON` 传给子进程（`f1ap-api/tools/e06/service_run.py`）。venv 缺失、改名或换目录时真实 run 直接失败，这是运行前提而不是可选优化。
- **私有环境**：RabbitMQ 的 host、port、vhost、用户名、密码由 `.omc/` 私有文件注入；vhost 为 `/` 或端口越界会被拒（`INVALID_PORT_OR_DEFAULT_VHOST`）。
- **目录隔离**：runtime 与 output 不得互相包含，也不得落在 `contracts` 目录内，否则 `OUTPUT_OVERLAP` / `OUTPUT_RUNTIME_OVERLAP`。
- **时限**：`--max-messages 1` 表示只处理一个 delivery，真实 run 因此每个轮次单独起进程；`--delivery-timeout` 默认 30 秒，`--confirm-timeout` 默认 10 秒。

## 6. 未验证与阻塞

- 正式 G3：BLOCKED。Agent 侧没有任何真实规则核验、真实截止验证或真实比赛数据；十题合成分支只用于工程验收。
- 真实统计能力：未实现、未验证。E05 的十题方法在真实规则下仍输出 `FAILED/RULE_BLOCKED`，E06 没有改变这一点。
- 本轮没有重跑 Agent 全量测试之外的额外探测；证据目录里也没有 Agent 侧的 E06 专属证据根，Agent 的产物全部位于 API 的 run 目录下。

## 7. 交接给 E07

1. 消费、持久状态、确认语义和零调用分支已经可用，E07 可直接复用 `f1ap-agent/src/f1ap_agent/messaging/consumer.py` 与 `f1ap-agent/src/f1ap_agent/worker/engine.py`。
2. 换成真实统计输出时，需要先解除真实规则与数据缺口，并把 `E06_SYNTHETIC_V1` 分支替换为受控的真实 profile；`call_limit=0`、`MOCK_ONLY` 的约束到时必须同步修改，不能只改 Agent 一端。
3. 任何契约变动都走冻结 release 与 `contracts.lock.json`，不在 Agent 侧维护第二份 Schema。
