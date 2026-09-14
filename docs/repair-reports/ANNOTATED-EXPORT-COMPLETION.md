# 带标签照片导出收口

状态：PARTIAL（首版静态 JPG/PNG 已完成；未声明其他格式或任意本机字体）。

起始提交：`2047d3abc65d9de603cd468747866bd3a9fbef67`。本次只增加单张带标签 PNG 导出及快速翻图意图失效保护；未改扫描、人脸计算、正式数据库或原文件。

## 实际效果

- 大图稳定后，“导出带标签照片”冻结当前 `#photo-mat` 的姓名标签位置、标签主题与底签结构；关闭、收藏、地图与悬浮导线不会进入成品。
- 后端只按已显示资产 ID 读取原文件，普通静态 JPG/JPEG、8 位 RGB/RGBA PNG 经本机 Chromium 以纠正方向后的原图尺度生成一次 PNG；不走 `/api/preview`，不产生 JPEG 中间件。
- 单次一张、内存返回下载、40MP/单边 12000px 上限；缺原图、非首版格式、版式不安全、字体/渲染失败或超限均明确拒绝，不自动缩小或改样式。
- A→B→C 与 A→B→A 的新导航意图在点击时即令慢的中间候选失效，不再让其提交到当前查看器。

## 样图与保真证据

合成样图位置：

- [合成细纹理源图（缩略对照）](assets/annotated-export-synthetic-source.jpg)
- [带中文姓名与底签的 PNG 成品（缩略对照）](assets/annotated-export-synthetic-output.png)

`validation/test_annotated_export.py --write-evidence` 实际构造 5000×3000 合成网格 JPEG，含“王小明”和“2026 年秋 · 北京”底签；输出为 PNG，宽度至少 5000px、高度大于 3000px。测试同时比较源文件 SHA256，前后不变。报告所附为约 800px 的脱敏对照，不含真实照片、姓名、路径或数据库。

## 实际检查

```text
python validation/test_annotated_export.py --write-evidence  PASS
python validation/test_visual_polish_v2.py                  PASS
python validation/test_m1_frontend_browser.py               PASS（17 项）
python -m py_compile app.py photo_export.py                 PASS
node --check web/photo-export.js; node --check web/viewer.js PASS
git diff --check                                             PASS
```

渲染环境：项目 `.venv` Python 3.12.1，Playwright Chromium 143.0.7499.4；Playwright 为按需导入的既有可选组件，未修改 `requirements.txt`，未下载浏览器。

## 未完成与边界

- 未对真实正式图库/当前工作台执行导出，也未重启其服务；因此它尚未加载这次后端路由，刷新静态页面也不足以启用后端导出。下次正常启动后才会加载新版。
- 本次未验证自选“其他…”本机字体；该选择的实际字体是否可导出不在已通过范围内。首版不支持 RAW/HDR/HEIC/TIFF、动画图、批量导出、预取或自动播放改造。
- 合成样图验证了默认标签与完整底签；图片底牌、竖图 EXIF 样本和正式实例端到端下载尚未在本轮完成，故状态为 PARTIAL，不将其写成通过。
