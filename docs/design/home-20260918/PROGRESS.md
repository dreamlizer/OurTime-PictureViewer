# 首页改造进度 · 2026-09-18

## 目标

按选定的 02 布局落地可运行首页：往年今日、重访一个地方、人物这些年。保留浏览连续性和最近操作/撤销。不覆盖其他未提交改动，不重启正式 8765，不改正式库。

## 工作树基线

- HEAD: `7c314d0172ab730cef8dd341f24fa03877262b7f`
- 正式服务 `/api/health`: owner=true, runtime_version=`da4d37d...`, data_dir=正式 data/, pid=37848, job=completed, assets≈183166
- 已有未提交：浏览连续性、最近操作、大图 HUD/加载中等。本任务只白名单接入。

## 已锁定的产品选择

- 视觉以用户确认的 02 布局为准（主图左、说明右，下方地点+人物），不是 01 拼贴。
- 文案：往年今日 / 重访一个地方 / 人物这些年。
- 分类切换做成标题下紧凑选项。
- 图中诗句、假人名、示例日期不进真实页面。
- 地点第一版用完整有效地点分段，不做新的 GPS 聚类。
- 时钟注入：环境变量 `PHOTO_HOME_AS_OF=YYYY-MM-DD`。
- 快照：打开组时物化有序 asset_id；数据修订变化或成员不再合格则 `409 recommendation_expired`；进程重启后旧快照仍可读，但修订号对不上即失效。

## 实现合同

- `GET /api/home/recommendations?category=all|on_this_day|place_revisit|people_years&cursor=`
- `POST /api/home/groups/open` body: `{group_id}`
- `GET /api/photos?recommendation_snapshot=` 走同一瀑布/大图序列
- 算法版本 `home-discovery-v1`

## 阶段

1. 基线与合同：完成
2. 规则与查询：完成（隔离单测 10/10）
3. 静态布局与状态：完成（02 布局）
4. 完整浏览和撤销：隔离浏览器已点通组列表、大图、返回、分类、时间流；撤销单测 U01 通过
5. 回归与交付：smoke / 最近操作 / 浏览连续性已跑过

## 验证摘要

- 源码：已实现。新模块 `home_recommendations.py`、`web/home-discovery.js/.css`
- 隔离：`python -m unittest validation.test_home_recommendations` PASS；`python validation/test_home_discovery_browser.py` PASS；`python validation/smoke.py` PASS；最近操作与浏览连续性回归 PASS
- 正式 8765：未重启、未加载。当时 health runtime_version 仍是 da4d37d，data_dir 为正式库
- 真实 18 万张库的冷/暖查询耗时：未测

## 阻塞

无

## 阻塞

无
