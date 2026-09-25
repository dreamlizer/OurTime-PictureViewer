# 拾光相册人名标签｜独立验收与补修清单

验收日期：2026-09-25  
审查版本：`master / 9ba5fe6952642c782d69beeef99fb696a46bb8c7`  
实施提交：`f7bf5bda5c27a280858781b3290787125ed6ec08`  
对照基线：`ad20f676fc069bafb095a3cfec30e002a82f0a2d`  
对照任务：`FL-20260925` / `OurTime_FaceLabels_Repair_TaskCard_2026-09-25.md`

## 一、结论

**部分通过，尚不能按原任务卡结项。** 共享模型、主预览样式、恢复外观默认、逐标签横排、横版底板检查和导出接线有实质进展；但统一规则尚未覆盖所有调用者，存在已复现的边界错误和可见的预览不一致，真实照片审美及部分端到端验收仍缺证据。

保留本轮有效成果，不建议全部撤销重来；下一轮应定向补修并补足验收。

## 二、本次验收实际覆盖什么

- 通过 GitHub 连接读取最新 master、参考版本差异、核心 JS/CSS、导出代码、验收脚本与完成报告。
- 从所读源码提取关键函数原文，在 Node.js 中对合成数据运行最小复现。
- 用相关 CSS 原文及与源码一致的关键父子关系，在 Chromium 144.0.7559.96 中验证选择器作用范围、预览方向与字号、资源回退后的计算样式。
- 读取原任务卡，将实际证据与相应用例对照。
- 没有修改远程仓库、正式服务或用户照片。没有连接用户本机正式页面，没有运行整个应用的全部回归，也没有独立重跑执行 Agent 报告中的完整浏览器/导出测试。

**重要：** 证据包里的 HTML 是定位问题的最小 DOM，不是拾光正式 UI 的克隆或正式服务截图。它不能证明四款主题在真实相册中的整体审美。字体仅用系统回退完成布局计算，没有下载、复制或打包项目字体，也没有访问私人图片。

仓库中的 `completion.md` 和 `test-results.json` 仍写着未推送/未部署；代码已能在 GitHub master 读取，因此这些说明至少有部分没有同步更新。用户已告知本地部署完成，本报告不据旧文档否定该事实，只将“正式部署已生效”列为未独立核实。

## 三、已经确认的有效改进

1. `face-label-model.js` 已实现统一的文字解析、别名处理、方向和偏好规范化函数。只有别名、历史占位名、单字母英文等基础场景的模型级检查通过。
2. `namedFaces()` 和 `faceLabelText()` 已转发到统一解析；主样式面板预览也使用解析后的显示文本。
3. `restoreFaceAppearanceDefaults()` 包含应用样式、保存偏好和必要重排，不直接调用照片坐标清除接口。该判断为源码链路核验，不等同于正式库端到端写入测试。
4. 默认楷体通过 `faceFontStack()` 接入内置 `LXGW WenKai GB Screen` 的字体栈。
5. 主样式面板和照片标签已共享一组视觉规则。最小 CSS 复现中，两者在基础倍率下均为 13px、竖排、相同 padding。
6. 暗朱横排装饰已写到单标签 `.is-horizontal` 分支，不再只依赖整层横排。
7. 主题检测已包含横竖两张底板，失败不永久覆盖用户所选主题；但回退的实际视觉仍有 A05 所述问题。
8. 导出代码已接入 `face-labels.css`，并加入方向资源变量和新阅读提示的排除规则。完整导出一致性仍需补测，不能仅凭这些接线判全部通过。

## 四、必须补修的问题

### A01｜纯英文姓名与别名同时显示，错误变成竖排

**级别：应优先修复的正常使用路径错误。**  
**位置：** `web/face-label-model.js`：`resolveFaceLabelState()`、`isLatinDisplayName()`、`faceLabelVerticalFor()`。  
**关联用例：** FL-017、FL-030、FL-031。

模型会把不同的英文姓名和英文别名组合为 `Alex / Alex Chen`，但拉丁文本判断只允许空格、句点、撇号和连字符，未允许应用自己插入的 `/`。结果是：

| 输入/模式 | 期望 | 本次实际结果 |
|---|---|---|
| `Alex` / auto | 横排 | 横排 |
| `A` / auto | 横排 | 横排 |
| `Anne-Marie` / auto | 横排 | 横排 |
| `Émile` / auto | 横排 | 横排 |
| `Alex / Alex Chen` / auto | 横排 | **竖排** |
| `Alex / Alex Chen` / vertical | 保持英文例外、横排 | **竖排** |
| `陈宁` / auto | 竖排 | 竖排 |

