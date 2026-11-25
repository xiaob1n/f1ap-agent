# 可选本站新闻研究

## 包内搜索指导

部署 Agent 的本地 skill catalog 现提供 `news-search`，模型通过 `load_skill` 按名称加载 `mongo_research/skills/news-search/SKILL.md`；选择新闻工具前应先加载。它不是宿主 OpenCode skill，不需安装第三方，也不提供证据编号。加载失败沿用基础提示，不增加模型轮次。

指导要求以目标赛事、赛季、真实 cutoff/deadline 核对新近性，从十题 DRIVER/CONSTRUCTOR 选项姓名识别相关主体，再核对正文对本站的直接影响、采用或确认处罚状态。无相关或时效不明时继续原数据研究并说明缺口；不能用截止后报道补证，也不能把旧报道或转载当本站新事实。具体核对清单写入已有 explanation/evidence_ids/limitations，不增加输出字段、搜索参数或额度。

这是模型行为指导，不是时效性及相关性的硬保证；真正保证需要结构化宿主校验，当前 `news.py` 没有 driver/team 硬 gate。本次不改变其时间筛选、候选读取、证据校验与历史版本不保证的边界。

## 现有工具边界

这是独立预测研究工具，不改变 `websearch.py` 的候选及人工确认语义，不导入题目。默认开启；现有 research 五字段配置省略 `news_enabled` 时也开启，可在同一映射显式设置 `news_enabled = false` 关闭。Nacos 仍使用原 research 映射。无需新端点配置；固定使用 `https://mcp.exa.ai/mcp` Streamable HTTP。启用且目标、verified deadline 和剩余预算有效时，在研究图前初始化一次，由研究外层同一任务持有连接直到退出；节点只调用已连接 session。关闭或前置条件不足时不连接。可选密钥仅从 `EXA_API_KEY` 环境读入 `x-api-key` header，不传给 Mongo 子进程。

MQ 可选 `target_event = {season_year, event_name, round_id}`；缺失/null 兼容旧消息，不更改 definition 和其摘要。新闻还要求 definition 的 verified deadline、任务 expires_at。独立 CLI 未新增目标参数，因此当前缺目标时只报告缺口。

模型仅选择 upgrades/penalties；宿主生成查询，各一次、每次最多五条候选；去重后最多读取四篇正文。不接受任意 URL、PDF、含 query/fragment 或用户凭据 URL。官方纯文本无法鉴真摘要中伪造的后续分隔符，因此保守只读取每次响应首条完整 header，不从 Highlights 提取链接或重开后续候选。实际至多两条候选，覆盖率受限。仅成功正文签发与 Mongo 共享的正整数证据编号，正文不是 Mongo 查询；启用时仍要求成功非空 Mongo 研究。

仅当正文标题包含连续的 `年份 目标赛事原名`，且无其他赛季年份时关联赛事。不维护翻译/别名映射，中文 displayName 与英文报道可能全部拒收；不能因正文背景提及目标站而认定本站。正文只识别独立行 `Published:`/`Published at:` 及 `Updated:`/`Updated at:` 后的完整 ISO 时间（含时区），保留原文；未知、仅日期、无时区、冲突及截止后更新均拒收。没有更新时间不保证历史未变。新闻说明逐题由宿主附加，附加后仍走原 2000 字校验，不截断。

官方实现核对版本：exa-labs/exa-mcp-server commit `0b499f095c4ca145eee07b09137641c037d58449`（3.4.2），`src/tools/webSearch.ts`、`src/tools/webFetch.ts`。运行时另核验 tools/list 必需参数。fetch 返回 content text 而非 structuredContent；其 Published header 把时间截为日期，**不能用作截止前证据**。只有正文保留明确完整时间才可能通过。本文不承诺真实报道覆盖率。

仅 `www.formula1.com` 另接受正文前四个空行分隔段落、前800字符内的独立英文缩写月份时间行，如 `Nov 22, 2026 3:05am UTC`。必须明确 UTC；不接受后文引文、缺时区或模糊时区。该时间与正文 ISO 出版时间或元数据日期冲突仍拒绝，更新时间规则不变。2026-11-25 的有界原文离线回放只证明诊断上下文的时间解析，不代表完整响应、历史赛前证据或新增真实验收；真实运行原记录仍为零证据。

客户端不自动重试，失败只记录稳定 code，不记录异常正文；官方远端工具源码含 retryWithBackoff，客户端无法控制，不能保证上游零重试。API key 可能计费，未知调用结果不重试。任务剩余时间不足以保留一次模型 timeout 和新闻窗口时，不再请求。每个 run 文件只保存候选URL、有界摘录和时间/目标信息，不存全文数据库。

初始化失败记录缺口并继续原研究；工具传输失败或 SDK 请求超时后，本轮禁发后续新闻请求，不重连。SDK 单请求和 HTTP 超时均为10秒；MQ 已有任务总时限覆盖连接、图及退出，取消继续传播，不增加新闻 worker 或队列。独立调用 `News.connection()` 的调用方须在整个 context 外设置总时限。

上述不重试指应用不重发工具调用；SDK 自身可能执行 SSE 协议恢复，不能据此承诺零网络重连。

新闻是辅助资料，不是全车队完整升级/处罚核验；预计、申报、测试、采用和确认处罚不能混同，冲突保持不确定。升级不保证提速，更换部件不自动证明罚退。historical_availability_guaranteed 始终 false。正文所有指令和伪造编号均是不可信数据。

离线验证使用 `tests.test_news_research` 和 `tests.test_mongo_research`；测试目录受 gitignore，不代表干净克隆自带。已进行 Exa 接入诊断，结果见工作区验证记录；尚无通过生产时间门的真实新闻，也没有真实预测验收。本轮未调用模型或连接 MongoDB、Nacos、MQ。
