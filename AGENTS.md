# 拾光 · 本地照片资料库

本机只读照片档案：扫描本机图片，记录位置、元数据、人物候选、时间和地点。浏览器是操作界面；原照片不移动、不删除、不回写 EXIF。

## 怎么跑

- 用户入口：双击 `启动拾光.vbs`；停止：`停止拾光.vbs`。页面 http://127.0.0.1:8765 。
- 启动器优先用项目 `.venv\Scripts\python.exe`，或 config.local.json / PHOTO_PYTHON 指定的解释器。
- 技术入口：`python app.py --port 8765`。API 只监听 127.0.0.1。
- 隔离验证必须另给 `PHOTO_LIBRARY_DATA`（及需要时的 `PHOTO_WEB_ROOT`），不要打正式 `data/`。
- 正式扫描若在跑，不要重启、停止或改扫描参数，除非用户明确要求。已识别人脸不要重算。

## 技术栈

FastAPI + uvicorn + SQLite WAL；前端是 `web/` 静态 HTML/CSS/JS，无构建。ExifTool 13.59 常驻读元数据；Pillow / pillow-heif 做预览；InsightFace buffalo_l 优先走 GPU（DirectML，CUDA 可用时优先），失败回 CPU。地名优先用 `resources/geo`，区边界和 GeoNames 兜底。人脸模型默认 `resources/models`。可用 `PHOTO_MODEL_ROOT`、`PHOTO_GEO_ROOT`、`PHOTO_LIBRARY_DATA` 覆盖。

## 目录与约定

- 后台 `app.py`，元数据 `metadata_reader.py`，地名 `geo_labels.py`。
- 正式库：`data/library.sqlite3`、`data/thumbs/`、`data/faces/`；备份 `data/backups/`；PID/日志在 `data/`。
- 验证脚本在 `validation/`，证据在 `validation/reports/`；`validation/work/` 是隔离测试场。
- 导入 `app` 不应初始化数据库或恢复任务；初始化/关闭只走 FastAPI lifespan 或显式 `initialize_application()` / `shutdown_application()`。
- 扫描末尾缺失核对必须用目录谓词在 SQL 内限定当前 root；磁盘 `stat` 不放在数据库事务中，确认缺失后用路径、大小和 mtime 防并发覆盖地短事务更新。
- `/api/export` 的六张业务表来自同一显式 SQLite 读快照；它是 `format_version=2` 清单，不含完整 embedding，不替代 `Connection.backup()` 生成的数据库备份。
- `app.js` 先于 `viewer.js` 加载。`esc` / `prettyPlace` / `personLabel` 等公共函数必须放在 `app.js` 前部，禁止只写在 `viewer.js`。
- 用户合同看 [README.md](README.md)。不要把用户照片路径写入文档或记忆。

首页和合影详情用结构化筛选：人物、时间、地点、文件夹，条件之间为 AND。/api/photos 支持 person、date_from、date_to、place、directory，可与 filter=group:N 或 filter=favorites 组合。人物筛选默认只取已命名人物，不要一次把全部人物下载到浏览器。

## 改完怎么查错（两档，不要每次全量）

日常改 `web/`、文案、布局、分页加载：只跑 `python validation/smoke.py`。它做静态防呆，并在现役 8765 空闲时冒烟接口；不启新服务、不停正式扫描、不跑 Playwright。目标几秒结束。人物接口被扫描拖慢时允许 SKIP，不算失败。

只有改了入库、人物合同、扫描恢复、排除规则，或 smoke 已经抓到页面级错误需要隔离复现时，才跑 `python validation/validate.py`（临时库，默认 8766）。那是分钟级隔离回归，不是每次小改的门槛。

smoke 至少要拦住这些：

- `web/*.js` 不把 `undefined` 写进可见文案；`personLabel is not defined` 这类运行时缺口不能再出现。
- `prettyPlace()` / `personLabel()` / `esc()` 先定义再调用，且定义在 `app.js`。
- 照片流切换时先隐藏 `#no-results`，显示“正在加载照片”，不能先闪“没有照片”。
- 大图照片地图入口只能使用真实经纬度；单张地图默认约 0.2 倍，只绘制该照片的定位针和缩略图，点击缩略图必须回到同一张大图。没有坐标时提示，禁止按地点文字猜造坐标。
- 地点总览的聚合标记点击后必须保留产生标记时的完整视野边界；照片分页与大图连续浏览沿用该固定范围，不能改用移动后的地图视野或静默退成整个格网。
- 现役接口空闲时：`/api/photos/{id}` 有 `files` / `faces` / `effective_place`；`/api/people/{id}` 每张脸有数字 `asset_id`。人物详情按 `limit` 分页，默认 48 张脸；合并按钮必须是 `type=button`，提示要写在对话框内。
- 合影详情卡片的快捷排除必须二次确认并使用 `display_only`，只退出展示，不清理缩略图、人脸或姓名关系；恢复仍走现有“已排除”入口。

