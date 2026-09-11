# 拾光 · 本地照片资料库

本机只读照片档案：扫描本机图片，记录位置、元数据、人物候选、时间和地点。浏览器是操作界面；原照片不移动、不删除、不回写 EXIF。

## 怎么跑

- 用户入口：双击 `启动拾光.vbs`；停止：`停止拾光.vbs`。页面 http://127.0.0.1:8765 。
- 启动器优先用项目 `.venv\Scripts\python.exe`，否则 `C:\Users\A\PycharmProjects\ImageBrowser\.venv\Scripts\python.exe`。
- 技术入口：`python app.py --port 8765`。API 只监听 127.0.0.1。
- 隔离验证必须另给 `PHOTO_LIBRARY_DATA`（及需要时的 `PHOTO_WEB_ROOT`），不要打正式 `data/`。
- 正式扫描若在跑，不要重启、停止或改扫描参数，除非用户明确要求。已识别人脸不要重算。

## 技术栈

FastAPI + uvicorn + SQLite WAL；前端是 `web/` 静态 HTML/CSS/JS，无构建。ExifTool 13.59 常驻读元数据；Pillow / pillow-heif 做预览；InsightFace buffalo_l 优先走 GPU（DirectML，CUDA 可用时优先），失败回 CPU。地名优先用 `G:\CodexModels\geo\osm\beijing-places.json`，区边界和 GeoNames 兜底。人脸模型默认 `G:\CodexModels\insightface`。可用 `PHOTO_MODEL_ROOT`、`PHOTO_GEO_ROOT`、`PHOTO_LIBRARY_DATA` 覆盖。

## 目录与约定

- 后台 `app.py`，元数据 `metadata_reader.py`，地名 `geo_labels.py`。
- 正式库：`data/library.sqlite3`、`data/thumbs/`、`data/faces/`；备份 `data/backups/`；PID/日志在 `data/`。
- 验证脚本在 `validation/`，证据在 `validation/reports/`；`validation/work/` 是隔离测试场。
- `app.js` 先于 `viewer.js` 加载。`esc` / `prettyPlace` / `personLabel` 等公共函数必须放在 `app.js` 前部，禁止只写在 `viewer.js`。
- 用户合同看 [README.md](README.md)。不要把用户照片路径写入文档或记忆。

## 改完怎么查错（两档，不要每次全量）

日常改 `web/`、文案、布局、分页加载：只跑 `python validation/smoke.py`。它做静态防呆，并在现役 8765 空闲时冒烟接口；不启新服务、不停正式扫描、不跑 Playwright。目标几秒结束。人物接口被扫描拖慢时允许 SKIP，不算失败。

只有改了入库、人物合同、扫描恢复、排除规则，或 smoke 已经抓到页面级错误需要隔离复现时，才跑 `python validation/validate.py`（临时库，默认 8766）。那是分钟级隔离回归，不是每次小改的门槛。

smoke 至少要拦住这些：

- `web/*.js` 不把 `undefined` 写进可见文案；`personLabel is not defined` 这类运行时缺口不能再出现。
- `prettyPlace()` / `personLabel()` / `esc()` 先定义再调用，且定义在 `app.js`。
- 照片流切换时先隐藏 `#no-results`，显示“正在加载照片”，不能先闪“没有照片”。
- 现役接口空闲时：`/api/photos/{id}` 有 `files` / `faces` / `effective_place`；`/api/people/{id}` 每张脸有数字 `asset_id`。人物详情按 `limit` 分页，默认 48 张脸；合并按钮必须是 `type=button`，提示要写在对话框内。

## 当前状态和下一步

版本 0.3。2026-09-10：正式六盘元数据已入库（D–I，不含 C；G 盘整盘排除）。人脸 GPU 补扫已结束（`completed_with_errors`），已识别 `face_state=1` 不重算。新脸合并阈值 0.50。2026-09-10 已按 0.50 收回未命名散组：并入已命名的人，并让未命名组互并；1–2 张脸的未命名组改走路人。备份 data/backups/library-20260910-160440-before-face-merge.sqlite3。库存和任务状态以 `/api/status` 为准。物体识别已接但工作台入口已隐藏；库内旧标签保留不删，不继续扫描。默认首页为时间线；原全部照片入口改为文件夹，按盘符勾选，取消勾走现有排除（不删原文件）。扫描新照片走增量，已入库会跳过。下一步是人工核对人物。地点细名以离线库 + 少量手填覆盖为准，不要让用户补完全库。地点页 Leaflet 用 `web/vendor/` 本地脚本，底图仍需联网。人物详情默认分页返回人脸，合并后不要一次渲染整组。
