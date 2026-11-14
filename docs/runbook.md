# E10 Agent 运维手册

适用范围为 2026-11-13 E07 回退基线、冻结契约 `1.0.2`、单 API 与单 Agent、`provider_code=MOCK` 和合成 `TEST_FIXTURE`。不运行 Chroma/RAG、新闻爬虫、真实 Embedding 或 E09 生成融合；成本只记录，UNKNOWN 不记零，也不触发预算停机。正式官方 LIVE 为 `NOT_VERIFIED`。

本手册的命令已对照当前源码与配置阅读核实，尚未在远端执行。Task11 必须逐条采集实际 argv、退出码、镜像摘要、耗时和清理回执，当前状态为 `PENDING_TASK11`，不能据此宣称容器或 G5 PASS。发布前另读 [API 发布清单](../../f1ap-api/docs/e10-release-checklist.md) 与 [运行接口](../../f1ap-api/docs/e10-interfaces.md)。

## 1. 启动前提

镜像使用 Python 3.12、`uv sync --frozen --no-dev`，从干净源码和 `uv.lock` 安装，不能复制工作区 `.venv`。51 条锁记录按目标 linux/arm64 marker 求值，项目自身加 49 个三方包，排除 emscripten 专用包；实际容器安装集合须由 Task11 核验。基础镜像当前固定标签，摘要仍待远端冻结。参见 [Dockerfile](../Dockerfile)、[Compose](../../f1ap-api/deploy/compose.yml) 和 [Task7 独立收据](../../f1ap-api/docs/e10-evidence/runs/e10-20261113T131700Z-a1/tasks/7/independent-verification.json)。

运维人员先取得当次远端和隔离资源授权，准备已有外部 Docker 网络、独立 Nacos 配置、`f1ap` 库和预置 MQ 拓扑。应用不创建中间件。Agent 仅接收五个 RabbitMQ 变量与可选成对 mock 文件路径，契约路径由镜像成对固定。不得给 Agent 传 MySQL、Nacos、Redis、内部写 token 或 API 导入路径。`api_imports` 可能含 Answer，绝不挂入 Agent；Agent 仅写 `agent_runtime` 与 `agent_output`。

以下 shell 变量在获授权的发布工作区设置。`DEPLOY_ENV` 指向运维已准备的私有环境文件，镜像须使用已核验摘要。不要输出环境文件或展开后的 Compose 配置到证据。中间件地址必须为容器可达的外部 DNS 或主机网关，不能是容器回环地址。

```bash
ROOT=/Users/lubinze/Desktop/f1ap
COMPOSE="$ROOT/f1ap-api/deploy/compose.yml"
: "${DEPLOY_ENV:?须指定私有部署环境文件}"
PROJECT=f1ap-e10
dc() { docker compose --env-file "$DEPLOY_ENV" -p "$PROJECT" -f "$COMPOSE" "$@"; }
dc config --quiet
dc up -d --no-build f1ap-api
dc exec -T f1ap-api java -cp /app/probe com.f1ap.api.config.HealthProbe http://127.0.0.1:8080/actuator/health/readiness
dc up -d --no-build f1ap-agent
dc exec -T f1ap-agent .venv/bin/f1ap-agent-mq --healthcheck --require-ready --runtime-dir /data/runtime --output /data/output
dc ps -a
```

readiness 未成功时停止该流程排查，不能因容器已创建就继续判 PASS。宿主 API 发布仅绑定 loopback，Agent 无公网端口。两个容器均应实测 `10001:10001` 和 UTC。默认 Compose 不挂 mock 文件；启用 mock 必须同时提供 `F1AP_E07_MOCK_PLAN_FILE`、`F1AP_E07_NEWS_CATALOG_FILE`，并通过经审阅的 override 给双端只读挂同一脱敏夹具目录。仅设路径不会产生挂载。自定义 mapping 的容器变量路径与只读挂载目标必须一致。

## 2. 入口、卷复用和健康

镜像实际入口如下，不要在 `dc up` 后追加 CLI 参数；需修改入口参数时先审阅 Compose override，保留完整必需参数。

```bash
.venv/bin/f1ap-agent-mq --run --daemon --ensure-runtime --existing-topology --runtime-dir /data/runtime --output /data/output
```

`--ensure-runtime` 只初始化新目录或完全空目录。已有 `runs.json` 时验证注册表和现存日志后复用。非空目录缺注册表、日志校验失败均拒启，不能删文件把它伪装成空卷。`--init-runtime` 只用于明确的新空目录，不能用于故障恢复或普通重启。`--runtime-dir` 与 `--output` 必须互不嵌套且不与契约目录重叠。

实际可选参数包括 `--wait-topology-seconds 60`、`--delivery-timeout 30`、`--confirm-timeout 10`、`--shutdown-timeout 30`、`--max-messages 0`、`--health-file PATH`、`--mapping PATH`、成对 `--mock-plan PATH --news-catalog PATH`。超时为秒且必须为正的有限数；`--max-messages 0` 表示不限消息数。`--daemon` 让空闲消费持续运行，不把 delivery timeout 当进程生存期限。`--existing-topology` 被动等待已有交换机和任务队列，不等待 API 建拓扑，也不完整验证所有回包绑定。完整拓扑检查见发布清单。

