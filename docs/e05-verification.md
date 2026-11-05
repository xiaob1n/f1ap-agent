# E05 Agent 验证与交接

## 最终结论：2026-11-03 自建服务真实验收 PASS

下方记录为历史过程。本次最终E04入口run `e04-20261103T083731Z-60079920` 十一项真实场景PASS；最终E05 run `e05-20261103T085217Z-d3d782ed` 八项真实场景PASS，含入口及本地矩阵14/14 PASS、运行器exit0、独立证据校验exit0。详证和清理收据见 [API最终记录](../../f1ap-api/docs/e05-verification.md)。

实际数据来自用户自建OpenF1 HTTP服务及其MongoDB后端；目标摩纳哥1286/11299，特征P3 11294（SOFT、胎龄3–20、22候选每人至少5圈），历史加拿大排位1285/11287及进站1285/11291。未混合配方、未实施巴库轮胎校准、未降低样本门。范围旗号与截止处理修复后原bootstrap仍seed0/10000次/每次5圈。

真实MQ共11事件（10题+finished），十题均FAILED/RULE_BLOCKED，probabilities/recommendations为空、usage为0，独立Java验证通过。pace250样本；排位、pit和分类存在真实缺失或单位问题，`features.json`明确记录，未补造。工程链路通过不意味着真实十题算法/规则全部可用，更不产生正式VALID/LIVE。

Agent全量150/150，API110/110及真实IT1/1，工具98/98。临时MongoURI文件已删除，隧道结束，无模型/新闻/Embedding调用、DDL、部署或Git提交。

## 2026-11-03 后续真实输入验证

下文为原本地交接记录；固定场次现已确认，真实八端点已冻结到 `/private/tmp/f1ap-e05/e05-real-preparation/`。历史排位为1296/11384，历史 `pit_session` 为1296/11388，特征为P2 11390，不读取目标正赛/排位结果。

实际响应数：sessions1、drivers22、laps525、stints84、weather60、race_control102、pit25、session_result22。排位提取66个分段值；pace为OPENF1_EVENT_RANGE，pit为CANDIDATE_SAMPLES_MISSING，分类元数据21人完整。数据不足保持显式，不补造分类先验。

客户端已修正OpenF1比较参数编码，并仅将精确的404 No results found响应规范化为空结果；其余错误仍失败。快照保留原始字节和observed_fields，特征输入只投影登记字段。最新全量146项通过。E04入口尚未放行，E05八项跨服务验收仍未运行，详见 [API记录](../../f1ap-api/docs/e05-verification.md)。

日期：2026-11-03。范围：E05 本地层 Agent B3/B4，包括 4.3 OpenF1 白名单扩展与共享特征、4.4 十题统计方法、4.5 LangGraph 固定图与多策略输出。用户本次选择"先做本地，真实层记 BLOCKED"，所以没有调用真实 OpenF1、MQ、DB 或 Redis，也没有调用模型、新闻或 Embedding。工作在 `dev` 分支上完成，没有 Git 提交。

对端交接：[API E05 验证与交接](../../f1ap-api/docs/e05-verification.md)，统一证据门和场景矩阵都记录在那里。接口与版本化假设：[e05-interfaces.md](e05-interfaces.md)。子任务的原始验证记录：[e05-agent-local-verification.md](e05-agent-local-verification.md)。

## 1. 结论

- **本地层：PASS。** Agent 测试共 **142/142**（E04 基线 87），新增 55 项：十题四类 40 项（每题正例 / 反例 / 数据不足 / 未知规则各 1 项）、固定种子确定性 1 项、特征 6 项、语义 oracle 3 项、图场景 5 项。E04 的 profile、恢复和契约回归都包含在内。
- **真实层：BLOCKED。** 没有固定 OpenF1 ID，`config/openf1/round-mappings.json` 仍为空配置；扩展字段只核对过 OpenF1 公开文档，没有用真实数据抽验。
- 真实 V18 规则仍为 BLOCKED，研究链路只会产出 `FAILED/RULE_BLOCKED` 或 `DATA_INSUFFICIENT`。

## 2. 分层结果

