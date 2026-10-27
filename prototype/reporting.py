"""Reports derive numerical tables exclusively from saved tool results."""
from html import escape
import re
from .engineering import NOT_EVALUATED

def markdown(state):
    version = state["version"]
    review = state["gates"].get("G3")
    accepted = bool(review and review["decision"] == "accept" and review["version"] == version)
    mode = state["metadata"]["mode"]
    mode_label = {"external_llm": "当前 Codex 对话通过文件桥驱动工具", "demo_replay": "固定脚本演示（无模型调用）", "api": "模型 API 驱动工具"}[mode]
    s = state.get("selected")
    dual = state['case'].get('design_family') == 'dual_power_stream'
    scope = ('本报告覆盖给定等效转向阻力矩下的直行、缓弯与原地转向准静态计算；未辨识土壤转向阻力，也未完成横向附着、动态、热或结构验证。' if dual else
             '本报告适用于简化稳态直行模型。转向仅说明结构与运动学能力，尚未完成转向负载、热、制动或结构校核。')
    lines = ["# 联合收获机 HST 底盘初步设计报告", "",
        f"任务：{state['task_id']}　输入版本：{version}　状态：{'已接受初步方案' if accepted else '待 G3 审核'}", "",
        f"运行方式：{mode_label}。模型标识：{state['metadata'].get('model_id') or '未由运行环境提供'}。", "",
        scope, "",
        "## 1. 任务与构型", "", state["request"], "",
        f"构型：{state['configuration']['configuration']['label']}。", ""]
    for connection in state["configuration"]["configuration"]["connections"]:
        lines.append(f"- {connection}")
    lines += ["", state["configuration"]["configuration"]["note"], "", "## 2. 输入、来源与假设", "",
        "| 参数 | 数值 | 单位 | 来源/口径 |", "|---|---:|---|---|"]
    for field in state["parameter_ledger"]:
        lines.append(f"| {field['path']} | {field['value']} | {field['unit']} | {field['origin']}；{field['note']} |")
    lines += ["", "工况、效率、候选规格与版本原文保存在 requirements.json；本演示沿用已声明的算例假设，不对应完整厂家目录。", ""]
    if dual:
        lines += dual_tables(state)
    else:
        lines += independent_tables(state)
    lines += ["", "详细检查包含附着、压差、泵和马达转速、流量及总泵轴功率，各需求值与允许值保存在 state.json。", "",
        "## 5. 变更与约束反馈", ""]
    if not state["changes"]:
        lines.append("本版本尚无已接受方案的需求变更。")
    for change in state["changes"]:
        lines += [f"- v{change['from_version']} → v{change['to_version']}：{change['instruction']}",
            f"- 变更字段：{', '.join(x['path'] for x in change['fields'])}。原计算、候选、检查和审核已失效，要求重新检查。"]
        for f in change["fields"]:
            lines.append(f"- {f['path']}：{f['old']} → {f['new']}。")
    if state.get("previous_design"):
        old = state["previous_design"]
        lines += [f"- 已归档原候选：{old['selected']['candidate_id']}；当前候选：{s['candidate_id']}。"]
    failures = []
    for item in state["check_history"]:
        if item["version"] != version:
            continue
        for c in item["candidates"]:
            for failure in c["failures"]:
                failures.append(f"- {c['candidate_id']} / {failure['scenario_id']} / {failure['constraint']}：需求 {failure['required']:.2f}，允许 {failure['available']:.2f} {failure['unit']}，未通过。")
    lines += failures[:12]
    if len(failures) > 12:
        lines.append(f'共 {len(failures)} 条失败约束，上列前 12 条；完整记录见 state.json 的 check_history。')
    lines += ["", "## 6. 审核记录", "", "| 节点 | 输入版本 | 决定 | 记录方式 | 意见 |", "|---|---:|---|---|---|"]
    for r in state["review_log"]:
        lines.append(f"| {r['gate']} | {r['version']} | {r['decision']} | {r['mode']} | {r['note'].replace('|', '/')} |")
    lines += ["", "demo_script 表示演示预设意见，不能计为真实专家评价；human 表示通过独立审核入口录入的意见。", "",
        "## 7. 未评价项目", ""] + [f"- {x}" for x in state['checked'].get('not_evaluated', NOT_EVALUATED)]
    lines += ["", "## 8. 过程记录", "",
        "tool_calls.jsonl 保存实际动作、输入版本、计算输入与输出；events.jsonl 保存补充输入、审核和变更；snapshots/ 保存历史状态。",
        "llm_events.jsonl 保存 API 模式实际返回的模型事件。外部对话模式只记录显式提交的模型动作，不提供模型服务内部日志或独立盲测证据。", ""]
    return "\n".join(lines)

