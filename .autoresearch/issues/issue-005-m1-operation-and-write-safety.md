# M1：操作正确性与写入安全

## Goal
在不接触正式图库或正式服务的前提下，完成任务书定义的 M1：B01-R2 小收尾、前端异步与查询一致性、本机生命周期与单实例、批量写入/清理与人物合并安全，以及相关 API 合同。

## Inputs Ready
- 起始 `master` / `origin/master`：`b9734ddaafff0b8fba7111def6c393669836544c`
- `AGENTS.md`、`README.md`
- `docs/repair-reports/B01-R2-COMPLETION.md`
- `OurTime_M1_Review_and_Repair_Task.md`
- 当前应用、启动脚本和验证套件

## Scope
- A：完整性硬门槛、单次暂存目录枚举、state=2 可读说明
- B：实体会话、草稿版本、查询提交/回滚、重复事件绑定
- C：显式初始化、资料库 owner、遗留任务恢复、安全关闭、运行版本
- D：批量排除两阶段结果、幂等、清理重试、人物合并/撤销
- E：相关端点参数、结构化错误、Abort/timeout 与兼容合同
- M1 行为矩阵、组合回归、脱敏报告、非 force push 与远端核验

## Acceptance Criteria
- [x] M1-A01–A06
- [x] M1-B01–B12，关键场景有真实浏览器验证
- [x] M1-C01–C11，Windows 隔离进程实测
- [x] M1-D01–D12
- [x] M1-E01–E05
- [x] M1-I01–I04
- [x] 原 B01 12 项、R2 9 项作为硬门槛通过
- [x] 人脸路由、相邻人物/API、smoke 无新增回归
- [x] 所有写入位于 `validation/work/`，正式 8765 和正式 `data/` 不变
- [ ] 代码、测试和 `docs/repair-reports/M1-COMPLETION.md` 已提交并非 force push
- [ ] 远端包含被测试代码和完成报告

## Out of Scope
- M2、M3
- 正式数据库升级、本机工作台重启、历史人物/孤儿修复
- 视觉、导航业务、阈值、模型、图库范围
- 全仓重构、框架替换、云服务或外部协调服务

## Clarity Gate
- Score: 99/100
- Automation level: Level 3
- Remaining assumptions:
  - M1 允许必要的增量 schema，但只在合成隔离库验证
  - 前端必要状态文案不构成视觉改造

## Notes for Codex
- 单 Agent 连续执行，不在内部包之间停等
- 先对当前真实实现建立失败测试，再实施最小补丁
- 保留每个故障注入、迁移、owner 与浏览器证据
- 最终停止在 M1，不进入 M2