| 层级 | 命令 / 证据 | 结果 |
| --- | --- | --- |
| `LOCAL_TEST` | `TMPDIR=/private/tmp .venv/bin/python -m unittest discover -s tests -q`（主会话于 `2026-11-03T03:41:59Z` 起最终重跑） | `Ran 142 tests … OK` |
| `LOCAL_TEST` | `python -m f1ap_agent.e05_fixture --scenario {all-valid,partial,rule-blocked}`，由 API 端 `run-e05.py` 在 run `e05-20261103T034608Z-851b6cd8` 中调用 | 三场景 CLI 退出 0；VerifyTen 均 PASS（`results=10 events=11`）；run_status 依次为 SUCCEEDED / PARTIAL / FAILED |
| `LOCAL_TEST` | 冻结契约 `evaluate` 逐事件复验（子任务记录） | 33/33 事件接受 |
| `LOCAL_TEST` | 定向 basedpyright（questions、graph、strategies、features/ten.py、e05_fixture.py、worker、data/openf1、baselines/podium.py） | 0 errors，78 warnings；该安装把 warnings 也算作非零退出，未加任何压制 |
| `OFFLINE_STATIC` | `uv.lock` + 导入测试 | `langgraph==1.0.3` 已锁定，`from langgraph.graph import StateGraph` 导入成功 |
| `REAL_SERVICE` | 真实 OpenF1 读取与冻结 | BLOCKED |

全量 `src` 的 basedpyright 检查有 63 个错误，全部在 E05 之前已存在的 `contracts/` 模块中（artifact 37、cli 5、raw_json 2、semantics 12、verifier 7），本阶段没有修改它们。

## 3. 交付与对应测试

| 计划项 | 交付 | 能证明它的测试 |
| --- | --- | --- |
| 4.3 | `data/openf1/{config,client,prepare,snapshot}.py`：E05 八端点白名单（新增 `session_result`，`pit.stop_duration`/`lane_duration`，`drivers.team_name`）；`features/ten.py`：带单位、样本数、缺失原因、可得时间的特征 | `tests/questions/test_features_e05.py`：未登记端点或字段被拒、缺 `stop_duration` 时不拿 `lane_duration` 代替、cutoff 之后的数据被拒、单位错误、元数据记录、MockTransport 请求边界 |
| 4.4 | `questions/{race_outcome,qualifying,fastest_lap,pit_stop,classification}.py` | `tests/questions/test_ten.py` 40 项四类测试、固定种子逐字节一致；`test_semantics.py` 验证 Q10 在同一批样本上联合计数，不把边际概率相乘 |
| 4.5 | `graph/`：9 节点固定图，`recursion_limit=12`，新闻节点关闭，无模型节点；`strategies/`：同一份概率生成 `EXPECTED_POINTS_V1` 和 `HIT_RATE_V1`；Worker 接入（替换 `UNSUPPORTED_E04`，E04 profile 保留）；`e05_fixture.py` | `tests/graph/test_graph.py`：全部 VALID、PARTIAL、全部 RULE_BLOCKED、单题异常不影响其他题、重复运行一致；`test_semantics.py` 覆盖两个策略目标的差异和 Q1 HIT_RATE 排序 |

HIT_RATE 采用用户确认的定义：FIXED 题选命中概率最高的候选；Q1 选进入前三边际概率最高的 3 名车手，按该概率排序依次占第 1–3 席。两个策略的 `expected_score` 都按规则计分公式计算。

## 4. 版本化假设（不是现实中已确定的概率）

Q1/Q7/Q9/Q10 共用一次练习圈 bootstrap（seed=0、10000 样本、每样本 5 圈），属于练习代理，不是经过校准的正赛概率。Q2–Q6 和 Q8 使用历史点先验；分类使用显式的受控先验 `CLASSIFICATION_FIXED_V1`，不从 DNF/DNS/DSQ 推断。完整说明见 [e05-interfaces.md](e05-interfaces.md)，各题与规则阻塞项的对照见 [支持矩阵](../../f1ap-api/docs/e05-support-matrix.md)。

## 5. 交接

- 真实层：需要用户登记固定 ID（含 `qualifying_session`）和 `question_bindings`，并用真实数据抽验 `session_result` / `stop_duration` 字段的可用性；字段不可用的题记 `DATA_INSUFFICIENT`。
- E06 及以后：校准真实概率、新闻节点、模型调用都不在本阶段范围内。规则门 R01–R05、R10 由 API 端负责解除。
