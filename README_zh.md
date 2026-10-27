# 联合收获机底盘智能体设计：代码与复现材料

本仓库对应论文 *Large Language Model Agent-Assisted Chassis Design for Combine Harvesters*，只发布现稿使用的 v3、v4、v5，不发布 v1、v2。

包含工程计算、固定流程和智能体调度代码，以及冻结题面、原始提示词、脚本审核答复、评分参考、完整正式运行记录和源码快照。两种构型均为履带式。题目为合成任务，正式批次的审核由脚本完成。

## 不调用 API 的复现

建议使用 Python 3.12，无第三方依赖。

```bash
python scripts/reproduce.py verify
python scripts/reproduce.py summarize
python scripts/reproduce.py replay --batch v4_formal
```

完整离线验证：

```bash
python scripts/reproduce.py all
```

- `verify` 检查压缩包、每个文件及原始冻结哈希。
- `summarize` 从逐次结果重算分组结果、请求数和主要配对统计。
- `replay` 使用各批次的原版源码和已记录的模型输出，重建消息、动作、工程结果、状态、审核事件和评分；不产生新模型回答。
- 输出写入新的 `.repro/` 子目录，不修改公开证据。

## 数据范围

共 252 次正式运行，另附 12 次 GLM 接口检查。正式失败一并保留。v5 的 S 组隔日补测；一次本地中断的部分会话单独保存，不计入正式成功率，其后在新会话中的重跑保留明确记录。

原始源码、提示词、任务和记录的字节未修改。`data/manifest.json` 给出各批次范围和 SHA-256；`data/export_audit.json` 记录导出检查。密钥、项目验收原件、参考论文、论文稿件和审稿意见不在仓库中。

## 新的 API 运行

先做免费检查：

```bash
python scripts/rerun.py --batch v4_formal --output new_runs/my_replication --dry-run
```

自行在本机环境变量中设置 `CHASSIS_API_KEY` 后，使用相同命令并将 `--dry-run` 改为 `--allow-paid`。该操作会调用模型并可能产生费用。本次公开材料整理仅运行离线检查。

新的调用属于新的实验，不会覆盖历史结果。服务端模型可能更新，因此可复算已有记录，不代表未来调用一定得到相同成功率。

## 结果解读

在所测 DeepSeek 条件下，智能体较少遗漏输入更新；在 GLM 条件下，各组均合格，固定流程请求较少。核心证据是需求、计算依据和方案之间的可追溯一致性，以及流程选择对模型行为的依赖。未证明智能体普遍优于固定流程，也未测量工程师工时或整车性能收益。

完整说明见 [英文指南](docs/REPRODUCIBILITY.md)、[协议索引](docs/PROTOCOL.md) 和 [结果边界](docs/RESULTS.md)。
