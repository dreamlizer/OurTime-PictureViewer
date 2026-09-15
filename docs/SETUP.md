# 源码安装与故障

适用当前源码目录，不是已打包的绿色软件。

## 准备

1. Windows，Python 3.12。
2. 在本目录创建 .venv，并安装 requirements.txt。
3. 启动器优先使用本目录 .venv。若要用已有解释器，复制 config.example.json 为 config.local.json，填写 python_exe。
4. 人脸模型放到 resources/models/models/buffalo_l/，或在 config.local.json 指定 model_root。
5. 地名数据放到 resources/geo/，ExifTool 放在 tools/exiftool/。
6. 带人名标签导出是可选项，需要本机 Playwright/Chromium；没有也不影响普通看图。

不要把作者的 data/ 复制给别人。首次运行会在 data/ 新建空库。

## 生成绿色包

维护者双击项目根目录的 `打包拾光.cmd`。脚本优先使用 `PHOTO_PYTHON`、项目 `.venv` 或 `config.local.json` 中的 Python，并从本机配置读取人脸模型和地名资源。成品输出到 `dist/拾光相册-绿色版/` 和同名 ZIP；只创建空白 `Data/`，不会复制当前资料库。

打包完成后运行：

```powershell
python tools/packaging/smoke_packed.py "dist/拾光相册-绿色版.zip"
```

该检查会把 ZIP 解压到两个全新临时目录，用随机端口从成品 `拾光.exe` 启动，实际验证 InsightFace、离线地名、Chromium 导出链、扫描入库、重复启动和真实端口占用。地图底图仍需联网。

## 启动失败

- 未找到 Python：按上面创建 .venv，或在 config.local.json 指定 python_exe。
- 资料库无法写入：把程序放到可写目录，或指定 data_dir。
- 端口被占用：不要结束无关 python.exe；换 PHOTO_LIBRARY_PORT，或停掉占用 8765 的拾光实例。
- 人脸模型缺失：可先只添加照片，稍后再识别。

日志在 data/server.log 和 data/server-error.log。
