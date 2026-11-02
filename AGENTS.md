# f1ap-agent

## OVERVIEW
Python 3.12 单进程 Worker：E01 契约校验 + B1 持久执行日志 + E03 RabbitMQ 收发 + E04 OpenF1 研究链路（零模型调用）。独立 git 仓库，`dev` 分支。入选原因：独立仓库/独立领域（评分约 16）。

## WHERE TO LOOK
| 任务 | 位置 | 说明 |
|------|------|------|
| 契约校验（独立实现） | `src/f1ap_agent/contracts/` | `verifier.py` parse/canonical/digest/evaluate；`semantics.py` 事件语义；`artifact.py` 哈希锁定加载 |
| Worker 状态机 | `src/f1ap_agent/worker/engine.py` | `Worker.run` → `Task.parse` → 持久日志 → `FakeExecutor` → `_send` |
| 契约路径与版本 | `src/f1ap_agent/worker/models.py:15` | 硬编码兄弟目录 `../f1ap-api/contracts/v1` 且只认 1.0.2 |
| E04 研究计算 | `worker/research.py`、`baselines/podium.py`、`features/cleaning.py` | 10 条结果 + 1 finished；Q1 之外均 UNSUPPORTED |
| RabbitMQ | `messaging/consumer.py`（ACK/requeue）、`messaging/__init__.py`（confirm 发布、`LocalSender`） | 只消费 `prediction.requested.v1` |
| OpenF1 准备 | `data/openf1/`（`client.py` 白名单 7 端点、`snapshot.py` 冻结快照、`config.py` 映射） | 运行时只读冻结文件 |
| 持久化原语 | `runtime/__init__.py` | `Json` 别名、`RuntimeFault`、原子写 + `fcntl` 单写锁 |
| 合成夹具 | `e04_fixture.py` | 离线驱动真实 Worker |
| 阶段计划/验收 | `docs/phase-plan.md`、`docs/e0X-verification.md`、`docs/e04-interfaces.md` | phase-plan 是 B0–B7 规划，非现状 |

## CONVENTIONS
- 依赖只锁 3 个：`aio-pika==9.5.7`、`httpx==0.28.1`、`jsonschema==4.25.1`；uv 管理，`.python-version`=3.12.14。新依赖须先锁版本（E05 计划引入 `langgraph==1.0.3`，尚未安装）。
- 不用 Pydantic：`dict[str, Json]` + frozen/slots dataclass + `Protocol`/`TypedDict`/`NewType`。
- 严格类型：bool 不当 int；ID 一律字符串；概率/分数/金额用十进制字符串 + `Decimal`；解析拒绝浮点/指数，整数上限 int32。
- 确定性输出：自定义 canonical JSON → SHA-256；`uuid5` 事件 ID；时钟经 `--now` 注入，不读墙钟。
- 失败关闭：`RuntimeFault(code)` / `Rejection(stage, code, path)`；CLI 退出码 0 成功、1 输入/状态失败、2 待发/配置失败、3 模拟中断。
- argparse 用 `Namespace` 子类，`_ = parser.add_argument(...)` 满足类型检查。
- MQ 环境变量 `F1AP_RABBITMQ_HOST/PORT/VHOST/USERNAME/PASSWORD` 无默认值；vhost `/` 被拒。`f1ap-agent-mq` 默认不连接，需 `--run`。
- 新代码注释用中文（E03/E04 模块已如此）。

## ANTI-PATTERNS
- 不得手写第二套 Schema 或消息对象；升级契约只能跟随 API 新 release + 更新 `contracts.lock.json`。
- 发布只在收到 `Basic.Ack` 且无 return 时算已发送；入站 ACK 必须在全部出站事件确认之后。不要为“简化”关掉 `mandatory` 或 publisher confirm。
- 损坏的日志/状态必须失败关闭，绝不重置为新状态；保留 `pending/`、`sources.json`、`CALL_STARTED` 语义。
- 不在进程内加重连/退避：发布失败返回 2 并退出，由外部重启。
- OpenF1 只走白名单端点与显式配置的场次 ID；不按名称推断 meeting/session，不补造数据；`config/openf1/round-mappings.json` 故意为空。
- 不把当前官方 Answer 当输入；Q2–Q10 不得伪造结果。
- `runtime`、`contracts/verifier` 被引用最多（14/9 处），改动先跑全量测试。

## COMMANDS
```bash
uv sync --locked                       # 或 --offline
TMPDIR=/private/tmp .venv/bin/python -m unittest discover -s tests -v   # 87 项
.venv/bin/python -m unittest discover -s tests/contracts -v
basedpyright --pythonpath .venv/bin/python src/f1ap_agent   # 不要信 LSP 守护进程的缺包报错
.venv/bin/python -m f1ap_agent.worker --help
```

## NOTES
- `pyproject.toml` 的 description 仍写“E01 only”，已过时，以代码为准。
