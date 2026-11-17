# ARM64 Agent：现有 MQ 拓扑、MongoDB MCP 与真实模型研究。
# 基础镜像为 linux/arm64 兼容的官方 uv 镜像（自带 Python 3.12 与 uv 0.12.10 固定标签）；
# 按用户决策「arm64 标签待远端定摘要」，基础镜像 digest 在任务 11 远端构建时冻结锁定。
FROM node:22-bookworm-slim AS node-runtime
FROM ghcr.io/astral-sh/uv:0.12.10-python3.12-bookworm-slim

# 与 Python 阶段使用同一 Debian 系列，Node 为 MCP 子进程运行依赖。
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN node /usr/local/lib/node_modules/npm/bin/npm-cli.js install \
    --prefix /opt/mongodb-mcp --omit=dev --no-audit --no-fund mongodb-mcp-server@3.3.0

# 时区固定 UTC；契约目录与唯一权威锁（f1ap-agent/contracts.lock.json）成对指向 /app；
# 禁止 uv 下载其他解释器，镜像内仅使用自带 Python 3.12。
ENV TZ=UTC \
    F1AP_CONTRACTS_DIR=/app/contracts/v1 \
    F1AP_CONTRACTS_LOCK=/app/contracts.lock.json \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/mongodb-mcp/node_modules/.bin:$PATH

WORKDIR /app

# 仅复制锁定文件、干净源树与暂存进上下文的只读契约；
# uv sync --frozen --no-dev 按 uv.lock 51 条目以目标平台标记求值安装，
# 无 dev 依赖、无 Chroma/E08 残留包、无答案或导入文件。
COPY pyproject.toml uv.lock contracts.lock.json ./
COPY src/ src/
COPY config/hourly-prediction.docker.toml /app/config/hourly-prediction.toml
COPY contracts/v1/ /app/contracts/v1/

RUN uv sync --frozen --no-dev

# 非 root 运行身份（10001:10001）；/data/runtime 与 /data/output 仅 Agent 独占可写。
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/runtime /data/output \
    && chown -R 10001:10001 /data/runtime /data/output
USER 10001:10001

# 订阅级健康探针：只读 /data/runtime 状态文件，零网络、零模型、零副作用；
# 仅当消费订阅就绪、心跳新鲜且进程代际匹配时才算健康（--require-ready）。
HEALTHCHECK --start-period=30s CMD [".venv/bin/f1ap-agent-mq","--healthcheck","--require-ready","--runtime-dir","/data/runtime","--output","/data/output"]

# 常驻守护入口：复用既有 runtime 卷幂等启动（--ensure-runtime），
# 绝不声明 MQ 拓扑（--existing-topology）；先写 output/ 再 ACK。
ENTRYPOINT [".venv/bin/f1ap-agent-mq","--run","--daemon","--ensure-runtime","--existing-topology","--runtime-dir","/data/runtime","--output","/data/output"]
