# B01-R2：已提交人脸状态与暂存裁剪收尾

## Goal
在当前真实模块和 API 上复现并修复 B01 复核提出的 R1、R2、R3，使零人脸已提交结果可无推理恢复、`face_state=2` 可查询且状态闭环、明确清理仅删除本资产拥有的正式和暂存裁剪文件。

## Inputs Ready
- 当前仓库 `master` / `origin/master`：`cb4c56dcf11aebece976959ca75e446dd34820f3`
- 项目 `AGENTS.md`、`README.md`
- `OurTime_B01_Review_and_R2_Task_20260913 (1).md`
- 现有 `validation/test_b01_consistency.py` 与隔离测试机制

## Scope
- R1：零人脸、已提交 `face_state=2` 的恢复
- R2：`face_state=2` 的问题筛选、独立计数和恢复后收敛
- R3：明确 purge 时，本资产正式、pending、recover 裁剪的所有权与清理
- 新增真实模块/API红绿测试
- 更新必要合同说明和脱敏完成报告
- 非 force commit/push，并核验远端代码和报告

## Acceptance Criteria
- [ ] R2-T01–R2-T09 在隔离数据上通过
- [ ] R2-T02 至少一次 fresh import / 新进程恢复
- [ ] R2-T04 至少一次从真实 `process_faces()` 注入发布或最终提交故障
- [ ] 原 B01 12 项回归全部通过
- [ ] 人脸路由和相邻人物操作无新增回归
- [ ] 测试未访问正式 `data/`，未启动真实模型/GPU
- [ ] 正式 8765 服务未停止或重启，正式库未迁移
- [ ] 代码、测试、脱敏报告已提交并非 force push
- [ ] 远端包含代码提交和最终报告提交

## Out of Scope
- B02、F04–F20
- 正式部署、正式迁移、历史人脸修复或重扫
- 视觉、人脸阈值、聚类与命名规则
- 全仓重构、依赖升级或真实模型/GPU验证

## Clarity Gate
- Score: 100/100
- Automation level: Level 3
- Remaining assumptions:
  - 当前授权远端仍为 `dreamlizer/OurTime-PictureViewer`
  - 报告使用脱敏相对路径与匿名计数，不提交本机测试工作目录

## Notes for Codex
- 使用 Codex 单 Agent 连续执行
- 先红灯、后最小实现、再回归
- 所有测试显式指向 `validation/work/` 下的新隔离目录
- 禁止 import 新代码时默认落到正式 `data/`
- 不执行 force push
