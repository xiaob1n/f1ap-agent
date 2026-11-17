# 每小时预测当前分站

独立调度进程先复用官网当前分站同步，再调用现有 ANSWERS_V2 预测入口。API 经 Outbox 投递任务，常驻 MQ Agent 完成查询和模型调用，结果由 API 入库。

启动时立即执行当前小时一次，之后每个 UTC 整点执行（北京时间也是每小时整点）。不补跑停机期间的历史小时。request_id 由数据库 round_id 与 UTC 小时确定；重启或重复启动时先读回同一请求，API 行锁及已有幂等校验避免重复创建任务。同小时题目变化不再创建第二个预测。失败不立即重试，下一小时执行新的周期。

`config/hourly-prediction.toml` 的 `reference_session_key` 是明确的历史参考会话，当前默认 11412（Interlagos 正赛），不代表目标分站数据；不会自动猜测新会话。需要更换参考资料时修改该配置后重启。模型每任务调用上限沿用 Agent 配置，当前为六次。

凭据通过环境或私有引导文件注入。工作区入口从 API Nacos 配置读取内部令牌：

```bash
set -a
source f1ap-agent/.env.nacos.local
set +a
export F1AP_NACOS_DATA_ID=f1ap.yaml
bun local-ui/start.ts predict
```

单次检查追加 `--once`。也可从 Agent 仓库运行 `.venv/bin/python -m f1ap_agent.hourly_prediction --config config/hourly-prediction.toml`，需预先提供 `F1AP_INTERNAL_WRITE_TOKEN`。

必须先启动启用无积分预测的 API 与常驻 MQ Agent；调度进程本身不启动它们。每轮输出 JSON，包含 SCHEDULED、ALREADY_SCHEDULED、SKIPPED_PROTECTED_ROUND 或 FAILED，以及下个整点时间。新任务请求有效期到下个整点；临近整点启动可能因剩余时间不足而过期，不自动追加调用。

Ctrl+C 或 SIGTERM 停止调度，当前有界 HTTP 请求完成后退出；还需分别停止 API 和 MQ Agent。此为本机常驻进程，不安装开机服务，电脑睡眠或关机期间不执行。启用前需明确授权持续真实服务访问及每小时模型计费。
