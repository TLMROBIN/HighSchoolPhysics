# 整卷导入与 Markdown 题库施工文档

版本：1.0，2026-09-28。交付对象：负责 HighSchoolPhysics 开发、测试、运维及题目复核的人员。

本文规定要做什么、改哪些代码、数据如何流转、怎样检查完成，以及失败时怎样恢复。本文和附件属于施工契约，**不是已经实现的功能说明**。文中新增模块、接口、环境变量、脚本及 worker 服务均为待开发项目；标注“现有”的命令可以直接用于恢复现场。

配套文件：

- [现状诊断与产品设计](../specs/2026-09-28-document-first-question-ingestion.md)
- [数据库新表 DDL 草案](2026-09-28-document-ingestion-schema.sql)
- [验收用例与交付记录模板](2026-09-28-document-ingestion-acceptance.md)

## 0. 交付目标与不可缩减范围

教师的主流程必须是：**选择整份 Word/PDF → 自动转换 → 自动拆题 → 集中复核与修改 → 批量入库 → 页面重新渲染 → 下载 Markdown 和图片**。不得要求教师预先转成文本、制作 JSON、裁切每一道题或逐题复制粘贴。

必须交付：

1. `.docx`、旧 `.doc`、文本 PDF、扫描 PDF 的文件入口与真实转换路径。
2. 可编辑题干、选项、公式、答案/解析；题目插图独立存储并在正确位置引用。
3. 完整大题及其小问的内容结构；兼容现有逐空结果记录。
4. 原卷对照、Markdown 编辑与预览、拆分/合并/调序/题号修正/图片归属调整。
5. 可恢复异步任务、批量确认、重复操作幂等、错误定位。
6. 单题及整卷 Markdown ZIP 导出，解压后可用相对路径显示图片。
7. 当前两批已入库内容的核对修复，保持学生首次作答和练习历史。
8. 真实样本、普通教师浏览器入口与远端发布验收。

允许人工复核识别疑点；不允许用整题截图冒充 Markdown 转换完成。主观大题也要转换、保存与导出，但本项目此次不增加主观题自动判分，不恢复分数制，不重做 SSO 或知识体系。

## 1. 开工前恢复现场

### 1.1 基线与边界

本轮文档编制基线为本机 `main` 的 `6c79ab00`，`SCHEMA_VERSION=11`。上一轮现场检查显示生产也在该提交；开发开工必须重新读取，不能把这个结论当永久状态。

现有仓库：`/Users/binyu/Projects/HighSchoolPhysics`。
部署机：`yub@10.50.159.62`，目录 `/home/yub/Documents/trae_projects/HighSchoolPhysics`。
生产数据库：该目录内 `data/school.sqlite3`，不是代码默认的 `data/highschoolphysics.sqlite3`。
验收入口包含 `/physics/` 反向代理子路径，也要验证服务直连路径。

现有恢复命令：

```bash
cd /Users/binyu/Projects/HighSchoolPhysics
bash scripts/hsp_recover_context.sh
git status --short --branch
git log -5 --oneline
```

记录本地与远端 HEAD、dirty 文件、服务、数据库位置。保留已有 `output/`、`tmp/` 和未提交工作，不清理 `.mavis/`、`.opencode/`、`references/`，不将参考/学生材料自动提交。

按仓库实际协作安排开发；需要分支时使用 `codex/document-first-ingestion`。先确认 main 未被其他工作推进，发布前与当前 main 对齐。生产原件、数据库及资产不得作为自动化测试写入目标。

### 1.2 必须阅读的现有入口

| 文件/函数 | 当前职责 | 本次施工点 |
| --- | --- | --- |
| `db.py: initialize_database` | schema v11、SQLite WAL、外键 | 增量迁移和高版本拒绝降级 |
| `parsing.py: run_parser` | 旧文本适配器及拆题 | 保留旧调用兼容，新增文件模型适配链路 |
| `repository.py: create_parse_task/run_parse_task/save_parsed_question` | 原卷、任务和逐题保存 | 新链路不调用旧的文本临时文件流程，不使用逐项内部 commit 拼批量事务 |
| `repository.py: create_question/update_question` | 题库兼容字段 | 新内容统一写入版本服务，禁止这些入口直接覆盖已绑定题的旧字段 |
| `repository.py: create_assessment_from_paper` | 生成测评快照 | 同事务固定正文版本和 child_key |
| `server.py: _do_POST/_read_payload` | 会话鉴权、JSON/表单请求 | 加上传路由和请求体限额，避免将二进制按 UTF-8 解码 |
| `learning_views.py: teacher/student/exams/export_wrong_book` | 当前结果制页面 | 接入整卷导入和统一内容渲染 |
| `learning.py: api('question'/'assessment'/'solution')` | 当前录题/组卷/解析 | 兼容手工补题；判对错用原规则；解析按绑定版本读取 |
| `exam_import.py/exam_views.py/exporting.py` | 旧考试包、资产、导出 | 旧记录继续可读；新题显示不能退回摘要或占位选项 |
| `assets/learning.js` | 结果制交互 | 新导入页用独立 JS；现有解析输出改为共用安全渲染 |
| `scripts/hsp_release_check.sh` | 发布核验 | 新 JS、worker、转换 smoke 检查需补入 |

### 1.3 实测样本

| 样本 | 路径 | 特殊点 |
| --- | --- | --- |
| A | `/Users/binyu/Documents/高三答题卡/第一次周测/高三第一次周测.docx` | 旧式公式/嵌入对象，题卷与其他内容分区 |
| B | `/Users/binyu/Documents/高三答题卡/第二次周测/2027届高三物理周测2.docx` | 原生 OMML 数学公式、图片 |
| B 答案 | 同目录 `2027届高三物理周测2_解析.docx` | 独立答案文件按题号关联 |
| C | `/Users/binyu/Documents/高三答题卡/9月联考/【物理试卷】2027届高三供题训练.pdf` | 六页扫描件，需 OCR、多栏及跨页验证 |

另准备有授权的传统 `.doc`、文本 PDF、旋转页和故意损坏文档。上述路径仅供开发样本，服务不得依赖开发机绝对路径。真实材料不进 Git；提交小型合成回归夹具和脱敏评估记录。

## 2. 先固定的技术决策

### 2.1 维持项目技术栈

继续使用 Python、SQLite、现有 HTTP 服务、服务端 HTML、原生 JavaScript。新增一个独立 Python worker 进程；第一版无需引入 Redis、Celery、新前端框架或额外数据库。

文件转换通过明确的 Python 适配器和受控子进程实现。生产已安装过的版本仅作线索，不等于运行可用。M0 必须在目标机记录转换器版本、命令、模型位置及输出 schema，以 lock 文件或精确版本清单固定，禁止部署时无版本上限升级整个解析栈。

