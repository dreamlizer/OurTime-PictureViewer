# 本地接线合同

这份文件是 Luna 的实施依据。以下已知字段来自读到的 app 入口；所有未给出真实函数名的回调都必须在本地找到对应实现，不能假设某个 ID/函数存在。

## 两层组件

`OurTimeHomeUI.mount({host, adapter})` 负责 UI。返回 `sync()`、`openFilters()`、`closeFilters()`、`destroy()`。

`OurTimeHomeBridge.create(bindings)` 负责对接旧扁平 `state`，返回上面的 adapter。桥已实现 q/person/directory 的一次写入、sort 独立更新、旧输入值同步、状态过期检查和自身失败回滚；它不实现照片分页与批量业务。

这两个是完整工厂。需要 Luna 写的是一个小 init：提供真实 host 和以下 bindings。**不需要重新编写筛选抽屉或另起照片加载器。**

## 必需 bindings

| 名称 | 接入要求 |
|---|---|
| `source` | 现有 `state` 对象本身。不是 JSON 副本，不是新增的第二个全局 state。 |
| `isActive(source)` | 明确判断何时使用新首页工具栏。当前已读默认值是 `timeline`；保留此内部标识。不能盲目把 `all` 或所有页面都当成首页。 |
| `scopeKey(source)` | 当前独立浏览范围的稳定标识。至少包含 view；若年/地点范围另存字段，也需要纳入。只读，不改变导航。 |
| `reloadPhotos({reason,signal})` | 调用现有照片流**重建/从头加载**路径，返回完成的 Promise。不是加载下一页，不重写瀑布流。必须核对现有 `loadPhotos` 实参、分页快照/offset/maxId/generation 重置方式。 |
| `getPeople({signal})` | 返回 Promise 或数组 `[{id:'真实人物ID',label:'现有显示名'}]`。复用已有人物筛选数据源，核对是否分页完整。不能把页面只加载的前 48 组误当作全库。 |
| `setSelecting(on,context)` | 复用原进入/退出选择函数，更新原 selecting/selected Set 和缩略图 checkbox。取消按既有语义清理选择。 |
| `selectVisible(context)` | 复用“选择当前屏幕”，不能偷偷变成“整个结果集全选”。 |
| `clearSelection(context)` | 清空原 Set 并触发原选择 UI 刷新。 |
| `editSelected(context)` | 进入原批量补录弹窗；不直接提交补录。 |
| `excludeSelected(context)` | 进入原排除确认流程；不能绕过确认直接发写请求。 |

## 可选但按本地能力应接入的 bindings

| 名称 | 接入要求 |
|---|---|
| `legacyInputs` | `{q,person,directory,sort}` 对应旧 DOM 节点引用。用于仍有代码引用原输入值的项目；桥仅写 `.value`，不 dispatch change/input。节点需从本地真实 ID 获取。 |
| `personLabel(personId)` | 返回原人物显示名/别名；特别用于人物页跳回首页时的标签。不能给未知 ID 编造“张三”。 |
| `chooseFolder({current,signal})` | 返回所选路径字符串或取消时的 null。只写草稿；不能在选择器完成后自动应用、设置扫描路径或发起扫描。必要时给既有选择器加一个“返回路径”回调分支，不改变其其他用途。未安全接入此回调时按钮自动不显示，路径输入仍可用；应把此项列为未完成，不能假装完整复用了原选择器。 |
| `onStateChange()` | 使用 `homeUI?.sync()` 更新工具栏；不能在此再次 `loadPhotos()`，否则形成重复刷新。 |
| `refreshAvailable(source)` | 复用真实图库更新/扫描待刷新标志。无真实信号就返回 false；不靠随机数或虚构差额。 |
| `refreshPhotos(context)` | 原手动刷新路径，不是扫描。 |
| `selectionKind(source)` | 默认 `'exclude'`。只有真正复用到已排除照片范围时才返回 `'restore'`。本轮不必为了该能力重排已排除页面。 |
| `restoreSelected(context)` | 原恢复流程；正式库自动测试不提交。 |

## 接线形状示意（不是可直接粘贴的现役代码）

下面 `local...` 名称表示 Luna 必须从本地实现中取出的真实函数，不是本包声称项目中已存在的名称。禁止把这些示意名称原样接入后宣布完成。

```js
let homeUI = null;
const homeAdapter = OurTimeHomeBridge.create({
  source: state,
  isActive: s => s.view === 'timeline', // 按本地实际首页标识核对
  scopeKey: localReadBrowseScopeKey,
  reloadPhotos: localExistingResetAndReload,
  getPeople: localReadExistingPersonFilterOptions,
  setSelecting: localExistingSetSelectionMode,
  selectVisible: localExistingSelectVisible,
  clearSelection: localExistingClearSelection,
  editSelected: localExistingOpenBatchEditor,
  excludeSelected: localExistingOpenExclusionConfirm,
  chooseFolder: localPickFolderForDraft,
  legacyInputs: {q: localSearchInput, person: localPersonSelect,
                 directory: localDirectoryInput, sort: localSortSelect},
  personLabel: localExistingPersonLabel,
  onStateChange: () => homeUI?.sync(),
  refreshAvailable: localReadActualRefreshFlag,
  refreshPhotos: localExistingManualRefresh
});
homeUI = OurTimeHomeUI.mount({ host: localHomeToolbarHost, adapter: homeAdapter });
```

若本地现有函数写成按钮内匿名回调，可以只把该回调抽出为共享函数，再由旧入口和桥引用。不要通过 `.click()` 隐藏按钮反向驱动整个系统；特别不能用这种办法伪装目录草稿或批量确认。

## 同步与竞态：不能漏的接线

- 原 `setView` 完成导航后同步。切出首页需要立即隐藏新组件，关闭未应用的抽屉，取消待提交关键词。
- 从人物详情、年份、地点、文件夹等入口改变 q/person/directory/sort 后同步。不能只在新组件自己的点击后刷新。
- 原照片 checkbox、当前屏幕选择、批量取消更新 Set 后同步。新模块本身不往照片卡片添加 checkbox。
- 人物选项异步就绪后，新组件应读取同一份真实数据。不会自动请求新 API。
- 原加载器必须保留“旧请求不得覆盖新范围”的保护。桥的状态过期检查不能代替瀑布流自身的 generation/取消机制。
- `applyQuery`/`applySort` 失败时桥只回滚仍属于自身的字段；不能撤销已被其他入口更新的查询。加载器应抛出可处理的错误；不得吞掉请求错误却返回成功。
- 原全局键盘监听需尊重当前输入框和弹窗；核对 Esc 在抽屉里只关闭抽屉，不误关另一个大图。不要为此重写 viewer 的按键系统。

## 为何不用复制整文件

本地 `app.js` 与 `index.html` 可能已经含有查看器、人名浮层、翻图等最新接线。本包故意不提供它们的替代全文。新文件可以标准补丁添加，现役文件只做对应位置的小修改；这样才能保留本地状态和既有修复。