## 当前状态和下一步

版本 0.3。2026-09-10：正式六盘元数据已入库（D–I，不含 C；G 盘整盘排除）。人脸 GPU 补扫已结束（`completed_with_errors`），已识别 `face_state=1` 不重算。2026-09-10 曾按 0.50 收回未命名散组，并把 1–2 张脸的未命名组人工整理为路人；这是历史整理，不是自动路人规则。备份 data/backups/library-20260910-160440-before-face-merge.sqlite3。库存和任务状态以 `/api/status` 为准。物体识别已接但工作台入口已隐藏；库内旧标签保留不删，不继续扫描。默认首页为时间线；原全部照片入口改为文件夹，按盘符勾选，取消勾走现有排除（不删原文件）。文件夹浏览默认看本机磁盘，作者可用本机配置限制盘符；“添加照片”目录选择器可看本机固定磁盘和可移动磁盘，普通界面统一在添加照片页提交扫描。扫描新照片走增量，已入库会跳过。新脸先将带 `suggested_person_id` 的待确认碎片折叠到对应正式人物，再按统一的 0.52 相似度阈值归入已命名人物、用户确认的路人或未命名候选组；只有两个真正不同人物的分数相差不足 0.08 时才保留待确认，仍有歧义的候选只作提示，不进入后续自动匹配索引。低于阈值的陌生人进入待确认，同一照片仍禁止两张脸归入同一人物。不得按出现次数自动判断路人。下一步仍是人工核对人物。地点细名以离线库 + 少量手填覆盖为准，不要让用户补完全库。单张照片地图可按该照片 GPS 复核 1–500 米范围，九张缩略图只作预览，大图浏览必须覆盖范围内全部照片；批量确认只写人工地点并逐张留痕，不改原图 GPS。地点页 Leaflet 用 `web/vendor/` 本地脚本，底图仍需联网。人物详情默认分页返回人脸，合并后不要一次渲染整组。
2026-09-15 收口核对：当前正式 8765 的 `/api/health` 返回 `owner=true`、`data_dir=data/`、`runtime_version=4d27c31`，最近扫描任务已结束；该后端进程没有加载工作树中尚未提交的地点层级、地图稳定聚合等 Python 改动，工作树的启动器和绿色包也不代表正式服务已更新。不要为了让正式服务加载这些改动而自行重启；须先备份并确认扫描安全窗口。
当前源码已支持国内地点的省/市/区县/街道层级、地图总览稳定父子聚合、跨世界经度视野，以及 `打包拾光.cmd` / `tools/packaging/smoke_packed.py` 的绿色包流程。`tools/relabel_place_hierarchy.py` 只在隔离库验证过，正式库地点尚未批量重标。
本机 `dist/拾光相册-绿色版/` 是 2026-09-15 生成的本地草稿，清单标记 `source_dirty=true`，且未完成本轮绿色包验收；不把它当作发布物或当前正式运行态。下一步仍是人工核对人物、在安全窗口决定是否加载新版后端，并分别完成绿色包验收。
浏览器的全部照片、文件夹、地点、合影、收藏和人物档案视图统一固定顶栏、标题和工具区；390px 等窄屏不得横向溢出。大图左下角、资料栏上方的收藏状态写入 SQLite，不回写原照片。大图“素笺”主题使用 `web/vendor/fonts/` 内置屏幕阅读版文楷；“茶棕”主题只提供同目录内置的 Ma Shan Zheng、Long Cang、Liu Jian Mao Cao 三款毛笔字体并默认 Ma Shan Zheng；“暗朱”主题只提供 ZCOOL XiaoWei、Noto Serif SC、Zhi Mang Xing 三款题签字体并默认 ZCOOL XiaoWei。图片标签的字号和底牌透明度可调，其他主题和固定样式控件按 `viewer.js` 的主题合同执行。