**修复要求：** 方向判断应兼容应用的显示分隔符。可按结构化姓名/别名判断其文字系统，也可对显示字符串使用受控分隔符规则；不得粗暴把所有非汉字都当英文，不改 `姓名 / 别名` 的内容合同。

**复验：** 中英文、纯英文双字段、混合字段，覆盖 off/with/only × auto/horizontal/vertical；断言实际按钮方向、预览方向及导出方向，不只检查函数布尔值。

### A02｜本机字体弹窗没有真正接入共享预览，并丢失中文竖排

**级别：直接可见的预览不一致。**  
**位置：** `web/viewer.js`：`buildLocalFontDialog()`、`localFontPreviewText()`、`updateLocalFontPreview()`；`web/face-labels.css`；`web/viewer-overrides.css` 的 `#local-font-preview-label`。  
**关联用例：** FL-011、FL-017、FL-040、FL-044。

实际弹窗通过 `document.body.appendChild(dialog)` 放在 body 下，与 `#detail-dialog` 是独立区域。共享规则却要求 `#detail-dialog #local-font-preview-label`，所以不能命中它。

与此同时，旧固定样式仍然保留 20px 字号、固定黑底、固定 padding。更新函数仅切换 `.is-horizontal`，已经没有独立设置 writing-mode；在共享规则未命中的情况下，中文预览停在横排。

**最小 CSS 实测：**

| 对象 | 字号 | 方向 | padding |
|---|---:|---|---|
| 实图标签 | 13px | vertical-rl | 5px 3px |
| 主样式面板预览 | 13px | vertical-rl | 5px 3px |
| 本机字体弹窗预览 | **20px** | **horizontal-tb** | **5px** |

另一个遗漏：`localFontPreviewText()` 仍返回第一个 face 的 `.name`，不是统一解析后的显示文本。只有别名或只显示别名时，它仍可能显示错误示例。

**修复要求：** 使用不依赖 `#detail-dialog` 祖先的共享视觉类，或给独立字体弹窗显式传入相同主题变量和同一组件。不要只追加一个 writing-mode 补丁而保留第二套配色、字号和边框。预览内容同样经统一解析，试选字体是唯一允许的局部差异。

**复验：** 中文、英文、只有别名、with/only 模式；在相同基础倍率和试选字体条件下，对比三个位置的 computed style 与实际截图。无字体权限时应能正常取消。

### A03｜偏好值为 JSON null，仍能中断查看器初始化

**级别：低频、较大影响的健壮性错误。**  
**位置：** `web/viewer.js`：`readViewerPrefs()` 后的 viewer 初始化与原始偏好读取。  
**关联用例：** FL-024。

虽然新 `normalizeViewerPrefs()` 能处理 null，但 viewer 初始化仍直接读取：

```javascript
faceLabelPosition: FACE_LABEL_POSITIONS.has(storedViewerPrefs.faceLabelPosition)
  ? storedViewerPrefs.faceLabelPosition : 'auto'
```

当存储内容为合法 JSON `null` 时，本次最小复现得到：

```text
TypeError: Cannot read properties of null (reading 'faceLabelPosition')
```

这是构造数据证明的错误路径，不代表用户正式浏览器当前存在这种存储内容。

**修复要求：** 原始存储值只在迁移入口被读取；运行时统一使用经过校验的对象。全仓检查 `.slideDelay` 等残余直接读取，不只修一个属性。不得清空用户整个 localStorage。

**复验：** null、损坏 JSON、数组、字符串、正常旧设置、新旧冲突值；真正加载页面并检查无初始化异常，随后打开照片，而不是仅调用纯函数。

### A04｜统一人物状态没有覆盖 HUD、统计及旧点击判断

**级别：状态规则仍有双轨；异常数据兼容未完成。**  
**位置：** `web/viewer.js`：`faceHasUsableName()`、`photoPeopleSummary()` 及其调用者；`web/face-label-model.js` 的 `ignoredFlag()`。  
**关联用例：** FL-016、FL-019；P1-01 接入范围。

旧 `faceHasUsableName()` 与 `photoPeopleSummary()` 仍使用 `!face.ignored` / `Boolean(face.ignored)`，没有统一经过模型的标记规范化。

同一合成输入 `{name:'陈宁', ignored:'0'}`：模型判定为 named；HUD 输出 `{named:0,pending:0,passerby:1}`。这不是仅仅多保留一个旧函数，而是两个现役入口存在矛盾。

另外，两张脸缺失 person_id 时，统计通过 `Number(undefined)` 折叠成同一个 NaN key，本次构造两名人物实际只计 1。正式接口通常提供有效 ID，仍应按任务卡明确的异常规则处理，不能静默合并。

