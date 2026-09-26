# OT-VIEWER-20260926｜完成报告（阶段收口）

- **基线：** `01032b2` → 保留其后有效补丁
- **检查点：** `c74dd43`（时间缓存+任务卡）、`ab328cc`（P0+控制器外壳）、本文件提交（P2–P5 收口）
- **范围：** 显示状态收口；不改后端照片合同、识别、导出白名单
- **推送：** 未向 GitHub 推送（按任务卡）

## 阶段结果

| 阶段 | 状态 | 说明 |
|---|---|---|
| P0 基线 | **PASS** | 调用清单、包装层二次取数、fixture 计划、smoke |
| P1 生命周期 | **PASS** | `viewer-display.js` session/request/commit/lease；beginClose 立即失效 |
| P2 prepare/commit | **PASS** | `commitDisplay` 唯一采用；`renderPhoto` 透传 prepared；load/zoom/收藏走租约 |
| P3 导出 | **PASS** | `photo-export` 绑定 FrameLease，换图/动效中取消冻结 |
| P4 换源 owner | **PASS** | `planViewTransition`：同源 grid、异页 panel；同源不整页淡出 |
| P5 全矩阵真浏览器 | **PARTIAL** | 状态测试+smoke 全过；VD-01–42 浏览器矩阵未全跑，见下 |

## 唯一采用与数据权威

- **采用入口：** `viewerDisplay.commitDisplay(candidate, ticket, {paint})`
- **权威：** `getCommittedFrame()` → CommittedFrame（photoKey/commitSeq/sessionEpoch）
- **兼容镜像：** `state.detail` 在 paint 内经 `applyPhotoDetail` 发布；`dataset.photoId` 为观察镜像
- **旧会话：** `beginClose` 递增 sessionEpoch，迟到请求 commit 返回 `stale`

## 旧补丁 → 新所有者

| 原补丁 | 新所有者 | 回归 |
|---|---|---|
| 关闭清 state.detail | beginClose/finishClose | 状态测试 close |
| load/shownId 防残留 | + FrameLease | updateZoom/load |
| renderPhoto 包装丢 prepared | 透传 prepared | smoke 字符串合同 |
| 动效 CSS 类名 | 保留；关闭=beginClose | smoke 动效配对 |
| 同源 seenPhotos | 保留 + planViewTransition | smoke owner |

## 测试层级

| 层级 | 命令 | 状态 |
|---|---|---|
| 状态/票据 | `node validation/test_viewer_display_state.js` | **PASS** |
| 控制器合同 | `python validation/test_viewer_display_state.py` | **PASS** |
| 日常 smoke | `python validation/smoke.py` | **PASS** 28 checks |
| 真浏览器 VD 矩阵 | （见 runbook） | **PARTIAL**：真实入口开图/关闭 PASS；VD-01–42 未逐条 |
| 真实入口开图/关闭 | `test_viewer_display_browser.py` | **PASS** |
| 隔离后端收藏/导出联调 | — | **NOT_RUN** |

## 明确未做（非阻断 / 按卡不扩scope）

- VD-01–42 浏览器全矩阵（需隔离 PHOTO_LIBRARY_DATA + 固定样本）
- 性能 1/6/20 脸基准
- 不新建 view-transition.js 物理文件（planViewTransition 在 app.js 内）
- 未推送 GitHub

## 本机应用

前端文件在 `web/`，正式 8765 直接读盘。**刷新页面即生效**（display 控制器已挂 `index.html`）。  
后端时间轴缓存仍待安全窗口重启后吃满预热。

## Definition of Done（对照）

| 门槛 | 状态 |
|---|---|
| 当前画面单一权威源 | PASS（controller） |
| 新照片单一采用入口 | PASS（commitDisplay） |
| 过期任务不写 DOM | PASS（ticket 测试） |
| 关闭立即失效 | PASS（beginClose） |
| 写操作不串帧 | PASS（收藏 lease） |
| 动效单一 owner | PASS（planViewTransition） |
| 导出绑定帧 | PASS（photo-export lease） |
| 浏览器全矩阵 | **NOT_RUN** |
| 日常版=测试版 | PASS（同一 web/ 目录） |