### 2.2 模块分工

以下是拟新增模块，开发者可以拆分过长文件，但不能将所有逻辑塞入 `server.py`：

```text
highschoolphysics/
  document_store.py          # 原件、上传分片、资产、原子落盘
  document_ingestion.py      # 上传完成、任务创建、检查、批量入库事务
  document_worker.py         # 领取、租约、转换、取消、恢复
  document_models.py         # schema、校验、规范化哈希
  document_adapters/
    __init__.py
    docx_native.py           # 段落/表格/自动编号/OMML/图片锚点
    office_legacy.py         # DOC/嵌入对象的受控转换
    mineru_pdf.py            # 实际安装版本的明确输出适配
  question_splitter.py       # 版面顺序、题号、父子题、答案区关联
  question_content.py        # 版本、绑定、快照与历史纠错解析
  question_rendering.py      # 统一 Markdown/公式/资源解析
  question_export.py         # 单题/整卷 ZIP
  document_views.py          # 导入、任务列表、复核、题库详情
  assets/document-import.js
  assets/question-content.js
  assets/document-import.css
  assets/vendor/             # 数学引擎及其字体，版本与许可证清单
scripts/
  hsp_document_smoke.py       # 待实现：真实文件转换 smoke
  hsp_content_migrate.py      # 待实现：预览/应用/撤销历史纠错
  hsp_document_backup.py      # 待实现：一致备份和校验
  systemd/highschoolphysics-document-worker.service
tests/fixtures/documents/    # 合成小样本，不存学生资料
```

### 2.3 第一版默认限额

这些值是施工初值，M0 测试后可调整并记录理由，不是已测得的性能承诺。

| 项目 | 初值 |
| --- | --- |
| 原文件 | 每文件 50 MiB，最多 100 页 |
| DOCX 解压检查 | 累计 250 MiB，5000 个条目，拒绝路径越界/异常压缩比 |
| 上传分片 | 原字节 512 KiB；JSON 请求总长上限 1 MiB |
| 其他内容 API | 默认 2 MiB；大任务使用分页，不返回整卷 base64 |
| 任务并发 | 全局 1 个转换任务，每用户 2 个待处理任务 |
| 转换时间 | 单任务 20 分钟硬上限，阶段耗时独立记录 |
| worker | 5 秒扫描队列、10 秒心跳、60 秒租约；超时最多自动重试 2 次 |
| 导出 | 同步 ZIP 上限 100 MiB，超过上限明确提示按题分批，不截断内容 |
| 未完成上传 | 24 小时过期；清理前核对状态/租约 |

前端、HTTP、反向代理和 worker 限额必须一致。识别在独立进程执行，HTTP 只等待上传或短事务；不通过无限延长请求超时解决转换耗时。

## 3. 内容契约

### 3.1 中间文档模型 DocumentIR v1

转换器输出必须先适配成这个模型，不得让拆题器读取某个工具的私有 JSON：

```json
{
  "schema_version": 1,
  "document_id": "doc_...",
  "conversion_id": "conv_...",
  "source_sha256": "64位哈希",
  "pages": [{"page": 1, "width": 1200, "height": 1700, "rotation_applied": 0}],
  "blocks": [{
    "id": "p1-b001", "type": "paragraph", "page": 1, "column": 0,
    "order": 1, "bbox": [0.1, 0.1, 0.8, 0.15],
    "markdown": "1．某物体……$v=at$",
    "asset_ids": [], "source_locator": {"kind": "pdf", "page": 1},
    "issues": []
  }],
  "assets": [{"id": "asset_...", "sha256": "64位哈希", "mime_type": "image/png"}],
  "issues": []
}
```

约定：PDF page 对用户和内部模型均从 1 开始；bbox 为旋转校正后页面的归一化 `[x0,y0,x1,y1]`，原点左上，范围 0—1。适配器负责原坐标换算并保存变换。Word 原生段落可以 `page=null,bbox=null`，使用 `part=word/document.xml,paragraph_index,relationship_id` 定位；不得伪造页码。Word 转换副本有独立文件 ID，原始段落与渲染页映射另存。

块类型至少含 paragraph、heading、list_item、table、formula、figure、caption、header、footer。每个有效正文块都应归属题目或明确标记为非题目；未归属块进入复核队列。保留完整 `document.md` 与 IR JSON，两者使用相同块和资产清单。

### 3.2 完整大题 QuestionDocument v1

```json
{
  "schema_version": 1,
  "number": "11",
  "kind": "experiment",
  "stem_md": "用图示装置研究……\n\n![实验装置](asset:asset_a)",
  "options": [],
  "answer_md": "",
  "analysis_md": "",
  "answer_state": "missing",
  "grading_rule": null,
  "children": [{
    "key": "part_1", "label": "(1)", "kind": "fill",
    "stem_md": "周期为____。", "options": [],
    "answer_md": "$T=2\\pi/\\omega$", "analysis_md": "",
    "answer_state": "needs_review", "grading_rule": null,
    "source_spans": [{"block_id": "p3-b004"}]
  }],
  "source_spans": [{"document_id": "doc_...", "block_id": "p3-b001"}],
  "asset_refs": ["asset_a"],
  "issues": [{"code": "answer_unverified", "severity": "review", "field": "children.part_1"}]
}
```

规则：

- JSON 为唯一规范化结构，所有可编辑内容字段均为 Markdown；`question.md` 是从同一对象确定生成的可移植文本，不另外维护一份正文。
- 选项为有序数组 `[{"key":"A","markdown":"……"}]`，不得依赖字典排序。图片选项在自身 markdown 中引用图片。
- children 第一版为单层稳定作答项；复杂 `(2)①` 可扁平保存 `label="(2)①"`，公共条件留在父题或该项正文，不丢失语义。`key` 创建后不随题号或调序改变。
- `answer_md` 用于显示，`grading_rule` 用于判对错，不从渲染后的答案反推规则。规则必须兼容现有 `learning.check`；超出当前能力的规则禁止自动判分。
- 空答案可保存草稿；题目正文核实与答案核实是两个状态。只有内容、答案、标签均满足当前组卷要求的可作答项才可创建结果制测评。
- `source_spans` 每处指定文档/转换/块；多页多个 span，不用单个矩形假装覆盖跨页题。
- 内部 `asset:` URI 只允许资源解析器识别。导出变成 `images/文件名.png`；任何修改后的引用必须在当前用户有权的资产集合内。
- 哈希采用 UTF-8、LF、JSON 键排序、固定分隔符；不压平内容空白，不对公式/原始题号做破坏性规范化。

### 3.3 Markdown 编辑契约

