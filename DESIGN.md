---
name: "HighSchoolPhysics"
description: "把物理测评证据变成学生可执行纠错行动的安静学习工作台。"
colors:
  primary: "#08766f"
  primary-soft: "#dff3ef"
  ink: "#17212f"
  text-muted: "#667085"
  text-quiet: "#41556f"
  border: "#d8dee8"
  graph-edge: "#9aa7b6"
  graph-hierarchy-edge: "#7c8999"
  surface: "#ffffff"
  canvas: "#f4f7f9"
  warning: "#b7791f"
  warning-text: "#704000"
  warning-soft: "#fff2d6"
  danger: "#b42318"
  danger-soft: "#fde3df"
  success: "#2f6f3e"
  success-soft: "#e4f4e8"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, PingFang SC, Noto Sans CJK SC, sans-serif"
    fontSize: "28px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "normal"
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, PingFang SC, Noto Sans CJK SC, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "normal"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, PingFang SC, Noto Sans CJK SC, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "normal"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, PingFang SC, Noto Sans CJK SC, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, PingFang SC, Noto Sans CJK SC, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.42
    letterSpacing: "normal"
  student-metadata:
    fontSize: "14px"
  student-card-title:
    fontSize: "17px"
  student-score:
    fontSize: "24px"
  student-mobile-display:
    fontSize: "26px"
  compact-meta:
    fontSize: "12px"
  dense-caption:
    fontSize: "11px"
rounded:
  tag: "4px"
  control: "6px"
  panel: "8px"
  card: "10px"
  pill: "999px"
spacing:
  "1": "4px"
  "2": "6px"
  "3": "10px"
  "4": "14px"
  "5": "18px"
  "6": "24px"
  "8": "32px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "7px 12px"
    height: "44px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.control}"
    padding: "7px 12px"
    height: "44px"
  input-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "8px 10px"
    height: "40px"
  panel-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "14px"
  chip-primary:
    backgroundColor: "{colors.primary-soft}"
    textColor: "{colors.primary}"
    rounded: "{rounded.tag}"
    padding: "2px 7px"
  status-warning:
    backgroundColor: "{colors.warning-soft}"
    textColor: "{colors.warning-text}"
    rounded: "{rounded.pill}"
    padding: "3px 9px"
---

# Design System: HighSchoolPhysics

## 1. Overview

**Creative North Star: "安静的物理解题桌"**

界面像学生摊开试卷、草稿纸和订正记录的一张安静书桌：所有信息都指向理解证据和完成下一步，而不是争夺注意力。冷灰台面承载稳定的日常使用，白色内容区保持清晰，沉静松绿只在主操作、当前选择和可行动线索上出现。

这是一个有证据但不冷硬的学习工具。学生端宽松、可触、少术语；教师和管理员端可以更紧凑，但必须保持同一套控件和状态语言。系统明确拒绝游戏化奖励轰炸、后台式学生界面、炫技式“AI 感”和放大学生失败感的视觉表达。

**Key Characteristics:**

- 浅色、低饱和、长时间使用不疲劳。
- 学生行动优先，视觉层级从薄弱点直接通向纠错动作。
- 证据状态有文字、有颜色、有来源，不靠装饰制造权威。
- 学生端宽松可触，教师与管理员端紧凑精确。
- 动效只说明状态变化，并尊重减少动态效果设置。

## 2. Colors

这是一套以沉静松绿为唯一主操作色、以校订琥珀和语义红绿解释学习状态的克制配色。

### Primary

- **沉静松绿** (`#08766f`)：只用于主要操作、当前导航、可点击知识线索和清晰焦点。
- **松绿薄雾** (`#dff3ef`)：用于主色的低强度背景、标签和选中状态，绝不充当大面积装饰。

### Secondary

- **校订琥珀** (`#b7791f`)：用于待复核、需关注和教师校订状态。
- **校订深琥珀** (`#704000`)：用于琥珀浅底上的文字，确保提示信息保持清晰对比。
- **琥珀便签** (`#fff2d6`)：承载温和提醒，不制造紧急感。

### Tertiary

- **掌握绿** (`#2f6f3e`) 与 **掌握浅绿** (`#e4f4e8`)：只表达已完成、已就绪或已掌握。
- **纠错红** (`#b42318`) 与 **纠错浅红** (`#fde3df`)：只表达失败、阻塞或未掌握；必须同时出现文字解释和下一步动作。

### Neutral

- **石墨墨色** (`#17212f`)：标题与正文的主要文字色。
- **讲义灰** (`#667085`)：说明、证据摘要和次级文字。
- **蓝灰批注** (`#41556f`)：标签、表头和紧凑后台信息。
- **铅笔分隔线** (`#d8dee8`)：表格、控件和面板边界。
- **白色纸面** (`#ffffff`)：主要内容表面。
- **冷灰台面** (`#f4f7f9`)：页面背景，降低长时间学习的视觉负担。

**The Evidence Color Rule.** 颜色只能表达操作优先级或有文字证据的真实状态；同一屏主色占比保持克制，状态不得只靠颜色区分。

## 3. Typography

**Display Font:** 系统中文无衬线体（`-apple-system`, `PingFang SC`, `Noto Sans CJK SC`, `sans-serif`）
**Body Font:** 同一系统中文无衬线体
**Label/Mono Font:** 不另设字体；数据与标签继续使用同一字族

**Character:** 单一字族保证课堂平板和校内电脑上的稳定渲染。层级来自字重、字号与间距，不使用展示字体或过度压缩字距制造个性。

### Hierarchy

