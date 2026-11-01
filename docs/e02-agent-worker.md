# E02 B1 本地 Worker

本实现仅验证单写入者、单进程、每次一个合成 PREDICT 任务的本地链路。使用现有 `f1ap_agent.contracts.artifact.Contract` 装载 release 1.0.2；不复制 Schema，不修改 lock，不引入新依赖。边界经 E01 完整校验后转为冻结 Task，保留字符串 ID、整数调用上限、Decimal 分值、UTC 时间。采用现有 argparse/unittest 和标准库 dataclass，不安装候选 Pydantic/CLI 框架。

```bash
cd f1ap-agent
.venv/bin/python -m f1ap_agent.worker \
  --init-runtime \
  --fixture ../f1ap-api/contracts/v1/fixtures/valid/prediction-valid.json \
  --runtime-dir /absolute/new-runtime \
  --output /absolute/new-export
```

`--init-runtime` 只允许空/新目录。以后交付相同任务时省略它；已存在目录不得重新初始化。`--now` 是显式 UTC 毫秒测试时间；省略时使用任务 `data_cutoff_at`，不读墙钟。`--reject-send` 模拟本地接受失败，`--interrupt-after-call-start` 在假副作用计数后抛出中断。它们不是 MQ 故障模拟。

## 持久格式

- `runs.json`：run_id 到原请求 payload 指纹的登记清单；先登记再建立执行日志。已登记缺日志或损坏日志均失败关闭，不再调用。目录或清单缺失必须人工核实，启动不创建空历史。
- `runs/<run_id>/log.json`：带 SHA-256 的原子日志快照，记录请求、阶段、调用状态、结果、完整事件和本地接受列表。
- `calls/<call_key>.json`：CALL_STARTED 调用意图，执行完成/恢复后为 SUCCEEDED/UNKNOWN。调用前落盘，不自动重复 UNKNOWN 副作用。
- `pending/<event_id>.json`：发送前落盘的完整 envelope。重启重发保持 ID 和载荷；成功后删除待发文件。
- `sent/<event_id>.json`：假发送端本地收据；不表示 Broker confirm 或 Java 事务提交。
- `fake-executor.json`：真实假执行次数，不表示外部模型次数。

JSON 写入使用临时文件、fsync、原子替换和目录 fsync；没有本地数据库。仅支持一个写进程，不提供多进程锁或远程丢盘确认。

## 固定输出与退出码

固定输出只为 VERIFIED 合成题生成 `VALID/SUFFICIENT`：按定义顺序选择候选或数值下界，固定 JOINT 概率和零分占位值；这不宣称真实推理、十题业务规则或真实数据已经可用。其他失败、截止和 UNKNOWN 路径输出 FAILED。成功假调用生成一次初始用量（假 token 数 10/20）；费用状态 UNKNOWN，金额为 null，不是零。call_limit=0 和过期任务不调用、不发用量。调用中断恢复生成 UNKNOWN 用量和失败结果/finished，不阻塞另一个合法 run。

`--output` 每次必须是新目录，旧证据不会覆盖。`request.json` 是完整原请求 envelope，`events.json` 是完整结果/用量/finished envelopes 数组，`summary.json` 是本地运行身份与接受结果。输出禁止与 fixture、运行目录、契约目录或 lock 重叠。运行目录使用非阻塞文件锁排除第二个写进程。LIVE 任务同时检查 expires_at 和定义 deadline_at；默认 cutoff 是合成夹具时钟，旧夹具的墙钟年龄不影响它，显式 `--now` 用于验证截止。

退出码：0 为全部本地事件已接受；2 为仍有待发；3 为注入中断；1 为输入/持久状态/配置拒绝。0 不表示预测成功或真实服务通过。

## 验证边界

```bash
TMPDIR=/private/tmp .venv/bin/python -m unittest discover -s tests -v
```

macOS 默认 TMPDIR 的 `/var` 为符号链接，E01 原有输出路径测试会拒绝该祖先路径。显式使用解析后的 `/private/tmp` 避免环境假失败；不修改 E01 校验逻辑。根发现通过空 `tests/contracts/__init__.py` 包含原有契约测试。

外部 RabbitMQ、mandatory/confirm/return/nack、外部 SDK、生成/Embedding 模型、OpenF1、新闻、Chroma、部署和付费调用均为 **NOT_RUN**。本文件是 Agent B1 说明，不是 E02 总验收报告。