默认提供按题干、选项、答案、解析切换的 Markdown 编辑器，每项均可多行编辑，旁边显示整题实时预览。另提供“整题 Markdown”模式，固定顶层章节 `## 题干`、`## 选项`、`## 小问`、`## 答案`、`## 解析`，选项和小问通过稳定 ID 标记关联。

实现 `serialize_question_md(document)` 与 `parse_question_md(text, original_document)`；先测试无损往返。整题编辑丢失/重复章节或 ID 时显示具体错误，保存为浏览器草稿但不能覆盖服务端结构。元数据、来源和评分规则不通过正文猜测；不把用户输入当可执行模板。

整题模式的题干中同名二级标题需转义或降为三级；编辑器说明这项约定。从预览切回编辑不得丢失换行、LaTeX 反斜杠、图片位置或表格。

## 4. 数据库与存储施工

### 4.1 表的职责和关系

附带 SQL 创建以下新表：

| 表 | 唯一职责 |
| --- | --- |
| document_files | 原文件及转换副本元数据 |
| document_uploads / document_upload_parts | 可恢复分片与完成凭据 |
| document_conversions / conversion_asset_refs | 某次转换不可变产物及来源资源 |
| document_assets | 学校内图片字节资源 |
| question_content_groups / question_content_revisions | 完整大题及其不可变版本 |
| content_asset_refs | 内容版本到图片的引用，用于授权和保留 |
| question_content_bindings | 现有 questions 作答项到完整大题/子项的映射 |
| snapshot_content_bindings | 创建测评时固定的正文版本 |
| historical_content_corrections | 历史快照的显式显示纠错，可撤销 |
| import_item_publications | 拆题候选的幂等发布记录 |
| content_operation_keys | 更新/确认等写操作的请求幂等结果 |

跨表校验必须包含 school_id 和所有者关系。外键只能证明 ID 存在，不能证明学校相同；服务层在事务内校验，测试必须覆盖合法 ID 的跨学校组合。新服务不能仅依赖 `_require_question_bank_actor`，该方法只检查角色。

`question_content_groups.current_revision_id` 指向同组版本，由服务检查；创建时先插组（指针 null），插版本，再更新指针。内容版本提交后不可原地 UPDATE 正文，修改必须新建版本。旧版本及其资产有快照或导出引用时永不清理。

### 4.2 旧表增量字段

使用 `_ensure_column` 对下列字段逐一添加，已存在时跳过；不要在旧库重复执行裸 ALTER。除特别说明外均 nullable，兼容旧任务和题目：

```python
ADDITIONS = {
    "document_parse_tasks": [
        "input_document_id text references document_files(id)",
        "phase text not null default ''",
        "progress_json text not null default '{}'",
        "generation integer not null default 1",
        "attempts integer not null default 0",
        "lease_token text", "lease_until text", "heartbeat_at text",
        "cancel_requested integer not null default 0",
        "error_code text not null default ''",
        "available_at text", "updated_at text",
        "conversion_id text references document_conversions(id)",
        "created_by text references users(id)",
    ],
    "parsed_question_items": [
        "conversion_id text references document_conversions(id)",
        "document_json text", "review_revision integer not null default 1",
        "issues_json text not null default '[]'",
        "disposition text not null default 'active'",
        "updated_by text references users(id)", "updated_at text",
    ],
}
```

增加队列索引 `(status,available_at,lease_until)`、候选索引 `(conversion_id,disposition,item_index)`。新候选的旧 `stem/options_json/answer_json` 字段填兼容投影；`document_json` 不为空的候选以新模型为准。旧接口收到新候选/新绑定题目必须重定向到新编辑流程或返回明确冲突，不允许双写分叉。

旧 `parsed_question_items` 有 `unique(parse_task_id,item_index)`：一次成功转换后不在原 task 上覆盖重跑。用户要求重新转换时新建 task 和新 conversion；旧候选、教师编辑、已入库版本继续保留。失败但尚未产出候选的 task 可以重试，generation 用于任务执行尝试的产物隔离。

### 4.3 迁移顺序与保护

1. schema 12 是本基线建议目标；若开工时已有其他迁移，重新分配递增版本并更新测试。
2. 迁移前读取 `pragma user_version`。高于程序支持版本立即拒绝启动，不能把高版本改写回低版本。
3. 为 v11 数据库制作 SQLite 在线备份及文件资产备份。在副本执行迁移、重启和回归。
4. 将现有初始化逻辑拆出版本迁移，确保 `BEGIN IMMEDIATE` → 新表 → `_ensure_column` → 索引 → 外键/完整性检查 → 最后设置 user_version → COMMIT。
5. 生产迁移执行器不要调用会隐式提交的 `sqlite3.executescript`。附件可使用 `sqlite3.complete_statement` 分离完整语句后逐条 execute；不能用简单按分号切字符串处理通用 SQL。
6. 任一语句失败则 ROLLBACK；再启动应可继续或重新完整执行。不得 seed demo 到生产库。
7. 保留旧表/列；新功能关闭时旧题照常可读。新库回退运行旧提交会被旧初始化代码写回 user_version，因此必须准备支持 v12 的兼容回退版本，不能直接检出 `6c79ab00` 启动。

### 4.4 文件布局与原子落盘

拟配置 `HSP_DOCUMENT_ROOT`，默认在数据库父目录的 `documents/`，测试可注入临时目录：

```text
data/documents/
  schools/<school_id>/
    originals/<doc_id>/source.docx
    assets/<sha256-prefix>/<sha256>.png
    conversions/<conversion_id>/document.md
    conversions/<conversion_id>/layout.json
    conversions/<conversion_id>/manifest.json
  staging/<upload_id>/part-000001
  work/<task_id>/<lease_token>/
```

路径由服务生成，不使用用户文件名拼路径。先写同文件系统临时文件、flush/fsync，再 rename；确认哈希和大小后写 DB 引用。数据库提交前资源已经持久存在；崩溃可产生未引用文件，不能产生已发布但文件尚未落盘的记录。清理只处理超过保留期、无引用、无活跃租约的 staging/work/孤立对象。

同校重复字节可复用资产；跨校不能通过可猜哈希访问。图片按解码后的真实 MIME 校验；第一版渲染 PNG/JPEG/WebP，SVG/EMF/WMF 转成安全栅格显示副本并保留来源。转换参数和尺寸进 manifest，避免误把整页当题目插图。

## 5. 上传与 HTTP 接口

### 5.1 上传实现选择

第一版使用受限 JSON base64 分片，以兼容当前 HTTP 栈；浏览器读取文件字节并按分片发送，教师仍只做“选择文件”。不要调用 `File.text()` 转 Word/PDF，不把原文件解码成 UTF-8，也不要复用已被结果制禁用的 `/api/exams/upload-chunk`。

