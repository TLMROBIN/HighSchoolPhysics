# HighSchoolPhysics

本项目是 `docs/superpowers/specs/2026-06-05-high-school-physics-knowledge-graph-blueprint.md` 的首期 MVP 实现，聚焦一个班级的一次测评闭环。

## 运行

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

启动显式演示实例：

```bash
python3 -m highschoolphysics.server --demo --host 127.0.0.1 --port 8765 --db data/highschoolphysics.sqlite3
```

打开 `http://127.0.0.1:8765/`。

`--demo` 会写入示范学校、班级、测评和以下已知密码账号，并在登录页展示它们。默认启动不会创建或展示这些账号。

全新的演示数据库会自动安装并发布 Phase 2B 默认物理体系：人教版 2019 六册教材目录形成 158 个知识节点，另含 15 个解题能力标签和 18 个物理学科核心素养标签。

## 生产化依赖

核心开发测试仍可只使用标准库路径。生产能力通过 extras 安装：

```bash
python3.10 -m pip install -e ".[production]"
python3.10 -m playwright install chromium
```

完整生产解析能力建议使用 Python 3.10-3.13：MarkItDown 新版和 MinerU 生产包要求 Python 3.10+。在 Python 3.9 环境中，核心应用和测试仍可运行，但运行时检查会把 MarkItDown/MinerU 标记为 degraded，而不是误报 ready。

也可以按能力拆开安装：

```bash
python3.10 -m pip install -e ".[ocr]"
python3.10 -m pip install -e ".[parsing]"
python3.10 -m pip install -e ".[pdf]"
python3.10 -m pip install -e ".[sso,providers]"
```

安装后运行生产能力检查：

```bash
python3 -m highschoolphysics.runtime_check --json
```

管理员页面的“生产化就绪度”会显示 PaddleOCR、MarkItDown、MinerU、PDF、OIDC SSO 和密钥加密能力的当前状态。缺少模型、命令、凭据或浏览器二进制时，系统会明确显示为未就绪，而不是把配置预留误报为可用。

生产运营相关界面已经接入后台能力：

- Provider 运营：管理员可保存 LLM 与 MinerU API 配置，secret 使用 Fernet 加密保存，只显示掩码；系统记录每日调用、月预算、单次预算和 provider usage ledger。
- 原卷解析：`markitdown`、`mineru_local`、`mineru_api` 均有真实适配器边界；MinerU API 会从加密 Provider 配置注入 endpoint/token，并把解析调用写入用量台账。
- PaddleOCR：本地适配器会归一化识别文本、置信度和 bbox，并复用答题卡 scan batch 与低置信复核流程。
- PDF 生成：错题本 HTML 可通过 Playwright PDF 服务生成 artifact，`export_tasks` 与 `generated_export_files` 会保存状态、文件、大小、引擎版本和失败原因。
- OIDC SSO：管理员可保存 OIDC 配置；登录启动会生成 state、nonce 与 PKCE code challenge；回调会一次性消费 state，并按 existing-user-only 策略绑定本地账号。
- Product Design：`docs/product-design/highschoolphysics-design-system.md` 记录当前风格深化后的 tokens、组件、生产运营页面模板和 PDF/打印规则。

### 演示账号

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 教师 | `teacher_li` | `teacher123` |
| 学生 | `stu_1001` | `student123` |
| 管理员 | `admin` | `admin123` |

## 学校实例

先为新的数据库交互式创建首个管理员：

```bash
python3 -m highschoolphysics.server --db data/school.sqlite3 --init-admin school_admin
```

命令会要求输入并再次确认密码。密码至少 10 位，且同时包含字母和数字。数据库中只保存密码哈希；若数据库已经存在任何用户，初始化会拒绝覆盖。

然后正常启动，不要添加 `--demo`：

```bash
python3 -m highschoolphysics.server --host 127.0.0.1 --port 8765 --db data/school.sqlite3
```

非演示数据库不会静默写入默认体系。首个管理员登录后，可在管理员页面点击“安装或补齐默认体系”。该操作幂等执行：重复安装不会产生重复节点；管理员已经改名、停用或补写说明的默认项会被保留。

