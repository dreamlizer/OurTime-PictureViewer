# 来源、基线与验证边界

## 已取得的资料

参考仓库： https://github.com/dreamlizer/OurTime-PictureViewer

2026-09-11 读取了仓库 README、AGENTS，以及 `web/app.js` 的入口片段。README/AGENTS 说明当前为无构建 HTML/CSS/JavaScript 前端；原照片只读；默认首页为时间线；日常 UI 先跑 smoke，隔离检查不得使用正式 data。

已读入口片段包含：

```js
const state={view:'timeline',q:'',person:'', /* 其他既有字段 */ directory:'',sort:'date_desc', /* ... */};
```

上述是摘录，不是完整原文件。桥接器采用这些已读的扁平字段，但照片加载器和按钮处理函数的完整实现必须由 Luna 从本地源码确认。

## 没有取得的资料

完整仓库 clone / archive 下载失败；后续源码展开也未成功。因此不能声明已取得最新完整源码，不能声明提交 hash，也没有生成带现役文件上下文的修改补丁。

`01-add-files.patch` 的基线是“这三个新文件尚不存在”。它不是给 `app.js/index.html` 的完整改版补丁，应用后仍需接线才会改变页面。

## 本地基线以什么为准

以 Luna 执行当时的工作区为准，包括未提交改动，不以远程仓库或本包设想覆盖本地。读取 README、AGENTS、近期任务卡，记录 Git HEAD、dirty 状态、涉及文件的 SHA256。禁止为了匹配本包去 reset、checkout、clean 或 pull 覆盖本地。

此前查看器改动与本包是不同的工作。特别保留 `viewer.js`、viewer 相关 CSS/DOM、`waterfall.js`、浏览范围、翻页并发、拖动关闭、底栏、人物标签和说明高度。新 CSS 不设全局按钮/输入框/标题规则，不修改 42 px 瀑布流说明或 72/88 px 查看器布局合同。

## 验证分层

本包：Node 语法检查、新模块 + 状态桥的合成宿主浏览器检查、新文件补丁应用检查。

本地必做：真实接线、原有加载器的 reset/分页语义、人物数据完整性、目录选择器回调、搜索回调、人物页跳回首页、年份入口、真实大图打开/翻页/返回位置、原有批量操作确认链路。

不能把前一层 PASS 写成后一层已完成。正式照片库不做自动化写入验证；排除、恢复、补录提交只在隔离测试数据中验证。