在 `_read_payload()` 读取之前按路由检查 Content-Length、Content-Type 和最大请求体；无长度或长度非法、超限时明确拒绝，不进行无限 read。分片解码使用严格 base64 校验，最终验证文件魔数和容器结构。中文文件名仅作为元数据。

### 5.2 接口表

路径为服务内部路径；浏览器 URL 使用页面提供的 base URL，覆盖 `/physics/` 部署。统一复用现有 SSO 会话。

| Method / 路径 | 输入要点 | 返回/用途 |
| --- | --- | --- |
| POST `/api/documents/uploads` | name,size,sha256,role,title,request_key；答案文件带 original_paper_id | upload_id、chunk_size、total_parts、状态；重复返回原上传 |
| POST `/api/documents/uploads/{id}/parts` | index,sha256,data_base64 | 已接收序号；同序号不同字节返回 409 |
| GET `/api/documents/uploads/{id}` | — | received_parts、expires_at；不返回其他用户上传 |
| POST `/api/documents/uploads/{id}/complete` | request_key | 原文件持久化，创建原卷/批次/任务，202 返回 task_id |
| POST `/api/documents/uploads/{id}/cancel` | request_key | 标记取消，重复无副作用 |
| GET `/api/documents/tasks` | cursor,limit<=50 | 当前教师自己的任务；管理员可筛同校 |
| GET `/api/documents/tasks/{id}` | — | status,phase,进度,错误,review_url |
| POST `/api/documents/tasks/{id}/retry` | request_key | 未成功任务重试；有成果则建立新任务 |
| POST `/api/documents/tasks/{id}/cancel` | request_key | 排队即取消，运行中请求合作终止 |
| GET `/api/documents/tasks/{id}/items` | cursor,limit<=50 | 候选与 issue 汇总 |
| POST `/api/documents/items/{id}/save` | expected_revision,document,request_key | 校验后保存、review_revision+1 |
| POST `/api/documents/items/restructure` | action,ids,expected_revisions,split_anchor/ordering,request_key | 合并/拆分/调序；返回新候选和废弃关系 |
| POST `/api/documents/tasks/{id}/attach-answers` | answer_task_id,expected_item_revisions,request_key | 同一原卷答案任务与题卷候选关联，歧义进入复核 |
| POST `/api/documents/tasks/{id}/confirm` | items[{id,expected_revision}],request_key | 批量事务入库，返回完整大题/作答项 ID |
| POST `/api/questions/content/{group_id}/save` | expected_revision_id,document,reason,request_key | 新建不可变内容版本 |
| POST `/api/questions/content/preview` | document 或 revision_id | 服务端校验后的同源安全预览片段 |
| GET `/document-source?id=...&page=...` | 原件或渲染页 ID | 教师原卷对照，鉴权，不公开静态目录 |
| GET `/question-asset?id=...&revision=...` | 资源和内容上下文 | 与当前角色/测评发布状态相符才返回字节 |
| GET `/export/question-markdown?revision=...&child=...&solution=0` | 指定不可变版本 | ZIP；教师可 solution=1 |
| GET `/export/paper-markdown?paper_id=...&solution=0` | 按原卷顺序选择当前确认版本 | 整卷 ZIP，manifest 固定本次选择的所有版本 |

成功统一 `{ "ok": true, "result": ... }`；错误兼容现有 `{ "error": "code", "message": "中文说明" }`，可扩展 `details` 为字段错误，不返回堆栈或原件路径。409 用于编辑冲突/幂等键内容变化，413 超限，415 格式不符，422 内容无效，503 转换依赖不可用。轮询接口是 200，后台任务失败通过 `status=failed` 表达。

### 5.3 上传完成与幂等

上传初始化时校验总大小、分片数、格式后创建记录。相同学校/用户/request_key 且 request_hash 相同返回原记录，否则 409。分片文件先独立落盘，再短事务登记；并发重复上传通过唯一键核验哈希，禁止互相覆盖。

complete 通过短事务将 uploading 改为 assembling 并分配 assembly_token 和租约；事务外顺序拼接、校验文件 SHA/真实格式；最终短事务再次核对 token，创建 document_files、original_papers、question_import_batches、document_parse_tasks，更新 completed。失败不留下部分题目；complete 重试返回同一个 document_id/task_id。assembling 超时可由维护过程回收，旧 token 不能提交。

同一文件的不同 request_key：默认提示已有同文件导入，可选择打开已有任务或显式创建新版本；文件哈希去重不等于题目唯一性判断。文件字节相同但用途/答案角色不同可以建立独立逻辑引用。

### 5.4 权限边界

上传、转换草稿、原卷对照仅创建教师和同校管理员可访问；确认入库后的题目按现有同校题库共享。共享题目修改沿用同校教师权限但必须 CAS 版本检查和审计。跨校均拒绝；学生没有上传、复核、原卷或草稿下载权限。

学生资产必须从其有权查看的已发布测评/错题/内容版本反向授权；不能只判断资源在同校。source_page/formula_evidence 原始资源与可展示 figure 分开授权。原卷可能包含答案，不能给学生开放原卷页。

对于学生 `solution=1` 的导出第一版统一拒绝；教师默认无答案导出，可勾选包含答案。学生学习解析仍走现有显式查看动作，保持当天学习/验证状态规则；不得为预览/预取解析意外改变练习用途。

原卷上传 role=paper 时创建 original_paper；role=answers/rubric 时必须指定自己可访问的 original_paper_id，形成独立文档与转换任务。答案任务不产生可独立发布的题库题目。attach-answers 只修改未发布且版本匹配的题卷候选：先输出匹配候选，唯一匹配写入答案待复核状态；不覆盖教师已经核实的答案，冲突列入 issues。已入库题目的答案更新必须走新内容版本，已有测评评分快照保持不变。

拆分接口 split_anchor 使用 block_id 以及该块 Markdown 的字符偏移，偏移基于服务端当前文档版本，不能使用屏幕像素。merge 按请求的候选顺序合并并保留所有 source_spans；同一资源可去重，题号和答案冲突保留待核实。调序只重排显示位置；写入旧 item_index 唯一索引时需在事务中先分配临时不冲突序号再写最终序号。重构必须产生可逆的审计映射，不能删除已发布候选。

所有写路由遵循同源会话保护：增加 Origin/CSRF 校验并与现有登录方式测试，不能因新增路径跳过用户状态检查。不得把生产会话写入 QA 报告。

## 6. Worker 与任务状态

### 6.1 状态机

