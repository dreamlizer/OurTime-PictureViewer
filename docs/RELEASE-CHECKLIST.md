# 发布检查

## 源码候选

- 启动默认不再指向作者其他项目或固定盘符。
- 新用户文件夹页看到本机磁盘，不默认只看 I 盘。
- 作者本机用未提交的 config.local.json 保留原环境。
- 当前仅有源码候选；没有 GitHub Release、安装器或已发布版本。

## 本机绿色包草稿（非发布物）

本机存在被 `.gitignore` 忽略的 `dist/拾光相册-绿色版/` 和同名 ZIP。它们是 2026-09-15 生成的本地草稿，`PACKAGE-MANIFEST.json` 标记 `source_dirty=true`，来源为 `4d27c31`；当前工作树随后仍有未提交修改，因此不能当作当前源码的完整构建物，也未在本轮收口中标记为绿色包验收通过。

目标形态：

拾光相册/
  启动拾光.vbs
  程序文件（app.py、web、runtime、resources、tools）
  data/   （首次运行新建；迁移时整夹带走）

禁止打进包：作者库、WAL/SHM、faces/thumbs、个人模板、日志、.git、config.local.json。

构建入口是 `打包拾光.cmd`；构建后用 `tools/packaging/smoke_packed.py` 在临时目录和随机端口执行真实验收。`tools/release_inventory.py` 仍可做源码候选的只读清单检查。
