# B01-R2 脱敏完成报告

日期：2026-09-13

范围：仅 B01-R2（R1、R2、R3）

起始基线：`cb4c56dcf11aebece976959ca75e446dd34820f3`

代码提交：`c4376cfa6d1f282779a1f6c71667b3e23f5c29ad`

## 1. 结论

B01-R2 已完成本地隔离修复和远端源码 clean-room 复验：

- R1：`face_state=2` 的已提交零人脸结果可直接恢复为 1，不加载模型、不重复推理、不创建人物或人脸。
- R2：`face_state=2` 进入现有“读取问题”查询，并通过 `face_publish_pending` / `face_publish_errors` 与仍需推理的 `faces_pending` 分开统计；恢复后立即失效状态缓存并从问题范围收敛。
- R3：明确 purge 时，仅按当前资产持久化的 face ID 清理其正式裁剪、`.pending-<face_id>.jpg` 和 `.recover-<face_id>-*.jpg`；preserve、其他资产和越界路径不受影响，I/O 中断后可重试。

本报告不宣布独立复核通过。正式部署、正式数据库迁移和历史修复均未执行。

## 2. 修复前红灯

在业务实现未修改时新增 `validation/test_b01_r2.py`，导入真实 `app.py`、真实 schema、真实查询/API，只替换人脸模型并在真实数据库提交和 `os.replace` 边界注入故障。

有效红灯结果：9 项中 7 FAIL、2 PASS。

| 测试 | 修复前结果 | 观察 |
|---|---|---|
| R2-T01 | FAIL | 零人脸恢复再次推理，engine 调用从 1 增至 2 |
| R2-T02 | FAIL | fresh import 恢复加载了模型 |
| R2-T03 | PASS | state=0/-1 仍进入显式重试路径 |
| R2-T04 | PASS | 既有有脸部分发布恢复保持身份字段 |
| R2-T05 | FAIL | state=2 不在问题查询，且没有独立状态计数 |
| R2-T06 | FAIL | 零人脸恢复不能无模型收敛 |
| R2-T07 | FAIL | purge 后遗留本资产 pending/recover 文件 |
| R2-T08 | FAIL | 目标暂存文件遗留；preserve 资产未被误删 |
| R2-T09 | FAIL | 清理未取得 pending 所有权，故障注入点没有被执行 |

最初一次测试调用因脚本模块搜索路径错误而退出；这是测试设施错误，修正后从新隔离目录重新运行，不计为产品红灯。

## 3. 修改文件和行为

| 文件 | 变更 |
|---|---|
| `app.py` | 零脸已提交恢复；状态缓存失效；独立发布待完成统计；按 face ID 枚举并清理正式/pending/recover 裁剪 |
| `browse_queries.py` | `filter=errors` 纳入 `face_state=2` |
| `validation/test_b01_r2.py` | R2-T01–T09 真实模块/API红绿测试 |
| `README.md` | 明确 `face_state=2` 与 `faces_pending` 的合同 |
| `.autoresearch/issues/issue-004-b01-r2-state-and-staging-closeout.md` | 本批范围、门槛和停止条件 |

没有修改前端视觉文件、人脸阈值、聚类资格或人物命名规则。

## 4. 状态合同

| 状态 | 含义 | 查询/恢复 |
|---|---|---|
| `face_state=0` | 尚需模型推理 | 计入 `faces_pending` |
| `face_state=-1` | 上次识别真实失败，可显式重试 | 进入“读取问题” |
| `face_state=2` | 检测结果已提交，包括有效零人脸；发布或完成状态待收口 | 进入“读取问题”，计入 `face_publish_pending`；有持久错误时另计 `face_publish_errors`；恢复不推理 |
| `face_state=1` | 人脸处理完成 | 正常快速跳过 |

现有前端“读取问题”视图调用 `/api/photos?filter=errors`，因此无需新增写操作或后台修复入口即可定位 state=2；列表/API 项另返回 `face_status=committed_pending_publish`。GET 状态和照片列表不会启动推理或修改资料库。

## 5. 暂存文件所有权与失败语义

- 正式裁剪由 `faces.crop` 指向。
- pending 所有权由全局唯一 face ID 编码为 `.pending-<face_id>.jpg`。
- recover 所有权由同一 face ID 编码为 `.recover-<face_id>-<uuid>.jpg`。
- 清理先确认 `derivative_policy=purge` 且资产不活动，再读取该资产的 face 行；只枚举这些 face ID 的文件。
- 所有删除仍经过 managed-folder 路径、符号链接和解析后父目录检查。
- 不使用全目录 `.pending-*` / `.recover-*` 通配清理；未知历史孤儿不在本批处理。
- 文件删除中断时数据库 face 行和 `face_state=2` 保留，异常明确表示未完成；重试删除剩余文件后再提交数据库清理。

## 6. 实际测试证据

测试运行时：