健康探针读取 `/data/runtime/.agent-health.json`，不占写锁、不连网络、不调用模型。仅 `HEALTHY`、订阅已注册、CONNECTED、心跳年龄 0 至 15 秒且 PID 的内核启动身份一致时 exit 0。心跳每 5 秒更新。当前实现有无 `--require-ready` 均要求同样的真实就绪条件；运维固定使用该参数。

| 状态或退出码 | 处置 |
| --- | --- |
| STARTING | 正在连接或订阅，核对预置拓扑与配置，不能用 PID 存在替代就绪 |
| DEGRADED | 连接失效，守护模式退避重连，恢复订阅后才转 HEALTHY |
| DRAINING | 已收到停止信号，不接新任务，等待在途处理 |
| FAILED 或探针 exit 1 | 核对进程、心跳、状态及持久卷，保留原件，不手工改健康 JSON |
| CLI exit 2 | 参数、配置或 RuntimeFault；读取脱敏错误码，修正后复用原卷 |
| CLI exit 1 | Broker/存储失败或排空超时，保留待发与未 ACK 消息 |

## 3. 停止与异常恢复

```bash
dc stop -t 35 f1ap-agent
dc stop -t 30 f1ap-api
dc ps -a
```

正常 SIGTERM 使 Agent 停拉取、写 DRAINING，等待在途线程、持久化、回包 confirm 和输出完成再退出。Agent `--shutdown-timeout` 默认 30 秒，Compose 35 秒只是外层强杀窗口；超时进程 exit 1，不能当成功排空。API 每生命周期阶段默认 25 秒，Rabbit listener 自身默认 20000ms，Compose 30 秒。25 秒并非整个进程的累计上限，真实排空耗时待 Task11。当前 Compose 未透传 `F1AP_SHUTDOWN_TIMEOUT` 和 listener 超时变量，单在宿主 `.env` 改值不会改变容器默认值；变更需显式容器 environment override 并重新核对停止窗口。

处理顺序是 Worker 持久日志、mandatory 发布且 confirm ACK 无 return、分别原子写 `output/request.json` 和 `output/events.json`，最后 ACK 源任务。两个文件不是跨文件事务，也不是所有 run 的归档；写失败或中途崩溃时不能 ACK。恢复重投后从已校验 runtime 日志重建输出，复用原 event_id。Broker confirm 不等于 API 已提交，API 还须 Inbox 事务提交后 ACK。

SIGKILL 只在 Task11 获准故障演练中使用。普通恢复先保留卷和错误证据，修正外部故障，再以同一已核验镜像和原卷执行 `dc up -d --no-build f1ap-api`、`dc up -d --no-build f1ap-agent`。`pending/` 中事件在确认后才进入 accepted 状态并删除待发文件；confirm 后、accepted 落盘前崩溃可能重发相同事件，由 API Inbox 去重。不要改 event_id、payload_sha256 或手工删 pending。仅重启不会凭空补齐已丢失的 Broker 消息，缺源 delivery 时必须先与 API/MQ 对账。

| 错误码 | 恢复动作 |
| --- | --- |
| WRITER_BUSY | 停止额外写进程，确认原写者退出后重试；锁文件存在不等于锁仍被持有，不删锁文件绕过 flock |
| LOG_CORRUPT、REGISTRY_CORRUPT、STATE_MISSING_OR_CORRUPT | 停写封存原卷，核对一致备份与 API 状态；不新建空 Store 覆盖历史 |
| CRASH_UNCERTAIN | 调用意图已持久化但副作用结果未决，按 UNKNOWN 对账；不自动重做同一调用 |
| TOPOLOGY_MISSING_ON_TEST_VHOST | 停止发布，核对交换机/队列/绑定，等待用户预置或当次建置授权 |
| MISSING_ENV、INVALID_PORT_OR_DEFAULT_VHOST | 核对五个 Rabbit 变量、合法端口及非默认 vhost，不输出值 |
| NEWS_FILES_REQUIRED、NEWS_CATALOG_HASH | 修正成对 mock 文件及计划绑定摘要，不换成真实新闻服务 |
| OUTPUT_RUNTIME_OVERLAP、OUTPUT_OVERLAP | 修正挂载目标，不让输出覆盖 runtime 或契约 |
| BROKER_OR_STORAGE_FAILURE | 区分连通、可写权限与磁盘错误；保留未 ACK 消息，修复后重启 |

`CRASH_UNCERTAIN` 是 Worker 恢复语义，未必作为 CLI stderr 原样输出；消费器可能拒绝非法或损坏任务，不可假设所有 RuntimeFault 都会自动重入队。需同时核对 API 运行状态与 Broker 投递记录。

