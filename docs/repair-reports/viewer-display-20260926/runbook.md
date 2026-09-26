# viewer-display 运行手册

## 日常

```bash
python validation/smoke.py
node validation/test_viewer_display_state.js
python validation/test_viewer_display_state.py
```

## 真实入口（只读，打到现役 8765）

```bash
# 需要 Playwright（本机 ImageBrowser venv 已带）
C:\Users\A\PycharmProjects\ImageBrowser\.venv\Scripts\python.exe validation\test_viewer_display_browser.py
```

期望输出末行 `ENTRY_OK`，并看到 open 后 `content=committed`、close 后 `lifecycle=closed`。

## 隔离写验证（收藏/导出）

- 使用独立 `PHOTO_LIBRARY_DATA` 与空闲端口启动 `python app.py --port 8766`
- 不重扫、不重识别、不覆盖用户未提交工作

## 回滚

```bash
git log --oneline -5
git checkout <checkpoint> -- web/ validation/
```

检查点：
- `c74dd43` 时间缓存 + 任务卡
- `ab328cc` P0 + 控制器外壳
- 其后：租约/导出/owner 收口

## 日常应用

前端在 `web/`，8765 直接读盘。改完刷新浏览器即可；一般不必重启后端。