沿用 document_parse_tasks：`queued → running → parsed / partially_parsed / failed / cancelled`。phase 独立取 `inspect,convert,collect_assets,split,validate,complete`。parsed 表示转换及拆题产物已经持久化、可以复核，**不表示已入库**；入库状态从候选和 publication 计算。

`partially_parsed` 表示部分块有可用结果、部分失败；UI 显示失败页及缺失范围。没有任何有效题目时 failed。取消不是 failed；允许查看已保存原文件，但不能把临时产物发布。

### 6.2 领取与恢复算法

```text
BEGIN IMMEDIATE
找到 input_document_id 非空、queued 且到 available_at 的任务
检查 attempts 上限及 cancel_requested
设置 running、唯一 lease_token、lease_until、attempts+1
COMMIT

事务外运行适配器，worker 心跳线程续约
子进程与任务一个进程组；取消/超时先终止再强制回收
收集、验证、持久化产物到本 token 工作目录

BEGIN IMMEDIATE
再次核对 status、lease_token、cancel_requested、generation
插 conversion、资源引用、候选；更新 task 和批次计数
COMMIT
```

每个 DB 更新线程有独立连接；不能跨线程共用默认 SQLite connection。转换期间不持有写事务。lease_token 作为 fencing token：租约被回收后旧 worker 即便最终成功，也不能提交成果。

重启扫描过期 running：尝试终止该实例已知的子进程（先核验实例标识，不能凭过期 PID 误杀）；超过重试次数则 failed，否则回 queued 并 backoff。不能仅有心跳就绕过总执行超时。退出 SIGTERM 时停止领取，给当前工作可控退出时间。

### 6.3 可观察性

任务记录输入哈希、适配器版本、页数、阶段时间、字节数、题数、未归属块数、公式/图片警告数、失败类别。日志只放 task_id、阶段、错误摘要；原卷全文、用户敏感信息及 API 密钥不入普通日志。

错误码至少包括 `unsupported_format,invalid_container,encrypted_document,dependency_missing,model_unavailable,conversion_timeout,invalid_adapter_output,empty_document,unresolved_assets,split_failed,storage_full,cancelled`。不存在可靠页级进度的适配器只显示阶段和已耗时，禁止伪造百分比。

## 7. 转换与拆题实现细则

### 7.1 M0 技术验证先于大量页面开发

在隔离测试目录对 A/B/C 和传统 DOC 做真实转换，保存原件哈希、实际命令/配置、输出文件清单、耗时、内存峰值、关键公式和插图对照。不能先宣布某工具适用再等上线时发现模型未安装。

默认本机/部署机受控解析，不自动上传第三方云。若安装的工具无法满足要求，开发者记录失败样例，选择可部署替代实现并复测；不得以“依赖安装成功”关闭 M0。新增 GPU/外部服务或付费依赖需要先形成资源/成本方案，不借本任务停用其他生产服务。

### 7.2 DOCX

按 OOXML 正文顺序遍历段落和表格，解析 numbering/styles 得到可见自动题号；处理正文中绘图、VML 和 relationship 关系。图片提取同时建立引用点，不能将所有图片顺序附加到文末。识别浮动图与正文锚点，存在歧义则提供候选位置供复核。

OMML 使用经过样本验证的转换组件转 LaTeX，不从 `w:t` 拼接当完整公式。记录输入公式节点数、成功转换数和未覆盖节点；每个失败点有原公式证据。脚本/样式不进入正文。

OLE/MathType 或无法处理的矢量对象：受控 LibreOffice 渲染副本后按版面/公式识别。禁止执行宏和远程引用；转换临时用户配置独立，命令不使用 shell 拼接。原文件与副本都保留哈希及来源关系。旧 DOC 通过同一路径标准化。

### 7.3 PDF

先检查加密、损坏、页数、旋转和文字覆盖率；文字层只作为线索。原生文本页与扫描页可分别处理，但输出相同 IR。MinerU 适配器明确读取已验证文件名称/schema，验证每页覆盖情况；禁止取“第一个 JSON”。

多栏先恢复栏内顺序，再按主布局拼接；页眉页脚和页码不是题干。扫描识别必须保留中文、上下标、单位、负号、分式、根号、矢量和图形对应。示意图可以裁取，但裁图对象应是插图或公式证据，不能以页面切条替代全部正文。

### 7.4 拆题

拆分器输入 IR，输出候选完整大题，不直接创建 questions。处理顺序固定：

1. 分区：题目、参考答案、解析、答题卡、页眉页脚。
2. 识别大题号与章节上下文；兼容半角/全角点、顿号、自动编号及无空格。
3. 将题号后的连续块归到该题；识别选项 A—H，保留多行/同一行多选项及图片选项。
4. 将跨栏/跨页续题合并；跨页的选项或插图不得丢失。
5. 提取小问与作答空，同时保留完整公共题干；题号作为显示字段，不作唯一数据库键。
6. 答案文件按原卷、章节、题号关联；重复题号/选择题答案表/解析内重新编号均要消歧。未能唯一关联则待确认。
7. 输出覆盖检查：正文块未归属、同块异常多归属、题号重/漏、选项缺失、孤立图、公式失败等。

规则优先提供可解释结果；可选视觉/语言模型只生成有来源块的候选，不允许根据常识补题。必要字段必须经过 schema 校验，模型返回指令不能作为系统命令执行。

### 7.5 复核门槛

issue severity 分 `blocking,review,info`。缺正文、资源断链、无法确定来源、已知公式丢失为 blocking，不得确认。边界/题号/答案归属不确定为 review，需要教师逐项解决或注明核对结果；不是点一个“全部忽略”即可发布。原卷没有答案为信息缺失，可入库为内容已核实/答案缺失版本，但不能自动判对错。

复核操作与 source_spans 一起保留；内容修改后重新计算 issues 和 asset_refs。不能让修改题号后保留旧的“已核实”状态而不记录修订。

## 8. 批量入库与版本衔接

### 8.1 入库事务

confirm 请求携带所选候选 ID、各自 expected_revision 和一个 request_key。处理顺序：

```text
BEGIN IMMEDIATE
检查角色、同校、任务所有权、候选版本、disposition 和 issues
检查幂等记录；相同请求已完成则直接返回旧结果
对每个未发布候选：
  创建完整大题 group + revision
  为可作答根题/child 创建 questions 兼容记录和绑定
  生成 asset_refs，保存标签候选/已确认标签
  插 import_item_publications，更新候选 saved 状态
更新 batch item_count/saved_count，写 audit、幂等结果
COMMIT
```

所选集合全成功或全回滚。教师可以选择一部分题先确认，未选中的保持草稿。重试同一集合不重复建题；同候选再次确认返回原 publication，若它已改变则要求新修订，不静默重复发布。