用量补价也是写者，先停守护 Agent，再用同一卷运行一次，不能与常驻消费者并发：

```bash
: "${RUN_ID:?}" "${CALL_KEY:?}" "${REVISION:?}"
: "${MOCK_PLAN:?容器内只读路径}" "${NEWS_CATALOG:?容器内只读路径}"
: "${MOCK_REVISIONS:?容器内只读路径}" "${MOCK_SCENARIO:?}"
dc stop -t 35 f1ap-agent
dc run --rm --no-deps f1ap-agent --mock-plan "$MOCK_PLAN" --news-catalog "$NEWS_CATALOG" --mock-revisions "$MOCK_REVISIONS" --mock-scenario "$MOCK_SCENARIO" --reconcile-usage "$RUN_ID:$CALL_KEY:$REVISION"
dc up -d --no-build f1ap-agent
```

`dc run` 参数追加在镜像已有入口之后，因此继承 `--run --daemon --ensure-runtime --existing-topology` 和两个数据路径；reconcile 分支仅补发登记修订后退出。上述四个文件/场景必须由受控计划登记且实际只读挂载。跨月核验按不可变 call_started_at 归属 UTC 月，不是实钟跨月长跑。

## 4. 停写一致备份与恢复

这套 SOP 备份 Agent 文件状态和 API 导入文件，不是 MySQL/Broker 全库灾备。先停止上游导入及运维写入，依上节顺序停止 Agent、API，并确认两者已停止、没有额外写者。不要停止共享中间件。记录备份时间、镜像摘要、卷名、最后 run/event 身份和外部 DB/MQ 对账点。只有两个容器都停止后才能复制三个目录，否则 runtime/output 不一致。

使用已存在的停止容器复制，避免临时工具镜像拉取。`BACKUP` 必须是新建的私有绝对目录，包含 API 数据的备份绝不转交 Agent。

```bash
: "${BACKUP:?须为新的私有绝对目录}"
umask 077
mkdir -m 700 "$BACKUP"
API_CID=$(dc ps -a -q f1ap-api)
AGENT_CID=$(dc ps -a -q f1ap-agent)
test -n "$API_CID" && test -n "$AGENT_CID"
test "$(docker inspect -f '{{.State.Running}}' "$API_CID")" = false
test "$(docker inspect -f '{{.State.Running}}' "$AGENT_CID")" = false
docker cp "$AGENT_CID:/data/runtime" "$BACKUP/runtime"
docker cp "$AGENT_CID:/data/output" "$BACKUP/output"
docker cp "$API_CID:/data/imports" "$BACKUP/imports"
tar -C "$BACKUP" -cf "$BACKUP/agent-state.tar" runtime output
tar -C "$BACKUP" -cf "$BACKUP/api-imports.tar" imports
shasum -a 256 "$BACKUP/agent-state.tar" "$BACKUP/api-imports.tar"
```

记录两个摘要到私有备份登记表。恢复先重新计算摘要比对，禁止覆盖原卷或删除原备份。若只是重建容器，`dc up -d --no-build` 保留同一 project 的命名卷，无需重新初始化。恢复到新卷时使用同一 Compose 文件和另一个 project，仅创建停止的容器，禁止两套写者同时运行：

```bash
: "${RESTORE_PROJECT:?须为未使用的新 project}"
restore_dc() { docker compose --env-file "$DEPLOY_ENV" -p "$RESTORE_PROJECT" -f "$COMPOSE" "$@"; }
restore_dc create --no-build f1ap-api f1ap-agent
NEW_API_CID=$(restore_dc ps -a -q f1ap-api)
NEW_AGENT_CID=$(restore_dc ps -a -q f1ap-agent)
docker cp "$BACKUP/runtime/." "$NEW_AGENT_CID:/data/runtime"
docker cp "$BACKUP/output/." "$NEW_AGENT_CID:/data/output"
docker cp "$BACKUP/imports/." "$NEW_API_CID:/data/imports"
restore_dc run --rm --no-deps --user 0:0 --entrypoint chown f1ap-agent -R 10001:10001 /data/runtime /data/output
restore_dc run --rm --no-deps --user 0:0 --entrypoint chown f1ap-api -R 10001:10001 /data/imports
```

上面两个一次性 root 命令仅修正新卷所有权，不启动应用；正常应用仍为 `10001:10001`。源容器与恢复容器必须保持停止，先对账备份中的 runs/log/pending/accepted 与外部数据库、Broker。**若外部数据库已领先备份，严禁初始化空 Store 或直接启动旧备份消费**，必须由 API/MQ 所有者确认一致恢复点及重投方案。缺有效一致状态时停止于 BLOCKED，不生成新历史。

对账通过后才依次 `restore_dc up -d --no-build f1ap-api`、API readiness 检查、`restore_dc up -d --no-build f1ap-agent`、订阅探针和同 event_id 幂等核验。保留旧卷供回退，禁止 `down -v`、共享卷删除或全局 prune。Task11 还须实测 cp、tar、权限、待发续传和清理，当前均待执行。