默认体系的运行时真相来自仓库内已提交的 JSON 清单：

- `highschoolphysics/data/pep2019_knowledge.json`
- `highschoolphysics/data/physics_abilities.json`
- `highschoolphysics/data/physics_literacies.json`
- `highschoolphysics/data/taxonomy_sources.json`

`tools/extract_pep2019_toc.py` 和 `pdftotext` 只用于开发阶段从本机教材 PDF 重建知识清单；正常部署和启动不会读取 PDF。来源记录允许本机路径缺失，因此另一台机器没有同样的教材文件也能使用已提交清单。

升级旧演示数据时，当前题目标签会通过替代映射迁移到新默认节点；已发布测评快照、错题快照、评分记录和审计历史不会被重写。

## Phase 2C 真实题库与原卷解析

教师和管理员可以在教师端“真实题库”工作台中新增题目、创建原卷解析任务、执行解析、复核拆出的题目，并把解析项保存为正式题库记录。题目记录会保留原始来源信息，包括原卷、页码、题号、来源学校或出版方、考试类型、导入批次、解析任务和解析置信度。

内置 `deterministic_text` 解析器支持按题号拆分纯文本题目，并把选项、答案、解析置信度和警告统一保存为 parsed item。`markitdown`、`mineru_local`、`mineru_api` 是可配置的外部适配模式；默认策略为 fail closed，缺少命令或适配失败时会记录失败原因。只有显式配置 `fallback_policy=deterministic_text` 时才回退到内置解析器。

题目标签确认现在覆盖三类正式标签：

- 知识标签
- 能力标签
- 核心素养标签

每一类正式确认最多允许 3 个 active 标签。停用或已删除的默认/校本标签不能用于新题确认；历史测评快照仍保留发布时的标签证据。

Phase 2C 不执行答题卡 OCR 批改、主观题评分、掌握度计算或错题重做算法；这些仍属于 Phase 2D 和 Phase 2E。

## Phase 2D 测评批改与错题重做闭环

教师和管理员可以在教师端“组卷与答题卡”工作台中从题库记录组装试卷，并为生成的测评创建不可变题目快照和答题卡模板。测评创建后会按班级学生生成参与记录，后续批改只读取测评快照，不回写题库历史版本。

PaddleOCR 在当前阶段体现为可导入的 OCR payload、scan batch 和低置信复核队列，不随项目捆绑生产 OCR 二进制或外部服务凭据。低置信或冲突识别项会把批改阻塞在复核状态；教师复核后才能发布客观题批改结果。

发布后的普通重新批改仍会被拒绝。需要修正成绩时，教师/管理员必须走显式“批改修订”流程：系统会记录 revision 和 revision item，并更新学生 response 与错题状态，但不会重写 `question_version_snapshots`。

错题重做是独立证据，不覆盖原始错题行。学生提交的 redo attempt 会保留答案、状态和提交时间；教师/管理员复核后写入分数、反馈、复核人和复核时间，并同步更新错题的 `latest_redo_status`。

错因标签由教师/管理员维护并绑定到错题，适合作为讲评、分层练习和后续统计的元数据。管理员端还可以保存错题本导出配置。错题本 HTML 导出默认隐藏正确答案和解析，可显式配置显示答案、解析、错因和重做历史。

## Phase 2E 确定性掌握度指标

系统现在会从已发布测评的不可变 `question_version_snapshots.tag_snapshot_json` 中计算学生在知识点、能力标签、核心素养标签上的确定性掌握度。每个标签记录评测作答次数、评测正确/错误/空白、已复核重做次数、重做正确/错误、eligible attempts、综合正确数、综合错误数、空白数、正确率和掌握状态。

重做记录作为独立证据保存，同时计入综合正确率：`eligible attempts = 评测作答次数 + 已复核重做次数`，`correct_rate = (评测正确 + 重做正确) / eligible attempts`。空白答题单独计数，属于 eligible attempts，但不混入非空错误数。

