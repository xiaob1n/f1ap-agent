# DeepSeek 独立预测

本次新增独立模型研究分支，算法标识为 `LLM_PREDICT_V1@<mapping SHA-256>`，模型配置引用为 `DEEPSEEK_FLASH_V1`。统计预测和 E07 mock 分支保留。冻结契约仍为 1.0.2。

LLM 分支由 LangGraph 编排：`START → prepare → generate → validate → END`。prepare 加载冻结输入并检查准入，generate 执行真实模型调用，validate 规范化并校验模型结果。过期或规则全部阻塞时 prepare 直接进入 validate，不执行模型调用。统计十题图仍是独立图，不做统计与 LLM 融合。

Worker 继续负责唯一的持久化日志和消息发送，未引入另一套 LangGraph checkpointer。generate 在调用前保存意图、返回后保存响应；恢复时有响应就复用，无响应则记录 UNKNOWN，不再次调用。图结束后的结果由 Worker 统一落盘、回传；已完成任务的重复投递不再进入图。

Agent 配置保存在本地。使用 `config/llm.example.toml` 的字段建立本地 TOML，通过 `--llm-config <路径>` 传给常驻 `f1ap-agent-mq` 或单任务 `python -m f1ap_agent.worker`。密钥从配置中 `api_key_env` 指定的环境变量读取；不要写入配置、消息或日志。

官方请求地址为 `https://api.deepseek.com/chat/completions`，模型 `deepseek-flash` 对应 V4.1 Flash。使用非流式 JSON 输出，关闭 thinking。HTTP 不自动重试，超时或已登记调用意图后中断均视为 UNKNOWN，重复投递只重发已落盘事件。

输入使用既有 mapping 指向的冻结 OpenF1 快照和十题定义。模型只能生成已验证规则的预测；未知规则仍返回 RULE_BLOCKED。结果用冻结消息契约检查候选项、数值范围、选项数量、概率和与策略。模型概率未经校准，不代表准确率验证。没有已验证题目时不调用模型。

API 在 Nacos 中设置 `f1ap.llm.enabled: true` 后，使用现有 `POST /internal/v1/rounds/{id}/research-runs` 入口，传入 `mappingSha256`、`dataCutoffAt`、`expiresAt` 和上述算法版本。仍要求 CONTROLLED_RESEARCH 来源、OPEN 状态和已登记 mapping；DRAFT 分站不能直接调度。每个模型任务一次调用且不自动创建重试 attempt。

用量回包保存 DEEPSEEK / deepseek-flash 及 token 数。服务未返回金额时成本保持 UNKNOWN/null；网络结果未知时 token 字段按冻结契约的非空整数约束填 0，并结合 UNKNOWN 状态理解，不能当作实际零消耗。

本地替身验证和真实服务验证分别记录；仅本地通过不代表真实 DeepSeek 或 API/MQ 联调通过。
