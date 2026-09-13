# M2 精简修复完成报告

日期：2026-09-14  
仓库：`dreamlizer/OurTime-PictureViewer`  
分支：`master`  
起始 HEAD：`8fd23da75cafcf1cd632b6acf12d64dbac95881b`

## 结论

本轮五项核心均为 DONE：R1、R2、R3、人物详情分页、地图聚合。
O1/O2 按任务书预算规则 DEFERRED，没有进入 B02/M3 或其他扩展工作。

全程只使用 `validation/work/` 隔离库和随机端口。
未修改正式 `data/`、原图、人脸数据或阈值，未重识别人脸，未重启当前工作台。

## 红灯证据

补丁前定向测试共 4 项失败：

- R1：真实模块没有按锁分片编号生成稳定顺序。
- R2：队列完成后 `pendingCount` 不可用，原实现尾 Promise 身份比较错误。
- R3：回执解释器不存在，pending/rejected 无法与成功区分。
- C：501 个密集合成点最大聚合仅 22 张，证明原 `round(...,4)` 基本没有形成格网。

人物分页按真实页面链路复现原逻辑风险：首屏 48 张移出 1 张后，
旧 offset 仍为 48，会跳过服务端删除后新的第 48 项。

## 逐项结果

- R1 DONE：digest 先映射为实际锁分片，去重后按分片编号升序持锁。
  保持 operation → asset → DB 顺序，DB 写锁外等待资产锁。
- R2 DONE：调用方 Promise 保留真实成功/失败；内部 tail 吞掉仅用于排序的拒绝，
  且用同一个 tail 身份清理 Map。失败后下一次写入可继续。
- R3 DONE：公共解释器区分 committed、cleanup pending/failed、pending 和 rejected/failed。
  响应丢失只查询同一 operation ID，不重发写入；清理未完成会显示明确状态。
- B DONE：移出已加载人脸时使旧请求 generation 失效，并按当前已加载数量重建 offset/more。
  100 张合成人脸移出首张后加载得到 2–100，共 99 张，无遗漏、无重复。
- C DONE：聚合和点击查询统一使用 `floor(coordinate/cell)` 整数格网，
  负坐标与边界正确；返回 `truncated` 和 `total_clusters`，页面明确提示截断。
- O1 DEFERRED：未检查/修改扫描末尾目录范围查询。
- O2 DEFERRED：未检查/修改导出一致性快照。

## 最终测试

- `python -X utf8 -m unittest -v validation.test_m2_frontend_contracts validation.test_m2_backend validation.test_m1_operation_safety validation.test_m1_frontend_contracts`
  - 28/28 PASS。
- `python -X utf8 validation/test_structured_query.py`
  - PASS。
- `python -X utf8 validation/test_m2_browser.py`
  - 4/4 PASS；真实 Chromium；分页移出和地图点击链路通过。
- `python -X utf8 validation/test_m1_frontend_browser.py`
  - 17/17 PASS；响应丢失恢复、窗口隔离和连续保存回归通过。
- `python -X utf8 validation/smoke.py`
  - 21/21 PASS，0 SKIP。

合计 71 项/检查通过。测试均基于最终源码。
未运行全仓门禁、新 clone、全局 `-W error`、模型/GPU 或正式图库测试。
本轮没有功能性 warning；Git 仅提示现有 Windows 行尾转换策略。

## 数据与迁移

本轮没有 schema 变化，不需要新增迁移。
正式服务仍需在用户另行安排的安全窗口重启后才会加载新后端；本轮未执行。

## 时间粗记

阅读与基线约 7 分钟，红灯与实现约 14 分钟，
定向浏览器验证约 7 分钟，组合回归与收尾约 5 分钟。

本轮到此停止，等待下一批复核。
