# Phase 2B 默认知识、能力与素养体系设计

## 背景

Phase 2A.1 已完成权限、发布边界、密码生命周期、SQLite 并发和学生导航门禁。Phase 2B 的目标不是继续扩大演示数据，而是建立一套可以解释来源、重复安装、由管理员调整、按版本发布的高中物理默认体系，为题库导入、标签审核、掌握度统计和三类角色导航提供稳定底座。

本设计使用本机已核验的正式资料：

- 教材根目录：`/Users/binyu/我的云端硬盘/01_教学与物理/10_教材课标与参考资料/教材课本`。
- 人民教育出版社 2019 版普通高中物理教材六册：
  - 必修 1，128 页
  - 必修 2，111 页
  - 必修 3，140 页
  - 选择性必修 1，118 页
  - 选择性必修 2，118 页
  - 选择性必修 3，140 页
- 《普通高中物理课程标准（2017 年版 2020 年修订）》。
- 已批准蓝图中的物理解题能力动作清单。
- 《中国高考评价体系》及物理学科命题、能力评价研究作为能力标签的补充书目证据。补充资料只用于说明标签依据，不覆盖教材目录和课程标准的规范地位。

六册教材目录经本机 PDF 文本抽取核验，共包含：

- 6 个课程模块节点。
- 27 个章节点。
- 125 个节节点。
- 默认知识树共 158 个节点。

## 设计目标

Phase 2B 完成后：

- 新演示数据库能够安装完整、可编辑的六册默认知识树。
- 知识、能力、素养是三个独立标签家族，不共用同一张业务表伪装层级。
- 默认知识树采用三级结构：课程模块、章、节。
- 系统允许管理员后续创建第四级知识点，但默认清单不制造空节点或占位节点。
- 每个默认节点和标签都能追溯到来源记录及具体定位信息。
- 默认清单可重复执行而不产生重复数据。
- 管理员能查看启用和停用项；教师、学生和自动候选流程只能读取当前启用项。
- 现有演示数据库可以增量升级，历史测评快照和已发布错题记录不被重写。

## 方案比较

### 方案一：服务器启动时直接解析 PDF

优点是资料变化后可重新抽取。缺点是 PDF 目录版式、字体编码和文本顺序不稳定，生产启动会依赖本机文件路径和解析工具，也无法保证同一版本在不同机器产生相同 ID。

不采用。

### 方案二：把全部默认数据写进 SQL 或 Python `seed_demo_data`

实现简单，但 158 个节点、来源信息和标签证据会使数据库初始化代码难以审查，差异不可读，也不适合后续增加教材版本。

不采用。

### 方案三：版本化 JSON 清单加确定性导入器

开发阶段从核验过的资料生成并人工复核 JSON 清单；运行时只读取已提交的清单，验证后在一个事务中安装。清单、导入器和数据库记录都有版本标识。

采用此方案。

## 层级与稳定编码

### 知识树

一级节点是六册课程模块：

- `K.PEP2019.R1`：必修 1
- `K.PEP2019.R2`：必修 2
- `K.PEP2019.R3`：必修 3
- `K.PEP2019.S1`：选择性必修 1
- `K.PEP2019.S2`：选择性必修 2
- `K.PEP2019.S3`：选择性必修 3

二级节点是教材中的章，例如：

- `K.PEP2019.R1.C01`：运动的描述
- `K.PEP2019.R2.C08`：机械能守恒定律
- `K.PEP2019.S3.C04`：原子结构和波粒二象性

三级节点是教材中的节，例如：

- `K.PEP2019.R1.C04.S03`：牛顿第二定律
- `K.PEP2019.R2.C08.S04`：机械能守恒定律
- `K.PEP2019.S3.C04.S02`：光电效应

第四级知识点由管理员导入、教师校本整理或后续题目标注证据创建。其编码追加 `.KNN`，例如 `K.PEP2019.R1.C04.S03.K01`。Phase 2B 默认清单中不存在四级节点。

数据库主键使用稳定编码的规范化形式生成，不使用随机 UUID：

- `kn-pep2019-r1`
- `kn-pep2019-r1-c04`
- `kn-pep2019-r1-c04-s03`

这样同一清单重复安装时能确定性命中同一实体。

### 课标主题映射

课标的“课程模块 → 主题 → 内容要求”不是教材目录层级，不作为知识树的额外父子层。

课标主题作为来源证据与语义映射记录。例如教材“第四章 运动和力的关系”可以关联课标主题 `1.2 相互作用与运动定律`。一条教材节点可以关联一个或多个课标主题，课标主题也可以覆盖多个教材节点。

