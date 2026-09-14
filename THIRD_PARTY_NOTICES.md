# 随程序使用的第三方组件

本清单只说明程序里用到了什么，不代替各上游自己的说明。

- Python 依赖见 requirements.txt。
- InsightFace 用于人脸检测/识别；代码和模型文件不是同一件事，模型按你本机已有文件使用。
- ExifTool 在 tools/exiftool。
- 页面地图使用本地 Leaflet；底图仍走网络。
- 主题字体在 web/vendor/fonts，带各自 OFL 文本。
- 导出可选 Playwright/Chromium。

地理数据和模型请按各自来源放进 resources/，不要把作者家庭数据打进分发包。
