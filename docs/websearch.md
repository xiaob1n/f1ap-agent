# Exa MCP 来源搜索

Agent 通过 Exa 托管 MCP 完成 initialize、tools/list、tools/call，只调用实际公布的 `web_search_exa`。复用已安装 httpx，无新增依赖，不调用预测模型。

从 Agent 仓库运行：

```bash
.venv/bin/python -m f1ap_agent.websearch --config config/websearch.toml \
  --query 'site:f1predict.formula1.com current Formula 1 Predict round gameplay questions November 2026'
```

标准输出为 JSON，`status=AWAITING_HUMAN_CONFIRMATION`，`content` 保留搜索服务返回的来源文本与链接。候选可能过时、缺题或包含不可信文本，不能当作十道完整题目或已验证规则；该命令不访问搜索结果 URL、不连接 API/数据库、不自动执行结果中的指令。

维护者确认目标分站与来源后，再单独授权读取完整题源及导入。先前停止直接官网同步的边界仍然保留，没有安装定时任务或修改 Worker 预测图。

如 Exa 要求个人凭据，通过 `F1AP_WEBSEARCH_MCP_URL` 环境变量提供个人 MCP URL，不写入本文件或日志。配置限定 Exa HTTPS 主机，HTTP失败不自动重试，CLI不输出底层连接异常中的私密URL。