掌握阈值为：0 次 `未练习`，正确率低于 30% `未掌握`，低于 60% `有困难`，低于 80% `不熟练`，80% 及以上 `已掌握`。学生端知识图谱按确定性状态上色；能力和核心素养以摘要卡显示对应颜色和证据。已有手动知识点掌握标记会作为显示覆盖和备注保留，不会替代底层计算结果。

## Phase 2F 学生三类导航

学生端仍以知识图谱为首页。图谱下方新增知识、能力、核心素养三类并行导航，每个标签卡都会显示当前掌握证据、该学生已发布测评中可见的关联题、自己的错题记录和待重做任务。

学生从任一知识点、能力标签或核心素养标签都可以进入对应题目。关联题跳转会先激活目标三类面板，再滚动到题卡；同一题目重复出现在不同列表时使用各自唯一的元素 ID，避免页面定位冲突。未发布的教师题库草稿不会进入学生相关题列表。

## Phase 2G 教师与管理员掌握度分析

教师端新增“Phase 2G 掌握度分析”：按知识、能力、核心素养三类展示本班掌握图谱、状态分布、eligible attempts、正确率、错误率和空白率。教师可以展开自己班级的学生明细，同时只能看到同年级 aggregate-only 聚合对比，不暴露其他班学生行。

管理员端新增“Phase 2G 年级掌握分析”：展示年级掌握趋势和按年级聚合的标签掌握表。管理员视图默认只输出聚合数据，不渲染学生明细属性；错误率使用 `wrong_count / eligible_attempts`，空白率使用 `blank_count / eligible_attempts` 单独展示。

## Phase 3+ 图谱与运维成熟度

学生端关系图谱已从 demo 坐标改为确定性分层布局。导入的非 demo 知识节点会按层级分列、同层稳定排序并获得不重叠坐标；SVG 输出带有 `data-layout="deterministic-layered-v1"`，不再依赖硬编码 demo 节点 ID。

图谱支持缩放详情规则、键盘节点选择和触控/指针平移。低缩放时保留模块级信息并弱化或隐藏子节点标签，高缩放时展示子节点标签；节点带 `role="button"`、`tabindex="0"` 和可读 `aria-label`，适配课堂平板视口。

备份导出现在使用统一表清单，覆盖题库、测评快照、学生作答、错题、掌握度、题库解析、错因、导出配置和审计等核心数据。备份可恢复到新 SQLite 数据库，并通过外键、核心表、答题快照、掌握度和标签 JSON 的一致性检查。Phase 3+ 将 schema version 记录为 `6`，用于证明 Phase 2G 数据库能向前迁移且不丢失测评、错题、掌握度或本体历史。

## 已覆盖的 MVP 链路

- 本地账号、班级、教师授课范围、学生持久 Cookie 登录。
- 默认知识点、能力标签、核心素养标签、来源证据、图谱关系、本体版本和掌握度版本表。
- 真实题库、原卷 provenance、解析任务、拆题复核、题目快照、评分规则快照、标签快照。
- LLM 候选标签生成、缓存、教师审核确认三类标签和审计。
- 组卷、答题卡模板、OCR payload 导入、低置信复核、客观题批改、发布。
- 显式批改修订、错因标签、学生重做提交、教师重做复核。
- 确定性掌握度指标、学生三类标签导航、教师班级掌握分析、年级聚合对比、错题本生成、学生掌握标记、班级诊断。
- 管理员年级掌握趋势和 aggregate-only 标签掌握聚合。
- 确定性分层知识图谱、缩放详情规则、键盘选择、触控/指针平移。
- A4 可打印错题本 HTML 导出、导出配置保存、核心数据 JSON 备份导出、备份恢复和一致性检查。
- 管理员用户/班级、LLM Key 配置、解析任务、隐私留存、审计日志页面。

## 当前边界

生产化接口已经进入可安装、可配置、可测试、可审计的状态，但重型能力仍按 extras 与显式运行时检查启用。没有安装 PaddleOCR、MinerU、Playwright 浏览器或 Authlib 时，相关能力会显示缺依赖或未配置，不会静默假装可用。真实远程 LLM/MinerU 费用、学校 IdP 联调和 Playwright 浏览器安装仍需要部署现场提供凭据、额度和二进制环境。
