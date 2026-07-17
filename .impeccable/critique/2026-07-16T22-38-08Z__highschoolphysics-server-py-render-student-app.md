---
target: 学生端首页（/app）
total_score: 15
p0_count: 0
p1_count: 4
timestamp: 2026-07-16T22-38-08Z
slug: highschoolphysics-server-py-render-student-app
---
Method: dual-agent (A: /root/critique_a · B: /root/critique_b)

# 学生端首页（/app）设计评审

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 2/4 | 当前标签可见，但学生端没有实际渲染 `#action-status`；重做与掌握度保存可能无反馈。 |
| 2 | Match System / Real World | 2/4 | 物理知识名称自然，但“计算、显示、标签、Phase 2E”等内部语言泄漏给学生。 |
| 3 | User Control and Freedom | 2/4 | 有固定底部导航、折叠项和图谱重置，但掌握度写入后立即刷新，没有撤销或确认。 |
| 4 | Consistency and Standards | 3/4 | 颜色、字体和控件较统一，但学生标签页只靠 class/颜色表达选中，没有 `aria-selected`。 |
| 5 | Error Prevention | 1/4 | 636 个重复掌握度按钮可直接写入，没有撤销、草稿或保存中保护。 |
| 6 | Recognition Rather Than Recall | 2/4 | 导航和按钮有文字，但 159 个扁平知识筛选项要求学生先知道知识点归属。 |
| 7 | Flexibility and Efficiency | 1/4 | 有缩放和基础键盘激活，但没有搜索、全部折叠、最近薄弱点或推荐路径。 |
| 8 | Aesthetic and Minimalist Design | 1/4 | 配色安静，但 158 个默认展开节点、重复卡片与 44,785px 页面使结构极不简洁。 |
| 9 | Error Recovery | 0/4 | 学生端重做错误写入不存在的状态区域；掌握度失败也没有可靠的学生端恢复提示。 |
| 10 | Help and Documentation | 1/4 | 有简短说明，但没有解释掌握证据、图谱操作、空状态或明确下一步。 |
| **Total** | | **15/40** | **Poor — 核心体验需要结构性调整** |

## Anti-Patterns Verdict

**Start here：它会让人觉得是 AI 生成的吗？会，主要是结构而非视觉特效。**

**LLM assessment：** 页面避开了渐变文字、玻璃拟态、排行榜和夸张动效，但落入另一种常见 AI 模式：把后台完整分类树直接倾倒成“青绿色教育仪表盘”。五张同权重数字卡、两种并列图谱、158 个默认展开模块和重复状态按钮，让“克制配色”变成了没有任务主线的通用卡片系统，而不是“安静的物理解题桌”。已触发的明确反模式包括粗侧边色条、hero metric 模板、同质卡片阵列，以及把所有未选筛选按钮都涂成主色。

**Deterministic scan：** 检测器扫描了 [app.css](/Users/binyu/Projects/HighSchoolPhysics/highschoolphysics/assets/app.css) 和 [app.js](/Users/binyu/Projects/HighSchoolPhysics/highschoolphysics/assets/app.js)，退出码为 2，共发现 29 项：6 个 `side-tab` warning、21 个字阶偏离 advisory、2 个颜色未登记 advisory。6 个侧边色条位置中，与学生页面直接相关的包括 [app.css:305](/Users/binyu/Projects/HighSchoolPhysics/highschoolphysics/assets/app.css:305)、[app.css:462](/Users/binyu/Projects/HighSchoolPhysics/highschoolphysics/assets/app.css:462) 和 [app.css:524](/Users/binyu/Projects/HighSchoolPhysics/highschoolphysics/assets/app.css:524)。多项 12px 字号 advisory 也真实对应学生证据文字；但登录页、教师/管理员区域的多数告警不属于本次 `/app` 范围，24px 掌握度数字则是已记录的组件例外，不应按缺陷处理。

**Visual overlays：** 没有可靠的用户可见覆盖层。浏览器的 `document.title` 写入与 `<script>` 创建预检均失败，证明当前自动化表面只读；因此没有启动 Live 检测服务，也没有注入 `detect.js`。替代证据来自独立的新浏览器标签页、768×1024 与 1024×768 截图、计算样式、触控尺寸、DOM/ARIA 和溢出测量。

## Overall Impression

视觉底子是可靠的：冷灰背景、白色纸面、沉静松绿和固定底部导航很适合校内平板长期使用。真正的问题是信息架构没有从学生任务出发——系统把“拥有完整知识体系”误当成“学生应该一次看到完整知识体系”。最大机会不是换色，而是把首页改成最多 3–4 个有证据的下一步，把完整图谱退到探索层。

## What's Working

1. **安静而不羞辱的视觉基础。** 没有奖励轰炸、排行榜或庆祝噪声，符合学生纠错场景，也与 PRODUCT.md 的情绪目标一致。
2. **底部主导航清楚且可触。** 四个目的地都有文字，按钮高度 58px，学生能随时在知识图谱、错题本、待重做和最近考试之间退出或切换。
3. **证据通常不只靠颜色。** 掌握状态大多带文字、路径和次数；图谱节点具备键盘焦点与可读 `aria-label`，焦点环实际可见。

## Priority Issues

### [P1] 图谱首页是分类树倾倒，不是下一步学习路径

