# OT-VIEWER-20260926｜P0 基线报告

- **核对基线：** `01032b2`（Smooth view and photo transitions without residual face labels）
- **执行起点：** `c74dd43`（含时间筛选缓存、任务卡落盘；保留其后有效补丁）
- **日期：** 2026-09-26
- **状态：** P0 完成，静态盘点 + smoke 通过；完整浏览器入口在 P1 阶段出口前建立

## 1. 版本与工作区

| 项 | 值 |
|---|---|
| HEAD | `c74dd43` |
| 任务卡 | `docs/OurTime_Viewer_Display_Refactor_TaskCard_2026-09-26.md` |
| 日常静态根 | `G:\Trea\照片浏览器\web\`（正式 8765 直接读盘） |
| 未纳入本轮 | `resources/public-faces/**`、`合影标签的图片/**` |

## 2. 脚本加载顺序（web/index.html）

```
operation-runtime.js → app.js → home-discovery.js → map-area-editor.js
→ face-label-model.js → viewer.js → photo-export.js
→ recent-operations.js → browse-continuity.js → waterfall.js
→ home-query-bridge.js → home-query-ui.js → home-query-init.js
```

## 3. 显示采用入口清单（P1/P2 迁移对象）

| 入口 | 位置 | 现状 | 风险 |
|---|---|---|---|
| `openPhoto` | viewer.js | seed + displayPhoto；关闭时清 detail | 同 ID 重开仅靠 detail=null，无 session epoch |
| `displayPhoto` | viewer.js | prepare → 离屏 load → renderPhoto → applySrc | 准备与发布边界在，但 load/zoom 可再绘标签 |
| `renderPhoto` 原函数 | app.js:1137 | `prepared\|\|preparePhotoDetail` 再 `applyPhotoDetail` | 纯读+混发布 |
| **`renderPhoto` 包装** | **app.js:1573-1581** | **`renderPhotoImplementation(id)` 丢掉 prepared** | **二次取 detail；entity session 在包装层** |
| `applyPhotoDetail` | app.js:1136 | 写 `state.detail` + 资料 DOM + `showDialog` | 读/显/开窗混合 |
| `#detail-img` load | viewer.js:2491+ | `renderFaceNames(state.detail)` | 已有 photoId 校验；仍读全局 |
| `updateZoom` | viewer.js:1346 | rAF 里 `renderFaceNames(state.detail)` | 检查与执行跨异步 |
| 主题/别名/方向 | viewer.js 多处 | 直接 `renderFaceNames(state.detail)` | 无租约 |
| 收藏回执 | togglePhotoFavorite | 按 photoId 刷新 | 与加载锁可能互踩 |
| 导出 | photo-export.js | 等字体/两帧后 freeze | 未绑定 commitSeq |
| 关闭 | closePhotoViewer + dialog close | 150ms timer + 清 detail | timer 与 native close 双入口 |
| 页面切换 | setView + prepare/reveal + waterfall | view token + preserveUntilReady | 动效 owner 仍可叠 |

### 3.1 `renderPhoto` 包装实证（S2）

```js
const renderPhotoImplementation = renderPhoto;
renderPhoto = async function (id) {           // 不接收 prepared
  const session = entitySessions.open('photo', Number(id));
  entitySession.photo = session;
  const rendered = await renderPhotoImplementation(id); // 再次 preparePhotoDetail
  ...
};
```

`displayPhoto` 调用 `renderPhoto(id, prepared)` 时 **prepared 被丢弃** → 每次换图多一次 `/api/photos/{id}`。P2 必须透传并合并 entity session 时点，不破坏识别状态提示。

## 4. 已有防护（保留，P1 收编）

- 关闭/open 时 `state.detail=null`、`delete dataset.photoId`
- load / updateZoom 绘制前 `detailId===shownId`
- `clearFaceNames` + `renderFaceNames` 无 photoId 则不画
- 动效类名 `viewer-photo-arriving/forward/backward/viewer-closing` + smoke 锁
- 同源换源 `seenPhotos` 预标记、`preserveUntilReady`
- 退出再进不残留人名（ab12ebd + 后续 session 清理）

## 5. 异步边界 → 可写 DOM/state（摘要）

| 异步 | 可写 | 现有防护 |
|---|---|---|
| preparePhotoDetail | 无（应保持） | ticket |
| 离屏 Image load/decode | 无 | presentation token |
| applySrc | 主图/签名/标签/按钮 | 同步段 |
| detail-img load | 标签/zoom | shownId |
| rAF (updateZoom/reveal) | 标签/面板 class | 排队前检查 |
| 收藏/坐标/命名回执 | 按钮/标签/缓存 | 部分 photoId |
| 导出 freeze | 冻结树 | ready() |
| close timer | dialog.close | closingTimer |

## 6. Fixture 计划（隔离 PHOTO_LIBRARY_DATA）

| 样本 | 用途 |
|---|---|
| A/B/C 身份、姓名、签名、收藏明显不同 | VD-01～11 串图 |
| A 再开 A | VD-05 同 ID 重开 |
| 无脸图 | 标签空层 |
| 长中文名 / 中英混合 / 纯英文 | VD-13 |
| 横幅 + 竖幅 | 几何/标签 |
| 1/6/20 脸 | VD-41 性能边界 |

## 7. 测试门槛

- 日常：`python validation/smoke.py`（本阶段通过，26 checks）
- 聚焦显示：P1 起 `validation/test_viewer_display_state.*` + 真实入口脚本
- **不**用 `validate.py` 全量替代；不重扫、不碰正式库

## 8. P0 出口

| 项 | 状态 |
|---|---|
| 版本/未提交工作记录 | PASS |
| 调用与包装层清单 | PASS（发现 prepared 丢弃） |
| 已有补丁保留策略 | PASS |
| fixture 计划 | PASS |
| smoke | PASS |
| 真实浏览器入口命令 | 进行中 → P1 出口前完成 |

**允许进入 P1。**
