# 按新题、练习赛和截止时间预测当前分站

独立调度进程先复用官网当前分站同步，再调用现有 ANSWERS_V2 预测入口。API 经 Outbox 投递任务，常驻 MQ Agent 完成查询和模型调用，结果由 API 入库。

每30分钟同步当前分站题目。首次成功取得一组新题或新版本后立即预测；FP1开始前，以该版本首次取得的时间为锚，每12小时一次；FP1开始后，以FP1开始时刻为锚，每2小时一次。新题晚于FP1发布时也立即执行一次，随后回到FP1锚点。旧星期和周日规则不再使用。

调度状态保存在 `--state-dir` 的 `schedule.json`，包含首次取题时间、题目摘要、截止时间与已提交意图。独立文件锁防止两个进程同时写；request_id由round_id、题目摘要及周期起点生成。重启只执行当前窗口，不补跑停机小时；提交前先GET读回，POST意图先落盘，传输结果不明时不重发。不要删除状态卷以重跑预测。

截止来自小游戏赛程 `/feeds/schedule/raceday_en.json` 中同一Gameday/Tour/Season的首个计时场次 `SessionStartDateISO8601`，必须含时区。官方页面实际倒计时在该时刻加40秒；按本次产品选择，本系统在场次开始时提前锁定，`deadline_basis`标记 `SYSTEM_EARLY_LOCK`。不采用陈旧、无时区的constraints `DeadlineDate`，也不在排位开始后把截止顺延至正赛。时间缺失或身份无法核对时停止该次创建并报告原因，不猜截止。

FP1来自现有MongoDB：先按官网 `FOMMEETINGSESSIONKEY` 精确查session，核对开始时间、类型及年份，再使用该session的meeting_key读取Practice列表。官网MeetingId不直接当Mongo meeting_key。FP1前使用配置中的显式历史 `reference_session_key`（默认11412），必须存在截止前正圈速；FP1后优先选择本站最近已开始且有有效圈速的练习会话，最新无数据则检查更早的本站练习，不退回其他赛道。本站尚无有效圈时返回 `WAITING_PRACTICE_DATA`，不创建付费任务。

目前一次任务仍读取一个参考会话，尚不合并多个练习赛。skill允许基于练习圈估计杆位、队内排位和晋级，但不能把推断称为已发生的排位成绩。模型调用上限沿用Agent配置，当前为六次。

API在轮次行锁内按数据库时间拒绝截止瞬间及之后的新请求，并将到期回包记为 `IGNORED_STALE`。每次任务有效期至多15分钟且不超过截止。后台冻结器锁定截止前已入库、同题目版本的最后完整一轮，保留其中不足题，不逐题拼接；没有完成结果则锁定为空。`GET /internal/v2/rounds/{id}/predictions/final`读取不可变快照，超过48小时历史列表窗口仍可用。网页默认展示锁定答案并允许查看历史，迟到结果不改锁定快照。

凭据通过环境或私有引导文件注入。工作区入口从 API Nacos 配置读取内部令牌：

```bash
set -a
source f1ap-agent/.env.nacos.local
set +a
export F1AP_NACOS_DATA_ID=f1ap.yaml
bun local-ui/start.ts predict
```

单次检查追加 `--once`。也可从Agent仓库运行 `.venv/bin/python -m f1ap_agent.hourly_prediction --config config/hourly-prediction.toml --state-dir runtime/scheduler`，需提供 `F1AP_INTERNAL_WRITE_TOKEN` 或其Secret文件，并通过Agent Nacos引导或环境变量提供Mongo只读连接。`local-ui/start.ts predict`先从API Nacos读取令牌，再将Agent Data ID交给子进程。调度器只使用Mongo配置，不调用模型、不消费MQ。

必须先升级API、Agent和网页，再启用新调度器。根Compose为scheduler增加Nacos引导和独立 `scheduler_runtime` 卷，不能只更新镜像而遗漏Compose。日志状态包括SCHEDULED、ALREADY_SCHEDULED、WAITING_DEADLINE、WAITING_PRACTICE_DATA、SUBMISSION_NOT_RETRIED、LOCKED、SKIPPED_PROTECTED_ROUND或FAILED，以及UTC格式next_run_at。调度器在已知截止处先锁旧轮再同步题源，官网暂时失败不延长截止；API也独立扫描到期轮次补锁。

Ctrl+C或SIGTERM停止调度，当前有界HTTP/MCP请求完成后退出。启用前需明确授权持续真实服务访问与模型计费。本轮只实现、离线验证和只读核验来源；没有部署、创建预测、修改真实数据或新增付费模型调用。
