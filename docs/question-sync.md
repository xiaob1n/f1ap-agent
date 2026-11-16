# 每30分钟同步当前分站

当前状态：**2026-11-16 已切换无积分协议 ANSWERS_V2**。真实同步将round140升级为version2（题目1251–1260），十题101选项；再次同步返回 `UNCHANGED`。旧version1历史保留。启动立即执行及1800秒循环保持原实现，前轮已验证真实30分钟周期；本轮验证新版导入与幂等。

本次 Java 定向25项、Python同步2项通过，既有websearch2项通过且源码未改。旧手工录题测试依赖已删除类，未运行全量Java测试；Java语言服务超时、TypeScript语言服务未安装，未记为检查通过。证据在工作区 `.omo/evidence/question-sync-20261116/`。

由独立 Agent 同步进程执行，API 仍是业务库唯一写入方。进程启动立即同步一次，每次执行结束后等待1800秒再取下一次；失败不立即重试，下一周期重新检查。它不启动MQ预测Worker或模型。

从 Agent 仓库根目录启动（已有实例时不要重复启动）。通过进程环境或操作者维护的私有文件提供 `F1AP_INTERNAL_WRITE_TOKEN`，不得把令牌写入命令行或日志：

```bash
.venv/bin/python -m f1ap_agent.question_sync --config config/question-sync.toml
```

执行一次用于人工检查：

```bash
.venv/bin/python -m f1ap_agent.question_sync --config config/question-sync.toml --once
```

安装包提供 `f1ap-question-sync` 命令，参数与模块入口一致；默认配置路径为当前工作目录的 `config/question-sync.toml`。该文件只配置本机 API 地址和超时。同步进程不连接 MySQL、MQ 或模型，API 是唯一业务库写入方。

本工作区也可复用 `local-ui/start.ts` 从 Nacos 取内部令牌，无须手工复制令牌。从工作区根目录运行：

```bash
set -a
source f1ap-agent/.env.nacos.local
set +a
export F1AP_NACOS_DATA_ID=f1ap.yaml
bun local-ui/start.ts sync
```

此处读取 API 的 `f1ap.yaml`，不是 Agent 的模型配置。需要单次执行时追加 `--once`。API 启动可使用同样引导环境，设置 `F1AP_LOCAL_JAR` 为当前源码构建产物后运行 `bun local-ui/start.ts api --f1ap.unscored.enabled=true`。

## 来源与数据边界

首页实际使用公开赛程 `https://f1predict.formula1.com/feeds/schedule/raceday_en.json`。按官网选择函数相同的MatchStatus优先级2、3、5、1、0，排除额外赛季题，选择首条比赛；读取该行明确的Season、TourId、GamedayId和MeetingName，不使用最大编号或本机日期推断。当前选择21站，与首页“Make your predictions”跳转一致。

完整题源为 `https://f1predict.formula1.com/feeds/questions/questions_<GamedayId>_en.json`。十题经ANSWERS_V2边界校验后调用 `POST /internal/v1/round-syncs`，保留题面、说明、候选来源身份、类型、标签及选择数量/顺序。不导入Points、Answer和Chance，不生成评分规则；截止null/false，轮次保存为CONTROLLED_INPUT/DRAFT，不触发预测。

同步入口沿用内部令牌，复用导入事务；在同一round行锁内只允许维护DRAFT且未锁定的轮次。已OPEN或LOCKED返回409，输出 `SKIPPED_PROTECTED_ROUND`；不会周期追加非活动修订，也不会覆盖已经核实并投入预测的资料。普通手工导入原有语义不变。

## 运行反馈

每轮输出一行JSON：`IMPORTED`、`UNCHANGED`、`SKIPPED_PROTECTED_ROUND`或`FAILED`，同时给出1800秒间隔和下一次时间。出现错误保留已有定义，不猜题或降级到搜索摘要。

成功结果含 `gameplay_url`、数据库 `response.round_id` 和 `definition_url`。后续流程可带内部令牌 GET 该 `definition_url`，即 `/internal/v2/rounds/{round_id}/predictions/definition`，取得无积分定义及哈希；API 需启用 `f1ap.unscored.enabled`。同步本身不创建预测任务。答案预测与官方答案正确性核对参见 API `contracts/v2/README.md`，不计算积分。

在对应终端Ctrl+C停止，等待当前有界HTTP请求返回。常驻进程依赖本机开机和联网，未安装登录启动或系统守护；电脑休眠期间不会执行。不要通过重复启动创建多个同步器。
