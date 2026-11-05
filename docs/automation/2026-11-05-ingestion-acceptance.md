# 2026-11-05 固定录入流程验收

## 已实现与范围

固定状态机、逐题纠错、上传和答案匹配、标签生成与采纳、断点恢复、独立质量请求、后台 watchdog 已实现。入口为 `python3 -m highschoolphysics.ingestion_pipeline`，操作说明见 [paper-ingestion.md](paper-ingestion.md)。本轮为 CLI 与后台服务，不含新增教师网页操作面板。

测试目标是部署机上的独立数据库：

`/home/yub/.local/state/highschoolphysics-ingestion/sandbox/app.sqlite3`

只在隔离库创建演示学校和账户，并复用部署机已配置模型做真实调用；不复制学生业务数据，不输出模型凭证。测试卷没有写入生产题库或正式考试。

## 实际样本

本机 2022—2026 五个目录共发现 182 个文件，配对为 91 组，50 组能从原始 OOXML 读到唯一可见题号；41 组标记为需要显式核对题号。50 组是盘点可继续，不等于内容已验收。完整清单保存在本机忽略的运行目录 `data/paper-ingestion/20261105/inventory.json`。

| 样本 | 解析大题数 | 实际阻断状态 |
| --- | ---: | --- |
| 2022 上海空白卷 | 20 | 第一道完成文字审查；随后图像选项需要视觉证据，返回 `review_blocked` |
| 2023 广东空白卷 | 15 | `blocking_conversion_issue`，其中 7 个 `embedded_formula_unconverted` |
| 2024 广东空白卷 | 15 | `blocking_conversion_issue`，其中 1 个 `embedded_formula_unconverted` |
| 2025 广东空白卷（5—8、11—15） | 9 | `unassigned_source_figure`：卷首块包含未归属图片；需核对是否为装饰或题图 |
| 2026 广东原卷 | 15 | `unassigned_source_figure`：卷首块包含 2 个未归属图片；需核对用途 |

合计 74 道大题完成真实原卷上传和解析，均未由测试流程入生产库。2025 样本明确保留缺题范围和 `partial=true`；未补造缺题。解析产物保留在隔离 documents 存储，原件不修改。

这些结果验证了失败关闭，而非高考卷自动导入成功。当前环境尚未配置并验收视觉 Provider；未转换公式与图片归属问题也未在本轮修复。无法据此宣称这些卷已完成纠错、答案核验和标签采纳。

## 真实模型闭环

另建一题无图、无公式的合成单选卷，用真实 DOCX、现有解析器和现有学校模型完成：

原卷上传 → 题文审查 → 整卷确认 → 答案上传 → 唯一题号关联 → 答案纠错 → 内容 watchdog → 题库和试卷保存 → 三类模型标签 → 标签 watchdog → 原子采纳 → 回读验收 → `complete`。

结果为 1 道大题、1 个作答单元、1 道独立抽查，`report.json` 的范围为 `question_bank_only`。结果路径：

`/home/yub/.local/state/highschoolphysics-ingestion/synthetic-smoke/report.json`

真实调用中发现过 watchdog 混淆标签 ID：生成器给出的“牛顿第二定律”正确，抽查模型却称其为动量守恒。修订后仅向抽查请求提供本题实际选中 ID 及权威名称/路径，不再提供整套无关标签目录；另外隔离只读抽查与告警消除字段，拒绝在 watchdog 中提交任何修订。每次失败保留原始结果与恢复理由，最终重新独立抽查通过，没有直接忽略失败。

## 自动化与发布验证

- 新增 16 项流程测试：重启、写库后断电、精确请求重放、标签重复采纳、人工编辑冲突、并发锁、源文件变化、模型越权输出、watchdog 只读协议、最初暂停原因保留、入库后漂移、视觉门禁等。
- 相关回归此前 86 项通过；最终本机全量运行 604 项，`OK (skipped=1)`。首轮一次旧 HTTP 页面测试超时，单项复测及最终全量均通过。
- `VERIFY_TARGET=local` 发布检查通过；本机部分生产能力未安装的状态保留为 runtime 降级，不冒充线上能力。
- 已通过严格远端发布检查：本机/远端/GitHub HEAD、远端 origin/main、服务与 document worker、HTTP `/`、代理入口、静态资源、schema 和 runtime readiness。
- 已安装四个 systemd 模板；`gaokao-2022-sample` 的 runner/watchdog timer 和 `synthetic-smoke` 的 watchdog timer 已启用。前者已按真实质量问题暂停，后者独立检查返回 `complete / stopped=false`。
- 有 `STOP.json` 时 systemd 用 `ExecCondition` 跳过重复执行，避免不断重试模型或重复报同一错误。恢复须提供明确原因；第一条失败证据不会被后续超时覆盖。

部署机到 GitHub 的 HTTPS fetch 曾挂起。本轮通过本机已推送提交的增量 Git bundle，沿用原发布脚本完成更新，并恢复原 auto-update timer；GitHub main 的一致性另由严格发布检查确认。未改动原自动更新服务配置。

## 后续使用边界

流水线与 watchdog 可以使用；当前真实样本保持暂停状态。要完成这些高考卷，需要先修复/复核对应的公式和图片问题，并配置与实际验证视觉模型，或由实际看过原件的审核者提交绑定哈希的视觉回执。不能仅删除 STOP、修改 JSON 状态或把装饰图未经核实地排除后宣称完成。