confirm 可为每项附 `confirmed_tags={knowledge_node_ids:[],ability_tag_ids:[],literacy_tag_ids:[]}`，服务沿用现有 active 标签和每类最多三个的校验；未确认标签允许入内容库，但不满足组卷条件。既有 parsed_question_items.saved_question_id 对单项题写对应 ID，对多小问题只可保留首项作为旧界面兼容指针，完整列表始终从 import_item_publications.question_ids_json 读取；新逻辑不得把首项当整个大题。

现有 repository 方法多处内部 commit：应提取事务内无 commit 的底层方法，或建立统一显式 UnitOfWork；禁止在外层 BEGIN 后调用会 commit 的 create_question 假装原子。不要把临时连接包装技巧扩散成不可见事务行为。

### 8.2 父题与现有 questions

单选/多选独立题：group + revision + 一个 questions 行，child_key 为空。
实验大题：group + revision + 多个 questions 作答项，child_key 稳定对应。父题作为内容容器，不额外计入作答数。
纯主观大题：创建内容组，允许展示和导出；当前结果制无法支持的作答项不加入选择/填空组卷列表。

兼容字段 stem/options/analysis 从规范内容投影完整 Markdown，不再写“见原题图”等占位内容。现有旧视图须路由到公共 renderer，不能将 Markdown 当普通字符串显示给学生。

### 8.3 测评快照和后续编辑

创建测评时，同事务写 `question_version_snapshots` 和 `snapshot_content_bindings`。后续题库编辑只推进 group.current_revision_id，已发布测评继续使用当时版本。answer_json/grading_rule_json/tag_snapshot_json 仍按原规则固定。

内容读取优先级：历史纠错（若 active，且仅允许显示字段）→ snapshot_content_bindings → 旧 snapshot + exam_assets。题库页面则读 group.current_revision_id。这个规则集中在 `resolve_question_content`，各页面不得自行选择最新版本。

历史纠错返回旧评分答案作为权威；更正内容的 answer_md/analysis_md 不能悄悄替换已发布参考答案。若解析依赖被纠正的答案，要阻塞该解析显示并进入单独复核，不在正文迁移中改判。

## 9. 页面与统一渲染

### 9.1 页面布局和状态

教师工作台主入口“导入整份试卷”，手工录题收在“补充录入”。新 `/documents` 页显示任务列表和导入按钮，`/documents/review?id=...` 显示复核工作区。

桌面：左侧原卷、右侧题目编辑/预览，上方显示当前题号、页/块位置、已确认数和异常数。点击题目同步定位原卷，点击图片可查看来源和调整归属。390px 屏幕切换“原卷 / 编辑 / 预览”标签，工具栏可达，不把两栏硬挤并排。

必须有：未选择文件、上传中、暂停/重试、转换排队、处理中、部分失败、完全失败、待复核、保存中、编辑冲突、已入库等状态。编辑保存失败保留本地草稿；任务服务端状态刷新后仍可继续。批量确认前显示“完整大题数 / 可作答项数”，避免把 19 个分项误报成 19 道大题。

合并/拆分事务采用新候选 + 旧候选 disposition=superseded；记录原 ID 与新 ID 到审计。已发布候选不允许重构，须在新修订/新导入任务中处理。题目调序不改变稳定 child_key，未关联块仍在待处理列表。

### 9.2 公共渲染

服务器使用已固定版本的 Markdown renderer，关闭原始 HTML，并以允许列表生成链接/图片。数学语法统一 `$...$` 与 `$$...$$`；物理正文内普通美元符号需转义。数学库使用本地固定版本，禁用信任模式和危险宏，错误公式保留源码并显著提示。

题库、教师考试页、学生错题页、练习控件选项、答案解析和学习单使用相同渲染函数。学生序列化结果先投影为不含答案的对象，再渲染；不能仅 CSS 隐藏答案。所有图有 alt，宽度自适应，点击可放大。

现有 `_serve_asset` 只取 Path.name 且仅区分 CSS/JS，不能直接服务 vendor 子目录和字体。必须增加安全的相对路径解析、路径包含检查、真实 MIME 和字体类型；测试 `../`、编码穿越、不存在文件。不要为加载数学字体把整个项目暴露为静态目录。

### 9.3 性能与兼容

每页候选分页，整卷图片懒加载；轮询 2 秒起、后台页降频、有终态停止。转换任务不阻塞正常学生作答。缓存键含内容版本、角色可见性、渲染器版本；鉴权资产使用 private 缓存策略，不将学生权限结果共享缓存。

## 10. Markdown ZIP 导出

### 10.1 单题包

```text
question-001/
  question.md
  images/figure-001-<hash8>.png
  source.json
  README.md
```

question.md 以 UTF-8/LF 保存，标题、题干、选项、小问完整。solution=0 排除答案/解析；solution=1 仅授权教师可用。子项导出默认附带完整公共题干和所有必要图，并突出当前小问，不导出孤立填空句。

source.json 包含 schema、原文件名及哈希、原题号、来源页/块、内容版本号/哈希、图片文件及哈希、是否含答案。不得包含学生/答题卡、内部存储路径、会话、供应商配置或学校内无关数据。

### 10.2 整卷包

包含 `paper.md`、`questions/001/question.md` 等分题文件、每题所需图片、`manifest.json`。若 paper.md 复用分题图片，引用写为 `questions/001/images/...`。manifest 固定题目顺序和每题版本，并显式列出未确认/被排除的项目；默认整卷导出只包含已确认内容，下载前说明排除项数量。

### 10.3 实现要求

通过 Markdown AST 识别图片引用（包括链接式引用），不对全文做不可靠字符串替换。资源哈希、扩展名、路径生成一致；同名不同图不得覆盖。打包前遍历所有引用，缺一个即失败，不能生成残缺成功包。

ZIP entry 禁止绝对路径/`..`，中文下载名按标准 Content-Disposition 编码并提供 ASCII fallback。导出固定同一组内容版本后再读资源，不在打包中途读取“最新版本”。解压到随机临时目录后，在断网或拦截生产域名状态下验证图片/公式；公式兼容支持 LaTeX 的阅读器，README 说明语法。

## 11. 旧题迁移与错误图片修复

### 11.1 执行顺序

新导入链路通过验收后再迁移。只读盘点两个已入库考试的 questions、snapshots、responses、wrong_questions、redo_attempts、标签和 exam_assets，生成基线指纹。以实际库为准，不硬编码“当前必须为 36 条”。

重新导入原试卷为候选，复核完整内容，再创建映射清单。禁止按 SQL 行顺序、文件名或摘要关键词自动替换。已证实联考第 1—4 题错误图片哈希见设计文档；其他题逐项核对。

### 11.2 映射文件示例

