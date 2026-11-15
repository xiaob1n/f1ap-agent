# f1ap-agent（Python Worker 仓库）

> init-deep 评分 10/17：文件数>20×3、自有 pyproject×1、模块边界×2、符号密度×2、导出×2；代码占比未测量、不计分；子目录数未过 >5 阈值、引用中心度未测量。独立域：单进程 Python Worker 仓库。包内代码结构见 src/f1ap_agent/AGENTS.md。本文件自足可用；本地工作区另有根 AGENTS.md 汇总跨仓共享规则，存在时作补充参考。

## OVERVIEW
Python 3.12（uv + hatchling）单进程 Worker：消费 f1ap.agent.tasks.v1，固定图计算十题预测并回发事件；自带与 Java 端语义一致、代码独立的契约校验器。运行依赖仅 aio-pika/httpx/jsonschema/langgraph（pyproject.toml）。本仓库不连 MySQL/Nacos/Redis。

## WHERE TO LOOK
| 任务 | 位置 |
| --- | --- |
| 运行/部署 | docs/runbook.md、Dockerfile（`uv sync --frozen --no-dev`；容器入口带 `--existing-topology`，绝不声明 MQ 拓扑） |
| 契约锁定 | contracts.lock.json（manifest 摘要）↔ 本地同级 f1ap-api 的 contracts/v1/manifest.json |
| LLM 接入 | docs/llm-prediction.md、config/llm.example.toml（只含 model、api_key_env=DEEPSEEK_API_KEY、timeout、max_tokens） |
| 题目同步（停用未交付；本地补充） | docs/question-sync.md、src/f1ap_agent/question_sync.py、config/question-sync.toml（均未跟踪，克隆中无） |
| Exa 来源搜索（本地补充） | docs/websearch.md、src/f1ap_agent/websearch.py、config/websearch.toml（均未跟踪，克隆中无） |
| 依赖矩阵 | docs/dependency-matrix.md、docs/candidate-dependencies.in/.lock |
| 阶段验收历史 | docs/ 内 e01–e07、e10 的 verification/interfaces 文档（计数为历史回执，当前树无可复现测试套件） |

布局：src/f1ap_agent/（业务代码，另有指南）、config/、docs/（*evidence* 目录被 gitignore）、tests/（gitignore，仅 2 个文件）、.venv/、uv.lock。

## CONVENTIONS
- 凭据只经环境变量：F1AP_RABBITMQ_HOST/PORT/VHOST/USERNAME/PASSWORD、F1AP_INTERNAL_WRITE_TOKEN、DEEPSEEK_API_KEY、F1AP_WEBSEARCH_MCP_URL（本地 websearch 补充用）；密钥不进配置文件与日志。
- 落盘一律 atomic_json（临时文件+替换）；跨进程状态用 runtime 的 Store.exclusive() 文件锁。
- 容器以非 root 10001:10001、TZ=UTC 运行。
- pyproject.toml 的 description 是历史文案（"E01 offline contract verification only"），与实际能力不符，勿据此判断。

## ANTI-PATTERNS
- 网络失败不自动重试（deepseek 传输异常 → UNKNOWN/LLM_TRANSPORT_UNKNOWN）；出站 HTTP 端点强校验（本地补充 question_sync 仅 127.0.0.1/localhost，websearch 仅 https://mcp.exa.ai）。
- websearch（本地补充）只输出 AWAITING_HUMAN_CONFIRMATION 的候选来源，不当作题目或已验证规则。
- question_sync（本地补充）调用的 POST /internal/v1/round-syncs 不在 API 主源码中（本地 docs/question-sync.md 自述无法重建）；重启同步前须先恢复 API 侧入口，且不要重复启动多个同步器。
- 2026-11-15 起的补充源码（question_sync.py、websearch.py、两份 toml、两份 docs）均为未跟踪的本地工作树补充，不在提交范围、克隆中不存在；tests/ 与 docs/*evidence* 亦不入库。
- 不修改 contracts.lock.json，不维护第二份 Schema。

## COMMANDS
```bash
cd f1ap-agent && TMPDIR=/private/tmp .venv/bin/python -m unittest discover -s tests -v   # 仅 2 个本地测试文件（gitignore，克隆中无）
.venv/bin/f1ap-contract-verify --contracts ../f1ap-api/contracts/v1 --lock contracts.lock.json --report <文件> --roundtrip-out <回环输出目录>   # 契约目录取自本地同级 f1ap-api；--roundtrip-out 必填（contracts/cli.py），为已收纳夹具的规范化回环输出目录，不得与契约/lock/report 重叠
.venv/bin/python -m f1ap_agent.worker --runtime-dir <目录> --fixture <文件> --output <文件>   # 单任务本地 CLI
F1AP_RABBITMQ_HOST=… F1AP_RABBITMQ_PORT=… F1AP_RABBITMQ_VHOST=… F1AP_RABBITMQ_USERNAME=… F1AP_RABBITMQ_PASSWORD=… \
  .venv/bin/f1ap-agent-mq --run --daemon --ensure-runtime --existing-topology …   # 消费进程，详见 docs/runbook.md
```