- **Display**（700，28px，1.2）：学生姓名和页面最高层标题；产品界面不使用流体超大标题。
- **Headline**（700，18px，1.3）：面板主标题、后台关键区块标题。
- **Title**（700，15px，1.4）：卡片标题、表单分组和知识模块标题。
- **Body**（400，16px，1.55）：学生说明、题干和行动指导；连续说明文字限制在约 65–75 个字符宽度。
- **Label**（600，13px，1.42）：表头、字段标签、状态元数据；禁止用全大写和加宽字距制造层级。

**The Student-First Reading Rule.** 学生端不得为了容纳更多信息而把正文压到 14px 以下；教师与管理员的表格标签可使用 13px，但题干、错误原因和操作反馈必须保持可读。

## 4. Elevation

系统采用环境式分层：绝大多数表面保持扁平，以冷灰台面、白色纸面和铅笔分隔线建立结构。现有登录页和少数运营面板保留环境阴影作为聚焦层，但它是既有例外，不应扩散到普通卡片。

### Shadow Vocabulary

- **聚焦环** (`0 0 0 3px rgba(8, 118, 111, 0.26)`)：键盘焦点与导航目标，必须清晰可见。
- **既有环境卡片** (`0 14px 36px rgba(23, 33, 47, 0.1)`)：只保留在现有登录或运营聚焦表面；新组件不得同时叠加装饰性细边框与宽柔阴影。

**The Flat-at-Rest Rule.** 普通面板、题卡、表格和导航静止时一律扁平；深度首先由底色和结构间距表达，阴影不能成为“卡片感”的默认来源。

## 5. Components

### Buttons

- **Shape:** 克制圆角（6px）；学生端主要触控目标至少 44×44px，后台紧凑按钮最低 32–34px。
- **Primary:** 沉静松绿底、白字，用于每个局部流程唯一的主动作。
- **Hover / Focus:** 悬停只轻微加深；键盘焦点使用 3px 松绿聚焦环。所有状态变化控制在 160–200ms，并提供减少动态效果替代。
- **Secondary:** 白色纸面、沉静松绿文字与同色边界；不得与主按钮争夺视觉优先级。

### Chips

- **Style:** 标签使用 4px 小圆角；状态芯片使用全圆角。主色标签为松绿薄雾底，提醒为琥珀便签底，成功与失败使用各自浅色背景。
- **State:** 每个芯片必须保留可读文字；颜色只增强语义，不能替代状态名称。

### Cards / Containers

- **Corner Style:** 普通面板 8px，少数运营聚焦面板 10px；禁止超过 16px 的卡片圆角。
- **Background:** 白色纸面位于冷灰台面之上，学习状态可使用对应浅色表面。
- **Shadow Strategy:** 默认无阴影，遵守 Elevation 中的环境式分层。
- **Border:** 使用 1px 铅笔分隔线建立结构；禁止用粗侧边色条作为卡片装饰。
- **Internal Padding:** 紧凑后台以 10–14px 为主，学生题卡和学习面板以 14–18px 为主。

### Inputs / Fields

- **Style:** 白色背景、1px 分隔线、6px 圆角；标准高度 40px，学生端关键输入提升到至少 44px。
- **Focus:** 使用松绿聚焦环并保持边界清晰。
- **Error / Disabled:** 错误使用纠错浅红背景与明确原因；禁用态降低强调度，但文字仍须满足可读对比度。

### Navigation

- 顶栏保持白色半透明纸面和 1px 底部分隔线；学生端固定底部导航使用四个等宽触控区，当前项为沉静松绿底和白字。
- 教师与管理员分区导航采用白底松绿字，当前项切换为松绿底白字；导航变化必须同步 `aria-selected` 或等价状态。
- 820px 以下，双栏和多栏任务区收敛为单列；760px 以下，紧凑表单与班级分配控件也转为单列。

### Knowledge Evidence Map

- 图谱节点使用白色填充、松绿描边和石墨文字；掌握状态替换为对应语义浅色背景与深色描边。
- 选中节点加粗描边并显示相关题目，键盘焦点使用校订琥珀；缩放时优先保留模块级信息，避免标签互相覆盖。
- 图谱是学习证据入口，不是装饰插图；任何节点都必须可点击、可聚焦并带可读标签。

**The Role-Density Rule.** 学生端宽松可触，教师与管理员端紧凑精确；不同密度共享同一颜色、圆角、焦点和状态语言，不为不同角色发明三套组件。

## 6. Do's and Don'ts

### Do:

- **Do** 用沉静松绿标记唯一主动作、当前导航和可行动证据。
- **Do** 让错误、掌握度和运行状态同时拥有文字标签、颜色与证据来源。
- **Do** 保持学生端主要触控目标至少 44×44px，并在 820px 以下转为清晰单列。
- **Do** 使用 6px 控件圆角、8px 普通面板圆角和 10px 少数运营面板圆角。
- **Do** 在键盘操作时始终显示 3px 松绿聚焦环，并尊重减少动态效果设置。

### Don't:

- **Don't** 把学习做成积分、排行榜和奖励轰炸的游戏化产品。
- **Don't** 把学生端做成教师后台，不向学生堆叠表格、专业术语和运维信息。
- **Don't** 使用炫目渐变、玻璃卡片或无意义动效制造“AI 感”。
- **Don't** 用刺眼红色、羞辱性措辞或公开比较放大学生的失败感。
- **Don't** 使用粗侧边色条、渐变文字、装饰性网格背景或重复的同尺寸卡片阵列。
- **Don't** 在同一表面叠加 1px 装饰边框和宽柔阴影；普通卡片必须保持扁平。
- **Don't** 让长题干、公式、图谱标签或表格在平板视口中溢出容器。
