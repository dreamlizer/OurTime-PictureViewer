# 文档说明

项目当前的用户合同以根目录 [README.md](../README.md) 为准；运行、数据安全和验证约束以 [AGENTS.md](../AGENTS.md) 为准。

本目录中的 `修复任务卡-20260910.md`、`二轮返工任务卡-20260910.md` 和 `修复完成报告-模板.md` 是 2026-09 的历史任务规格、复核材料或报告模板，不描述当前版本的功能状态，也不能替代现役文档。

`design/home-20260918/` 是 2026-09-18 首页设计轮的历史材料，文内“待执行”是当时口径，该轮后续已实施（现役合同见根 README 与 AGENTS）。`repair-reports/` 按日期记录各轮实际改动与验收，是对应任务卡的结果文件。

`validation/reports/` 下的内容是按当时环境形成的验证证据；阅读时应保留其日期、输入和验证范围，不将其推导为当前正式服务的状态。

## 在册任务

- [设计与体验优化任务卡（2026-09-22）](设计与体验优化任务卡-20260922.md)：T1–T5 核心实施已完成，穷举验收仍有未覆盖项。实施范围与验收用例见任务卡；实际结果、证据与剩余边界以 [收口报告](repair-reports/DESIGN-EXPERIENCE-20260922.md) 为准。
- [人名标签修复任务卡（2026-09-25）](OurTime_FaceLabels_Repair_TaskCard_2026-09-25.md)：FL-20260925，已合并推送到 master。复验链为 Acceptance_9ba5fe6 → [Reacceptance_aba6c88](OurTime_FaceLabels_Reacceptance_aba6c88.md)（未通过）→ [Acceptance_0c74ffe](OurTime_FaceLabels_Acceptance_0c74ffe.md)（四项主修复通过，留两个收尾项）；最新提交 68381fe 尚无复验记录，整卡未宣布全部完成。收口材料另见 `repair-reports/face-labels-20260925/`。
