# Agent 启动配置

正式入口 `f1ap-agent-mq` / `python -m f1ap_agent.messaging`、
`python -m f1ap_agent.worker`、`f1ap-mongo-research` / `python -m f1ap_agent.mongo_research`
支持启动时一次性从 Nacos 2.4.3 读取 TOML。禁用的本地 question_sync/websearch 工具不在范围内。
契约校验器和 MQ 集成验收工具也不作为业务运行入口接入。

引导信息只读进程环境，不自动读取任何 dotenv 文件：

| 变量 | 含义 |
| --- | --- |
| `F1AP_NACOS_SERVER_ADDR` | 与 API 相同的服务地址，`host:port` 或 `http(s)://host:port`，不带路径 |
| `F1AP_NACOS_NAMESPACE` | namespace ID；public 必须显式设为空字符串，不猜测实际 namespace |
| `F1AP_NACOS_GROUP` | 如 `DEFAULT_GROUP` |
| `F1AP_NACOS_DATA_ID` | 如 `f1ap-agent.toml` |
| `F1AP_NACOS_USERNAME` / `F1AP_NACOS_PASSWORD` | 非空登录凭据 |

任何 `F1AP_NACOS_` 变量存在即选择 Nacos。引导缺失、登录失败、配置不存在/为空/非法都立即失败，
不回退本地文件或旧环境配置。未设置任何引导变量时，保留原有环境凭据及 `--llm-config` / `--config` 路径。

将 `config/nacos-agent.example.toml` 内容填入服务端；空凭据是示例占位，校验时会拒绝。
五个表均必填，未知键和缺失键均拒绝；先完整校验，再一次注入固定的七个业务环境键。
`model` 与本地 DeepSeek 使用同一校验器，仅支持 `deepseek-flash` 和固定 `DEEPSEEK_API_KEY`。
`research` 复用 Mongo 研究配置的五项整数范围校验；研究模型固定为 `deepseek-flash`，
其 timeout/max_tokens 来自 `research`，预测 Worker 则使用 `model`。
`mq` 四项超时必须为正整数秒；`paths` 只提供 runtime_dir 和 mapping 默认值。
配置文件本身不含任务问题、会话、cutoff 或输出路径，这些仍由当次 CLI 明确传入。

CLI 的 `--runtime-dir`、`--mapping` 及 MQ 超时选项优先于远端默认；
Nacos 启用时拒绝同时提供 `--llm-config` 或研究 `--config`，避免悄悄混用配置来源。
`--fixture`、`--output`、研究 `--question` / `--session-key` / `--cutoff` / `--definition`、
MQ `--max-messages`、初始化/守护/拓扑与 mock/reconciliation 选项仍保持 CLI 行为。
固定路由、既有拓扑、ACK/confirm/send 行为不变。

从 Agent 仓库根目录执行（私有引导文件由操作者维护，不提交）：

```bash
set -a
source .env.nacos.local
set +a
.venv/bin/python -m f1ap_agent.nacos
.venv/bin/python -m f1ap_agent.messaging --check-config
.venv/bin/python -m f1ap_agent.worker --check-config
.venv/bin/python -m f1ap_agent.mongo_research --check-config
```

每条检查命令独立登录一次、取配置一次，只输出成功标记或脱敏错误码；不安装业务环境、不写输出目录，
不连接 MQ/Mongo、不调用模型。上述命令不需要任务输入。`--help` 永不联网，
MQ `--healthcheck --runtime-dir <目录>` 仅读本地健康文件，不取 Nacos。
MQ 启用 Nacos 时必须有 `--run` 或 `--check-config`，否则直接报 `NACOS_RUN_OR_CHECK_REQUIRED`，
保证原有不带 `--run` 的预检不会意外联网。

```bash
# 以下运行命令仅在当次已授权真实业务连接/模型调用时执行。
.venv/bin/f1ap-agent-mq --run --daemon --ensure-runtime --existing-topology --output /private/tmp/mq-output
.venv/bin/python -m f1ap_agent.worker --fixture <任务文件> --output <新目录>
.venv/bin/f1ap-mongo-research --question '<研究问题>' --session-key <明确会话ID> \
  --cutoff '<UTC时间>' --output <新目录> --authorize-model
```

研究仍必须显式 `--authorize-model`；配置检查不构成调用授权。
Mongo MCP 子进程继续仅接收必要 URI 与既有执行环境白名单，不继承 Nacos、MQ 或模型密钥。
HTTP 禁用环境代理与重定向，超时 10 秒，无自动重试；登录 token 与原始配置仅在内存，
无磁盘缓存、无自动刷新。修改 Nacos 后需重启进程。

错误码：`NACOS_BOOTSTRAP_REQUIRED` / `NACOS_BOOTSTRAP_INVALID`、`NACOS_TRANSPORT_FAILED`、
`NACOS_AUTH_FAILED`、`NACOS_CONFIG_NOT_FOUND`、`NACOS_FETCH_FAILED`、`NACOS_CONFIG_EMPTY`、
`NACOS_PARSE_FAILED`、`NACOS_CONFIG_INVALID`、`NACOS_LOCAL_CONFIG_CONFLICT`。
错误中不携带 URL、响应正文、token 或凭据。
