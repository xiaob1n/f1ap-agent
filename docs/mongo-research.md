# MongoDB 当前数据研究

图为 prepare → model → query → model → validate；模型自主选择允许的集合、车手和圈号。CLI 可独立运行，也可由正式 MQ Worker 执行，见 `unscored-mq.md`。通过 `--definition` 加载十题后，输出 `UNSCORED_RESEARCH_V1` 无积分研究结果：只考虑最可能选项或有序组合，不计算预期积分，不受计分规则 BLOCKED 阻塞；仍检查候选合法性、选择数量、顺序和证据。依据不足时输出 DATA_INSUFFICIENT，不编造选项。

`predictions.json` 使用 request_id；MQ 路径替换为可信任务身份，通过独立完成消息回传 API 入库。旧冻结1.0.2协议不变，不将无积分结果标成其 VALID 或送入结算。Mongo 查询证据单独保存，不伪造截止前冻结证据。最终响应接受纯 JSON 或单个完整 json 代码围栏，拒绝额外正文、重复键和非法数值。结果校验通过不代表准确率提升或历史回测无泄漏。

Python 3.12，`uv sync --frozen`；主机需要 Node/npm。先运行 `npm install --prefix .local/mongodb-mcp --no-audit --no-fund mongodb-mcp-server@3.3.0`，成功后执行 `export PATH="$PWD/.local/mongodb-mcp/node_modules/.bin:$PATH"`。CLI 直接启动已安装的 `mongodb-mcp-server`，不在初始化期间下载。2026-11-15 已核实官方 3.3.0 并实际完成 stdio 初始化和真实 MongoDB 查询；filter 使用 EJSON 日期对象，结果读取 structuredContent.documents。

仅在用户当次明确授权后设置环境变量 `MDB_MCP_CONNECTION_STRING`、`DEEPSEEK_API_KEY`。从 Agent 根目录安全加载用户自行保存的私有文件（须自行确保不入版本库，文件权限应为 600，禁止 `set -x`）：

```sh
set +x
set -a
. ./.env.mongo-research.local
set +a
```

然后执行：

```sh
uv run --frozen f1ap-mongo-research \
  --question '比较本场已观测到的车手圈速，给出预测参考并说明不足' \
  --session-key 12345 --cutoff 2026-11-15T00:00:00Z \
  --config config/mongo-research.example.toml \
  --output /private/tmp/f1ap-research-new-run --authorize-model
```

示例 session key 仅为参数示意，须提供真实数字，绝不按名称猜测。URI 通过安全环境交接，不写命令参数、配置或报告。既有 principal 为 `agent`，认证库 `admin`，角色 `read(openf1-livetiming)`；工具额外开启官方 readOnly。固定 DeepSeek 地址 `https://api.deepseek.com/chat/completions`、模型 `deepseek-flash`，HTTP 禁用重定向与自动重试。授权范围是本次运行最多 `max_model_rounds` 次请求；该轮数是无限循环断路器，不是费用预算。

输出目录必须不存在；失败目录也不可恢复或覆盖，防止重放既有付费意图。`request.json` 保存问题与观察时间，`model-NNN.json` 在请求之前记录 UNKNOWN，结束后记录 token 与结果，未知 token 保持 null，cost 始终 null/UNKNOWN。`query-NNN.json` 保存实际约束、观察时间与有界结果；`answer.json` 保存答案、查询编号引用与局限；`status.json` 为最终状态。失败退出非零并保留证据。SDK 的异步上下文负责关闭会话与子进程。第三方 stderr 与 Mongo MCP 日志关闭，错误报告只含类别，避免连接串进入日志。

查询仅允许 find，固定库 `openf1-livetiming`，禁止任意 shell、聚合、原始 MQL、库名、投影和排序输入。模型可选 sessions/drivers/laps/pit/weather/race_control/stints，附加正整数 driver_number/lap_number。sessions 通过数字 `_id` 精确匹配，其余通过 session_key；查询行数和字节在服务器与客户端均受限，返回显式标记 sample_only。投影为字段白名单，剔除内部元数据及 stints 的可变 lap_end。

