# E05 Agent 离线接口

范围：`LOCAL_TEST`，无真实 OpenF1 GET、MQ、DB、Redis、新闻、LLM、Embedding。真实 V18 的 R01–R05/R10 保持 BLOCKED；只有 API 提供的 `TEST_FIXTURE` 合成规则可驱动 VALID。

## 依赖、profile 与入口

LangGraph 固定 `1.0.3`，`uv.lock` 锁定传递依赖。图为 validate → classify → time_quality → features → news_disabled → probability → strategy → explain → output_validate，9 节点、`recursion_limit=12`，无循环/模型/checkpoint；恢复沿用 Worker 日志。

E05 profile：

```json
{"algorithm_version":"TEN_QUESTION_GRAPH_V1@<mapping 原始字节 sha256>","tools":["OPENF1_READ"],"strategies":[{"strategy":"EXPECTED_POINTS","version":"EXPECTED_POINTS_V1"},{"strategy":"HIT_RATE","version":"HIT_RATE_V1"}],"model_config_ref":"NO_MODEL","price_version":"NOT_APPLICABLE"}
```

保持 `purpose=RESEARCH`、`fixture_class=CONTROLLED_INPUT`，0 usage。`PODIUM_PACE_BOOTSTRAP_V1@...` 原 E04 profile 与 UNSUPPORTED_E04 行为继续兼容。每次完整输出十个 completed 与一个 finished，按有效题组合得 SUCCEEDED/PARTIAL/FAILED。

```sh
python -m f1ap_agent.e05_fixture --output <不存在的新目录> --scenario all-valid
python -m f1ap_agent.e05_fixture --output <不存在的新目录> --scenario partial
python -m f1ap_agent.e05_fixture --output <不存在的新目录> --scenario rule-blocked
```

根目录产物为 request.json、events.json、context.json、mapping.json、manifest.json 和八个原始响应文件；runtime/ 为恢复日志，不含完整模拟样本。`build_fixture(root, scenario)` 只构造输入，`run_fixture(root, scenario)` 经真实 Worker 运行。三种场景分别为十题 VALID；Q6 数据不足、Q10 RULE_BLOCKED、其余 VALID；十题 RULE_BLOCKED。加载 `f1ap-api/tools/e05/fixtures/rules/definition-{verified,unknown}.json`，不读取 answers/。

## mapping 与冻结白名单

沿用 `OPENF1_MAPPING_V1` 信封。E04 七端点字段保持不变。E05 通过新增 `question_bindings` 和 `qualifying_session` 选择扩展配置，必须明确列出 `E05_FIELDS` 八端点的所有字段；不接受未知端点、额外字段或重复字段。新增：

真实层增加可选 `pit_session: {meeting_key, session_key, session_type: "Race"}`，仅决定 pit 端点的历史来源，禁止与目标正赛相同。未提供时保留原练习场次行为。提供时不套用练习场次的事件开始下界，但仍限制在事件截止之前；历史进站不会作为当前练习圈的进站排除记录。实际登记为新加坡1296/11388，历史排位为1296/11384。

上述“额外字段拒绝”针对 mapping 声明。上游原始响应可含更多字段，冻结字节及 `observed_fields` 完整保留；快照消费只投影 mapping 登记字段。OpenF1 URL 比较参数名以 `>` / `<` 结束，由编码器添加分隔等号。仅精确404正文 `{"detail":"No results found."}` 规范化为空数组，其他非200响应仍失败。

- drivers：`team_name`，合成场景用受控车队键作为队名；真实适配必须显式登记队伍绑定，不按显示顺序推断。
- pit：`stop_duration`（停驻秒数）、`lane_duration`（维修区总秒数）。Q6 仅用前者，不以 `lane_duration` 或已弃用的 `pit_duration` 替代。
- session_result：meeting_key、session_key、driver_number、position、dnf、dns、dsq、duration、number_of_laps。来自明确的 `qualifying_session`；duration 数组为 Q1/Q2/Q3 最佳圈秒数，null 表示无该阶段有效成绩。

