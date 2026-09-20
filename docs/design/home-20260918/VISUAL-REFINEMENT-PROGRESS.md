# 首页第二轮视觉修正进度

日期：2026-09-19。工作目录：`G:/Trea/照片浏览器`。未重启正式 8765，未写正式库。

## 根因

- 偏右：`.home-discovery` 用 1180px + `margin:auto`，超宽屏上正文相对主栏可用区偏窄；标题和照片不在同一容器宽度里。
- 人物卡空底：`.home-years span` 命中工具层，工具作为第 5 个网格单元折到第二行，撑出整条绿色空底。
- 年份错配：后端 `years` 是全部年份升序，封面是最早/最晚/中间挑图，前端 `years.slice(0,4)` 按位贴标签。
- 主角不清：选图只偏好“有人脸”，没有按目标人物脸框大小和人数筛选，前端整图居中裁切。
- 地点选图：通用 `pick_covers` 无条件优先人脸，标题用完整行政区。

## 改动

- `web/home-discovery.css`：正文 `max-width: 1440px` 在主栏内居中，左右 40px；首页隐藏面包屑；日期两层；主图约 62:38；人物媒体框与地点卡 16:10 对齐；工具层移出年份网格。
- `web/home-discovery.js`：封面改读 `covers[]`；年份只显示该 asset 自己的年；目标脸用 `object-position`；工具点击不打开组；文案改为数量/日期。
- `home_recommendations.py`：算法 `home-discovery-v4`。返回 `{asset_id, year, target_face_id, object_position, preview}`；跨度取首尾和 1/3、2/3；人物按目标脸可见度选图；地点不再优先人脸，标题缩成“沈阳 · 沈河区”。
- 测试：`validation/test_home_recommendations.py` 增加年份绑定、大脸优先、地点不优先人脸；`validation/test_home_visual_refinement.py` 做隔离截图和几何测量。

## 验证

| 项 | 结果 |
|---|---|
| `python -m unittest validation.test_home_recommendations` | 16 OK，含新的年份绑定 |
| `python validation/test_home_visual_refinement.py` | PASS，隔离夹具 |
| `python validation/test_home_discovery_browser.py` | PASS |
| `python validation/smoke.py` | 24 OK |
| 正式 8765 Python | 仍是 `runtime_version=7c314d0` / 旧算法，未重启，年份与地点选图尚未加载 v4 |
| 正式 8765 CSS/JS | 静态文件已是新版，布局/空底/日期/工具层已生效 |

证据目录：`validation/reports/home-visual-20260918/`。年份绑定见 `year-binding-evidence.json`（隔离夹具，2010/2014/2018/2024 与 asset/face 逐张对应）。

几何（隔离 1440×900，DPR 1）：标题/标签/主图/左卡 left=256；下排媒体高度 346.25 对齐；主栏左右空白各 40px。

## 未完成

- 正式库要等安全窗口重启 8765 后，人物年份和地点选图才会换成 v4。
- 地点封面仍可能是日常人物照；现在只是不再无条件优先人脸，也不会把吃饭说成景点。
- 未测正式库冷启动耗时。