时间过滤采用 BSON date 类型：sessions/laps 使用 date_start，pit/weather/race_control 使用 date，stints 使用 `_date_start_last_lap`，无日期或非 BSON date 行不会入选。drivers 没有事件时间，属于当前元数据。**这些字段映射仍须真实库验证**。截止过滤只约束字段时间，不能恢复过去的数据可见性：圈速可能在圈开始后更新、stint 可变、session 信息可能后来修订。本版只支持当前研究，不保证历史回测无泄漏。应把 cutoff 设为本次观察时刻，不能声称历史快照；模型建议未经校准或事实独立审计。

本地测试：`TMPDIR=/private/tmp .venv/bin/python -m unittest discover -s tests -p test_mongo_research.py -v`。真实数据库、真实付费模型和完整预测效果另需当次授权；本地 wire fake 测试不能替代。

## 模型按需加载研究 skill

系统提示只列出可用 skill 的名称和简介，由模型自主调用 `load_skill({"name":"lap-analysis"})` 读取正文。圈速要求位于 `src/f1ap_agent/mongo_research/skills/lap-analysis/SKILL.md`，随 Python 包发布，Docker 通过现有 `COPY src/ src/` 一并携带。正文不是默认注入，也不扫描本机其他 skill 目录；新增发布 skill 时在 `skills/__init__.py` 的 CATALOG 登记名称和简介。

`lap-analysis` 要求核对车手覆盖、有效独立圈、重复记录、胎种和赛段可比性，规划剩余查询轮次，并在数据不足时逐题说明。它不扩大现有 find 的会话、字段或行数权限，也不能取得当前工具不支持的数据。总模型轮数保持原配置；选择 skill 可能使用一轮，最后一轮仍只作答。

每次加载写入独立的 `skill-NNN.json`，包含时间、名称、结果和实际读取的正文。文件缺失、不可读或编码错误记为 `SKILL_UNAVAILABLE`，空文件记为 `SKILL_EMPTY`，未登记名称记为 `SKILL_NOT_FOUND`；模型收到原因后沿用基础提示继续。额外路径参数被拒绝。加载次数不计入 MongoDB 查询数，不提供 query_id，不能用作比赛 evidence_ids。模型也可以不选 skill，原有预测流程保持可用。

离线验证只能证明目录、加载、降级、证据隔离和输出接线；模型是否会主动选择、遵循要求，以及真实命中率是否提高，需要另外授权的真实模型与比赛答案对照验证。

## 本次本地核验记录（2026-11-15）

- `uv lock && uv sync --frozen`：成功解析 65 个包；`uv lock --check` 成功。新增依赖仅 MCP SDK 与 anyio 及其必要传递依赖。
- 官方 registry 查询 `npm view mongodb-mcp-server@3.3.0 version dist.tarball --registry=https://registry.npmjs.org --json` 成功；官方包 `--help` 退出 0。隔离 npm 安装曾达到 300 秒超时，保留失败记录；已下载产物随后真实执行 initialize/tools/list，返回 18 个只读模式工具与 find schema，正常关闭。没有配置连接串，没有连接数据库。schema 要求 connectionId，代码固定传 `preconfigured`。
- 实际 CLI 使用本地 stdio 假服务与 HTTP wire fake：覆盖授权缺失、目录防覆盖、成功研究落盘、缺少凭据失败落盘；图测试覆盖工具结果、十进制圈速、元数据剔除、调用 token、无自动重试、轮数上限与非法证据引用。
- 最终受影响回归：`python -m unittest discover -s tests -p test_mongo_research.py -v`，6 tests / 0 failures / 0 errors，1.934 秒；`ruff check src/f1ap_agent/mongo_research tests/test_mongo_research.py` 为 All checks passed；`python -m compileall -q src/f1ap_agent/mongo_research` 退出 0。未跳过任何测试。
- 全套 `python -m unittest discover -s tests -v` 已运行：既有 question_sync 的 5 个错误均为缺少 `f1ap-api/docs/examples/manual-round-request.json`；未修改或隐藏。该轮新 CLI 测试装配错误已修复，后续只重跑受影响研究套件。既有 websearch 的 2 项通过。
- `basedpyright --pythonpath .venv/bin/python src/f1ap_agent/mongo_research --outputjson`：0 errors / 10 warnings；包括 LangGraph 缺类型存根/部分未知类型及标准 JSON 解码的 Any，不宣称类型检查全绿，没有压制诊断。
- 测试中合成数据不是比赛事实。真实 MongoDB 读取、真实 DeepSeek 调用、十题冻结预测、历史可用性与预测效果均未验证。
