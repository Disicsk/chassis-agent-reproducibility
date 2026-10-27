# 决策指标与工具接口

本文件随Skill注入C组；同一份指标定义、工具签名和工程公式也必须放入A/B组的共同上下文。实验差异是如何组织决策，不是是否知道目标或能否使用计算器。

## 明确的选型条件

```json
{
  "priority": ["changed_spec_positions", "size_index"],
  "minimum_hydraulic_margin": 0.0
}
```

上例只表示接口结构，不是正式任务条件。priority从高到低，必须由用户给定或确认；允许以下三项：

| 字段 | 方向 | 定义与用途 |
|---|---|---|
| hydraulic_margin | 大优先 | 所有工况、适用支路的压力及流量裕量最小值；单项为1−需求/允许值。仅代表当前准静态模型下的相对裕量 |
| changed_spec_positions | 小优先 | 与公开原方案比较的规格位置变更数；独立双HST比较泵、马达两项，双功率流比较两支路的四项。不是实际拆换件数 |
| size_index | 小优先 | 各规格位置的排量除以当前允许目录该位置最大排量，再取均值。是透明的规格代理指标，不是成本、重量、体积或能耗 |

minimum_hydraulic_margin为当前任务确认的最低压力/流量相对裕量，取值为[0,1)。独立工程约束先全部通过，才能按此门槛和优先级筛选。没有对应原规格时，不使用变更数。

该指标不适用于跨构型比较。构型切换后可用相应模型重新匹配，但不能把两类构型的规格指数直接解释为工程优劣。优先级按字典序，数值比较保留10位小数，全部并列最佳候选均被接受。

## 共享补丁

项目代码：`prototype.chassis_skill_v4.decision`。它没有密钥、API或私有答案访问。

- `EvidenceSession()`：每次设计会话新建一次，只在该会话内复用证据。
- `assess(case, candidate_ids=None)`：当前输入校验、构型检查、载荷、候选匹配及约束校核；省略范围即全目录，传入ID列表则只检查该集合。
- `compare_candidates(case, evidence, policy, starting_spec=None)`：依据明确偏好返回排序、并列候选及覆盖范围。旧输入证据被拒绝；局部排序不宣称全局最优。
- `fixed_decision(session, case, policy, starting_spec=None)`：固定流程基线，完整目录校核后按同一偏好排序，具有相同缓存能力。

候选×工况是复算计数单位，不是模型请求数，也不是底层浮点运算数。缓存键包括构型、需求、效率、限值、机构、单工况和候选规格；每次重查目录成员资格。改变全局物理条件会使全部工况证据失效；只改偏好不会改变物理计算结果。缓存仅保存已实际计算的结果。

输入采用既有原型的case结构和单位；接口不补齐缺失值。开发期可运行本地入口：

```powershell
python -B -m prototype.chassis_skill_v4 --input <本地输入JSON> --output <新的结果JSON>
```

输入JSON包含`revisions`数组，每项有`case`、`policy`，可选`starting_spec`与`candidate_ids`。命令只做工程计算；不会调用模型，也不是完整API试验运行器。