Phase 2B 保存主题级映射，不把每一条“内容要求”拆成默认知识节点。

## 能力标签

能力标签回答“完成题目需要执行什么思维动作”，保持扁平结构。默认安装 15 个标签：

| 稳定编码 | 名称 |
| --- | --- |
| `A.INFO` | 信息提取 |
| `A.CONTEXT_MODEL` | 情境建模 |
| `A.FORCE` | 受力分析 |
| `A.PROCESS` | 过程划分 |
| `A.MODEL` | 模型建构 |
| `A.CRITICAL` | 临界条件 |
| `A.CONSERVATION` | 守恒思想 |
| `A.GRAPH` | 图像转化 |
| `A.EQUATION` | 方程建立 |
| `A.CALC` | 数学运算 |
| `A.EXPERIMENT_DESIGN` | 实验设计 |
| `A.DATA` | 数据处理 |
| `A.ERROR` | 误差分析 |
| `A.ARGUMENT` | 推理论证 |
| `A.TRANSFER` | 情境迁移 |

能力标签来源证据可以同时关联课程标准、评价体系和研究文献。来源链接记录摘要和定位，不存入受版权保护的长篇原文。

## 物理学科核心素养标签

素养标签严格依据课程标准，采用两级结构，共 4 个维度节点和 14 个要素节点。

### 物理观念

- `L.CONCEPT.MATTER`：物质观念
- `L.CONCEPT.MOTION_INTERACTION`：运动与相互作用观念
- `L.CONCEPT.ENERGY`：能量观念

### 科学思维

- `L.THINKING.MODEL`：模型建构
- `L.THINKING.REASONING`：科学推理
- `L.THINKING.ARGUMENT`：科学论证
- `L.THINKING.QUESTION_INNOVATE`：质疑创新

### 科学探究

- `L.INQUIRY.QUESTION`：问题
- `L.INQUIRY.EVIDENCE`：证据
- `L.INQUIRY.EXPLANATION`：解释
- `L.INQUIRY.COMMUNICATION`：交流

### 科学态度与责任

- `L.ATTITUDE.NATURE`：科学本质
- `L.ATTITUDE.ATTITUDE`：科学态度
- `L.ATTITUDE.RESPONSIBILITY`：社会责任

四个维度自身也作为可导航的一级素养节点：

- `L.CONCEPT`
- `L.THINKING`
- `L.INQUIRY`
- `L.ATTITUDE`

能力“模型建构”与素养“科学思维 / 模型建构”名称相近但语义不同，必须保留在不同标签家族中。

## 默认清单

仓库新增三个可审查数据文件：

- `highschoolphysics/data/pep2019_knowledge.json`
- `highschoolphysics/data/physics_abilities.json`
- `highschoolphysics/data/physics_literacies.json`

再新增：

- `highschoolphysics/data/taxonomy_sources.json`

每个清单包含：

- `manifest_version`
- `ontology_label`
- `source_keys`
- `records`

知识记录包含：

- `id`
- `stable_code`
- `name`
- `parent_id`
- `level`
- `node_type`
- `textbook_scope`
- `aliases`
- `description`
- `source_refs`

来源引用包含：

- `source_key`
- `page_start`
- `page_end`
- `locator`
- `evidence_summary`

来源清单包含：

- 来源类型
- 书名或文献名
- 出版或修订版本
- 课程模块
- 原始文件名
- 本机路径（可空）
- 页数
- SHA-256（可空）
- 抽取工具与版本
- 核验日期

运行时不要求 PDF 存在。清单保留已核验的文件名、页数和哈希；若当前机器能找到文件，安装流程补记实际路径和哈希核对结果。

## 数据模型

### 新表 `taxonomy_sources`

保存教材、课标、评价体系和研究文献的来源元数据：

- `id`
- `school_id`
- `source_key`
- `source_type`
- `title`
- `edition`
- `volume_code`
- `file_name`
- `local_path`
- `sha256`
- `page_count`
- `parser_name`
- `parser_version`
- `verified_at`
- `metadata_json`

`school_id + source_key` 唯一。

### 新表 `taxonomy_source_links`

保存实体与来源的多对多关系：

- `id`
- `school_id`
- `entity_type`
- `entity_id`
- `source_id`
- `page_start`
- `page_end`
- `locator`
- `evidence_summary`
- `created_at`

`entity_type` 仅允许 `knowledge_node`、`ability_tag`、`literacy_tag` 和 `curriculum_topic`。

### 新表 `curriculum_topics`

保存课标主题，不混入知识树：

