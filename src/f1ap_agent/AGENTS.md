# f1ap_agent（Agent 业务包）

> init-deep 评分 13/17：文件数>20×3、11 个子包>5×2、纯 Python×2、__init__ 模块边界×2、符号密度×2、导出×2；引用中心度未测量。独立域：Worker 状态机与契约校验实现。仓库级打包/命令见同仓库 ../../AGENTS.md。本文件自足可用；本地工作区另有根 AGENTS.md 汇总跨仓共享规则，存在时作补充参考。

## OVERVIEW
预测 Worker（持久状态机）+ Python 侧独立契约校验 + OpenF1 数据准备 + 十题计算器与固定图编排。

## STRUCTURE
| 模块 | 职责与关键符号 |
| --- | --- |
| worker/ | engine.py 的 Worker（:186）run 状态机（validate_log :35 是恢复唯一闸门；_finish :414 生成 usage+逐题+finished 事件并逐条回调契约校验）、llm.py（BLOCKED 题直接 RULE_BLOCKED）、news.py（NewsDependency 可重投 / NewsInvalid 拒收）、research.py、models.py（load_contract 强制 release 1.0.2） |
| contracts/ | verifier.py 的 evaluate、semantics.py（禁入字段与结果断言，validate_result）、artifact.py（safe_file 拒 symlink/逃逸、manifest 字节校验）、cli.py（离线契约门）——独立实现，不 import 发布方代码 |
| messaging/ | consumer.py（手工 ACK、延迟确认）、__main__.py（argparse 入口）、health.py |
| models/ | calls.py（调用意图先落盘再执行，崩溃按 UNKNOWN 不重做）、mock.py、deepseek.py（无自动重试） |
| features/ | cleaning.py（旗号状态机、胎龄窗口、每候选 ≥5 圈门槛）、ten.py、race_events.py |
| graph/ | 九节点固定图 run + output_validate 逐结果自校验 |
| questions/ | 6 个题型模块各导出 calculate(...)（classification/fastest_lap/pit_stop/qualifying/race_events/race_outcome）；__init__.py 只提供统一返回类型 Distribution/exclusive，不导出 calculate |
| strategies/ | recommend/score（排列枚举 + ROUND_HALF_EVEN） |
| runtime/ | atomic_json、Store.exclusive() 文件锁、JSON 类型守卫 |
| data/openf1/ | client/prepare/snapshot/config/boundary（safe_path 白名单读取） |
| baselines/ | podium.py 领奖台 bootstrap |

顶层模块：e04_fixture.py、e05_fixture.py（合成夹具生成）；websearch.py、question_sync.py 为未跟踪的本地工作树补充，不在提交范围、克隆中不存在（用法记录在仓库 docs/ 的未跟踪同名文档）。

## WHERE TO LOOK
| 任务 | 位置 |
| --- | --- |
| 恢复/崩溃语义 | worker/engine.py 的 validate_log、models/calls.py 检查点 |
| 结果断言（策略/picks/概率族/求和/证据时间） | contracts/semantics.py 的 validate_result |
| 事件发送顺序 | worker/engine.py 的 _send（先写 pending/ 再本地接受） |
| 新增题型 | questions/<题型>.py，返回 questions/__init__.py 的 Distribution |

## CONVENTIONS
- 持久化先于副作用：事件先写 pending/ 再本地接受；调用意图 checkpoint 落盘后才执行 effect。
- 规则未 VERIFIED 的题只输出 RULE_BLOCKED；UNKNOWN 成本三金额必须全 null，KNOWN 五项必须非 null。
- 中文注释解释"为什么"；边界用 TypedDict/Protocol/dataclass 描述。
- Java（io.f1ap.verifier）与 Python（本包 contracts/）双端各实现同一语义、不共享代码：改语义必须两端同改，否则契约 round-trip 失败。

## ANTI-PATTERNS
- 禁入字段递归禁止（answer/chance/officialresult(s)/rawfeed/feed/apikey/secret/password/authorization/command/shell/sql/accesstoken，忽略大小写并删 `_-`）；仅 contracts/v1/fixtures/invalid/ 可含合成占位。
- 禁 symlink、路径逃逸、未登记文件（artifact.safe_file；输出目录同样受限，重叠即 OUTPUT_OVERLAP）。
- 不猜测规则凑 VALID；不把合成夹具当赛事事实（fixture_class=TEST_FIXTURE）。
