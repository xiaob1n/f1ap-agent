# E03 Agent 验证与交接

日期：2026-11-01。范围：B1 RabbitMQ consumer/publisher、pending 恢复、源 event 身份和 TEST_FIXTURE 固定输出。状态：**本地验证 PASS；真实 G1 PASS（run `e03-g1-20261101-033834-04df6c`）；E03 完成**。

## 1. 当前结论

### 本地已验证

- `aio-pika==9.5.7` 已加入 `pyproject.toml`，`uv.lock` 锁定其 `aiormq==6.9.4`、`pamqp==3.3.0` 及传递依赖。
- 使用真实临时目录 `TMPDIR=/private/tmp/f1ap-e03-agent` 执行：
  `.venv/bin/python -m unittest discover -s tests -v`。
- 当前结果：64 项通过，0 失败，0 错误；E02 原有 55 项全部通过，新增 MQ 单元覆盖 ACK 判据、return/nack/timeout/channel close、重复 delivery、源身份冲突、pending 恢复、UTC 时钟和 CLI 环境门。
- Agent 生产 CLI：`python -m f1ap_agent.messaging --help`；只有显式 `--run` 才允许连接 Broker。
- 实际 Worker 输出经过 `f1ap-api/tools/e03/verify-output.py` 验证：9 项离线正/负例通过，范围为 `ACTUAL_EVENTS_OFFLINE`，不是 RabbitMQ 证据。
- 使用正确 Agent venv 运行 `basedpyright`：生产 messaging 代码 0 errors、38 warnings。警告主要来自 argparse `Namespace`、第三方类型信息和未使用返回值；没有通过 `# type: ignore` 压制。
- 真实 G1 中 Agent 作为真实消费进程运行：消费 1 个 `prediction.requested.v1`，执行 1 次固定假调用，写出 12 个事件（1 usage、10 result、1 finished），pending 清空，源身份 1 条。
- Agent 侧真实运行证据归档在 `f1ap-agent/docs/e03-evidence/runs/e03-g1-20261101-033834-04df6c/`，包含 `agent-output/`、`agent-runtime/` 和 `summary.json`。

### 真实 G1 已验证

- 真实 Broker 场景覆盖 mandatory return、publisher nack、confirm timeout、channel close、回包绑定缺失后的未 ACK 与重投、源 event 冲突；14 个场景全部 PASS。
- 回包绑定缺失时原请求未 ACK，12 个 pending 事件保留；恢复绑定后原消息 redelivery，重发相同 event_id 和 payload_sha256，固定执行次数仍为 1。
- 独立 verifier 对实际 request/events 的 9 项检查通过；缺结果、缺 finished、错误 run/question、异 hash event 均被拒绝。
- 未运行且不属于 E03：OpenF1、新闻、Embedding、生成模型、部署。

## 2. 运行证据

| 层级 | 命令/位置 | 结果 |
| --- | --- | --- |
| OFFLINE_STATIC | E01 独立回归及 Agent contract tests | PASS |
| LOCAL_TEST | `TMPDIR=/private/tmp/f1ap-e03-agent .venv/bin/python -m unittest discover -s tests -v` | PASS；64/64 |
| LOCAL_TEST | `f1ap-api/tools/e03/verify-output.py` | PASS；实际 Worker 输出 9 项检查 |
| REAL_SERVICE | `.omc/e03-environment.local.txt` + `run-g1-from-file.py` | PASS；run `e03-g1-20261101-033834-04df6c` |
| REAL_SERVICE | `python -m f1ap_agent.messaging --run ...` | 消费 1 条请求，写出 12 个事件，pending=0 |

## 3. 运行配置

连接信息只从 `.omc/e03-environment.local.txt` 读取；本次由 `f1ap-api/tools/e03/run-g1-from-file.py` 装载，不需要手工导出环境变量。密码不进入 argv、日志、fixtures 或证据文件。

推荐入口：

```text
f1ap-agent/.venv/bin/python f1ap-api/tools/e03/run-g1-from-file.py
```

Agent 自身 CLI 仍可直接使用：

```text
python -m f1ap_agent.messaging \
  --run --runtime-dir <runtime-dir> --output <output-dir> \
  --init-runtime --max-messages 1
```

## 4. 下一阶段交接

E03 的真实 G1 已完成，Agent 侧证据独立归档；API 侧的 Broker/数据库/ACK/redelivery 证据在 `f1ap-api/docs/e03-evidence/runs/e03-g1-20261101-033834-04df6c/`。

E04 可以进入规划，但 E03 只证明 `TEST_FIXTURE` 的协议与可靠性，不证明真实预测能力；E04 仍需显式 OpenF1 场次、可用数据和相应授权。

本轮未执行部署、SQL DDL、真实模型、OpenF1、新闻、Embedding、付费调用或 Git 提交。
