# B1 本地证据

2026-10-31；仅 Agent 本地 B1，不是 E02 总验收报告。

- 冻结 release 1.0.2，manifest SHA-256 `6c9e34dfd002cb1f3309dc3d98871cd1cd93135f77c46810711674158124cd37`。
- 最终 LSP：worker/runtime/messaging/tests/worker 均 0 error。E01 原测试存在 12 个 setUp 实例字段初始化诊断，未修改。
- 命令（在 f1ap-agent）：`TMPDIR=/private/tmp .venv/bin/python -m unittest discover -s tests -v`，退出 0，55 tests，4.445s，OK；完整输出见 `python-tests.log`。包含原 37 个契约测试与新增 18 个 B1 测试。
- 命令：`PYTHONPATH=tests .venv/bin/python -m worker.evidence`，退出 0。精确子命令和 stdout/stderr/退出码见 `cli-results.json`。
- normal/export：正常假调用，退出 0，12 个事件，finished SUCCEEDED。
- zero-call/export：call_limit=0，退出 0，11 个事件，无 usage。
- unknown/export-recovery：初次中断退出 3，恢复退出 0，UNKNOWN 用量和 FAILED finished，不重复假副作用。
- pending/export-recovery：首次本地拒收退出 2，恢复退出 0。发送前完整事件保存在 `pending/pending-before-resend.json`，可与恢复导出比对 ID/载荷。

每个 export 均有 `request.json`（完整请求 envelope）、`events.json`（完整 envelope 数组）、`summary.json`。每个场景 runtime 均保留 runs.json、带校验和的执行日志、call intent、fake-executor.json 和本地 sent 收据。normal/unknown/pending 假调用次数均为 1，zero-call 不创建执行器计数文件。

无新增依赖，沿用 jsonschema 4.25.1、标准库 argparse/dataclass/unittest；不安装候选栈。所有输入通过现有冻结契约加载器校验，不另写 schema。默认时钟是 fixture cutoff；显式 now 测试验证 expires_at/LIVE deadline。

真实 MQ、publisher confirm/mandatory/return/nack、外部 SDK/模型、OpenF1、新闻、Chroma、部署、付费调用：NOT_RUN。单进程排他仅验证本地文件锁；不宣称分布式恢复、MQ 事务或真实推理。

初始默认 TMPDIR 契约基线因 /var 符号链接导致 7 failures/2 errors；最终使用 /private/tmp 运行上述全部测试通过，未修改 E01 测试逻辑。
