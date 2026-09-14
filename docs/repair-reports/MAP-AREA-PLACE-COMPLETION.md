# MAP-AREA-PLACE 完成报告

日期：2026-09-14
结果：核心功能完成，隔离后端与真实浏览器流程通过；正式工作台未重启。

## 起点与范围

- 任务开始时 `origin/master` 与本地 HEAD 均为 `d4c0f2b`，前一项“带标签照片导出”已提交并 push。
- 开始实施前，将已完成但未提交的 1–10000 米半径、文件夹勾选刷新和重启单实例修复独立提交为 `10a6bd6`，避免混入本任务提交。
- 协作树只有当前 Agent；同目录其他任务均非运行态。另终止了前一导出任务遗留、已停在交互式 `git add -p app.py` 的旧进程，未覆盖其已推送成果。
- 本轮没有新 clone，没有修改 `viewer.js`、`photo_export.py`、人脸、视觉主题或阈值。

## 完成内容

- 地点总览和单张照片位置页共用“框选修改地点”入口与同一个控制器。进入后冻结地图视野，支持任意方向拖框、重新框选、Esc/取消退出；极小选区不查询，离开页面和迟到响应不会恢复旧面板。
- OSM 直接按 WGS84 边界判断；浅色路网把每张照片的 WGS84 坐标按与现役前端一致的本地算法转换为 GCJ02 后逐点判断。选集只含活动且坐标合法的唯一资产，覆盖全部年份，不继承主页筛选或圆形半径。
- 预览返回准确总数、最多 9 张样片、已有人工地点数量。指纹包含算法版本、坐标空间、原始 bounds，以及每个选中资产的 id、摘要、GPS、人工地点和原始地点。
- 保存使用 `BEGIN IMMEDIATE` 单一写事务：先复用 operation 回执，再重算完整选集与指纹；有新增、排除、GPS/地点变化或同数量换成员时返回 `selection_changed`，整批不写。
- 真正变化的照片才更新 `manual_place` 并写一条 `asset:<id>` 编辑记录；相同地点计入 `unchanged`。超过 1000 张必须二次确认，但不是处理上限。
- 成功后恢复地图交互并刷新相关地点显示。原 1–10000 米半径编辑和带标签照片导出保持不变。

## 实际测试

| 检查 | 结果 |
| --- | --- |
| `validation/test_map_area_place.py`：活动成员、边界、WGS/GCJ、非法输入、变化检测、回滚、幂等、字段审计、1501 张 | PASS，8 个组合测试；1501 张完整修改 |
| `validation/validate_map_area_browser.py`：总览拖框保存、单张入口取消、三次开关、慢响应竞态、冲突、390px、pageerror | PASS，12 项 |
| M2/M3 地图边缘与聚合点击范围 3 项 | PASS |
| `validation/validate_photo_place_radius.py` | PASS，原 1–10000 米流程未回退 |
| `validation/test_annotated_export.py` | PASS，5000px 合成源图、中文标签与无损 PNG |
| `node --check web/map-area-editor.js web/app.js` | PASS |
| `validation/smoke.py` | 本轮新增静态合同通过；随后在既有 `viewer.js` “大图加载态应清空旧照片/隐藏控件”门槛失败。该断言与矩形地点功能无关，本轮未顺手修改查看器。 |

浏览器证据位于 `validation/reports/map-area-place/`：`rectangle-preview.png`、`single-photo-narrow.png` 和 `browser-validation.json`，全部使用隔离合成照片与地点。

## 数据保护与遗留

- 所有写入测试均使用 `validation/work/` 下临时 DATA 和随机端口；正式 `data/`、真实照片、GPS、EXIF、人脸、日期、备注和收藏均未修改。
- 没有扫描、重识别人脸或重启正式 8765。由于后端进程未重启，正式工作台尚未加载新增 API，不能把 push 等同于本机已经生效。
- 首版不支持无 GPS 照片、跨日界线矩形、套索/多选区、时间筛选和页面内一键撤销。GPS 与底图本身的误差仍需用户按预览人工判断。
- 实施与验证约 40 分钟，未增加增强项；测试临时服务均已停止。收尾时检测到另一个独立任务新产生的未暂存 `.gitignore` 与 `tools/export_people_bundle.py` 变更，本提交没有包含、修改或验证它们，因此结束时工作树不是 clean。
