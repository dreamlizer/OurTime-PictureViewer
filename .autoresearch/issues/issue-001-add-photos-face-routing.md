# 添加照片流程简化与人脸自动分流

## Goal
把所有普通扫描入口统一到极简“添加照片”流程，并让以后新处理的人脸按已命名人物、用户确认的路人和待确认人物进行保守分流。

## Inputs Ready
- 当前仓库 `AGENTS.md`、`README.md` 与实际前后端代码
- 用户提供的详细任务卡及本轮审阅后的调整结论
- 现有添加照片、人物、扫描和浏览器验证脚本

## Scope
- 添加照片页、目录选择器和扫描入口收口
- 人物级人脸评分、自动身份阈值、候选阈值与 margin
- 已命名人物、已标记路人、未命名候选三类状态分流
- 隔离 SQLite、合成 embedding、小规模只读真实照片校验和浏览器验收
- 仅同步本次行为变化涉及的 README

## Acceptance Criteria
- [x] 添加照片可选择多个本机固定或可移动磁盘目录，自动去重和移除
- [x] 文件夹浏览页继续只浏览 I 盘，但其扫描入口改为预填添加照片页
- [x] 普通 UI 只有添加照片页提交 `/api/scan`
- [x] 新脸只在高阈值且人物级 margin 足够时自动进入已命名人物或已有路人
- [x] 中等置信度已命名候选生成“可能是某人”，不污染已命名组
- [x] 未命名候选仍按聚类阈值归组，陌生人进入待确认
- [x] 同一照片不能自动把两张脸放入同一人物
- [x] `face_state=1` 仍直接跳过
- [x] 隔离验证不修改正式 `data/`、不重扫正式照片、不重算正式人脸
- [x] smoke、专项人脸测试、添加照片浏览器验收和相关核心回归均通过

## Out of Scope
- 修改原照片或 EXIF
- 重跑或重新聚类正式图库
- 新前端框架、桌面客户端或 Explorer 拖放桥接
- 重做主页、人物页、文件夹页或照片查看器
- 更换人脸模型、调整 SHA256 去重机制或无关重构

## Clarity Gate
- Score: 94/100
- Automation level: Level 3
- Remaining assumptions:
  - `0.68 / 0.08` 只作为保守初值，最终以隔离测试与小样本证据决定
  - 路人状态优先于 confirmed；两类高分候选相互冲突且 margin 不足时进入待确认
  - 当前未提交改动先作为独立安全检查点保存，不被本任务覆盖

## Notes for Codex
- Stay local-first and use Codex only
- Preserve originals, formal data, running service, and existing worktree changes
- Separate UI flow and face-routing changes into reviewable commits
- Do not push unless the user request and repository state continue to authorize it

## Execution Result
- `0.68 / 0.08` 通过正式库 embedding 的只读留一抽样：已命名、路人各 120 张均无自动错归；未命名 120 张无跨组错归。
- 添加照片真实浏览器验收 21 项、自动人脸分流 17 项、照片人物交互 18 项、smoke 17 项均通过。
- 正式服务未重启，正式资料库文件大小与修改时间在验收前后保持不变。
