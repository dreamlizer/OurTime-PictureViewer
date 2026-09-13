# M3 最终集中收尾报告

日期：2026-09-14  
仓库：`dreamlizer/OurTime-PictureViewer`  
分支：`master`  
起始 HEAD：`d7d86019ef698e4ac13c9ea2d14cfcb5ca2098d2`

## 结论

M3 的 A/B/C/D 均为 DONE，本次专项可以结束。
这表示本轮集中修复及主要相邻回归完成，不表示原审计 F01–F20 的所有增强项均已实现。

全程只使用 `validation/work/` 隔离 DATA 和随机端口。
未修改正式图库、原图、人脸记录或阈值，未重算人脸，未重启当前工作台。

## 补丁前红灯

- A：同格网两点中仅一点位于当前视野；标记为 1 张，点击原实现得到 2 张。
- B：扫描末尾仍读取全部 `files`，并在同一写事务中逐条执行磁盘 `stat`。
- C：JSON 导出逐表读取，但没有显式统一读事务。

## A：地图视野范围（DONE）

- 新增可选 `map_west/map_south/map_east/map_north`；提供时必须四项齐全、有限且顺序合法。
- 地图标记保存产生它的视野边界；点击后的照片分页和大图浏览继续使用该固定边界。
- 聚合与点击查询仍使用同一 floor 整数格网；负坐标、格网边界和截断合同保留。
- 边缘合成场景现在为标记 1 张、点击结果 1 张。

未扩展跨日界线或扫描期间的永久服务端浏览快照。

## B：扫描末尾目录核对（DONE）

- `reconcile_missing_files()` 复用目录谓词，在 SQL 内只分页读取当前 root。
- 每批先结束读连接，再在数据库事务外执行磁盘 `stat`。
- 只将明确 `FileNotFoundError` 视为缺失；权限和 I/O 错误记录但不标记消失。
- root 失联或暂停时保守退出；不启动新核对批次。
- 缺失更新使用 ID、路径、大小和 mtime 作为并发保护，并在短事务中提交。
- 测试证明 stat 阻塞时，另一连接仍能完成独立短写入。

未改扫描 attempt、历史页、模型执行或整体进度体系。

## C：JSON 导出快照（DONE）

- `collect_export_snapshot()` 在第一次读取业务表前显式 `BEGIN`。
- assets、files、people、faces、edits、excluded_roots 来自同一 WAL 读快照。
- 数据采集结束并关闭事务后才进行 JSON 序列化和响应。
- 并发写入可以提交，但不会让导出出现主表旧、关联表新的混合状态。
- 读取失败会回滚并释放连接；空库、`format_version=2`、字段及下载文件名保持不变。

JSON 仍是一次性内存清单，不含完整 embedding，也不是恢复包。
完整数据库备份仍使用 SQLite `Connection.backup()`。

## D：维护与结构（DONE）

- `AGENTS.md` 增加初始化、目录核对、地图范围、JSON 快照与备份边界。
- `README.md` 同步用户可见的缺失核对和导出说明。
- 只按自然边界抽出两个小函数，未拆分 `app.py` 或前端大文件，未进行全仓重构。

## 最终测试

- `python -X utf8 -m unittest -v validation.test_m3_closeout validation.test_m2_backend`：7/7 PASS；integrity=`ok`、FK=0。
- `python -X utf8 validation/test_m2_browser.py`：5/5 PASS；真实 Chromium；无 pageerror/未处理 Promise。
- `python -X utf8 validation/test_structured_query.py`：PASS。
- `python -X utf8 validation/smoke.py`：21/21 PASS，0 SKIP。
- 三个前端文件 `node --check`、Python `py_compile`、`git diff --check`：PASS。

合计 34 项行为检查通过。未运行新 clone、全局 `-W error`、完整全仓门禁、
真实模型/GPU、正式库写入或跨平台验证。Git 仅提示现有 Windows 行尾转换策略。

## Schema、运行态与遗留

本轮没有 schema 变化，不需要新增迁移。
只读核对显示当前 8765 仍返回旧版 health 格式，尚未加载 M1–M3 新后端。

主要遗留：并发改库下的全局不可变照片序列、扫描 attempt/历史与全面性能工程、
完整导入恢复和大导出流式化、人脸证据来源体系、全面上帝文件拆分、
独立安装包与跨机器验证。上述均为 DEFERRED，不自动进入 M4。

以后本机生效时，应先确认扫描安全、备份正式 SQLite，再按旧实例兼容方式核对
进程归属并安全停止，随后启动新代码并检查新版 health；禁止猜 PID 或批量强杀 Python。

## 时间粗记

阅读与定位约 7 分钟，失败测试和实现约 16 分钟，
定向及组合验证约 7 分钟，文档与收尾约 5 分钟。

本专项到此结束。
