# E05 Agent 本地验证记录

2026-11-03，完全离线的 Agent 交付；不代表真实服务层通过。API/契约文件未修改，未运行 git 命令。接口及所有版本化先验见 [e05-interfaces.md](e05-interfaces.md)。

## 实际命令与输出

```sh
cd f1ap-agent
TMPDIR=/private/tmp .venv/bin/python -m unittest discover -s tests -q
```

```text
Ran 142 tests in 29.614s
OK
```

从 E04 的87项增加55项：十题四类40项、确定性1项、特征6项、语义oracle3项、图场景5项。包括 E04 profile/恢复/契约回归。测试文件按仓库规则仅留本地。

```sh
basedpyright --pythonpath .venv/bin/python \
  src/f1ap_agent/questions src/f1ap_agent/graph src/f1ap_agent/strategies \
  src/f1ap_agent/features/ten.py src/f1ap_agent/e05_fixture.py \
  src/f1ap_agent/worker src/f1ap_agent/data/openf1 src/f1ap_agent/baselines/podium.py
```

```text
0 errors, 78 warnings, 0 notes
```

使用系统已有 `basedpyright`（虚拟环境没有该工具）。此安装将 warnings 也视为非零退出（exit 1），所以只声明错误数为0，不声称类型命令退出0。未加任何 suppression。warnings 包含既有边界 Unknown 传播、LangGraph stubs/Any 和既有 Worker CLI 未注解字段。

全量 `basedpyright --pythonpath .venv/bin/python --outputjson src/f1ap_agent` 实跑一次，63个既有错误：contracts/artifact.py 37、contracts/cli.py 5、contracts/raw_json.py 2、contracts/semantics.py 12、contracts/verifier.py 7。按主任务更正范围不修改这些文件。

## CLI 与独立契约验证

```sh
.venv/bin/python -m f1ap_agent.e05_fixture --output /private/tmp/e05-agent-delivery-20261103/all-valid --scenario all-valid
.venv/bin/python -m f1ap_agent.e05_fixture --output /private/tmp/e05-agent-delivery-20261103/partial --scenario partial
.venv/bin/python -m f1ap_agent.e05_fixture --output /private/tmp/e05-agent-delivery-20261103/rule-blocked --scenario rule-blocked
```

三条 CLI 均退出0，打印 `external_services=NOT_RUN`。对每目录 events.json 的每个事件用 `evaluate(canonical(event), contract=load_contract(), context=read_json(context.json))` 独立复验，断言全部接受，退出0：

```text
[('all-valid', 11, 11, 'SUCCEEDED'), ('partial', 11, 11, 'PARTIAL'), ('rule-blocked', 11, 11, 'FAILED')]
```

第一列场景、第二列事件数、第三列接受数、第四列 finished.run_status。每场10个 completed+1个finished，无 usage；partial 的 Q6 DATA_INSUFFICIENT、Q10 RULE_BLOCKED。新目录重复运行逐字节相同由 graph/test_graph.py 覆盖。所有 rules 来自 API 侧 TEST_FIXTURE definition，未消费 answers。

真实规则 R01–R05/R10、真实服务、正式 VALID/LIVE 和 G2/G3 仍 BLOCKED/未运行。Q2/Q3/Q4/Q5/Q6/Q8 为显式历史点先验，非现实确定概率；Q1/Q7/Q9/Q10 为共享练习圈 bootstrap 代理；分类固定先验不是 DNF 到 classified 的推断。依赖导入的上游 PendingDeprecationWarning 未压制。
