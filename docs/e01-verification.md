# Agent B0 / E01 修复后验证记录（1.0.2）

日期：2026-10-26。**本地契约门PASS，十题完整业务规则BLOCKED，真实服务/SDK/MQ NOT_RUN。** 联合结论与全部命令见 [API验收报告](../../f1ap-api/docs/e01-verification.md)。本次仅修复审查中的契约/验收缺陷，不开发Worker、业务工作流、Chroma库或模型客户端。

## 固定消费与修复

- API唯一来源 `../f1ap-api/contracts/v1`；release **1.0.2**、schema_version **1.0**。
- [contracts.lock.json](../contracts.lock.json) 固定manifest原始字节SHA-256：

```text
6c9e34dfd002cb1f3309dc3d98871cd1cd93135f77c46810711674158124cd37
```

装载器支持显式固定的1.0.x补丁版本，但必须与manifest/index/limits完全一致，并验证全部文件hash；不联网跟随最新版，不自动改锁。已校验文件字节用于后续Schema/fixture/context读取，输出目录不能覆盖输入。

本次修复：EXCLUSIVE按实际候选去重及NO_AWARD约束；Python有界JSON语法/深度错误顺序与Java一致；非法定义投影返回结构化拒绝和统一路径；报告严格区分bool/int/float；验收失败撤销旧PASS；来源/十题目录完整集合检查；测试临时目录去除个人路径。旧1.0.1漂移字节和历史报告保留为归档，不回填成通过。

## 实测结果

本次run：`6f91774df9ed46348b8a98e0f0e809d4`。

| 检查 | 结果 |
| --- | --- |
| Python unittest | **37个测试方法通过**，其中全量方法执行194个subtest；0失败/0跳过 |
| Java离线clean verify | **245项通过**，独立编译/执行，不借用Python结果 |
| 发布/验收工具回归 | **15项通过** |
| Python/Java各自CLI | **194/194固定期望匹配：63接受、131预期拒绝** |
| 联合比较 | 差异0、缺失0、跳过0 |
| 重复运行 | 两端各自报告字节hash不变 |
| 双向round-trip | 每方向63个接受向量重新校验；完整194项报告仍匹配固定期望 |
| 制品故障 | 每语言14类，共28次预期非0退出 |
| 报告故障 | 12类预期非0退出，包括布尔值被数字替代 |

- [当前运行状态](../../f1ap-api/docs/e01-evidence/summary.json)
- [本次run汇总](../../f1ap-api/docs/e01-evidence/runs/6f91774df9ed46348b8a98e0f0e809d4/summary.json)
- [Python报告](../../f1ap-api/docs/e01-evidence/runs/6f91774df9ed46348b8a98e0f0e809d4/python.json)
- [Python测试日志](../../f1ap-api/docs/e01-evidence/runs/6f91774df9ed46348b8a98e0f0e809d4/python-unit-tests.log)
- [联合报告](../../f1ap-api/docs/e01-evidence/runs/6f91774df9ed46348b8a98e0f0e809d4/joint.json)
- [round-trip报告](../../f1ap-api/docs/e01-evidence/runs/6f91774df9ed46348b8a98e0f0e809d4/joint-roundtrip.json)

Python报告SHA-256：`49a07eb6265a5a7f10e9cad51b89b82d9d3de6773a0a71021bd4f70e669b1203`。所有期望由发行时独立固定，不由被测实现现场生成。177项原有固定期望和input/context字节保持，新增17项审查回归。

## 环境与重放

CPython3.12.14、uv0.12.10、macOS27.2 arm64。运行依赖固定在 [uv.lock](../uv.lock)，候选110包仅解析、未安装；分层状态见 [依赖矩阵](dependency-matrix.md)。正式验收完全离线，不需要密钥。

从工作区根目录执行，PI_SCRATCH_DIR为现有临时目录，Maven依赖缓存先按API报告准备：

```bash
UV_CACHE_DIR="$PI_SCRATCH_DIR/uv-cache" uv sync --project f1ap-agent --locked \
  --default-index https://pypi.tuna.tsinghua.edu.cn/simple
PYTHONDONTWRITEBYTECODE=1 f1ap-agent/.venv/bin/python -m unittest discover -s f1ap-agent/tests/contracts -v
PYTHONDONTWRITEBYTECODE=1 f1ap-agent/.venv/bin/python f1ap-api/tools/run-e01.py
```

完整入口每次创建新的runs/UUID目录，当前summary先RUNNING，完成后原子写PASS或FAIL。根目录旧joint报告已标STALE且原字节保留；应从summary读取本次证据目录，不继续把旧PASS当当前结果。

## 保留边界

真实完整规则10/10仍BLOCKED，Q6计时、Q8/Q9分类、Q10条件完整性、完整计分/作废、截止/有效版本未补证。合成VERIFIED只代表测试规则，不表示真实题已核验。原始Answer/Chance不进入Agent任务或共享fixtures。

未调用OpenF1/MQ/生成/Embedding，未创建Chroma业务库，未指定供应商或默认模型。未启动E02/B1、执行SQL、部署或提交/推送Git。下一阶段须取得批准并沿用API规则目录、兼容与许可缺口。