文档字段核对来源（2026-11-03，仅读取公开页面，未调用数据 API）：[OpenF1 session result](https://openf1.org/docs/#session-result)、[pit](https://openf1.org/docs/#pit)、[drivers](https://openf1.org/docs/#drivers)。文档明确 session_result 在官方结果公布数分钟后可用；不将未来目标场次结果当赛前特征。使用的是独立历史场次，manifest 生成/首次观测时间必须早于 data cutoff；圈结束、pit 日期必须早于 event cutoff。

`question_bindings` 以逻辑题号 1..10 为键；不按模板 ID 路由。车手候选 `drivers` 是本题 option_key → Q1 规范 driver key；车队 `teams` 是本题 option_key → driver keys；Q4 members/stages、Q8 counts/classified、Q9 driver/categories/classified、Q10 conditions/yes/no 均须显式登记。Q10 conditions 每条为 driver/min/max，表示同一排列样本中的闭区间名次条件，全部同时成立才计 Yes。

分类先验 `CLASSIFICATION_FIXED_V1` 明确提供每名相关车手 true/false；这是受控输入假设，不从 DNF/DNS/DSQ、圈数比例或名字推断。Q8 必须恰四名车手。缺少先验不能产出 VALID。真实规则未核验时即使有先验也仍 RULE_BLOCKED。

## 逐题方法和不足条件

赛控范围适配（2026-11-03）：`race_control` 可在既有字段之外整组登记 `scope`、`sector`、`driver_number`，API与Agent均验证该字段集合。扩展映射按Track、Sector、Driver分别维护状态；CLEAR/GREEN只解除对应键，其他仍有效的黄旗不受影响。圈内任一时刻存在适用的非清除状态，该圈不用于统计。同秒不同范围的独立消息不按时间误判重复；完全重复的消息仍拒绝。未在截止前完成的圈按用户确认逐圈排除，候选数量、同配方和至少5圈要求不变。

公共源新加坡1296/11378重新冻结后按该实现核验：22名候选只有车号11（5圈）、77（13圈）达到MEDIUM/胎龄3–20/至少5圈要求，其余0–4圈，baseline仍为`OPENF1_COMPARABLE_LAPS_MISSING`。独立数据检查不等于E04/E05真实联调通过。摘要 `/private/tmp/f1ap-e05/singapore-quality-summary.json`，冻结目录 `/private/tmp/f1ap-e05/singapore-scoped-frozen/`。本次Agent150项、API110项回归通过。

| 题 | 方法 / 概率族 | 数据与不足条件 | 版本化假设 |
| --- | --- | --- | --- |
| Q1 | RACE_PACE_BOOTSTRAP_V1 / RANK_MARGINAL | 每车至少5条可比干地有效练习圈，至少3车 | PRACTICE_PROXY_V1：5圈抽样中位数模拟排名；不是校准正赛概率 |
| Q2 | QUALIFYING_EMPIRICAL_POINT_V1 / EXCLUSIVE | 所有候选三段历史排位成绩；无Q3有效成绩时 NO_AWARD | HISTORICAL_SEGMENTS_POINT_PRIOR_V1：历史分段点先验，选Q3最小圈时，不看发车位 |
| Q3 | 同Q2，共享 Qualifying 对象 / EXCLUSIVE | 两名指定车手成绩；全无成绩 NO_AWARD | 先阶段、后该阶段圈时；不混用处罚后发车位 |
| Q4 | 同Q2 / EXCLUSIVE | 两名指定队员三段成绩，缺少队员不足；全未参加 NO_AWARD | 用两车最大非空阶段，不能默认Q1 |
| Q5 | FASTEST_LAP_POINT_V1 / EXCLUSIVE | 所有候选有效圈池缺一不足 | PRACTICE_MINIMUM_POINT_PRIOR_V1：历史有效圈最小值点先验；无前十/完赛限制 |
| Q6 | PIT_SERVICE_POINT_V1 / EXCLUSIVE | 所有车队成员 stop_duration 样本缺失或单位/时间错误不足 | HISTORICAL_STOP_POINT_PRIOR_V1：历史停驻最小值；不使用维修区总耗时 |
| Q7 | RACE_JOINT_BOOTSTRAP_V1 / EXCLUSIVE | 共享Q1排列、四车显式分类先验；全未完成 NO_AWARD | CLASSIFICATION_FIXED_V1，比较允许分类车手在同一排列中的名次 |
| Q8 | CLASSIFICATION_POINT_V1 / EXCLUSIVE | 五个0–4 OPTION、四车显式分类先验，缺失不足 | CLASSIFICATION_FIXED_V1，固定分类点先验；非自由NUMERIC |
| Q9 | RACE_JOINT_BOOTSTRAP_V1 / EXCLUSIVE | 共享Q1排列、目标车手与分类先验 | Podium/4–10/11–22/Not Classified，最后一项正常计分 |
| Q10 | RACE_JOINT_BOOTSTRAP_V1 / EXCLUSIVE | 共享Q1排列与完整conditions | 同一10000样本逐项联合计数，绝不乘边际；不是冠军真实规则证明 |

所有并列按规范候选键稳定排序。Q1/Q7/Q9/Q10 复用 E04 的同一次 bootstrap：seed=0、samples=10000、laps_per_sample=5。完整排列只驻内存，不进 wire；方法/先验/种子/样本数/误差写 evidence.excerpt。离散计数除以10000生成最多4位小数 canonical 概率，EXCLUSIVE 严格和1，RANK_MARGINAL 每名次和1。抽样标准误差上界0.005仅指 Monte Carlo，不含模型失配；点先验没有抽样误差，也不代表现实确定性。

Feature 对象记录 unit=microseconds、sample_count、missing_reason、available_at；圈时用 Decimal 转整数微秒。另保留 classification 特征的 rank/dnf/dns/dsq/laps 原始状态向量，单位 `rank_dnf_dns_dsq_laps`，不把该向量擅自等价成游戏 classified。E05 manifest 显式声明 stop_duration/lane_duration 的 seconds、duration 的 seconds_by_qualifying_segment 和名次/圈数/布尔单位。数据不足的题不取消其他题。未知规则优先 FAILED/RULE_BLOCKED；非VALID没有概率/推荐；候选非法在请求/输出冻结契约边界拒绝。只支持上述版本化计分规则；未知 no_award 语义不得默认零分。

## 策略语义

同一份概率一次生成两个推荐，不重跑图、不重读数据。FIXED 的 HIT_RATE 选命中概率最大候选；EXPECTED_POINTS 选 `P(hit)*候选分值` 最大候选。Q1 HIT_RATE 取 top-3 边际最大三车，按该边际排序赋第1–3席；EXPECTED_POINTS 枚举不重复三席，得分为每车基础分乘 `P(top3)+(exact_multiplier-1)*P(exact rank)`。两者 expected_score 都按同一计分式算，不把命中率当积分。NO_AWARD 贡献0，不作为可选候选。并列用候选键稳定破同分。expected_score 仅在输出边界 ROUND_HALF_EVEN 至6位，概率不舍入。