**修复要求：** 统计、标签、预览、点击路径都消费同一状态解析。保留“人数按 personId、标签按 faceId”的粒度，不为对齐数字改变真实人物归属。缺 ID 采取明确的安全回退或诊断，不隐式折叠成同一人。

**复验：** 正常数字标志、字符串零/一、空值、只有别名、占位姓名、路人优先级；同一 fixture 同时检查 HUD、标签类别与点击路径。

### A05｜底板失败虽标为 classic，却没有真正应用 classic 外观

**级别：资源故障时的可见状态不一致。**  
**位置：** `web/viewer.js`：`applyFaceStyle()`、`ensureFaceLabelThemeAvailable()`；`web/face-labels.css` 的 effective-classic 规则。  
**关联用例：** FL-038、FL-039、FL-040。

`applyFaceStyle()` 先写入请求主题的字体、颜色、字号等变量；资源失败回调只改 `data-face-theme-effective=classic`。回退 CSS 继续引用请求主题的变量，并保留部分图片主题几何规则。

茶棕底板失败的最小 CSS 复现：

| 项目 | classic 预设 | effective=classic 的茶棕回退 |
|---|---|---|
| 字号 | 13px | **16px** |
| 背景 | rgba(20,24,18,.5) | **rgba(126,98,75,.66)** |
| 圆角 | 10px | **0px** |
| padding | 5px 3px | **9px 6px** |

这不意味着所有故障回退都必须改成 classic。可以保留经明确设计的无底图茶棕回退，但不能把该混合态标为“默认外观”并以该属性值判通过。按原任务卡，优先实现一致的 classic 临时回退，同时保留 requestedTheme。

**复验：** 分别阻断 1/4/7/8 图、损坏图片、恢复加载；断言实际 computed style、文字完整性、用户请求主题与有效主题，不能只检查 dataset。临时回退不得永久写掉用户偏好。

### A06｜架构收口尚未完成；新模块不是唯一权威来源

**级别：任务核心目标未完成。**  
**位置：** `web/face-label-model.js`、`web/viewer.js`、`web/face-labels.css`。  
**关联任务：** P2-01、P2-02、P2-05；Definition of Done。

当前仍能看到：

- model 的 `PRESETS` 与 viewer 的 `FACE_STYLE_PRESETS` 分别保留四套完整默认值。
- model 的 `PLATES`、viewer 的 `FACE_LABEL_PLATES`、CSS 内四个硬编码图片 URL 分别维护素材规则。
- model 与 viewer 都存在缩放函数及相关常量。
- model `PLATES` 中还有与当前 CSS 不一致的 slice/corner/edge 数值。这些数值至少说明描述没有真正成为当前视觉规则的权威来源，不能直接拿它们覆盖已正确的 CSS。
- 旧状态判断仍参与统计和交互，见 A04。

**修复要求：** 先列出实际调用关系，明确一个默认值/资源/状态来源，viewer 仅保留薄适配。CSS 保留职责清楚的视觉规则；资源变量由同一描述提供。删除无调用的旧值，不为了通过字符串测试保留双轨实现。旧偏好迁移可以保留，不能无证据删掉。

**复验：** 新增一个修改主题参数的测试，确认预设、恢复默认、预览、实图、资源探测使用同一数据；检查旧测试是否仍只测新模块而正式路径使用旧副本。

## 五、还没有充分验收的内容

### V01｜完整照片和真实底板的审美

执行报告明确只用了三张合成照片，没有真实多脸相册的视觉检查。其浏览器 seed 创建的是纯色底加椭圆人脸示意，而不是明亮/暗部/高纹理背景的完整对照。四主题循环主要在第一张图进行截图，不能替代任务卡 FL-090 所要求的四主题 × 三类背景。

需要在本地使用任务卡允许的少量真实图片及已有脸框，尤其检查高纹理底色、长竖排、拥挤合影、边缘人脸、茶棕与暗朱各三款字体。私人照片、截图不必也不应为此推送到公共仓库。

### V02｜长姓名、几何碰撞与性能

报告承认超过已测五字的长姓名未充分覆盖、未做数值性能基准。原任务卡不是要求穷举所有笛卡尔组合，但 8 字预览、实际边界碰撞测量、1/6/20/50 脸性能、30 次开关累积检查本身是指定用例，不能借“不穷举”省掉。

### V03｜字体和 CSS 就绪的测量时序

本次读到的 `renderFaceNames()` 仍立即布局；动态 CSS 注入未在该入口建立就绪屏障。viewer 内显式 `document.fonts.load` 出现在 signature 路径，而不是完整的标签资源测量流程。增加 render generation 能避免部分旧回调污染，但不能代替字体就绪后的最终测量。

