# 拾光：首页 Phase 1.1 收尾 + 合影计数修复（基于当前 GitHub master）

执行前先检查当前 HEAD。ChatGPT 复核时 GitHub `master` 为：
`6a835bbbdc4b3d7bde7a1e01d88d1573250992c2`

如果本地 HEAD 已经更新，不要 reset；以本地较新代码为准，但先比较本文件所指出的具体位置是否仍存在。

本轮不要进入 Phase 2。不要新增日期范围、地点结构化筛选、智能搜索解析。先把已经接入的首页和当前确定的计数 bug 收尾。

## 一、已经确认的代码级问题

### 1. 首页“全部照片”重复不是截图错觉

当前 `web/index.html` 中：
- masthead 有 `#page-title`
- library toolbar 里又有 `.section-heading > #collection-title + #result-count`
- `#home-query-host` 与这个旧 `.section-heading` 同时放在同一个 `.toolbar`

当前 `web/app.js` 的 `setView()` 又同时把：
- `#page-title`
- `#collection-title`
写成当前 title。

而 `titles.timeline` / `titles.all` 现在都使用“全部照片”。

因此首页会形成重复标题；同时旧 `.section-heading` 占据工具栏左侧，导致新搜索组件被挤到右半边。

### 2. “最新优先”掉到下一行是布局结构导致，不是数据问题

`#home-query-host` 目前是旧 `.toolbar` 中第二个 flex item；
左边旧 `.section-heading` 仍在，占掉大量宽度。

`home-query-ui.css` 中搜索框使用 `flex: 1 1 270px`，且排序 select 被设计成透明边框；
在 host 被挤窄后，排序就换行，视觉上像一行裸的“最新优先”。

### 3. “按年份查看”孤立出现是 `home-query-init.js` 明确保留出来的

当前 `syncLegacyControls()` 对 `#timeline-tools` 有特殊处理：
首页 active 时保留整个 `timeline-tools`，然后只隐藏 `[data-time-sort]` 两个时间排序按钮。

因此最后只剩一个孤立的 `#timeline-by-year` —— “按年份查看”。

本轮首页直接隐藏整个 `timeline-tools`。
保留现有 years 页面代码，不删除后台能力；本轮不要新增“时间线”或新的左侧入口。
Phase 2 做日期筛选后，再决定是否需要单独“年份”入口。

### 4. “合影”刷新后永远先显示 0 的根因已经确定

`web/index.html` 初始：
`<b id="groups-count">0</b>`

`web/app.js` 的 `refreshStatus()` 会更新：
- `#nav-count`
- `#excluded-count`
- `#s-people`
- `#people-count`
- `#s-uncertain`
- `#s-duplicates`

但没有更新 `#groups-count`。

`#groups-count` 目前只在 `loadGroups()` 中设置：
先请求 `/api/groups`，然后 `count.textContent = fmt(data.photos || 0)`。

所以浏览器刷新后必然先是 0；只有点击“合影”，触发 `loadGroups()` 后才变成正确数字。
这不是缓存偶发问题，是确定性的初始化缺口。

---

## 二、本轮首页最终结构

首页默认态只保留：

1. masthead：
   - 一个主标题：`全部照片`
   - 右侧现有统计
2. query toolbar：
   - 左组：搜索框 + 筛选
   - 右组：选择 + 排序
3. 有筛选条件时才出现 chips
4. 下面直接照片 grid

首页不要再显示：
- 第二个 `全部照片 + N 张`
- `按年份查看`
- 旧时间正序/倒序
- 旧人物下拉
- 旧目录输入条
- 旧刷新按钮

顶部 breadcrumb 这一轮先保留，不要同时大改 topbar。
这样页面只剩 breadcrumb 中的弱提示 + 一个真正 H1，不再有正文双标题。

---

## 三、具体修改要求

### A. `web/home-query-init.js`

1. 首页 active 时，把旧 collection section heading 一并隐藏：
   精确针对 `#library-view` 当前 `.view-chrome` 中、与 `#home-query-host` 同一 toolbar 的旧 `.section-heading`。
   不要全局隐藏所有 `.section-heading`，否则会破坏其他页面。

2. 首页 active 时隐藏整个 `#timeline-tools`。
   删除当前“首页保留 timeline-tools、只隐藏两个时间排序按钮”的特殊逻辑。
   不要留下孤立“按年份查看”。

3. 非首页 view 要恢复这些 legacy DOM 原来的 hidden 状态，不能影响其他页面。

4. `#home-query-host` 在首页必须成为这一行的唯一主内容，并能占满可用宽度。

### B. `web/home-query-ui.css`

桌面端（优先）实现：

`[ 搜索照片................ ] [筛选]                 [选择] [最新优先 ▾]`

要求：

