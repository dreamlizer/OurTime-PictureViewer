# 带标签照片导出修正完成

状态：DONE

起始提交：`18beaa8c805691ca4702b08d9aa67d19b932ab67`

## 完成内容

- 导出前从当前 `#photo-mat` 逐节点冻结计算后的布局、字体、颜色、透明度、背景、书写方向、间距、变换与伪元素；后台不再重新调用标签或底签布局函数。
- 后台只把同一资产的 `/api/original/{id}` 放入冻结版式，按原图尺寸等比放大整张成品。原图不可读、版式过期或格式不支持时明确失败，不以预览图降级。
- 默认导出高质量 JPEG（quality 92），工具栏紧邻保留 PNG 选择；成功提示和下载扩展名与实际格式一致。
- 关闭、人名开关、翻页、收藏、地图、底签切换、悬浮引导和提示浮层均不进入导出成品；快速切图时再次核对当前照片 ID。
- 地图框选按钮移除无意义矩形图标。待框选、拖动和计算时闪烁并显示进行中文案；选区完成后停止闪烁；取消或保存后恢复普通状态。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| 失败测试基线 | 旧实现缺少 `render_annotated_image`、v2 计算样式快照、JPEG/PNG 选择及框选阶段状态，按预期失败 |
| `python -m unittest validation.test_annotated_export_fix` | PASS，2 项 |
| `python -m unittest validation.test_export_and_map_ui_contract` | PASS，2 项 |
| `python validation/test_annotated_export.py` | PASS，5000px PNG 兼容回归 |
| `python validation/validate_annotated_export_fix_browser.py` | PASS，隔离合成照片 24 项 |
| `python validation/validate_annotated_export_real_readonly.py --data data` | PASS，正式库只读抽取 3 张，隔离 DATA/随机端口运行，未保存私人照片副本 |
| `python validation/validate_map_area_browser.py` | PASS，15 项 |
| `python validation/smoke.py` | 本轮导出与框选静态合同均通过；随后在既有“大图切换加载态必须立即清空旧图”的检查失败，与本轮两项修改无关，未扩大范围处理 |

三张真实照片分别覆盖 3 人、1 人和 2 人标签，原图尺寸为 `5408×3680`、`3648×5472`、`3680×5408`；查看器与导出缩放对照的平均像素差为 `5.859`、`1.627`、`3.484`。三张原片 SHA256 前后不变，正式数据库以只读模式打开。

隔离合成样图覆盖多人竖排、单人素笺和双人茶棕/不同底签，平均像素差为 `1.528`、`2.233`、`0.575`。同一张 3200px 样图的 JPEG 为约 1.47 MB，PNG 为约 3.99 MB。

## 脱敏样图

- [多人竖排：当前查看器](assets/annotated-export-fix/multi-vertical-viewer.jpg) / [导出成品](assets/annotated-export-fix/multi-vertical-export.jpg)
- [单人素笺：当前查看器](assets/annotated-export-fix/single-ivory-viewer.jpg) / [导出成品](assets/annotated-export-fix/single-ivory-export.jpg)
- [双人茶棕与不同底签：当前查看器](assets/annotated-export-fix/two-tea-viewer.jpg) / [导出成品](assets/annotated-export-fix/two-tea-export.jpg)

样图均为合成图和虚构姓名。真实照片只在内存与本次临时隔离目录中验证，目录退出后清理，没有写入报告或提交。

## 边界与限制

- 仍是单张导出，只支持普通静态 JPG/JPEG 和 8 位 RGB/RGBA PNG；不做 RAW、HDR 或批量导出。
- 维持 40MP、单边 12000px 的成品保护上限；超过上限明确拒绝，不自动缩小。
- 本轮没有修改正式数据库、原图、EXIF、GPS、人脸或扫描状态，也没有重启 8765。当前正式后台需以后正常重启才会加载新的导出接口实现。
- 工作区原有 `.gitignore` 和 `tools/export_people_bundle.py` 属于另一任务，本次不纳入提交。
