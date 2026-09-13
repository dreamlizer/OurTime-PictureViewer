# 修复人脸候选碎片归类

## Goal
把指向同一正式人物的待确认碎片视为同一个人物，以统一的 `0.52` 相似度阈值处理新脸，避免同一人物的正式组与候选组互相竞争。

## Inputs Ready
- 正式库中已保存的人脸 embedding 和 `suggested_person_id`
- 已复现样本：同一人物的正式组与候选组占据前两名，导致新脸未进入正式人物
- 现有隔离验证脚本与只读正式库抽样脚本

## Scope
- 调整 `app.py` 的人物索引和新脸归类决策
- 同一正式人物及其候选碎片统一为一个 canonical person
- 统一归类阈值为 `0.52`，只对两个真正不同人物保留 `0.08` 的近似打平保护
- 保留同一照片不能重复归入同一人物的保护
- 更新人脸路由隔离测试、只读抽样和现役合同检查
- 对历史候选碎片先做只读演算，确认后按新规则收拢

## Acceptance Criteria
- [x] 指向同一正式人物的候选组不再作为彼此的竞争者
- [x] canonical person 最高相似度达到 `0.52`，且领先另一个真实人物至少 `0.08` 时，直接进入正式人物或已确认路人
- [x] 未指向正式人物的候选组达到 `0.52` 时继续正常聚组
- [x] 低于 `0.52` 的陌生人保持待确认
- [x] 同一照片不会把两张脸归入同一个人物
- [x] 目标照片的两张脸在只读回放和现役接口中分别进入正确正式人物
- [x] 隔离人脸路由测试、正式 embedding 只读抽样和 smoke 通过
- [x] 不重新提取正式库中已识别的人脸

## Out of Scope
- 更换 InsightFace 模型
- 重算 `face_state=1` 的照片
- 修改原照片或 EXIF
- 调整人物标签视觉样式

## Clarity Gate
- Score: 96/100
- Automation level: Level 3
- Remaining assumptions:
  - 历史碎片写入前必须先通过正式库只读演算
  - 正式扫描若仍在运行，不重启服务

## Notes for Codex
- Stay local-only
- Use Codex as the only worker
- Keep formal data read-only during validation
- Preserve unrelated working-tree changes

## Completion Evidence
- 隔离人脸路由 19 项通过；5000 人、20000 张脸单次匹配低于 1 秒
- 正式 embedding 留一抽样 360 张，自动错归 0
- 目标照片现役接口返回人物 142（郑婷）和 1255（徐韬越／小布）
- 历史候选碎片共收回 608 张脸、删除 201 个空人物组；稳定复核后保留 48 张边界脸（35 张人物接近、13 张低于阈值）
- 正式库备份：`data/backups/library-20260913-183036-before-canonical-face-repair.sqlite3`
- 正式库 `PRAGMA integrity_check=ok`，扫描保持完成 2948/2948，未重算已识别人脸