- Python 3.12.1
- 解释器：项目既有 ImageBrowser 虚拟环境
- FastAPI 0.115.0
- Pillow 12.2.0
- 远端 clean-room 使用新克隆源码和项目声明的目标 runtime
- 全部应用数据路径均验证位于各自 `validation/work/` 隔离目录
- 没有使用正式照片、正式数据库或真实人脸模型/GPU

| 命令/检查 | 实际结果 |
|---|---|
| `python -X utf8 validation/test_b01_r2.py --output validation/reports/b01-r2-20260913/final-worktree/r2.json` | 9/9 PASS；`integrity_check=ok`；外键问题 0 |
| `python -X utf8 validation/test_b01_consistency.py --output validation/reports/b01-r2-20260913/final-worktree/b01.json` | 原 B01 12/12 PASS；`integrity_check=ok`；外键问题 0 |
| `python -X utf8 validation/validate_face_auto_routing.py` | 19/19 PASS，含 5000 人/20000 脸性能门槛 |
| `python -X utf8 validation/test_photo_people_interactions.py` | 隔离端口与浏览器/API 83/83 PASS，无 JavaScript 错误 |
| `python -X utf8 validation/smoke.py` | 21/21 PASS，0 skipped |
| `python -X utf8 -m py_compile app.py browse_queries.py validation/test_b01_r2.py` | PASS |
| `git diff --check` | PASS；仅 Git 的 LF/CRLF 工作区提示 |
| 远端新克隆 `c4376cf…` 后运行 R2 / 原 B01 | 9/9 + 12/12 PASS，零未解释 warning/error |

R2 关键结果：

- R2-T01：完成状态提交故障后为 state 2、0 faces、0 people；重试后为 state 1，engine 总调用仍为 1。
- R2-T02：fresh import 恢复成功，`engine_loaded=false`。
- R2-T04：第二份 pending 裁剪发布故障后，两张 face 行和身份字段完整；恢复后 state 1，engine 总调用为 1。
- R2-T05：两个 state=2 资产带来 `face_publish_pending +2`、`face_publish_errors +1`、`faces_pending +0`，且不依赖 `job_errors`。
- R2-T07：两张 face 对应 6 个正式/暂存文件全部按授权删除，封面引用为 0，state 回到 0。
- R2-T08：preserve 资产的 3 个文件和 face 行保留，越界 crop 路径被拒绝。
- R2-T09：pending 删除故障后 state 2 和 face 行保留；第二次清理完成且重复调用释放 0 字节。

`smoke.py` 会对仍运行的 8765 做只读检查，因此其后端结果只证明旧服务兼容，不代表新后端已经部署。新后端行为由隔离 TestClient、fresh import 子进程和远端新克隆源码验证。

## 7. 数据保护、迁移与部署

- 正式 `data/` 未修改。
- 正式服务未停止、未重启，新代码未加载。
- 未重扫、未重算或修复历史人物。
- 本批没有新增 schema 或数据迁移。
- 未运行 `validation/validate.py`、真实 InsightFace/GPU 或正式图库性能测试，因为本批明确禁止。

如果以后批准正式部署，仍需把前一批 B01 尚未部署的 schema 变更一起视为 D01：

1. 先在正式库副本验证升级。
2. 维护窗口内停止正式服务并建立可恢复的 SQLite 一致性备份。
3. 启动新代码一次，核验 B01 migration marker、`derivative_policy`、revision 和 `/api/status`。
4. 不自动处理历史 state=2、历史人物或未知孤儿文件。
5. 若回退到不理解 B01 schema/状态的旧代码，同时恢复迁移前数据库备份；不能只回退代码并保留新增写入。

上述仅为迁移方案，本次未执行。

## 8. Git 交付

- 授权远端：`dreamlizer/OurTime-PictureViewer`
- 分支：`master`
- 非 force 代码 push：成功
- 代码提交：`c4376cfa6d1f282779a1f6c71667b3e23f5c29ad`
- 代码 push 后通过 `git fetch origin`、`git rev-parse origin/master` 和 `git ls-remote origin refs/heads/master` 核对一致。
- 又通过远端新克隆读取并运行 `validation/test_b01_r2.py` 和原 B01 测试，证明远端代码实际可读。

本报告自身随下一提交交付，避免在文件内预填尚不存在的自身提交 SHA。最终交付 HEAD 和远端报告可读性在推送后核验，并在最终回复中给出。

## 9. 未验证项与停止条件

- 正式部署、正式迁移、正式图库和真实人脸模型/GPU：未验证、未执行。
- 源码独立可移植性：未验证；clean-room 使用项目声明的目标 runtime。
- B02、F04–F20：未执行。
- 独立复核：待远端复核者执行。

终态：**B01-R2 隔离测试通过，代码已推送；脱敏报告随最终交付提交推送后，停止等待远端独立复核。**