- **Why it matters：** 768×1024 竖屏下，158 个模块默认展开，关系图谱从页面顶部往下约 34,774px 才出现，整个图谱标签页高约 44,785px。学生无法在几秒内找到“现在最该纠正哪一点”。
- **Fix：** 首屏改为“下一步”区，只展示最多 3–4 个由真实测评支持的薄弱点；教材树按册/章默认折叠，增加搜索与全部折叠；“模块视图/关系图谱”改为互斥切换，而不是纵向连续堆叠；后代节点按需渲染。
- **Suggested command：** `$impeccable distill`

### [P1] 错题本入口先给出 159 个筛选项，而不是错题

- **Why it matters：** 学生进入核心纠错功能后，首屏是 159 个同样醒目的青绿色筛选按钮，第一道错题在折叠线以下。系统要求学生先知道知识点，才帮助他定位问题，顺序反了。
- **Fix：** 默认先显示“待处理”和“最近出错”；知识筛选改为分层的册/章选择或可搜索选择器；只有当前筛选项使用主色，其余保持中性；所有学生筛选控件至少 44×44px；删除“附属功能”这类削弱产品核心目的的文案。
- **Suggested command：** `$impeccable clarify`

### [P1] 学生操作缺少可靠的保存状态与错误恢复

- **Why it matters：** [app.js:14](/Users/binyu/Projects/HighSchoolPhysics/highschoolphysics/assets/app.js:14) 的 `setStatus()` 只写入 `#action-status`，但学生页面没有这个节点；[server.py:1063](/Users/binyu/Projects/HighSchoolPhysics/highschoolphysics/server.py:1063) 等状态区只存在于教师/管理员页面。慢网环境下，学生可能重复提交、放弃或不信任结果。
- **Fix：** 增加学生专用 `aria-live` 状态区和卡片内反馈；保存中禁用当前控件；失败时保留重做输入并给出具体修复方式；掌握度修改提供短时撤销；成功反馈完成后再刷新。
- **Suggested command：** `$impeccable harden`

### [P1] 学生端未达到已确认的触控与对比度基线

- **Why it matters：** 独立测量中，图谱标签页 806 个已渲染交互元素里有 799 个至少一维小于 44px；常见掌握度按钮约 34.5px 高。默认子节点文字因 20% 透明度有效对比度约 1.5:1，琥珀文字在浅琥珀背景上约 3.28:1，均不适合小字号学生界面。
- **Fix：** 学生控件和链接全部达到 44×44px；默认缩放下恢复图谱标签可读性；加深浅色状态背景上的文字；学生证据文字保持 14–16px；标签页补 `aria-selected`，缩放按钮补可读名称；增加 `prefers-reduced-motion` 分支。
- **Suggested command：** `$impeccable audit`

### [P2] 首屏和空状态没有鼓励行动的峰值与结尾

- **Why it matters：** 五个同权重的 0 把“尚未掌握”变成第一印象；“待重做”只有“当前没有待处理题目”，最近考试只剩表头，既不安慰也不给下一步。
- **Fix：** 用一个支持性的“下一步”替代五个零指标的主视觉；空状态说明为什么为空，并提供一个有价值的入口，如“查看薄弱点”或“复习最近错题”；把聚合指标降为次要证据。
- **Suggested command：** `$impeccable onboard`

## Persona Red Flags

### Jordan（第一次使用的学生）

- 首屏同时出现五个零、两种图谱和数百个动作，没有一个明确的第一步。
- “计算、显示、标签、Phase 2E”要求理解系统内部术语。
- 进入错题本后先面对 159 个知识筛选项，空状态又没有解释和后续入口。

### Sam（依赖键盘、读屏或低视力支持的学生）

- 绝大多数交互元素未达到 44×44px，低透明度图谱文字对比度严重不足。
- 学生标签页与标签家族只靠视觉 class 表达选中，没有 `aria-selected`。
- SVG 设置为 `role="img"`，内部又含 158 个 `role="button"` 节点，部分读屏器可能把交互后代压平。
- 学生端异步成功和错误没有实际渲染的 `aria-live` 通知。

### Casey（容易被打断的平板学生）

- 44,785px 长页面让返回后重新定位十分困难，关系图谱在竖屏中几乎不可到达。
- 错题筛选墙阻挡实际题目，没有“最近薄弱点”帮助快速恢复上下文。
- 保存与失败静默，学生无法判断中断前的操作是否完成。

## Minor Observations

- 顶栏角色显示英文 `student`，与其余中文界面不一致。
- “错题本是附属功能”与产品把纠错作为核心闭环的定位冲突。
- `+` / `−` 图谱控件应使用“放大图谱/缩小图谱”的可访问名称。
- 最近考试为空时应显示真正的空状态行或面板，而不是只有表头。
- 顶栏品牌和退出链接高度约 22.5px；退出链接宽约 32px。
- 平滑滚动与约 1.6 秒目标高亮没有减少动态效果替代。
- 学生证据文字大量使用 12px，低于已确认的学生阅读基线。
- 页面在 768px 宽度没有横向溢出，这是值得保留的响应式优点。

## Questions to Consider

- 如果学生测评后只有十分钟，首屏应该优先回答“薄弱在哪里”“为什么错”还是“现在重做哪一道”？能否由一张主任务卡同时回答三者？
- 关系图谱究竟是每日学习的主入口，还是理解知识关系的探索工具？如果是主入口，为什么竖屏下排在完整教材树之后？
- 五个同权重的零指标支持了什么学习决定？一个有证据的“下一步”是否更能建立信任？
- 学生是否应该在做题证据之前，对 158 个节点逐一自报掌握度？还是把手动掌握度降为证据之后的次级修正？
