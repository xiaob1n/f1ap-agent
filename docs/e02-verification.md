# E02 Agent B1 验收与交接

日期：2026-10-31。状态：**B1 本地基础通过；外部服务 NOT_RUN**。

跨端 E02 已补齐 API 侧获准 Nacos/MySQL 真实验收，1 项集成测试通过；见 [API 联合交接](../../f1ap-api/docs/e02-verification.md)。Agent 仍不直连业务 MySQL/Nacos，其外部服务状态不因 API 验收而改变。

## 验收结果

- Python 3.12.14、uv 0.12.10，未新增依赖。
- `TMPDIR=/private/tmp/f1ap-e02 .venv/bin/python -m unittest discover -s tests -v`：55 项通过。
- 覆盖单进程 Worker、固定假消息源、固定输出、重复投递幂等、同 task 新 run 重试、新窗口任务、过期任务、非法载荷、日志损坏、调用开始后中断恢复为 UNKNOWN、费用 UNKNOWN 不阻塞其他 run、待发事件原 event_id/载荷保留、单写入者和 CLI 导出。
- CLI 证据目录：`docs/e02-agent-evidence/b1-2f347e4062f84997bb80dd73e3daf5c0/`。
- 正常/零调用/UNKNOWN 恢复/待发重发的实际 `request.json` 与 `events.json` 已由 API 侧独立 Java 验证器校验通过。
- 外部 MQ、模型、OpenF1、新闻、Chroma、部署和付费调用均为 `NOT_RUN`。

## 交接边界

B1 的 LocalSender 只证明本地持久事件与重发行为，不证明 RabbitMQ confirm、mandatory、return、nack、断连或业务提交后的 ACK。上述行为属于 E03，必须使用获准 Broker 单独验收。
