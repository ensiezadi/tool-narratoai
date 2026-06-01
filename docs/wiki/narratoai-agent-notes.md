# NarratoAI Agent Notes

Created: 2026-06-01
Last updated: 2026-06-01 17:32:33 CST

## Hermes 副手与 Wiki 沉淀规则

- 时间: 2026-06-01 05:46:08
- 来源: Hermes CLI + Codex 校对

## 决策记录：Hermes（MiniMax-M3）副手职责边界

**决策**
Hermes 作为 NarratoAI 项目的本地轻量副手，负责生成反馈草稿、文档整理和局部表达建议；Codex 负责最终校对、工程判断、代码实现与验证。

**原因**
MiniMax-M3 对中文技术表达和结构化总结很有帮助，适合快速产出可审阅的反馈。但项目里的剪辑逻辑、前端实现和配置安全仍需要 Codex 结合代码上下文做最终判断，避免把未经校对的建议直接落地。

**使用方式**
- 对 UI、文案、工作流方案给出 5 条以内反馈。
- 将有长期价值的结论整理为 wiki 决策记录。
- 简单任务走 Hermes oneshot，复杂实现由 Codex 接管。

**边界**
密钥、Token、凭证不写入代码、日志或 wiki 正文。Hermes 反馈只有在 Codex 认为有复用价值时才沉淀进项目 wiki。

## Hermes 省 token 协作计划

- 时间: 2026-06-01 05:48:33
- 来源: Hermes CLI + Codex 校对

## 决策记录：Hermes 省 token 协作计划

**决策**
Hermes 只在任务复杂度值得时输出计划；简单执行类任务直接给结论、路径和状态。

**规则**
- 3 步以上、跨模块、涉及外部依赖或存在风险时，先给 3-5 条计划。
- 已确认的流水线操作、查状态、跑测试、改小配置，不生成长计划。
- Hermes 反馈默认不超过 5 条；Codex 负责筛选、校对和实现。
- 只有选型决策、踩坑修复、接口约定、稳定工作流写入 wiki。
- 密钥、Token、一次性报错和寒暄不写入 wiki。

**落地**
默认规则保存在 UI 的“本地 Hermes / 多 Agent”面板，可按项目阶段调整。

## 任务同步闭环借鉴记录

- 时间: 2026-06-01 16:52:13
- 来源: Hermes CLI + Codex 校对

## 决策记录：借鉴 Hermes Kanban / Dida365 的任务闭环

**决策**
NarratoAI 使用本地 JSON 作为任务事实源，同步到 Hermes Kanban 做 agent 协作；Dida365 作为用户可见任务系统，只有在凭据存在时同步。

**借鉴点**
- Hermes Kanban 的独立 board 隔离项目。
- `idempotency-key` 防止重复创建任务。
- 外部系统失败不阻断本地进度。
- 报告只保留摘要，避免长错误浪费 token。

**当前状态**
`narratoai` Kanban board 已创建，3 个任务已同步，2 个完成、1 个待办。Dida365 因缺少凭据被跳过。

**边界**
Dida365 token 不写入项目或 wiki；凭据只从环境变量或 `~/.hermes/credentials/dida365.json` 读取。

## 文档时间与成片前检查

- 时间: 2026-06-01 17:32:33 CST
- 来源: Codex 落地

## 决策记录：文档必须维护更新时间

**决策**
NarratoAI 项目文档统一维护 `Created` 和 `Last updated`；wiki raw 使用
`created` 和 `last_updated`。修改结论、命令、路径、状态或外部依赖时必须更新时间。

**落地**
新增 `scripts/check_highlight_script.py` 作为 Kaggle 精选脚本的成片前检查入口，当前
`resource/scripts/part4_benchmark_compact_script.json` 检查通过：7 段、总时长 89.0 秒、
单段 11.5-14.0 秒，事件分布为 5 个 `strong_reaction`、1 个 `puzzle_progress`、1 个
`death_fail`。
