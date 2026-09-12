# 拾光：首页 Phase 1 前端模块与接入包

交付日期：2026-09-11。目标项目：`dreamlizer/OurTime-PictureViewer`。

## 先明确交付范围

本包包含**已实现的首页交互组件、适配现有扁平 state 的状态桥、新文件补丁、接入规范与测试**，不是效果图，也不是整份 `app.js` 的替换版本。

制作时可以读取公开仓库的说明和部分入口代码，但完整 Git checkout / 源码下载失败，未取得可核实的完整提交。因此：

- **没有伪造“基于某个精确 commit 的整项目补丁”。** `01-add-files.patch` 只新增三个文件，不修改现役文件。
- **Luna 必须在本地完成真实函数接线、原控件收敛和验证。** `index.html` 与 `app.js` 的接入改动需基于本地版本形成，不可直接覆盖。
- 本包的 40 项浏览器检查针对“新模块 + 新状态桥 + 合成测试宿主”，**不等于现役应用、真实数据库或大图查看器回归已经通过**。

## 用户可见结果

首页常驻工具只有：`搜索照片… ｜ 筛选 ｜ 选择 ｜ 排序`。

筛选面板默认关闭。点“筛选”才打开右侧抽屉；本阶段只有**现有人物**和**实际文件夹路径**。选条件时不立即刷新；“应用筛选”一次性应用，关闭面板。取消、Esc 或点遮罩会丢弃尚未应用的草稿。操作正在提交时关闭按钮暂时禁用，避免把已经提交的操作误解为取消。

没有条件时，条件标签整行不占空间。已应用的关键词、人物、文件夹显示为可移除的标签。“清除全部”清除查询条件，不改变排序。“重置条件”只清空抽屉中的人物与文件夹草稿，不清除搜索框关键词。

点击“选择”才进入批量操作模式，复用原项目的当前屏幕选择、清空、补录和排除流程。排除仍然不是删除原文件。新模块不提供删除原照片功能。

## 安装方式

把 ZIP 放到本地项目根目录。解压后应得到：

```text
<项目根目录>/
  web/
  app.py
  OurTime_Homepage_Phase1/
    00_README.md
    LUNA_MAX_PROMPT.md
    01-add-files.patch
    files/web/home-query-ui.js
    files/web/home-query-ui.css
    files/web/home-query-bridge.js
    integration/
    tools/
    tests/
    evidence/
```

然后把 `LUNA_MAX_PROMPT.md` 的内容交给 Luna Max。**不要自行把整个 `files` 文件夹覆盖到项目，也不要拿测试页面替代首页。**

## 包内材料

- `files/web/home-query-ui.js`：首页工具栏、抽屉、草稿状态、条件标签、选择态、异步保护与无障碍交互。
- `files/web/home-query-ui.css`：仅作用于新组件的淡色控件与抽屉样式。默认 42 px 控件；窄屏自然换行。
- `files/web/home-query-bridge.js`：将组件连接到现有 `state.q/person/directory/sort/selected`，复用本地提供的照片加载与批量动作。
- `01-add-files.patch`：只把上述三个新文件加到 `web/`，不修改任何既有文件，也不自动接入。
- `integration/IMPLEMENTATION.md`：本地接入顺序、允许范围、旧按钮的去向。
- `integration/ADAPTER-CONTRACT.md`：实际函数与 DOM 依赖如何接线；必须落实的读写语义。
- `ACCEPTANCE-CHECKLIST.md`：模块已测项与本地待验证项分开列示。
- `BASELINE.md`：来源、未取得完整提交的限制、不可覆盖的范围。
- `tools/preflight.py`：只读检查包校验值、本地文件、加载顺序、工作区状态、新文件补丁能否应用。不安装、不写库、不修改代码。
- `tests/run_browser_checks.py`：在浏览器内存中加载合成宿主，不启动服务、不联网、不访问照片库。
- `evidence/browser-checks.json`：本次 40 项检查结果。

## 明确不做

不开发日期范围、结构化地点筛选、自然语言多条件解析；不把未实现功能做成灰色占位按钮；不建立“家庭/旅行”等标签。

不更换前端框架、不引入构建、不新增外部字体。不改 Python、数据库、扫描、人脸识别、排除规则、查看器或瀑布流实现。不重组路人/合影/整理等导航。

首页标题可以改为“全部照片”，但**保留内部 `timeline` 标识**。年份入口只连接已有 `years` 页面，不新增第二套时间线。
