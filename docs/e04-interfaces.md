# E04 Agent 本地接口

本接口只支持离线/本地工程验证。真实场次 ID、服务环境和 G2 规则门仍为 BLOCKED。

## 映射信封

顶层为 `{"schema_version":"OPENF1_MAPPING_V1","mappings":[...]}`，不得使用裸数组。每条记录字段如下：

```json
{
  "round_id": "字符串 ID",
  "definition_sha256": "64 位小写十六进制",
  "mapping_version": "受控版本",
  "source_ref": "来源/许可说明",
  "valid_until": "UTC 毫秒时间",
  "candidates": {"option_key": 1},
  "target_session": {"meeting_key": 1, "session_key": 2, "session_type": "Race"},
  "feature_session": {"meeting_key": 1, "session_key": 3, "session_type": "Practice"},
  "event_start_at": "UTC 毫秒时间",
  "event_cutoff_at": "UTC 毫秒时间",
  "endpoints": {"sessions": [], "drivers": [], "laps": [], "stints": [], "weather": [], "race_control": [], "pit": []},
  "method": {"seed": 0, "samples": 10000, "laps_per_sample": 5, "compound": "SOFT", "tyre_age_min": 1, "tyre_age_max": 20},
  "manifest": {"path": "manifest.json", "sha256": "64 位小写十六进制"}
}
```

以上仅说明结构，数字不是任何真实场次映射；生产配置不预填 ID。`endpoints` 数组必须列出实际允许字段。选择键为 `round_id + definition_sha256`，重复 round 拒绝。`manifest.path` 相对 mapping 所在目录；manifest 中响应路径相对 manifest 所在目录，拒绝绝对路径、`..` 与符号链接。

最终 mapping **原始字节** SHA-256 绑定 `profile.algorithm_version=PODIUM_PACE_BOOTSTRAP_V1@<hash>`。profile 固定 `tools=["OPENF1_READ"]`、`strategies=[{"strategy":"EXPECTED_POINTS","version":"EXPECTED_POINTS_V1"}]`、`model_config_ref=NO_MODEL`、`price_version=NOT_APPLICABLE`。准备采集完成并冻结 manifest 后才能生成最终 mapping/request。任务只读冻结文件，不发网络请求。

## Python / CLI

- `f1ap_agent.data.openf1.config.load_mapping(path, selection)`：`Selection(round_id, definition_sha256, mapping_sha256)`，返回 typed `MappingConfig`。
- `f1ap_agent.data.openf1.prepare.prepare(spec_path, output_dir, client)`：消费无 manifest 的准备配置，以受限客户端读取固定 session，冻结响应、manifest、mapping，返回最终 mapping 路径。
- `python -m f1ap_agent.data.openf1 --spec <spec> --output <new-dir> --base-url <approved-host/v1>`：仅校验；显式 `--run` 才采集，当前只用 MockTransport 验证此路径，未连接真实服务。
- `f1ap_agent.worker.engine.Worker(..., mapping_path=Path(...))`：已有构造参数兼容；研究路径不调用 executor，恰好十 result + 一个 finished，usage revision 0。
- `f1ap_agent.e04_fixture.build_fixture(root, verified=False)`：写入明确标记合成的离线 request/mapping/frozen 输入，返回 `FixturePaths(root, request, mapping, manifest, output)`。
- `f1ap_agent.e04_fixture.run_fixture(root, verified=False)`：构造上述 fixture 后驱动真实 Worker，导出 `request.json`、`events.json` 和 `context.json`，返回 `FixturePaths`。
- `python -m f1ap_agent.e04_fixture --output <new-dir> [--verified]`：离线联合验证入口，两个模式均不连接服务。`--verified` 仅构造合成 PODIUM 积分规则，不解除真实规则门。

所有输出目录必须独立且原本不存在；测试使用 `/private/tmp` 下唯一目录。Lead 负责统一 runner、独立 verifier、验收报告；本文件不宣称 E04/G2 完成。