- `#home-query-host`：`flex: 1 1 100%`，`min-width:0`。
- query form 一行对齐。
- 搜索框左对齐主内容区，不再漂到右半边。
- 搜索框不要无限拉满；桌面建议 `max-width` 约 560–620px，并允许在窄屏缩小。
- “筛选”紧跟搜索框。
- “选择”使用 `margin-left:auto` 或等价结构推到右侧。
- 排序紧跟“选择”。
- 排序 select 必须显示和其他控件一致的边框、白色背景、圆角及下拉箭头；不要透明边框，不要像裸文字。
- 四个控件高度一致。
- 没有 chips 时 toolbar 到 photo grid 的垂直间距约 20–28px。
- 不允许绝对定位硬凑坐标。

窄屏：
- 可以换两行；
- 搜索优先占整行；
- 其他三个控件不重叠；
- 不产生横向滚动。

### C. `web/app.js` / `app.py`：修复合影计数

采用“状态接口负责首页/导航计数”的方式修复，不要靠用户点击合影页触发。

在 `app.py` 的 `/api/status` stats 中增加一个明确字段，例如：
`group_photos`

其语义必须与 `/api/groups` 返回的 `photos` 完全一致：
- active asset
- 一个 asset 至少有 2 个 face
- 统计满足条件的照片数，不是人数、bucket 数

可以使用等价 SQL：
```sql
SELECT count(*) FROM (
  SELECT f.asset_id
  FROM faces f
  JOIN assets a ON a.id=f.asset_id
  WHERE <与 /api/groups 相同的 ACTIVE_ASSET 条件>
  GROUP BY f.asset_id
  HAVING count(*) >= 2
)
```

不要改变 `/api/groups` 现有 bucket 逻辑。

然后在 `web/app.js` 的 `refreshStatus()` 中加入：
```js
setText('#groups-count', fmt(s.group_photos));
```

不要通过 `setInterval` 额外每 8 秒再请求一次 `/api/groups`；
已有 `/api/status` 轮询就是导航计数的统一入口。

注意性能：
- 当前 schema 已有 `faces_asset` 索引。
- 在真实资料库上记录 `/api/status` 修改前后的大致响应耗时。
- 如果新增 group count 导致明显不可接受的退化，再提出优化，不要未经测量先引入复杂缓存。

### D. 左侧“全部照片”图标

当前 `全部照片` 沿用了以前“按时间”的时钟图标。
本轮把它换成更符合“全部照片/图库”的照片或图片类 outline icon。

不要因此新增“时间线”导航。

---

## 四、这轮不要顺手做 Phase 2

禁止：
- date_from / date_to
- 首页地点结构化筛选
- 新的年份/时间线页面
- 搜索语义解析
- 数据库 schema 变更
- viewer / waterfall 核心逻辑重构
- 人脸识别算法改动
- 扫描逻辑改动
- 删除原文件功能

`waterfall.js` 除非测试接线确有必要，不要修改。

---

## 五、顺带检查一个潜在性能问题，但本轮先报告，不强制重构

当前 `web/home-query-init.js` 的 `getPeople()` 会：
- 每次首次打开筛选时
- 以 `limit:48`
- 循环请求 `/api/people`
- 直到把全部人物读完。

当前人物约 5,199 个，这意味着约 109 个 API 请求后才完整加载。

请在本轮完成后报告：
- 第一次打开“筛选”时人物列表实际耗时；
- 网络请求数量；
- 是否有明显卡顿。

不要在本轮未经确认大改协议。
如果确实慢，下一轮优先把人物筛选改成服务端搜索/typeahead，而不是一次加载 5,000+ 人。

---

## 六、必须补回归测试

至少增加/扩展自动验证覆盖：

### 合影计数
构造隔离测试数据：
- 1 张照片只有 1 face
- 1 张有 2 faces
- 1 张有 3 faces

断言：
1. `/api/groups.photos == 2`
2. `/api/status.stats.group_photos == 2`
3. fresh page load 后，**不点击合影**，`#groups-count` 已显示 `2`
4. 点击合影后数值仍为 `2`，不能靠点击才修正

### 首页布局/DOM
在桌面宽度（例如 1920）至少断言：
- 首页只有一个正文主标题“全部照片”
- 旧 `#collection-title` 所在 section heading 首页不可见
- `#timeline-tools` 首页不可见
- 搜索、筛选、选择、排序同一行
- 排序控件有可见边框且不是裸文本
- query host 左边缘与 photo grid 基本对齐
- 没有筛选条件时 chips 不占高度

同时验证：
- 人物筛选仍工作
- 文件夹筛选仍工作
- 文件夹筛选不改变当前排序
- 选择模式仍工作
- 离开首页到人物/地点/合影等页面，旧页面布局没有被 homepage CSS 误伤

---

## 七、完成后先不要 push

给我：

1. 修改文件清单
2. 每个问题的根因与修复点
3. 默认首页截图（1920 左右宽度）
4. 筛选展开截图
5. 一个筛选 chip 生效截图
6. 选择模式截图
7. 刷新浏览器后、未点击“合影”时左侧合影计数截图
8. 测试结果
9. `/api/status` 修改前后大致耗时
10. 第一次打开筛选时加载人物的请求数/耗时

我确认后再决定是否 push，以及是否进入 Phase 2。