```json
{
  "schema_version": 1,
  "migration_key": "content-correction-20260928-01",
  "baseline_manifest_sha256": "...",
  "entries": [{
    "old_question_id": "q-...", "snapshot_ids": ["snap-..."],
    "expected_old_content_hash": "...", "expected_old_asset_hashes": ["..."],
    "new_revision_id": "rev_...", "child_key": "",
    "verified_original_number": "1",
    "reason": "原图实际为第5—7题，依据原卷第1题纠正",
    "reviewed_by": "实际复核者ID", "reviewed_at": "实际时间"
  }]
}
```

脚本预览比对旧内容哈希与快照归属，输出受影响测评/题目数及新旧图片预览，任何映射不唯一或原库已变化就停止。apply 只写历史显示纠错记录及审计，不 UPDATE 首次作答/判定/评分规则，不删除旧图。旧 questions 记录不强行合并或重编 ID。

新增题库版本可单独建立，历史 snapshot 通过 correction 关联；旧题“编辑后供未来组卷使用”的活跃绑定是另一步，需确认父子关系和唯一性，不能混在显示修复中隐式修改。

### 11.3 迁移工具契约（待实现）

```bash
python3 scripts/hsp_content_migrate.py preview --db PATH --mapping FILE --output DIR
python3 scripts/hsp_content_migrate.py apply --db PATH --mapping FILE --expected-preview-sha256 HASH
python3 scripts/hsp_content_migrate.py verify --db PATH --mapping FILE --baseline DIR
python3 scripts/hsp_content_migrate.py rollback --db PATH --migration-key KEY --reason TEXT
```

apply 只接受对应预览哈希、同校管理员身份和实际复核记录；CLI 使用受控运维身份及显式 actor_id 参数（实现时四个命令均补上），不得伪造创建者。同 migration_key+snapshot 只能出现一次；第二次 apply 返回已应用结果。不同内容重用键必须冲突。

撤销只将指定迁移的 active 纠错记录改 revoked，保留审计，不恢复整库，不覆盖迁移之后新增的学生作答。验证保护表所有既有记录内容和 ID 一致，除预期纠错与审计外无变更；正常并发新作答单独计入而非误判为漂移。

## 12. 分阶段施工任务

每个任务必须交付代码、针对性验证和证据。阶段之间不以“代码已写”作为通过条件；必须达到退出标准。

| 阶段 | 施工内容 | 依赖 | 退出标准 |
| --- | --- | --- | --- |
| M0 | 恢复现场、样本盘点、转换器真实探测、版本/性能记录 | 无 | A/B/C/DOC 的可行路径明确，公式和插图没有被静默丢弃 |
| M1 | 模型校验、schema 迁移、资源存储、版本服务 | M0 | 空库/v11/重复启动/失败回滚通过；已存旧记录一致 |
| M2 | 公共渲染、单题/整卷导出 | M1 | 合成复杂题显示与 ZIP 同源，离线引用完整，答案隔离通过 |
| M3 | 上传、异步 worker、Word/PDF 适配器和产物持久化 | M0/M1 | 从文件选择到完整 IR/Markdown/图片；重启恢复和限额通过 |
| M4 | 拆题、来源映射、复核编辑、批量入库 | M2/M3 | 普通教师可完成全流程，无逐题重新录入要求 |
| M5 | 现有题库/测评/错题/练习/学习单接入、快照绑定 | M4 | 新旧内容并存，首次作答、答案可见性、标签规则无回归 |
| M6 | 两批旧题修复、预览/幂等迁移/撤销 | M5 | 逐题映射审阅通过，错图修复且历史记录一致 |
| M7 | 部署、真实浏览器与样本验收、运维交接 | M1—M6 | 所有强制验收项通过并记录实际证据 |

建议提交边界：模型与迁移；资源与上传；worker 与适配器；拆题和复核；渲染和导出；业务接入；迁移工具与内容修复记录；发布与运维。每个提交围绕单一可验证行为，避免一次提交混入所有未验证能力。

### M0 交付清单

- [ ] 输入样本 SHA、页/题范围、公式/插图特殊点。
- [ ] 目标环境实际转换结果与错误记录；不能只给 pip list。
- [ ] 固定依赖和模型/字体部署清单、预期磁盘与内存使用。
- [ ] 传统 DOC 与旧 OLE 处理决定及未覆盖类型。

### M1—M3 交付清单

- [ ] 本文 SQL/字段迁移落地为版本迁移，生产回退兼容策略已测试。
- [ ] 对象存储原子落盘、资源权限、资产引用与备份恢复测试。
- [ ] Markdown 序列化往返、公式及多图渲染和 ZIP 校验。
- [ ] 上传断点、同分片冲突、重复 complete、取消和过期回收。
- [ ] Worker 租约 fencing、崩溃恢复、超时、缺模型、磁盘不足错误路径。

### M4—M7 交付清单

- [ ] 原卷定位准确；合并/拆分/CAS 冲突/草稿恢复都有浏览器证据。
- [ ] 批量入库事务和幂等；不可作答大题仍保存在内容库。
- [ ] 所有展示入口使用公共 renderer，旧写 API 不绕过版本。
- [ ] 历史题目预览对照、迁移保护指纹、撤销演练。
- [ ] 发布检查、远端真实文件导入、下载离线检查、交接报告。

## 13. 测试与验收方法

详细用例见配套验收文件。自动化覆盖应验证行为和失败边界，不只断言字符串包含“上传”。推荐新增测试模块：

```text
test_document_models.py       # 内容 schema、往返与来源
test_document_migrations.py   # 升级/回滚/版本保护
test_document_store.py        # 文件原子性、限额、路径
test_document_http.py         # 普通教师真实 API/权限/重试
test_document_worker.py       # 两 worker 竞争、旧 token、取消
test_document_adapters.py     # 输出 schema 与真实小夹具
test_question_splitter.py     # 全角、跨页、多栏、答案区
test_question_content.py      # 版本/快照/纠错优先级
test_question_export.py       # ZIP 相对路径和答案隔离
test_content_migration.py     # 幂等与历史保护
```

现有 `tests/http_support.py: LivePhysicsServer` 可作为 API 集成基座。测试使用临时 SQLite 和文档目录，创建教师/学生/他校教师；生产 SSO 不需被替换，测试会话只用于测试库。

开发者实现后运行（当前文档交付不执行未存在测试）：

```bash
python3 -m unittest tests.test_document_models tests.test_document_migrations tests.test_document_store -v
python3 -m unittest tests.test_document_http tests.test_document_worker tests.test_document_adapters -v
python3 -m unittest tests.test_question_splitter tests.test_question_content tests.test_question_export tests.test_content_migration -v
node --check highschoolphysics/assets/document-import.js
node --check highschoolphysics/assets/question-content.js
VERIFY_TARGET=local bash scripts/hsp_release_check.sh
```