这里列为代码风险与缺少验收，未声称在用户本机实拍复现了字体漂移。下一轮应注入冷缓存/慢字体、快速切主题/切图，证明最终布局正确，不能仅等固定 250ms 就认为字体稳定。

### V04｜导出不能只看成功响应

新浏览器测试对导出主要检查响应状态、renderer 头、文件签名、HTML 是否含姓名、成片是否大于某尺寸；不能由此证明每款实际字体、底板角花、位置和输出比例完全一致。

`test-results.json` 的 FL-082 被标 PASS，但说明只覆盖两个人名存在与手动位置；原卡该项还包括隐藏姓名、姓名+别名等子场景。必须分别执行或明确标为部分覆盖。

还需在冻结快照期间切图/切主题、资源失败后恢复，以及有 hover/focus/提示状态时导出进行针对性检查。特别检查冻结 computed style 后再删除瞬态 class 是否仍残留不应导出的 opacity/filter，以及伪元素实体化是否会与原 CSS 的伪元素叠加。后两项本次仅作代码审查风险，不列为已完成端到端复现的故障。

### V05｜报告与实际部署身份

更新报告时明确：实际本地部署时间、正式进程对应后端版本、实际静态目录、此次新增 model/CSS 文件是否已从该目录被加载。不应为了核验而盲目重启服务，也不能因为远程代码已更新就假定旧进程已加载所有后端修改。

补齐 FL 用例逐项状态与证据，未测项逐项记录。旧报告只有少数编号和几个自定义汇总项，不能用“未执行项均标为 NOT_RUN”替代完整映射。

## 六、给执行 Agent 的补修顺序

1. 读取当前 HEAD 和本报告；先确认本地与 `9ba5fe6` 的差异。已被后续修改修好的问题只补验证，不改回旧版本。
2. 在独立工作目录和测试数据中，先建立 A01—A05 的失败用例。不要在正式浏览器注入 null、修改真实姓名或制造底板损坏。
3. 修 A01、A03、A04 的统一解析与规范化入口，保持业务字段和人物关系不变。
4. 修 A02 的本机字体弹窗作用域、文字解析和共用外观，再修 A05 的 requested/effective 主题分工。
5. 将 A06 的重复默认值、资源和状态收口；禁止顺手重写整个 viewer 或替换所有现有样式。
6. 补 V03 的字体/CSS时序测试、手动位置失败与跨图回执测试；保留既有队列和 operation ID。
7. 在同一组本地样图完成 V01/V02，并对新组件和不同方向完成 V04 的实际导出核验。
8. 更新逐项测试结果与部署说明，交付准确的中文完成报告。按用户本轮授权决定是否部署，不能把本报告本身当作新的部署/数据修改授权。

## 七、复现证据及运行方法

相对目录 `ourtime_acceptance_9ba5fe6/`：

- `reproduce_logic.js`：源码函数原文片段 + 合成输入，输出 `logic-results.json`。
- `reproduce_css.py`：相关 CSS 原文 + 最小 DOM，输出 `css-results.json` 与选择器复现截图。
- `selector_reproduction.html`：浏览器复现页面，不是正式应用。
- `local_font_selector.png`：已打开检查的最小复现截图。

```text
node ourtime_acceptance_9ba5fe6/reproduce_logic.js
python ourtime_acceptance_9ba5fe6/reproduce_css.py
```

CSS 脚本依赖 Playwright，当前证据环境使用 `/usr/bin/chromium`。其他环境应将 executable_path 改成已安装、已核实的浏览器路径；不要为复现随意安装或升级用户环境。测试输出中的 FAIL 是被验证的产品合同失败，不是脚本没有运行。

这些最小复现只验证已列出的局部因果链。不能把它们当作完成全部 73 项验收、跑过正式服务或重跑了仓库整套回归的证明。

## 八、源码索引

所有源码指向仓库 `dreamlizer/OurTime-PictureViewer` 的固定提交 `9ba5fe6952642c782d69beeef99fb696a46bb8c7`：

- `web/face-label-model.js`：状态、方向、偏好与重复预设。
- `web/viewer.js`：统计、预览、恢复默认、资源回退、运行时状态、动态样式加载与布局调用。
- `web/face-labels.css`：共享外观、方向规则与临时回退。
- `web/viewer-overrides.css`：本机字体旧预览样式及交互层样式。
- `web/photo-export.js`、`photo_export.py`：快照收集、样式资源与渲染。
- `validation/test_face_label_browser.py`：实际场景、等待方法和断言覆盖。
- `docs/repair-reports/face-labels-20260925/completion.md`、`test-results.json`：执行方报告，非本次独立实测的替代品。

**最终建议：保留有效改进，按 A01—A06 定向补修；补齐 V01—V05 后再进行整体验收。**