- `id`
- `school_id`
- `ontology_version_id`
- `stable_code`
- `name`
- `course_module`
- `enabled`
- `version`
- `deleted_at`

### 新表 `knowledge_curriculum_mappings`

保存教材节点到课标主题的映射：

- `knowledge_node_id`
- `curriculum_topic_id`
- `mapping_type`
- `rationale`

联合主键防止重复映射。

### 新表 `taxonomy_replacements`

保存旧实体到新默认实体的替代关系：

- `id`
- `school_id`
- `entity_type`
- `old_entity_id`
- `replacement_entity_id`
- `reason`
- `created_at`

`entity_type + old_entity_id` 唯一。替代关系用于迁移当前标签和在管理员界面解释旧记录，不重写历史快照。

### 新表 `literacy_tags`

保存素养维度和要素：

- `id`
- `school_id`
- `ontology_version_id`
- `parent_id`
- `stable_code`
- `name`
- `description`
- `level`
- `enabled`
- `deleted_at`
- `version`
- `change_note`

### 现有表增量字段

`knowledge_nodes` 增加：

- `default_key`
- `is_default`

`ability_tags` 增加：

- `default_key`
- `is_default`
- `change_note`

所有增量迁移必须对已有数据库幂等执行。`initialize_database()` 在建表后检查列和迁移版本，不依赖删除数据库重建。

## 安装与发布流程

### 新演示数据库

`seed_demo_data()` 创建演示学校和管理员后调用默认体系安装服务：

1. 加载并验证四个清单。
2. 创建来源记录。
3. 创建默认本体版本。
4. 按父子顺序导入 158 个知识节点。
5. 导入 15 个能力标签。
6. 导入 4 个素养维度和 14 个素养要素。
7. 导入课标主题和教材映射。
8. 写入来源链接和审计日志。
9. 将默认版本设为当前 active。

整个安装过程使用一个数据库事务。任一记录无效时全部回滚。

### 已有数据库

管理员页面提供“安装/更新默认体系”操作：

1. 导入器读取当前清单版本。
2. 已存在相同 `default_key` 的记录执行受控更新，不覆盖管理员改名、停用或校本说明。
3. 新默认项被加入一个 draft 本体版本。
4. 管理员查看差异后送审、发布。
5. 发布沿用现有本体版本状态流转。

默认更新不得自动重新启用管理员已停用的标签。

### 旧演示节点迁移

旧五节点保留以维护历史引用，但设置为停用，并记录替代节点：

| 旧节点 | 新默认节点 |
| --- | --- |
| `kn-mechanics` | `kn-pep2019-r1` |
| `kn-kinematics` | `kn-pep2019-r1-c02` |
| `kn-newton` | `kn-pep2019-r1-c04` |
| `kn-newton-2` | `kn-pep2019-r1-c04-s03` |
| `kn-work` | `kn-pep2019-r2-c08` |

新增 `taxonomy_replacements` 表记录旧实体与替代实体。当前题库中仍启用的正式标签迁移到替代节点；测评快照、已发布错题、审计事件和历史 JSON 不修改。

## 管理员界面

管理员页面新增“默认体系与来源”区域：

- 显示清单版本、来源核验状态和安装状态。
- 显示 6 册、27 章、125 节的计数。
- 显示能力 15 项、素养 4 维 14 要素。
- 提供安装或更新默认体系按钮。
- 提供来源记录和定位详情。

原“知识图谱与能力标签”区域拆分为三个明确面板：

- 知识体系
- 能力标签
- 核心素养

管理员列表显示启用和停用记录，并可按家族、教材册、层级和状态筛选。

Phase 2B 为素养标签提供新增、修改和启停接口。题目上限、LLM 素养候选和批量审核属于 Phase 2C。

## 可见性规则

- `knowledge_nodes()`、`ability_tags()`、`literacy_tags()` 只返回启用且未删除记录。
- `all_*` 管理接口返回启用和停用记录，但不返回软删除记录。
- 教师候选生成只使用启用记录。
- 学生图谱只使用启用知识节点，并继续受已发布内容限制。
- 已停用默认标签仍可通过管理员页面和历史快照解释过去数据。
- 当前题目标签若指向停用实体，不参与新的候选和新统计；历史测评快照保持原样。

## 验证与错误处理

清单验证在写库前完成：

- 稳定编码和 ID 全局唯一。
- 父节点存在且层级正好相差 1。
- 不存在环。
- 默认知识清单恰好为 6 个一级、27 个二级、125 个三级、0 个四级节点。
- 能力清单恰好为 15 项。
- 素养清单恰好为 4 个一级、14 个二级节点。
- 所有来源引用能解析到来源记录。
- 页码范围合法且不超过来源页数。