按环境使用项目明确的 Python；不要为了通过命令污染系统 Python。待新增 `hsp_document_smoke.py` 输入 `--file --output --adapter auto --timeout`，输出机器可读 manifest 和 Markdown/图像用于人工核对；退出 0 仅证明产物检查成功，不替代原题正确性审阅。

真实样本逐题核对：题号、完整题干、各选项、每个关键公式、图示归属、跨页条件、答案来源。发布版本要求这些已核对项无已知遗漏/错配、资产引用 100% 可解析；自动首遍通过率和平均复核时间作为测量结果报告，不预先宣称 100% OCR 准确率。

## 14. 部署与回滚施工

### 14.1 功能开关（待实现）

`HSP_DOCUMENT_INGESTION_ENABLED` 控制新上传和任务入口；`HSP_DOCUMENT_WORKER_ENABLED` 控制领取；已发布内容渲染和导出不能因关闭上传而停止。历史纠错应用通过单独迁移工具，不能在服务启动时自动批量改题。

### 14.2 Worker systemd 模板

以下是安装前需依据现场 Python 和目录确认的模板：

```ini
[Unit]
Description=HighSchoolPhysics document conversion worker
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/yub/Documents/trae_projects/HighSchoolPhysics
Environment=HSP_DOCUMENT_ROOT=/home/yub/Documents/trae_projects/HighSchoolPhysics/data/documents
ExecStart=/usr/bin/python3 -m highschoolphysics.document_worker --db data/school.sqlite3
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
UMask=0077

[Install]
WantedBy=default.target
```

依赖若位于 venv，ExecStart 改成实际 venv Python；不要照抄 `/usr/bin/python3` 然后宣称可运行。内存上限根据 M0 实测设置，不能写一个无法运行模型的值。user-level systemd 与现有应用服务同属明确运维用户。

### 14.3 发布步骤

1. 本地测试、真实样本复核通过，提交代码与固定依赖；检查 Git main 和自动更新计时器。自动 timer 会立即拉 main，发布窗口必须避免它在迁移/依赖准备中间启动半成品。
2. 在明确维护窗口暂停自动更新 timer，记录原启用状态，停止领取转换任务并等待/终止到安全点。不得误停其他项目服务。
3. 备份 SQLite（使用 SQLite backup API 或停写后的完整备份）、文档资产和部署配置，保存哈希；完成一次副本恢复及引用校验。不能仅 cp 正在 WAL 写入的主数据库文件。
4. 准备固定依赖/模型/字体，部署经过验证的提交；先以新上传关闭状态迁移 schema，启动兼容新旧内容的服务和 worker。
5. 验证数据库、原有学生页面、SSO、资源和 worker；打开教师测试入口，先用无学生数据的合成卷进行在线 smoke，再执行真实题卷导入验收。
6. 普通教师完成选择文件、复核、确认、查看、导出；覆盖直连与 `/physics/`。在隔离测试班级验证学生显示，不能向真实学生发布测试考试。
7. 执行并保存 `REQUIRE_REMOTE_HEAD_MATCH=1 bash scripts/hsp_release_check.sh`。扩充脚本以检查新增 JS 及 worker 的提交/进程；服务运行旧代码即使 checkout 更新也不能通过。
8. 两批历史内容修复另走预览→应用→验证；不因新功能上线自动视为旧题修好了。
9. 恢复 timer 原状态，确认后续 auto-update 也能重启 worker 并验证同提交运行。维护结束记录服务、HEAD、实际 URL、产物哈希。

### 14.4 回滚分类

| 故障 | 操作 | 不能做的事 |
| --- | --- | --- |
| 新上传/识别不可用 | 关闭新入口和 worker 领取，保留已上传原件及已发布内容阅读 | 不删除 data/documents 或清空队列 |
| 新代码回归 | 部署预先验证过的 v12 兼容回退提交，保留新表；重新验证旧页面与新内容读取 | 不直接跑会降 user_version 的旧 v11 初始化 |
| 某批历史正文错误 | 撤销对应 migration_key 的纠错映射 | 不恢复整库覆盖新增学生作答 |
| schema 迁移失败 | 同事务回滚，检查副本与日志，修复迁移后重试 | 不手工删列/表继续凑合启动 |
| 严重数据库/磁盘故障 | 停写并恢复经过验证的一致备份，核对备份后的业务增量 | 不承诺恢复备份等于零数据损失 |

## 15. 开发完成时的交付包

开发者必须提供：

1. 提交 SHA、变更模块、依赖/模型版本、schema 版本。
2. 三份原卷及传统 DOC/文本 PDF 的导入结果和逐题核对表。
3. 普通教师桌面与 390px 操作证据、学生页面证据，注明未做物理设备验证的部分。
4. 至少一份包含插图/公式的单题 ZIP、一份整卷 ZIP；解压离线检查结果及哈希。
5. 旧题修复映射、应用前后保护指纹、撤销演练结果。
6. 自动化测试、完整发布检查、worker 及线上真实导入日志摘要。
7. 已知限制、待复核内容、运维恢复手册；不得将待核验项写成通过。

文档完成不等于实施完成。每项实际状态填写在验收文件，不删除失败记录掩盖问题。

### 本施工文档自身的校验记录

2026-09-28：在临时目录创建当前 v11 schema 和 demo 夹具，逐条执行附件 SQL（14 张新表）及本文 ADDITIONS；再重复执行增量 DDL。SQL 语法、外键检查、SQLite 完整性检查和旧 questions / question_version_snapshots / student_responses / wrong_questions / redo_attempts 记录不变检查通过。此测试只证明草案在当前表结构上的可执行性，不代表业务迁移、并发流程、真实识别或生产发布已经实现。未修改生产数据库。

## 16. 可直接交给开发者的执行指令

> 请在 HighSchoolPhysics 仓库实施本文及两个配套附件。先运行现有恢复脚本并阅读 AGENTS.md，确认实际 main、schema 和生产状态；按 M0—M7 顺序推进。目标是教师从整份 Word/PDF 文件选择开始，完成自动转换、拆题、Markdown 编辑预览、批量入库、正确图文渲染和离线导出。保持当前结果制和 SSO，保留全部首次作答、身份、答题卡、标签与练习历史。先在副本验证迁移与真实样本，不能用截图或占位选项替代可编辑正文，不能把安装成功当识别成功。批量写入必须事务化和幂等，旧题修复须预览、映射核验及可撤销。完成代码、测试、真实浏览器、远端发布检查与产物证据后，再把验收清单逐项标记通过；未达标项明确报告，不宣称已完成。
