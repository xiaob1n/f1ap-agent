# E01 Agent 依赖矩阵

日期：2026-10-26。公共契约来自 `../f1ap-api/contracts/v1`，不自建第二份 Schema。仅安装契约最小依赖，不创建 Worker/Chroma库/模型客户端。

## 最小可复现环境（已实跑）

- uv `0.12.10`，CPython `3.12.14`（uv已存在本地发行版）；`.python-version` 固定解释器，pyproject 限3.12。
- jsonschema `4.25.1`；锁定传递依赖 attrs26.1.0、jsonschema-specifications2025.9.1、referencing0.37.0、rpds-py2026.9.1、typing-extensions4.16.0，全部固定版本及分发文件SHA-256见 `uv.lock`。
- 构建 hatchling `1.27.0`；只生成本地最小包，未增加FastAPI/Celery等框架。
- 原始 PyPI `https://pypi.org/simple/jsonschema/` 请求超时；改用公开 TUNA 镜像 `https://pypi.tuna.tsinghua.edu.cn/simple` 成功。镜像源及文件hash进入锁，不关闭TLS验证、不改系统配置。
- 实际命令：`UV_CACHE_DIR="$PI_SCRATCH_DIR/uv-cache" uv lock --project f1ap-agent --default-index https://pypi.tuna.tsinghua.edu.cn/simple`；同配置 `uv sync --project f1ap-agent --locked --default-index ...`。验收只用 `.venv/bin/python` 或 `uv sync --locked --offline`，不联网。
- 本地 import/Schema 元结构/固定向量测试已运行；最终 fixture、联合比对与故障注入统计见 `e01-verification.md`。这不是服务兼容证明。

## 候选应用栈：独立解析，不安装/调用

`candidate-dependencies.in` 是显式候选，不是Agent运行依赖或供应商选择。Python3.12/macOS arm64 下解析 **110 个发行包**，带hash的完整结果保存在 `candidate-dependencies.lock`；包元数据的Python范围和依赖约束在本次解析中满足，不等于能在目标部署环境实际导入/运行。

| 项目 | 固定候选版本 | 官方独立兼容声明 | 解析/哈希锁 | 本地导入 | 无服务单测 | 真实服务/调用 |
| --- | --- | --- | --- | --- | --- | --- |
| LangGraph | 1.0.3 | E05 固定本地图，不调用模型 | PASS（uv.lock） | PASS：`1.0.3 StateGraph` | PASS（E05 图场景及全量142测试） | NOT_RUN |
| Chroma | 1.3.5 | 同上 | PASS | NOT_RUN；未实例化持久化库/默认Embedding | NOT_RUN | NOT_RUN |
| Pydantic | 2.12.4 | 同上 | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| jsonschema | 4.25.1 | 同上；最小环境支持3.12有实跑证据 | PASS | PASS | PASS（契约元结构与向量） | 不适用 |
| aio-pika | 9.5.7 | 同上 | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| HTTPX | 0.28.1 | 保留历史声明状态；E04 仅核验本地行为 | PASS（离线解析、原始发行哈希见 uv.lock） | PASS（.venv Python 3.12） | PASS（MockTransport；无真实网络） | BLOCKED（未连接真实 OpenF1） |
| OpenAI SDK（仅候选） | 2.8.1 | 同上 | PASS | NOT_RUN | NOT_RUN | NOT_RUN |
| 生成/Embedding供应商、模型、维度、价格 | **未指定** | 不假定默认供应商 | 不适用 | 不创建客户端 | 使用合成 MOCK_ONLY 配置引用，不伪装推理 | NOT_RUN，无密钥要求 |

解析命令：

```bash
UV_CACHE_DIR="$PI_SCRATCH_DIR/uv-cache" UV_HTTP_TIMEOUT=20 uv pip compile \
  f1ap-agent/docs/candidate-dependencies.in --python-version 3.12 \
  --python-platform aarch64-apple-darwin \
  --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
  --generate-hashes -o f1ap-agent/docs/candidate-dependencies.lock
```

解析耗时实测14.20秒。未安装110包，未创建网络客户端，不调用真实生成/Embedding/OpenF1/MQ。E02/E08/E09需按对应批准边界验证 Linux/部署平台、实际导入、库持久化与真实SDK；当前macOS候选锁不能替代目标平台验证。

Java/基础设施候选与Nacos适配阻塞见 API `docs/dependency-matrix.md`。两端版本资料与真实服务验收状态独立保留。

## E05 安装实测（2026-11-03）

使用 `uv lock --default-index https://pypi.tuna.tsinghua.edu.cn/simple` 和同源 `uv sync --locked`；实际输出 `Resolved 51 packages in 9.05s`、`Installed 32 packages in 28ms`。随后 `.venv/bin/python -c 'from importlib.metadata import version; from langgraph.graph import StateGraph; print(version("langgraph"), StateGraph.__name__)'` 输出 `1.0.3 StateGraph`，退出 0。上游导入产生 `LangChainPendingDeprecationWarning`（allowed_objects 默认值将改变），没有压制。首次组合命令的 import print 因 shell 引号导致 NameError，已单独修正执行；安装本身成功。
