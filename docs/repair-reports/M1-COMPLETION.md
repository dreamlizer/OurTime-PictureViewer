# M1：操作正确性与写入安全完成报告

日期：2026-09-14

仓库：`dreamlizer/OurTime-PictureViewer`

分支：`master`

起始 HEAD：`b9734ddaafff0b8fba7111def6c393669836544c`

被测试代码提交：`3b32cbf69634aae969c720b5e599b04c04b04872`

## 结论

M1 已完成并通过隔离验证：C1/C2/C3 收尾、F04/F05、F06、F07/F08，以及 F15 本轮关键接口子集。M2、M3 未进入。

本轮没有直接打开或修改正式数据库、原图、人脸裁剪或缩略图；没有重启或停止正式工作台；没有执行正式迁移、历史人物修复、真实模型/GPU 推理。`smoke.py` 对既有 8765 仅执行原有只读 GET 合同检查。

## 红灯与根因

补丁前先对当前真实模块和页面建立失败测试，确认：

- import 会创建/初始化默认 DATA，初始化与遗留任务恢复没有显式 owner 边界；
- 人物/照片写入在 `await` 后仍可能使用变化后的全局对象，旧保存会覆盖新草稿；
- 查询 loader 失败可被吞掉，导致新条件配旧照片墙；
- 批量排除逐项提交、清理归属和人物合并撤销凭据不足；
- 原 B01/R2 的完整性与外键结果没有控制最终退出码。

实施中真实浏览器还捕获到“照片 v1 保存完成后刷新并覆盖 v2 草稿”，修复为只有最新草稿版本才能清理草稿和刷新详情。

## 实际修复

### A：B01-R2 收尾

- `validation/db_gates.py` 将 case、`integrity_check`、`foreign_key_check` 统一变成硬门槛。
- 暂存人脸目录按单资产一次枚举并按精确 face ID 分组；自定义合法 crop 名兼容，`1/10/100` 不混淆，未知孤儿不清理。
- `face_state=2` 在资产响应和照片详情显示“结果已提交、裁剪待恢复”；GET 不触发模型或恢复。

### B：前端一致性

- 新增 `web/operation-runtime.js`：实体会话 generation、不可变写入快照、同实体写入队列、查询提交会话、操作 ID。
- 人物详情、快速命名/候选、合并、封面、拆分、路人操作和照片补录均固定实体 ID 与会话；旧响应/错误/`finally` 不再修改新窗口。
- 照片草稿按实体 revision 管理；同实体连续写入串行，旧版本不清理新草稿。
- 查询采用“成功后提交，失败回到最后成功版本”；0 条结果是成功；旧失败不能回滚新成功。
- 排序和刷新各保留一个事件拥有者；一次操作只触发一次首屏重载。

### C：生命周期与单实例

- import 不再建库、迁移或恢复任务；FastAPI lifespan 在取得 DATA owner 后显式初始化。
- `writer.owner.lock` 使用操作系统文件锁；同一 canonical DATA 即使端口或路径表示不同也只允许一个 writer。
- 兼容检测旧版 `server.pid`；stale PID 和无关进程不被停止。
- 关闭顺序为：请求扫描安全点、等待 worker、关闭 reader、释放 owner；超时不报成功且不主动释放 owner。
- `/api/health` 返回 DATA 指纹、PID、进程启动时固定的版本和 owner 状态。
- `start.ps1` / `stop.ps1` 尊重隔离 DATA 与端口，只停止健康信息和 PID 都匹配的实例，不 force kill。

### D：批量、清理、合并

- 批量排除先完整校验并去重，在稳定顺序资产锁内用单一 SQLite 事务提交状态和清理意图。
- 清理发生在状态提交之后；部分文件失败通过持久回执如实返回 `state_committed=true`、`cleanup_status=failed`，并可精确重试。
- 恢复会清除旧 `derivative_operation_id`；迟到清理因此失去所有权，不能删除已恢复资产。
- 操作 ID 与 canonical request hash 绑定；同 key 并发/重启重试返回同一结果，换 payload 返回冲突。
- 人物合并拒绝自指、已确认降级、环/悬空关系和未确认的同照片冲突；确认 token 绑定本次 face 范围。
- 合并日志记录精确 face/引用/封面状态；撤销先 dry-run，发现新增修改、封面变化、源 ID 复用或 moved-face 状态变化时拒绝覆盖。

### E：相关 API

- 本轮修改的写接口增加正整数、数组上限/去重、字符串长度、真实日期与枚举校验；未知 filter/sort、`group:0` 不再回落到全库。
- 错误保留旧 `detail`，并增加稳定 `error_code`、HTTP status 和 operation ID。
- 前端客户端同时支持调用方 AbortSignal 与 timeout，并在结束时清理监听器和 timer。
- 既有 `/api/photos/{id}` 的 `files`、`faces`、`effective_place` 和分页字段保留。
- F15 的全面分页、地图与空间查询整理仍留给 M2。

