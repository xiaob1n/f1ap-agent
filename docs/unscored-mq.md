# 无评分研究 MQ

`UNSCORED_RESEARCH_V1` 独立于冻结预测契约 1.0.2。消费入口在旧 `Task.parse` 前分流；旧预测与新闻处理保持原链路。API 的 `f1ap.unscored.enabled=false` 默认关闭入口、绑定和处理；启用由 API 负责。Agent 不声明任何新拓扑，新请求和完成消息沿既有 exchange `f1ap.v1`、任务/事件队列，通过 `unscored.research.requested.v1` 与 `unscored.research.completed.v1` 路由。

正式 MQ 入口将 Nacos 启动阶段 `Startup.research` 的冻结 `Config` 放入 `Worker.research_config`，启动时已有 `install()` 注入 URI/model 凭据。没有研究配置的进程拒绝新研究消息，不默认执行；每次 delivery 不访问 Nacos。运行研究会调用真实 MCP/模型，本次实现验收只使用本地替身，未启用服务。

请求 envelope 精确字段：`schema_version,event_id,event_type,occurred_at,trace_id,payload_sha256,payload`。时间为 UTC 毫秒，UUID 使用现有格式校验；`trace_id=request_id`。请求 event_id 接受任意有效 UUID，不重算 API 的 Java 原始字节 MD5 UUID。payload 精确字段：`request_id,run_id,round_id,definition,definition_sha256,reference_session_key,data_cutoff_at,expires_at,config`；ID 为规范数字字符串，session 为正整数，config 仅为 `{"model":"deepseek-flash"}`。definition 经过现有 `definition_projection` 校验，必须十题 OPTION、source_question_id 唯一、round_id 匹配；BLOCKED 积分规则不阻止研究选项。两个 SHA 使用既有 canonical/digest。截止不得晚于请求时间，到期必须晚于请求时间；到期重投可返回不足数据，由 API 按陈旧身份处理。

完成 payload 精确字段：`request_id,run_id,round_id,request_payload_sha256,prediction`。prediction 精确字段：`schema_version,request_id,definition_sha256,reference_session_key,data_cutoff_at,generated_at,model,results,limitations`。results 按定义排列十题，每项精确为 `source_question_id,status,picks,explanation,evidence_ids`。PREDICTED 保持选项顺序，校验数量、合法选项、去重、非空正整数证据；DATA_INSUFFICIENT 的 picks 为空，evidence_ids 允许为空。适配器把研究输出的随机 request_id 替换为任务 request_id，再校验和保存。完成 event_id 为本地稳定 UUID5；同一任务重发相同完成事件。

状态存在 runtime 下 `unscored/records.json`，整体 canonical SHA 校验；以 event_id、request_id、run_id 三个身份拒绝冲突，记录内完整请求和完成事件也再次校验。`Store.exclusive()` 覆盖状态与副作用。状态顺序为 REGISTERED → INTENT → COMPLETED → confirmed；每一步均用 `atomic_json`。INTENT 前崩溃可继续执行，INTENT 后崩溃绝不重做付费工作，生成十个 DATA_INSUFFICIENT/RESEARCH_INTERRUPTED 槽。模型异常只保存错误类别，不保存第三方异常正文。整体调用用剩余 expires 时间限制，无自动重试。

每个运行只写 `runtime/unscored/runs/<run_id>/` 的 definition、predictions、研究调用与查询回执。完成事件先持久化再发送，只有 publisher confirm ACK 且没有 mandatory return 才 ACK 源消息；未确认沿既有关闭连接重投行为。确认后、记录前崩溃会重发同一事件，API 必须幂等。原 model usage 事件不能表示多轮研究调用，本适配器不冒用该契约；已有 `model-*.json` 的逐次回执保留，不把未知成本记为零。运行记录与回执需要一起备份，不能删除 INTENT 后仅重建注册表来重做调用。

本地验证命令：在 `f1ap-agent` 执行 `TMPDIR=/private/tmp .venv/bin/python -m unittest discover -s tests -p test_unscored_mq.py -v`。测试覆盖调用前后崩溃、确认前后崩溃、超时、过期、未配置、身份冲突、坏 hash、非法类型、旧分发与 publisher confirm。无 RabbitMQ、Nacos、MongoDB 或付费模型访问；真实服务链路为 REAL_NOT_RUN。

API 一致性限制：单题 explanation 最多 2000 字符，evidence_ids 最多 32 个正整数，limitations 最多 20 项且每项最多 500 字符。工作线程用独立 `Context` 启动 `anyio.run`，避免 `asyncio.to_thread` 复制的异步库标记使新事件循环误判嵌套运行。

2026-11-15 本地证据：`/private/tmp/f1ap-unscored-mq-evidence-20261115/tests-r4.log` 为最后一次回归，12 tests、0 failures/errors/skips、exit 0（2.504 秒）；覆盖 evidence_ids 去重。`types-final.json` 为四文件独立 basedpyright 检查，0 errors、158 warnings，因此命令 exit 1，不报告全绿类型门。警告包括冻结 verifier 的未标注类型、既有 consumer 的私有/未使用返回值，以及测试参数推断和未使用返回值；没有关闭或压制诊断。编辑器曾缓存新文件的 missing-import，独立 CLI 已能解析。`tests.log` 保存第一轮线程上下文错误，r2/r3 日志和测试修改前副本一并保留。临时测试 runtime 由 unittest cleanup 清理，证据目录保留；未启动服务或提交代码。
