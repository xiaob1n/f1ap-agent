# 按星期定时预测当前分站

独立调度进程先复用官网当前分站同步，再调用现有 ANSWERS_V2 预测入口。API 经 Outbox 投递任务，常驻 MQ Agent 完成查询和模型调用，结果由 API 入库。

按北京时间固定触发：周一至周四00:00和12:00，周五至周六00:00、02:00直至22:00，周日不自动预测。进程在计划整点后一分钟内允许执行；其余时间启动只等待下一计划，不补跑。request_id由数据库round_id与计划时刻对应的UTC小时确定，保留原有命名便于与旧整点任务去重。重启先读回同一请求，API行锁与幂等校验避免重复创建；失败不立即重试。

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

必须先启动API与常驻MQ Agent。日志状态包括SCHEDULED、ALREADY_SCHEDULED、WAITING_SCHEDULE、SKIPPED_PROTECTED_ROUND或FAILED，以及UTC格式的next_run_at。任务有效期为本次12或2小时窗口；周六22:00任务在周日00:00到期，下一计划为周一00:00。周日不会访问官网或API。不会为失败任务追加模型调用。

Ctrl+C 或 SIGTERM 停止调度，当前有界 HTTP 请求完成后退出；还需分别停止 API 和 MQ Agent。此为本机常驻进程，不安装开机服务，电脑睡眠或关机期间不执行。启用前需明确授权持续真实服务访问及每小时模型计费。
