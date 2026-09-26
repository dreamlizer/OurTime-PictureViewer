# 发布检查

## 源码候选

- 启动默认不再指向作者其他项目或固定盘符。
- 新用户文件夹页看到本机磁盘，不默认只看 I 盘。
- 作者本机用未提交的 config.local.json 保留原环境。
- 当前仅有源码候选；没有 GitHub Release、安装器或已发布版本。

## 本机绿色包（非发布物）

本机存在被 `.gitignore` 忽略的 `dist/拾光相册-绿色版/` 和同名 ZIP。2026-09-25 已用当前源码重建，并通过 `tools/packaging/smoke_packed.py` 闭环验收（`GREEN_PACKAGE_ACCEPTANCE_OK`）。`PACKAGE-MANIFEST.json` 仍可能标记 `source_dirty=true`（打包脚本改动未提交前），不能当作对外发布物。

目标形态：

拾光相册/
  启动拾光.vbs
  程序文件（app.py、web、runtime、resources、tools）
  data/   （首次运行新建；迁移时整夹带走）

禁止打进包：作者库、WAL/SHM、私人 faces/thumbs、个人模板、日志、.git、config.local.json、公众人物 `references/` 参考照片、`SEED-REPORT.json`。`resources/public-faces/public-faces.sqlite3` 是程序资源（公众人物姓名与人脸特征），打进 `App/resources/public-faces/`，不进用户 `Data/`。

构建入口是 `打包拾光.cmd`；构建后用 `tools/packaging/smoke_packed.py` 在临时目录和随机端口执行真实验收。`tools/release_inventory.py` 仍可做源码候选的只读清单检查。