## 行为验收矩阵

| 范围 | 状态 | 证据 |
|---|---|---|
| M1-A01–A06 | PASS | 硬门槛注入、单次枚举、精确归属、state2 GET、越界/链接拒绝 |
| M1-B01–B12 | PASS | 真实 Chromium 页面 17 项：A/B/A、关闭失效、v1/v2、查询失败/空结果、一次刷新、响应丢失回执 |
| M1-C01–C11 | PASS | Windows 隔离子进程：同库单 owner、不同库并行、旧实例/PID、崩溃、初始化失败、停机超时、脚本启停、固定版本 |
| M1-D01–D12 | PASS | 事务回滚、清理部分失败/重试、恢复竞态、并发幂等、关系冲突、确认、精确撤销、锁分片 |
| M1-E01–E05 | PASS | 参数/筛选/日期/错误结构、Abort+timeout、响应兼容、旧 schema 两次迁移 |
| M1-I01 | PASS（组合） | B01 T08–T10/T12 + M1 B01–B06：扫描提交、索引失效、并发处理与窗口对象一致 |
| M1-I02 | PASS（组合） | B01 display-only + R2 T01–T06 + M1 state2 GET：隐藏、重启恢复、零脸不重复推理 |
| M1-I03 | PASS（组合） | M1 B07–B12 + D01–D05/D11：查询失败、新范围、排除、清理失败、恢复与回执一致 |
| M1-I04 | PASS | 受控关闭/重启后读取同一持久操作回执，owner 与状态可解释 |

## 实际测试证据

主工作区最终代码共 192 项检查通过：

| 命令 | 结果 |
|---|---|
| `python -X utf8 -m unittest -v validation.test_m1_lifecycle validation.test_m1_operation_safety validation.test_m1_frontend_contracts` | 31/31 PASS |
| `python -X utf8 validation/test_m1_frontend_browser.py` | 17/17 PASS；无 pageerror/unhandled rejection |
| `python -X utf8 validation/test_b01_consistency.py` | 12/12 PASS；integrity=`ok`；FK=0 |
| `python -X utf8 validation/test_b01_r2.py` | 9/9 PASS；integrity=`ok`；FK=0；真实引擎未使用 |
| `python -X utf8 validation/validate_face_auto_routing.py` | 19/19 PASS；Fake engine；5000 人/20000 脸合同低于 1 秒 |
| `python -X utf8 validation/test_photo_people_interactions.py` | 83/83 PASS；真实 Chromium；无 JS 错误 |
| `python -X utf8 validation/smoke.py` | 21/21 PASS；0 SKIP |

另通过 Node JS 语法、Python `py_compile`、PowerShell parser 和 `git diff --check`。

干净验收使用新 clone、Windows、Python 3.12.1、项目声明的外部 `runtime`，对代码提交 `3b32cbf...` 以 `-W error` 重跑 M1 31、真实浏览器 17、B01 12、R2 9，合计 69/69 PASS，零 warning/error。首次未带项目外部 runtime 的设施运行出现 Pillow 缺 `defusedxml` 警告；这与任务书所述“仓库不独立携带目标 runtime”一致，补齐声明的目标 runtime 后在第二个全新 clone 清零。

## Schema 与本机更新方案

新增且必要的最小 schema：

- `assets.derivative_operation_id TEXT`
- `operations`
- `operation_items`
- 迁移标记 `m1_operation_safety_v1`

合成旧 schema 连续升级两次均幂等，旧人物关系、face 状态、封面和原图不变；迁移不触发扫描。正式图库本轮没有运行该迁移。

将来让新版后端生效时需要一次单独确认的本机更新：

1. 先备份正式 SQLite；
2. 等扫描处于安全状态后关闭当前后台；
3. 用新版启动一次，由 owner 保护下执行增量迁移；
4. 核对 `/api/health` 的版本、DATA 指纹和 `/api/status`，再恢复使用。

若升级失败，保持后台关闭，使用升级前备份恢复数据库并回到旧代码；不能只回退代码而继续使用已变化的正式库。

## 未验证与保留边界

- 正式图库迁移、正式服务加载新版：未执行。
- 真实 InsightFace/GPU 识别准确率和性能：未测试；本轮只用 Fake engine 守住既有规则。
- M2 的全面分页、地图/空间查询、扫描进度与完整备份恢复：未执行。
- 精确模型 ID 与推理档：环境未暴露。

## 本机当前状态

提交时正式 8765 仍是本轮开始前的同一后台 PID 52056，未重启，因此 Python 后端尚未加载 `3b32cbf...`。刷新页面可取得新静态文件，但完整 M1 生效仍需上面的“一次备份后安全关闭并重新启动”；本轮没有执行。

本轮到此停止，等待远端复核，不继续 M2。
