# 对外发布前整理完成报告

代码基线：`554b039`。本轮在最新 master 上整理，不回退。作者当前 8765 工作台未重启，正式库未改、人脸未重算。

## 结果摘要

| 项 | 状态 |
| --- | --- |
| 代码和文案 | READY |
| 自有代码许可证 | 按作者指示本轮不处理 |
| 源码正式发布 | READY（源码候选，不是 GitHub Release） |
| 含模型独立包 | NOT_BUILT |
| 陌生机器免安装验证 | NOT_RUN |
| 作者当前后台 | NOT_RESTARTED |
| Git | 以 origin/master HEAD 为准，推送后核对应端 SHA |

## 做了什么

- 启动默认改为项目 .venv / 本机配置；去掉另一套 PyCharm 路径。作者下次启动用未提交的 config.local.json 继续原解释器和 G 盘模型、I 盘偏好。
- 模型、地理、底牌默认改到程序目录 resources/ 或 web/assets；没有就明确缺组件，不假装齐套。
- 新用户文件夹页看本机磁盘；不再写死只看 I 盘。
- 半成品文案已清：本地版 / 03、路人说明、排除目录、扫描新照片、街道实景、已排除缓存口径。
- 模板工具拒绝报告路径覆盖源/输出；导出渲染加了本机资源白名单。
- 物体识别新计算默认关闭，旧标签不删。
- 缺人脸模型时，带识别的扫描会被拒绝，需用户明确同意才只入库。
- 旧补丁 ZIP 和 Homepage 目录移到 docs/archived/。README 改为使用入口。
- 未来绿色包形态：启动入口 + 程序文件夹 + 可选 data/。本轮不打包。

## 测试

- `python validation/smoke.py`：PASS（含现役 8765 只读接口，未停正式扫描）
- `python -m unittest validation.test_release_readiness`：隔离空库、全盘符、禁物体识别写入口、缺模型扫描拒绝、导出 URL 白名单、模板路径冲突 PASS
- `validation.test_person_templates`、`validation.test_annotated_export_fix`、`validation/test_folder_paths.py` PASS

未验证：陌生电脑免安装、真实家庭照片识别、安装到 Program Files。

## R01–R12

DONE：R01 启动/资源默认，R02 文件夹盘符，R03 底牌与 DATA 分家（未复制作者底牌），R04 导出组件说明，R05 模板路径，R06 导出资源边界，R07 文案，R08 根目录减负，R11 smoke 翻图合同。
按作者指示跳过许可证专项：R09、R10。R12 仅整理当前跟踪文件，不改写 Git 历史。

作者下次若仍用「启动拾光.vbs」，会读 config.local.json，继续现有库和模型。不要删除该文件。