验证失败返回结构化 400，不写入部分数据。数据库唯一约束或迁移失败返回结构化冲突或内部错误，并回滚事务。

## 测试策略

### 清单测试

- 验证节点和标签计数。
- 验证稳定编码、父子关系、页码和来源引用。
- 验证默认清单不存在四级节点。

### 数据库测试

- 从 Phase 2A.1 数据库执行增量迁移。
- 默认安装幂等。
- 中途失败完整回滚。
- 旧演示节点替代映射正确。
- 历史题目快照、错题和掌握标记保持不变。

### 仓储与 HTTP 测试

- 管理员可安装、查看和启停默认项。
- 教师和学生查询不返回停用项。
- 素养 CRUD 和权限边界与知识、能力一致。
- 非管理员不能安装默认体系。

### 浏览器验收

在约 `1600x900` 视口使用全新演示数据库验证：

- 管理员看到来源、计数和三个标签家族。
- 六册知识树可展开到章、节。
- 停用一项后管理员仍能看到，教师和学生不再看到。
- 素养标签可编辑、停用和恢复。
- 页面操作有明确成功或错误反馈。

共同验收命令：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q highschoolphysics tests
git diff --check
```

## 实现与自动化验收记录

2026-06-11 已完成 Phase 2B 默认体系的清单、迁移、安装、仓储、HTTP 和管理员 UI 实现。自动化验收结果：

- 占位符扫描 `rg -n "TO[D]O|TB[D]|implement la[t]er|fill in deta[i]ls" highschoolphysics tools tests README.md docs/superpowers/specs/2026-06-11-phase-2b-default-taxonomy-design.md` 无匹配。
- `python3 -m compileall -q highschoolphysics tools tests` 通过。
- `python3 -m unittest discover -s tests -v` 通过，79 个测试全部成功。
- 全新演示数据库 `/tmp/hsp-phase2b-auto.3nYlXU/demo.sqlite3` 中默认知识节点按层级计数为 `1|6`、`2|27`、`3|125`，默认能力标签为 `15`，默认核心素养标签为 `18`。

## 浏览器验收记录

2026-06-11 08:29:12 CST 使用全新演示数据库 `/tmp/hsp-phase2b-browser.Jf7jsl/demo.sqlite3` 验收，服务地址为 `http://127.0.0.1:8879`，Browser 视口设置为 `1600x900`。

已验证流程：

- 管理员 `admin / admin123` 登录后看到默认体系概览，计数为 158 个知识节点、15 个能力标签、18 个核心素养标签。
- 展开“来源与版本”后可见六册人教版 2019 教材、课程标准、中国高考评价体系和校内能力动作清单来源；页面未渲染本机私有路径到教师端。
- 知识表按“必修第一册”筛选并搜索“牛顿第二定律”后，仅显示 `K.PEP2019.R1.C04.S03` 目标节点。
- 停用默认核心素养要素 `L.CONCEPT.MATTER / 物质观念` 后，管理员仍可见该停用行，启用计数从 18 变为 17。
- 再次点击“安装或补齐默认体系”后，停用状态被保留，没有被默认清单覆盖。
- 恢复 `L.CONCEPT.MATTER / 物质观念` 后，启用计数回到 18。
- 临时停用知识节点 `K.PEP2019.R1.C04.S03 / 牛顿第二定律` 后，教师 `teacher_li / teacher123` 页面仍显示启用体系数据，但不再显示该停用节点；教师访问 `/admin` 显示 Forbidden。
- 教师登录 cookie 调用 `POST /api/admin/taxonomy/install` 返回 `403`，响应为 `{"error": "forbidden", "message": "Admin role required"}`。
- 验收后已恢复 `K.PEP2019.R1.C04.S03 / 牛顿第二定律`，管理员页回到 158 个知识节点和 18 个启用核心素养标签；`1600x900` 下无横向页面溢出。

残余边界：Phase 2B 仅提供核心素养的管理员维护和来源说明；LLM 素养候选、题目多家族标签上限、能力/素养专属学生导航仍按非目标留到后续阶段。

## 非目标

Phase 2B 不实现：

- 从任意 PDF 自动解析未知教材。
- 题库 Word/PDF 拆题。
- LLM 素养候选和每家族最多三标签审核。
- 掌握度自动计算。
- 教师和学生的能力、素养专属导航页。
- 多版本教材并行选择。

这些工作分别属于 Phase 2C、2E 和 2F。