def independent_tables(state):
    s = state['selected']
    lines = [
        "## 3. 参数方案", "",
        f"- 左右变量泵最大排量：各 {s['pump_displacement_ml_rev_each']:g} mL/rev，共 2 台。",
        f"- 左右马达排量：各 {s['motor_displacement_ml_rev_each']:g} mL/rev，共 2 台。",
        f"- 既有总减速比：{state['case']['requirements']['fixed_reduction_ratio']:g}。",
        "- 选择规则：满足已设约束后，先选择较小泵排量，再选择较小马达排量；不表示综合最优。", "",
        "## 4. 逐工况结果", "",
        "| 工况 | 牵引力 N | 单侧马达转速 r/min | 压差 MPa | 单侧流量 L/min | 两泵轴功率 kW | 已设约束 |",
        "|---|---:|---:|---:|---:|---:|---|"]
    for r in s["scenarios"]:
        lines.append(f"| {r['label']} | {r['design_force_n']:.2f} | {r['motor_speed_rpm']:.2f} | {r['pressure_mpa']:.3f} | {r['motor_inlet_flow_l_min_each']:.2f} | {r['pump_shaft_power_kw_total']:.3f} | {'通过' if all(x['passed'] for x in r['checks'].values()) else '未通过'} |")
    return lines

def dual_tables(state):
    s = state['selected']
    lines = ['## 3. 参数方案', '', '| 支路 | 泵排量 mL/rev | 马达排量 mL/rev | 数量 |', '|---|---:|---:|---|']
    for branch, label in [('drive', '直行'), ('steering', '转向')]:
        spec = s['branch_specs'][branch]
        lines.append(f"| {label} | {spec['pump_ml_rev']:g} | {spec['motor_ml_rev']:g} | 各 1 台 |")
    coeff = state['loads']['coefficients']
    lines += ['', f"- 合成系数 A={coeff['A']:.8f}，B={coeff['B']:.8f}；ω左=Aω直−Bω转，ω右=Aω直+Bω转。",
        '- 齿数和分段减速比为示范设定，未复原实物变速箱；零速支路不计泄漏与保压功率。',
        f"- {state['checked']['ranking_rule']}。", '', '## 4. 逐工况结果', '',
        '| 工况 | 左/右速度 m/s | 设计转向阻力矩 N·m | 直行转速 r/min | 转向转速 r/min | 总泵轴功率 kW |', '|---|---|---:|---:|---:|---:|']
    for row in s['scenarios']:
        d, t = row['branches']['drive'], row['branches']['steering']
        lines.append(f"| {row['label']} | {row['left_speed_m_s']:.3f} / {row['right_speed_m_s']:.3f} | {row['design_yaw_moment_nm']:.1f} | {d['motor_speed_rpm']:.2f} | {t['motor_speed_rpm']:.2f} | {row['pump_shaft_power_kw_total']:.3f} |")
    lines += ['', '| 工况 | 直行压差 MPa | 转向压差 MPa | 直行流量 L/min | 转向流量 L/min | 已设约束 |', '|---|---:|---:|---:|---:|---|']
    for row in s['scenarios']:
        d, t = row['branches']['drive'], row['branches']['steering']
        lines.append(f"| {row['label']} | {d['pressure_mpa']:.3f} | {t['pressure_mpa']:.3f} | {d['motor_inlet_flow_l_min']:.2f} | {t['motor_inlet_flow_l_min']:.2f} | {'通过' if all(x['passed'] for x in row['checks'].values()) else '未通过'} |")
    return lines

def html_report(md):
    # Only a small, escaped Markdown subset generated above is supported.
    blocks, table, items = [], [], []
    def flush():
        if table:
            head, *rows = table
            blocks.append('<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{escape(c)}</th>' for c in head) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join(f'<td>{escape(c)}</td>' for c in row)+'</tr>' for row in rows) + '</tbody></table></div>')
            table.clear()
        if items:
            blocks.append('<ul>'+''.join(f'<li>{escape(x)}</li>' for x in items)+'</ul>')
            items.clear()
    for line in md.splitlines():
        if line.startswith('|'):
            if re.fullmatch(r'[| :\-]+', line):
                continue
            table.append([cell.strip() for cell in line.strip('|').split('|')])
        elif line.startswith('- '):
            if table:
                flush()
            items.append(line[2:])
        else:
            flush()
            if line.startswith('# '):
                blocks.append('<h1>'+escape(line[2:])+'</h1>')
            elif line.startswith('## '):
                blocks.append('<h2>'+escape(line[3:])+'</h2>')
            elif line.strip():
                blocks.append('<p>'+escape(line)+'</p>')
    flush()
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HST 底盘设计报告</title><style>
body{font-family:"Microsoft YaHei",system-ui,sans-serif;background:#edf2f4;color:#243342;margin:0;line-height:1.7}main{max-width:1120px;margin:36px auto;padding:44px;background:white;border-radius:14px;box-shadow:0 8px 36px #1b334510}h1{font-size:28px;margin:0 0 20px}h2{font-size:20px;margin-top:32px;border-left:4px solid #267b78;padding-left:12px}p,li{font-size:14px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px;margin:16px 0}td,th{padding:9px 10px;border-bottom:1px solid #dce5e9;text-align:left;vertical-align:top}th{background:#e7f1f0;white-space:nowrap}tr:nth-child(even){background:#f7f9fa}@media(max-width:700px){main{margin:0;padding:20px;border-radius:0}h1{font-size:23px}}@media print{body{background:white}main{box-shadow:none;margin:0;padding:0}.scroll{overflow:visible}tr{break-inside:avoid}}
</style><main>''' + ''.join(blocks) + '</main></html>'
