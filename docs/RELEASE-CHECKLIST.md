# 发布检查

## 源码候选

- 启动默认不再指向作者其他项目或固定盘符。
- 新用户文件夹页看到本机磁盘，不默认只看 I 盘。
- 作者本机用未提交的 config.local.json 保留原环境。
- 本轮不制作 ZIP / 安装器 / GitHub Release。

## 未来绿色包

建议形态：

拾光相册/
  启动拾光.vbs
  程序文件（app.py、web、runtime、resources、tools）
  data/   （首次运行新建；迁移时整夹带走）

禁止打进包：作者库、WAL/SHM、faces/thumbs、个人模板、日志、.git、config.local.json。

可用 tools/release_inventory.py 做只读清单检查。
