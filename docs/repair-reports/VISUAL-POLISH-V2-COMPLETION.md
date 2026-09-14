# 浏览质感与界面精修 V2 完成报告

日期：2026-09-14
起始 HEAD：`13bb83fe02a1344e003e0c0fe9c10e8ca4866998`
范围：移动硬盘核对保护、查看器连续翻图与既有界面的小范围统一精修。

## 结果

- **A DONE**：`reconcile_missing_files()` 每批开始及提交疑似缺失前都会重验根目录。可移动盘在中途失联时，未提交批次不会写成缺失，函数停止后续批次并返回 `interrupted` / `reason`；已在正常根目录下提交的早期批次不回滚。
- **B DONE**：查看器改为先取得详情并 decode 候选图，再一起提交实体会话、`state.detail`、资料、图片、底签和姓名层。等待时旧图与稳定的导航/工具保留，写入控件只暂时禁用；220ms 后才显示小型加载提示。失败继续保留旧实体，并可用同方向导航重试；关闭或后来的意图会取消旧图等待，不能重新打开旧会话。常规淡入为 140ms，去掉左右位移和缩放。
- **C DONE**：沿用原灰绿与既有主题，深化次级文字/控件边界对比；卡片说明区仍为 42px，地点 13px、日期 12px 等宽数字；已命名人物卡收为近白浅绿。侧栏将文件夹、收藏、维护换为对应的线性图标，未改 Logo、导航入口或“整理”。首页筛选的文件夹按钮补上真实的 `aria-expanded`；其余筛选、排序与查询合同未改。
- **D DONE（隔离）**：以四张纯色合成 JPEG 和临时 SQLite 运行真实 Chromium 页面。无正式照片、原图、正式 `data/`、人脸计算或扫描任务被触碰。

## 实际验证

| 命令 | 结果 |
|---|---|
| `python -X utf8 -m unittest -v validation.test_m3_closeout` | 5/5 PASS，含新增“首批中途断连不误标”用例 |
| `python -X utf8 validation/test_m1_frontend_browser.py` | 17/17 PASS，真实 Chromium；实体、草稿版本、延迟响应和操作归属回归通过 |
| `python -X utf8 validation/test_visual_polish_v2.py` | PASS；V01–V04、V09、V10 与无 pageerror / 未处理 rejection |
| `node --check web/app.js web/viewer.js web/home-query-ui.js`、`python -m py_compile app.py`、`git diff --check` | PASS |

合成画面对照（被 `.gitignore` 保持为本地验证证据）：

- `validation/reports/visual-polish-v2/desktop-home.png`：1280×800 首页，侧栏、筛选与照片墙。
- `validation/reports/visual-polish-v2/narrow-home.png`：390×844，无横向溢出。
- `validation/reports/visual-polish-v2/viewer-reduced-motion.png`：1280×800 大图与弱动效。

未录制视频：环境未使用现成录屏工具；改以同一真实浏览器流程的延迟状态断言和三个关键画面佐证，不将其表述为录屏验收。

## 保留边界

- 未做下一张预取、自动播放节奏优化、瀑布流列数/算法、标签主题、业务规则或全仓审计。
- 未模拟所有微秒级拔盘竞态；覆盖的是批次开始和提交前两个会导致误标的边界。
- 本轮约 60 分钟，未扩展到范围外功能。

## 运行态

正式 `data/`、原图和现有后台均未修改，当前工作台没有被停止或重启。推送的新代码与当前正式窗口是否已加载应分开看：本轮没有主动刷新该窗口，因此**尚未确认它已加载新版前端**；后端的断连保护需在以后经备份和扫描安全核对后的单独重启才会生效。
