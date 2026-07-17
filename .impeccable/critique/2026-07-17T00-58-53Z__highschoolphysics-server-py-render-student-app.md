---
target: HighSchoolPhysics 学生端首页（/app）
total_score: 34
p0_count: 0
p1_count: 0
timestamp: 2026-07-17T00-58-53Z
slug: highschoolphysics-server-py-render-student-app
---
Method: dual-agent (A: /root/critique_a_last · B: /root/critique_b_last)

# HighSchoolPhysics 学生端设计复评

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3/4 | 图谱焦点恢复和实时宣告已通过；图谱选择提示切换标签后仍可能短暂残留。 |
| 2 | Match System / Real World | 4/4 | “先重做一道题”“上次作答”“待巩固”等语言符合学生订正试卷的心智模型。 |
| 3 | User Control and Freedom | 3/4 | 导航、复位、折叠、筛选与撤销路径完整；本轮只读复评未提交真实数据。 |
| 4 | Consistency and Standards | 4/4 | 按钮、状态、间距、圆角和导航在五个学生端表面保持一致。 |
| 5 | Error Prevention | 3/4 | 选择题已使用 A–D 结构化单选并防止答案泄漏；未选择时提交按钮仍可点击。 |
| 6 | Recognition Rather Than Recall | 4/4 | 上次答案、关联知识、当前状态、相关题目和最近测评均保留在当前上下文。 |
| 7 | Flexibility and Efficiency | 3/4 | 支持搜索、教材浏览、直接跳转与完整键盘路径；未提供更高级快捷操作。 |
| 8 | Aesthetic and Minimalist Design | 4/4 | 主动作唯一，证据、全部关联和视图控制按需展开，视觉安静且克制。 |
| 9 | Error Recovery | 3/4 | 错误反馈、输入保留、撤销和状态提示均有实现；只读复评未触发网络错误。 |
| 10 | Help and Documentation | 3/4 | 关键页面均有就地说明和计算依据；没有独立任务帮助入口。 |
| **Total** | | **34/40** | **Good；P0=0，P1=0** |

## Anti-Patterns Verdict

**LLM assessment:** 通过。界面没有明显 AI 模板感：不使用玻璃拟态、渐变文字、夸张圆角、装饰网格或无意义动效。松绿主色、冷灰台面和扁平纸面与学生纠错场景一致。

**Deterministic scan:** `detect.mjs` 对 `highschoolphysics` 返回 0 条，未发现设计系统字号、颜色或常见反模式漂移。第二评审曾在旧缓存页面量到 19px 高的关联题链接；最终资源版本升级后， fresh page 实测该可见链接为 `inline-flex`、512×44px，因此该项判定为旧缓存假阳性。

**Visual overlays:** 注入前置检查失败，浏览器只读执行面拒绝修改 `document.title`，因此没有可靠的用户可见 overlay。回退证据为 fresh browser DOM/ARIA 快照、尺寸测量、键盘实操和 detector CLI。

## Overall Impression

学生端已从“后台数据堆叠”转成清楚的学习闭环：先告诉学生今天要解决什么，再用聚焦图谱解释薄弱点，最后进入不泄题的独立重做。最大改善是信息架构和任务可信度；当前剩余问题已降为可选的 P2/P3 打磨，不再阻塞使用。

## What's Working

1. **首页行动层级明确。** 首屏只有一个“开始重做”主动作，待重做、错题与最近测评作为上下文，不再让学生先穿过庞大体系。
2. **图谱成为可操作证据。** 桌面 7 个、窄屏 4 个重点节点，完整关联可展开；节点有可读名称、键盘激活、焦点恢复、`aria-current` 和实时状态宣告。
3. **重做流程可信。** 选择题使用 4 个大尺寸 radio；重做前只显示上次作答，不显示正确答案和解析；提交后才引导回错题本查看依据。

## Priority Issues

### [P2] 图谱选择提示可能跨标签残留

- **Why it matters:** 学生进入“待重做”或“最近测评”后若仍看到上一条图谱选择提示，可能误解当前内容仍被临时过滤。
- **Fix:** 在没有可撤销动作时，切换学生标签页自动清空瞬时 action-status；真正跨流程有效的撤销提示继续保留。
- **Suggested command:** `$impeccable polish`

### [P2] 未选择答案时提交按钮仍可点击

- **Why it matters:** 漏选是高频错误；先点击再依赖浏览器校验会制造一次可避免的中断。
- **Fix:** 对选择题初始禁用提交按钮，选中 radio 后启用，同时保留提交时校验兜底。
- **Suggested command:** `$impeccable harden`

### [P3] 极长图谱名称仍需要教学短标签

- **Why it matters:** 两行布局已避免碰撞，但“实验：探究加速度与力、质量的关系”仍会省略尾部，视觉用户需进入详情确认全称。
- **Fix:** 为少数长名称提供教学常用短标签，例如“探究 a 与 F、m”，完整名称继续保留在详情、`aria-label` 与 `title`。
- **Suggested command:** `$impeccable clarify`

## Persona Red Flags

**Jordan（首次使用的学生）:** 主按钮和四个选项非常清楚；唯一剩余干扰是切换标签后可能残留上一条图谱状态，以及空选时提交按钮看起来可执行。

**Sam（依赖键盘/辅助技术）:** 图谱节点 Enter 激活后焦点成功恢复到新节点，状态通过 polite live region 宣告；最终只读评审未用真实屏幕阅读器核验 SVG 名称的具体朗读顺序。

**Casey（容易分心的移动端学生）:** 固定底部导航、单列重做选项和 44px 控件有利于单手操作；最终严格时间窗没有再次完成 390px 全路径复测，但此前同轮整改验收未发现横向溢出或节点碰撞。

## Minor Observations

- “调整视图”“查看计算依据”“完整关联”默认折叠，显著降低图谱首屏密度。
- 最近测评卡同时提供“查看当前知识图谱”和“继续重做”，从证据到行动路径清楚。
- 桌面底部导航已与 1180px 内容区对齐，不再横跨整个 1280px 视口。
- 资源版本同时覆盖 CSS 与 JS，避免旧样式缓存掩盖触控整改。

## Questions to Consider

Questions skipped: 剩余问题均为低风险、实现方向明确的 P2/P3 打磨，不需要新的产品取舍即可处理。
