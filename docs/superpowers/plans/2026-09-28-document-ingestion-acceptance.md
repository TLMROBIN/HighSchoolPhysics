# 整卷导入施工验收用例与交付记录

配套：[主施工文档](2026-09-28-document-first-question-ingestion-implementation.md)。本文所有功能状态初始均为**待实施 / 待验收**。设计文档的静态检查结果不能填为系统功能通过。以下按日期追加执行记录；较早记录是当时快照，和最新证据不一致时，以首页当前状态、验收表更新行及文末最新执行记录为准。

## 1. 验收记录规则

每个用例填写：实际结果、测试时间、提交 SHA、环境、执行人、证据路径、缺陷编号。允许状态：待实施、待验收、通过、失败、阻塞、不适用（需理由）。“能 import”“接口返回 200”“题数相同”都不能单独证明内容正确。

| 批次信息 | 实际填写 |
| --- | --- |
| 本地 / GitHub / 远端 HEAD | 最终严格门禁确认本机 `main`、`origin/main`、GitHub `main` 与生产 checkout 一致；功能代码最后变更提交为 `15ff029356c7fc63bb0d4444ce271a2323d928c7`，其后只同步验收文档。当前 ref 以 `remote-strict-release-check-final-20260930.log` 为准。工作树仅有未跟踪的 `output/`、`tmp/` 证据/临时文件 |
| schema 版本 / 迁移前备份 | 生产核心 `user_version=11`，文档功能账本 `document_ingestion=12`；迁移后 64 张既有表指纹全一致、`integrity_check=ok`、外键错误 0。迁移前在线备份 `/home/yub/Documents/trae_projects/HighSchoolPhysics/data/backups/school.sqlite3-before-document-ingestion-20260930T041059Z.sqlite3`，SHA-256 `27b4e7c79644873d9b408adcf5467d47d8a759ba4d051a1efd9fa4b8ff0fda00` |
| HTTP / worker 实际运行版本 | 最终跟踪同步后，生产 app 与 document worker 均由 user-level systemd 重载并为 active；功能代码对应 `15ff0293`，验收文档后续为 docs-only 变更。真实 Chromium 入口与静态资源复验通过；没有登录生产教师账号 |
| Python / 转换器 / 模型 / 数学渲染器版本 | 生产 runtime readiness：MinerU `3.4.0`、PaddleOCR `3.7.0`、MarkItDown `0.1.6`、Playwright `1.60.0`、Authlib `1.7.2` 均可导入；本地 MinerU pipeline 为 `missing_configuration`，MinerU API `disabled`。KaTeX 新资源在生产 HTTP 200；程序和依赖就绪不代表 PDF 已识别 |
| 测试环境及入口 URL | `http://10.50.159.62/physics/login`；生产 Chromium 未认证打开 `/physics/documents` 收到 303 并最终到登录页 200。登录 CSS/JS 正确从 `/physics/assets/...` 加载，新导入/渲染资产全 200，浏览器无请求失败 |
| 操作账号角色（不含凭据） | 本轮生产浏览器只读验收为未认证上下文；真实教师 SSO 尚未登录。隔离测试另使用教师角色 session |
| 真实样本路径与 SHA-256 | Word A：`高三第一次周测.docx`，`3eb8cccd7bafb1fe2cb9bc7730a95785b199bafa8cd0c3d85d319c17a514f6c6`；Word B：`2027届高三物理周测2.docx`，`19ceac89048f0d6c42e23ae34c9672b45996d0605d2694102bac98adf7986b47`；PDF C：`【物理试卷】2027届高三供题训练.pdf`，`2570576fe207be04b7aec2ee2034d9d0694ee4e4e300e39d8a74dc22ec8ba968`，已做隔离真实 OCR/拆题/浏览器复核但语义未签字；传统 DOC 未验 |
| 全量测试报告 / release gate 日志 | 代理前缀修复后本机 release gate 332 项通过（1 skipped），compileall、Node、runtime readiness、`git diff --check` 通过；证据 `output/document-ingestion-evidence-20260929/local-release-check-proxy-fix-20260930.log`。最新严格远端 gate 的 Git/SHA、app/worker、HTTP、schema、integrity 与 runtime 结果见 `output/document-ingestion-evidence-20260929/remote-strict-release-check-final-20260930.log`。Chromium 最终路由及资源证据 `output/document-ingestion-evidence-20260929/production-browser-check-final-20260930.json` 和截图 |
| 已知限制与未通过项 | PDF 扫描识别暂不可用：MinerU 已安装，但生产 worker 指定的 `/home/yub/mineru.json` 及 pipeline 模型权重尚未配置；DOCX 流程尚待真实教师账号实用验收。PDF C 仍有 31 个公式事项、5 个图位和全题语义待核对；Word A/B 逐题复核待做；原生 DOC 按用户指示暂缓。PDF C 整卷生产入库/离线导出、真实 SSO 工作流、旧题生产映射及可撤销修复仍未验；不宣称 M0—M7 整体完成 |

## 1.1 本轮执行记录（2026-09-29）

- 本地/GitHub 基线仍为 `6c79ab00fd6b54048c6cb9840ea930c2089c39c5`；工作树有未提交实施改动。生产 SSH 连接重置；从公网 GET `/physics/login` 返回 200，页面仍显示 SSO 入口 `/physics/sso/login`。生产 HEAD/schema/service runtime 与 SSO 实际登录结果仍未知，没有部署或写生产数据。
- 本地实现 schema v12；v11 升级、二次启动、故障回滚、高版本拒绝以及身份、首次作答、题目标签、答题卡、错题/重做历史保留均在合成 SQLite 副本验证。该副本不代表生产备份。
- 历史快照：真实 Word B（SHA `19ceac89048f0d6c42e23ae34c9672b45996d0605d2694102bac98adf7986b47`）早期拆题曾得到 30 个候选、3 个未归属块；这一结果已被后续适配器/拆题规则更新取代，不代表当前状态。当前结果和静态浏览器复核见本文末尾的“Markdown 数学定界、章节边界与两份真实 Word 浏览器复核”。在该历史快照中未发布候选；本轮后续仅将经核对的 Q1 发布到隔离本地库，见最后一节。
- Word A 初次扫描把 268 个 OLE 公式对象列为阻塞；211 个 WMF 视觉资源可栅格化，未把这些图片作为可编辑公式。之后的 MTEF 源流转换结果见下条。扫描 PDF C 和传统 DOC 本轮没有由当前 adapter 验收。
- Word A MTEF 后续验证：适配器 v1.2.0 从 268 个 OLE 原生流得到 268 个可编辑 LaTeX 公式，268 个都关联对应公式预览，211/211 个 WMF 成功栅格化；公式正文没有使用预览图。268 个公式全部通过项目 KaTeX 严格语法解析，Chrome 本地证据页逐项呈现了 7 个配对抽样（7/7 渲染成功、7/7 预览加载）。真实拆题产生 30 个临时候选、2 个未归属块；诊断发现其中 11 个候选含“参考答案/答案/详解/故选”标记，说明答案解析内容被误拆为题目，故题数与 28 个选项顺序/重复提示都不作为抽题成功证据。图形疑点未解决，未发布任何题目。上述结果只证明源数据解析、语法和抽样渲染；268 个公式的物理语义仍待教师核对。3 个 WPS/VML `imagedata` fallback 节点没有 `r:id`，作为阻塞疑点保留。证据 `m0-word-a-mtef-conversion.json`、`m0-word-a-mtef-katex.json`、`m0-word-a-mtef-browser.json`、`m0-word-a-split-diagnostic.json`、`m0-word-a-mtef-visual-review.html` 及其两张截图。
- 当前本机环境没有可调用的 MinerU 程序、Python 模块或模型配置；PDF C 不能在此环境证明识别结果。恢复脚本已读到本地/GitHub `main` 相同，生产 SSH 仍在握手时重置。
- 本轮 test venv 的 runtime readiness 结果：MinerU、PaddleOCR、MarkItDown、Playwright PDF 和 Authlib 均不可用或未配置；secret encryption 可用。该报告只描述本轮本地 venv，不代表生产依赖状态。证据 `runtime-readiness-local.json`。
- 本地 Chrome 桌面及 390×844 仿真完成复核页、编辑/预览/保存后刷新、图片权限和图片加载检查。390px 下 20/20 个候选图片加载，0 断图；长公式使用内部横向滚动。此项不是实体手机验收，也不是生产 SSO 会话。
- 本轮在隔离合成任务的教师复核页加入草稿题目上移/下移；浏览器实际操作后顺序变为 2→1 再恢复 1→2，审计记录与数据库顺序一致，未保存 Markdown 编辑在同一候选中保留。已发布题目禁止调序。此条证据专门记录调序；拆分/合并及图片/来源块归属另见后续记录。证据 `m4-candidate-reorder-browser.json`。
- 复核卡片新增独立题号输入；浏览器把合成候选题号从 1 改为 11，保存并刷新后仍为 11、复核版本递增。正文未被改写；真实旧题映射尚未验证。
- 新增来源块在草稿题间事务化重分配；浏览器把合成来源块从题 11 移到题 2，保存后题干未变、修订递增且审计存在。图片资源可直接插入 Markdown 光标，浏览器预览加载成功并保存到草稿。光标落在稳定编辑标记时，插入保护会调整到安全正文位置。测试教师本地会话，不代表生产 SSO；合成图片不代表真实样本质量。证据 `m4-source-image-saved-final.png` 和 `m4-candidate-reorder-browser.json`。
- 拆分/合并按钮已接入事务 API：桌面 Chrome 在已保存题干的光标处分成两个可编辑候选，再将相邻候选合并；旧候选保留为 `superseded`，审计记录旧→新 ID 与内容哈希，新候选有阻塞复核项，未入库。API 同时拒绝已发布任务重构。证据 `m4-split-merge-browser.json`、`m4-split-merge-browser-final.png`；仍是合成样本。
- 桌面 Chrome 又完成合成教师批量入库：勾选两题、逐条勾选并写明三个拆并复核事项、点击批量入库后，页面刷新显示两题已入库，数据库有两条 publication/结果题和一条批次审计；这只验证界面与本机隔离库，事务回滚和幂等重试另由自动化测试验证。真实 Word B 未发布。证据 `m4-batch-publish-synthetic.json`、`m4-batch-publish-synthetic.png`。
- 273 个单元与集成测试通过（52.676 秒）；使用 `/tmp/hsp-doc-test-env` 执行本机发布检查通过（compileall、Node、runtime report、测试及 diff check）。该环境缺少 MinerU、PaddleOCR、MarkItDown、Playwright 和 Authlib。严格远端检查因 SSH `Connection reset by peer` 退出 255。
- 离线 ZIP 产物为合成题，解压后相对图片引用及 SHA 已核对；不得将其当作真实试卷题包验收。

### MathType 公式接入与最终本地/远端检查（2026-09-29）

- Word A 的 268 个 MathType v5 OLE 流现可提取为 Markdown 中的 LaTeX；每条公式都保留 `word/document.xml` 关系 ID、OLE part 路径和对应预览资源引用。未映射的 CJK 代码点按原始 Unicode 码点还原，百分号已按 LaTeX 转义。损坏 OLE 测试不会将预览图写进题干，转换结果保留阻塞项。
- Chrome 本地打开离线抽样核对页，7 个公式均由项目 KaTeX 显示，7 张源预览均加载；该页面用于比对证据，不是题目正文。自动 KaTeX 严格解析覆盖全部 268 条，但没有替代教师的物理语义签字。
- 最终本机发布检查通过：280 个单元与集成测试通过（54.160 秒），compileall、Node 语法检查、runtime readiness 和 `git diff --check` 通过。证据 `local-release-check-mtef-final.log`。严格远端门禁再次退出 255：生产 SSH 握手返回 `Connection reset by peer`，详见 `remote-release-check-mtef-final.log`。没有部署；生产 schema、worker、SSO 和新功能运行态仍未知。

阶段状态（截至 2026-09-30 当前）：M7 代码已发布到 `15ff029356c7fc63bb0d4444ce271a2323d928c7`，本机/GitHub/生产 checkout 对齐；严格远端 gate 通过，真实未认证 Chromium 已验证导题路由正确回到登录页、静态资源可加载。生产核心 schema 保持 v11，新增文档功能账本为 12，64 张旧表指纹不变。M0 仍待真实教师逐题语义/公式/图位核验；M1 迁移与数据保留副本/生产表指纹检查通过；M2/M3 的隔离渲染、导出与 worker 故障边界通过，但生产真实文件链路未验；M4 PDF C 整卷入库/导出待教师审签；M5 真实教师 SSO 与使用流程待验；M6 生产历史题映射、正式修复与撤销待验；MinerU 生产模型未配置，PDF OCR 暂不可用。M0—M6 不整体通过，M7 仅代码部署和未认证入口子项通过。

证据目录：`output/document-ingestion-evidence-20260929/`。当前自动化与发布门禁：`local-release-check-math-heading-final.log`（288 项通过）和 `remote-release-check-math-heading-final.log`（SSH 阻塞）；早期全量测试记录 `full-test-run-273.log` 与 `full-test-run-273.json` 保留作历史迭代证据；本地运行依赖：`runtime-readiness-local.json`；当前真实 Word A/B 静态浏览器证据见 `m0-real-word-current-browser-check.json`、`m0-word-a-current-browser-first-screen.png`、`m0-word-b-current-browser-first-screen.png`、`m0-word-b-current-browser-q12-preview.png`；合成工作流截图仍包括 `m4-candidate-reorder-browser.json`、`m4-source-image-browser.json`、`m4-split-merge-browser.json`、`m4-batch-publish-synthetic.png`。

### 本轮并发存储修复续验（2026-09-29）

- 修复 `DocumentStore.atomic_write` 的检查后覆盖竞态：临时文件完成写入和 `fsync` 后通过硬链接原子创建目标；若目标已存在，仅允许内容哈希相同的幂等返回，不同内容返回冲突；已有目标以 no-follow 方式检查并拒绝符号链接/非普通文件。
- 新增两线程屏障测试，分别验证相同 payload 并发均成功、不同 payload 只有一个落盘且另一方冲突。后续又验证同校并发 `store_asset` 返回相同 ID/key/hash，并在真实 worker 流程中确认 `conversion_asset_refs` 指向持久化图片且保留来源定位。带假 OIDC 的前缀回调测试也通过。全量测试 277 项通过（54.322 秒），日志 `full-test-run-final-this-turn.log`。
- 本机发布检查通过：`local-release-check-final-this-turn.log`（HTTP smoke 未启用）。严格远端检查因 SSH 握手 `Connection reset by peer` 退出 255：`remote-release-check-after-assets-and-refs.log`；没有部署。
- ST-02 仍待验收：已验证文件层并发不覆盖、同校资源复用，以及单个 worker 转换的数据库资产引用完整；多个并发 HTTP 上传/转换请求的组合和生产存储仍未验证。

### 生产登录入口浏览器复查（2026-09-29）

- 本机 Chrome 经 browser-harness 实际打开 `http://10.50.159.62/physics/login`，HTTP 200、页面标题为“🐴 登录 - 高中物理闭环系统”，显示统一平台 SSO 链接。跟随该链接后 `/physics/sso/login` 返回 500 JSON，未跳转到身份提供方；没有输入凭据。GET 可能创建短期登录 state，生产数据库无法通过 SSH 检查。本地隔离 HTTP 测试中用假 OIDC 配置验证 `/sso/login` 返回 303，回调 URI 正确包含 `/physics` 前缀；测试未访问外部 IdP。
- 页面未加载有效样式：`/physics/assets/app.css` 与 `app.js` 均返回 500 JSON，Chrome 样式规则数为 0；新导入/题目渲染 CSS 均返回 404。视觉证据 `production-login-unauthenticated.png`、`production-sso-endpoint-error.png`，结构化证据 `production-login-browser.json`。本地相同资产束通过带 `/physics` 转发前缀的 8 项 HTTP 测试；生产问题原因需远端检查才能确认。
- GitHub HTTPS `main` 为 `6c79ab00fd6b54048c6cb9840ea930c2089c39c5`，匹配本机；本机 GitHub SSH 端口 22 超时，生产主机 SSH 握手仍重置，远端代码和服务状态未知。

### 本轮真实 Word A 重新拆分与插图归属复核（2026-09-29）

- 在既有拆题修复上新增图片边界归属规则：若同一 Word 段落中，只有一段独立图片 Markdown 紧邻后续题号，则将图片随该题进入可编辑题干，并保留原段落 source span。新增回归用例覆盖前一题不得误占图片、后一题保留图片及边界需人工复核。定向 splitter 测试 8 项通过。
- 使用原 Word A（SHA-256 `3eb8cccd7bafb1fe2cb9bc7730a95785b199bafa8cd0c3d85d319c17a514f6c6`）重新转换与拆分：候选题号为 1–15；第 1–10 题的选择项均为 A–D；第 11–15 题分别保留 (1,2)、(1,2,3,4)、(1,2)、(1,2)、(1,2,3) 小问。当前候选正文未检测到答案/解析标记。19 个答题卡块和 158 个答案/解析块隔离为待映射材料，另有 2 个卷首块未归属；有 6 个同行题号边界标记需教师复核。
- 浏览器复核页改用全部 14 个真实样本图像资产内嵌数据 URI，未残留应用资产 API 地址。真实 CUA 浏览器确认第 1 题不再带入后续水碾插图，并在第 2 题预览中看到水碾图与对应题干同现；候选图像引用数逐题见 `m0-word-a-full-browser-review.json` 和 `m0-word-a-split-diagnostic-current.json`。该检查只覆盖已目视样例，不代表 14 张图均完成物理语义核对。
- 本轮改变了图像引用边界，故早先的“11/30 答案标记候选、30 道临时题”结论保留为历史记录，不再代表当前拆分结果。该阶段当时记录的“3 个 WPS/VML 节点缺少关系 ID”也已由适配器 1.3.0 的 AlternateContent 复核纠正为三个答题区矩形；当前阻塞为 268 个公式须教师逐项核对语义、6 个同行边界标记待复核、形状布局未进入编辑复核页、答案卡/答案区块待映射，PDF C、传统 DOC 和真实入库仍未验。
- 更新后的可复核产物：`m0-word-a-full-browser-review.html`、`m0-word-a-full-browser-review.json`、`m0-word-a-split-diagnostic-current.json`；当前页 SHA-256 `a1b9036fd1289a8afaa4989215bb3e7433b3f142329a244c769570e14a81f7cb`。本地门禁的 285 项测试通过（58.594 秒，HTTP smoke 未启用）；严格远端检查退出 255，SSH 握手为 `Connection reset by peer`；未部署。证据分别为 `local-release-check-after-image-boundary-fix.log` 和 `remote-release-check-after-image-boundary-fix.log`。

### WPS AlternateContent 与答题卡边框复核续验（2026-09-29）

- 现场恢复再次确认本机 `main` 与 GitHub `main` 均为 `6c79ab00fd6b54048c6cb9840ea930c2089c39c5`；恢复脚本在读取完本地/远端配置与 Git 状态后，于生产 SSH 握手 `Connection reset by peer` 退出 255。没有改动或部署生产服务。
- DOCX 适配器升至 1.3.0：对 `mc:AlternateContent` 选择 WPS DrawingML Choice 一支，保留形状文本和真实 `a:blip`，不再重复遍历 VML fallback；浮动图形的 `wp:posOffset` 不再被串入 Markdown。WPS 几何当前不伪装成题目插图，会产生带 shape id/name、几何和来源块的 blocking `word_shape_requires_visual_review`，教师对照后方可解除。明确存在但无效的图片关系仍按阻塞问题处理。
- 用原 Word A（SHA-256 `3eb8cccd7bafb1fe2cb9bc7730a95785b199bafa8cd0c3d85d319c17a514f6c6`）重转：适配器报告 0 个 `image_relationship_missing`、3 个真实浮动矩形待视觉处理；15 个候选题号、前 10 题 A–D、后 5 题小问标签、14 张候选图资产与上一版相同，复核页 SHA-256 仍为 `a1b9036fd1289a8afaa4989215bb3e7433b3f142329a244c769570e14a81f7cb`。当前 conversion、browser 和 split 结果见 `m0-word-a-current-conversion.json`、`m0-word-a-full-browser-review.json`、`m0-word-a-split-diagnostic-current.json`。
- 用 LibreOffice 26.2.4.2 将原 DOCX 渲染成 21 页 PDF 后，对第 7–9 页答题卡实际图像核对：shape 6/“矩形 6”对应第 13 题答题区外框，shape 7/“矩形 7”对应第 14 题答题区外框，shape 8/“矩形 8”对应第 15 题答题区外框；不是题目物理示意图。页图证据为 `m0-word-a-source-page7-lo.png`、`m0-word-a-source-page8-lo.png`、`m0-word-a-source-page9-lo.png`。题目候选浏览页与上轮实浏览器证据内容哈希一致；本轮 CUA 状态读取超时两次，因此没有声称重新完成浏览器操作验收。
- 新增 WPS AlternateContent 回归用例，验证 editable textbox 文本不重复、位置坐标不落入 Markdown、空 VML fallback 不误报缺图，并验证浮动几何仍作为可追溯阻塞项；原有失效图片关系测试仍通过。本机项目门禁此时 286 项通过（54.603 秒），详见历史日志 `local-release-check-wps-alternate-content.log`。
- CV-01 继续阻塞：三个形状虽然已从“疑似缺图”准确归类为答题卡外框，但几何布局没有进入可编辑复核页；仍有 268 个公式待教师逐项作物理语义核验、6 个同行题号边界待复核、答题卡和答案区块未逐项映射、PDF C/传统 DOC 未验。真实题目未发布。
- 同一 DOCX 适配器与当前拆题规则重跑 Word B（SHA-256 `19ceac89048f0d6c42e23ae34c9672b45996d0605d2694102bac98adf7986b47`）：279 个转换块，题号 1–15 共 15 个候选；前 10 题 A–D，后 5 题保留小问标签；142 个公式、21 个图片资产；187 个未分配块（181 个答案区、4 个题型标题、2 个卷首），无候选答案标记、无占位选项。此前 30 候选/3 未分配的 Word B 报告来自较早拆题版本，不代表当前拆分。静态浏览器逐候选核验见下方，不代表保存/发布。

### Markdown 数学定界、章节边界与两份真实 Word 浏览器复核（2026-09-29）

- Markdown 解析器新增数学定界 token 保护，避免 CommonMark 把 LaTeX 下标 `_` 拆成强调标记，也避免忽略公式定界符后的空格时把 `\%` 当 Markdown 转义删掉。Word B 中曾复现 1 个 KaTeX 错误（`F_1、F_2` 后接中文与填空线被吞入同一公式）；Word A 中曾复现 1 个 KaTeX 错误（`20{\\rm{ \\% }}` 百分号未按公式处理）。新增回归测试后，两项错误都清零。
- 拆题器现在把“ 一、单选题 / 四、解答题 ”这类章节题型标题保留为 `section_heading` 未分配块，避免把 Word B 的“四、解答题”塞进 Q12 的第 (4) 问。Word B 仍为 15 个候选，187 个未分配块；Word A 为 15 个候选，181 个未分配块（19 答题卡、158 答案区、3 题型标题、1 卷首）。
- 使用 browser-harness 在本机 Chrome 查看新生成的真实 Word A/B 静态复核页，窗口 1810×887。Word A：15 个编辑器与预览，95 个 KaTeX 节点、0 错误，14/14 张候选图已加载。Word B：15 个编辑器与预览，35 个 KaTeX 节点、0 错误，17/17 张图已加载；Q12 的 7 个公式均渲染，Q12 预览不含下一节标题。证据 `m0-real-word-current-browser-check.json`、`m0-word-a-current-browser-first-screen.png`、`m0-word-b-current-browser-first-screen.png`、`m0-word-b-current-browser-q12-preview.png`。这些静态页本身不连接任务 API；当前代码的真实应用 API 全链路续验见下一节，仅覆盖 Word B Q1。
- 最终本机 release gate：288 项测试通过（54.831 秒），compileall、Node、runtime readiness、`git diff --check` 通过；HTTP smoke 未启用。证据 `local-release-check-math-heading-final.log`。严格远端门禁 `REQUIRE_REMOTE_HEAD_MATCH=1` 仍以 255 退出，生产 SSH 握手 `Connection reset by peer`：`remote-release-check-math-heading-final.log`。没有部署。

### 当前代码真实 Word B 隔离教师应用全链路（2026-09-29）

- 从教师导入页的文件选择控件选择原件 `2027届高三物理周测2.docx`（9,147,057 字节，SHA-256 `19ceac89048f0d6c42e23ae34c9672b45996d0605d2694102bac98adf7986b47`），在临时目录的隔离 SQLite/文档存储中由当前 worker 解析。任务进入 `parsed`，得到 15 个候选，conversion SHA-256 `8cec4ef0ca14a90d210f2c1f33c220317593ef42c0cb51f243d57946057f90d0`；当前 Word B 的 142 个公式语义事项、187 个未分配块仍未清除。
- 当前教师复核页由本机 Chrome 实际打开；15/15 Markdown 编辑器和独立预览显示，35 个 KaTeX 节点无错误，完整滚动复核后 17/17 题目图片加载。题目第 1 题编辑正文含真实可编辑题干、稳定选项和 `asset:` 图片引用；与原卷第 1 页并排对照后确认题干、A-D 选项和火车图一致，且该题没有待核 issue。
- 对第 1 题执行保存后复核版本由 1 增至 2，正文 SHA-256 保存前后均为 `0e88383b4cc98c152896592b95450403a49933dc61d9bf74e8c186acf475ba2a`；从复核页勾选该题批量入库后发布版本为 3。只在临时 SQLite 发布了这一道经核对的真实样本题；其余 14 道未发布，生产库没有写入。
- 通过任务离线包导出路由取得 ZIP（HTTP 200，952,803 字节，SHA-256 `8d7de7b40b82739a6c47247b12792c247266b3cfca4819997b6d4062d5cb69cb`）。解压后包含 `paper.md`、`questions/001.md`、`source.json` 和相对路径 `images/fig-a7b82892daba7ba4.png`；Markdown 保留完整可编辑题干、四项选项和相对图片链接，无占位文字或应用 API URL；图片 SHA-256 与 manifest 一致。证据 `m0-word-b-current-app-e2e.json`、`m0-word-b-real-sample-task-export.zip`、`m0-word-b-real-sample-task-export-unpacked/`、`m0-word-b-current-app-upload-selected.png`、`m0-word-b-current-app-task-listed.png`、`m0-word-b-current-app-review-lower.png`、`m0-word-b-current-app-save-q1-after.png`、`m0-word-b-current-app-published-q1.png`。
- 此实样全链路只覆盖第 1 题与本地隔离环境；不等同于整卷 15 题语义签字、生产 SSO、生产迁移或生产发布。严格远端发布门禁仍因 SSH 握手重置未通过。

## 2. 数据迁移、存储与权限

| ID | 场景和步骤 | 通过条件 | 状态/证据 |
| --- | --- | --- | --- |
| DB-01 | 空测试库初始化到新版本 | 全部表/字段/索引存在，foreign_key_check 空，integrity_check=ok | 通过 — `PRAGMA user_version=11`、`app_schema_migrations.document_ingestion=12`，FK 0、integrity ok；tests.test_document_ingestion_migrations |
| DB-02 | 带旧题、测评、学生作答的 v11 副本升级 | 原 ID、内容、关联不变，新表增量建立 | 通过（实际生产库只读副本）— 生产 v11 在线备份副本迁移并关闭重开后保持核心版本 11、功能版本 12；64 张旧表、10,339 行类型指纹一致，integrity_check=ok、foreign_key_check=0；live 未迁移。证据 `m1-production-copy-migration.json` |
| DB-03 | 同一新库再次启动 | 不重复迁移，不重复生成内容，不降 schema | 通过 — 重复 initialize 保持核心版本 11 和功能版本 12，数据稳定；`tests.test_document_ingestion_migrations` |
| DB-04 | 在迁移中途注入异常 | 整个迁移回滚，原版本和旧数据不变；修复后可重试 | 通过 — 注入 CREATE INDEX 失败后 schema/data 回滚并可重试；tests.test_document_ingestion_migrations |
| DB-05 | 程序读比其支持版本更高的库 | 明确拒绝，不覆盖 user_version | 通过 — v13 库被拒绝且 user_version 不变；tests.test_document_ingestion_migrations |
| DB-06 | 新功能关闭状态启动兼容回退版本 | 新旧题可读，既有新内容不会消失 | 通过（隔离合成副本）— 基线 `6c79ab00` 服务在含已发布 Markdown 新题、图题和结果记录的副本启动；学生练习页显示题干、选项公式和图题标记，旧 `exam-media` 返回有效 PNG。旧版启动前后核心版本 11、功能账本 12，内容表/题目数不变，integrity_check=ok。未对生产库回退。证据 `m1-op06-baseline-published-content-rehearsal.json` |
| ST-01 | 文件已 rename、DB 尚未提交时杀进程 | 无发布内容引用缺失文件；孤立对象可安全回收 | 通过（隔离环境）— 两项 HTTP 测试分别注入提交前故障与真实 POSIX SIGKILL。SIGKILL 子进程在原件原子 rename 后、document/task 事务前退出；原件存在、upload 保持 assembling、document/task 行均为 0；租约过期后重试回收并只创建一份 document/task/batch，使用同一确定性 document ID。新增 reference-aware GC 默认只读 dry-run，显式 `--apply` 才回收过保留期且无数据库引用/活跃上传/worker 租约的对象；合成 DB 验证保留已引用和活跃对象、只回收孤儿。生产存储未执行 GC。证据 `m1-storage-orphan-recovery-20260929.json`、`tests.test_document_http.DocumentHTTPTests.test_sigkill_after_original_rename_leaves_no_published_reference_and_recovers`、`tests.test_document_maintenance`。 |
| ST-02 | 两请求同时上传同一资源 | 无覆盖损坏；同校复用，引用关系完整 | 部分通过 — 真实 HTTP 并发分片竞态：相同字节的两请求均返回 200，数据库恰有一行且磁盘 SHA/字节一致；不同字节同时争同一分片时恰有一个 200、另一个 409，数据库记录与获胜磁盘内容一致。新端到端回归再通过 HTTP 创建两个不同文档任务，阻塞首个 worker 转换期间证明第二个 claim 被单槽约束拒绝；随后两个任务依次转换同一张图片，数据库只保留一行资产，两个 conversion 均有完整引用，题目候选均指向同一资产。因为该 worker 设计禁止并行转换，生产路径不存在两个 worker conversion 同时争资产的场景；底层同校并发图片存储复用仍由 `tests.test_document_store` 直接覆盖。真实生产存储演练未做。证据 `tests.test_document_http.DocumentHTTPTests.test_concurrent_http_upload_parts_are_idempotent_and_conflicts_do_not_overwrite`、`tests.test_document_http.DocumentHTTPTests.test_two_http_tasks_are_single_slot_and_reuse_one_worker_asset_with_complete_refs`、`tests.test_document_store`、`m1-cross-task-worker-asset-reuse-20260930.log`。 |
| ST-03 | 越界文件名、ZIP Slip、异常压缩比 | 受控拒绝，未写到存储根之外 | 通过（DOCX 上传前容器校验）— 隔离测试确认拒绝 `../`、反斜杠穿越、`.` 路径段、Windows 盘符绝对/相对路径及超过阈值的 ZIP 压缩比；校验在原件持久化前运行。尚未覆盖磁盘耗尽和所有归档边界组合。证据 `tests.test_document_store.DocumentStoreTests.test_docx_archive_rejects_zip_slip_and_windows_absolute_paths`、`test_docx_archive_rejects_suspicious_compression_ratio`。 |
| ST-04 | 磁盘不足、图片损坏、写权限不足 | 明确失败，不出现成功但断图的版本 | 部分通过（注入存储异常）— 图片解码损坏由 `store_asset` 拒绝；注入写权限失败时上传分片接口返回 503 `storage_unavailable`、不写分片行且可重试；注入 ENOSPC 时完成接口返回 507 `storage_full`、上传仍可重试，未生成 document/task 行；fsync 失败会清理临时文件。真实磁盘耗尽/权限配置环境尚未现场演练。证据 `tests.test_document_store.DocumentStoreTests.test_atomic_write_removes_temporary_file_when_disk_sync_fails`、`tests.test_document_http.DocumentHTTPTests.test_upload_part_permission_error_is_explicit_and_retryable`、`test_upload_complete_disk_full_returns_retryable_507_without_partial_rows`。 |
| ACL-01 | 他校教师持合法 task/item/revision/asset ID | 所有读取、编辑、导出和图片接口拒绝 | 部分验证 — 合成 HTTP 用例验证外校教师对未发布 task/item/source/preview/export/asset/review 读取及 cancel/attach/preview/save 写入返回 404，数据库不变；未覆盖已发布 revision 的共享策略与生产身份。证据 `tests.test_document_http.DocumentHTTPTests.test_document_task_reads_and_writes_are_limited_to_owner_and_school`。 |
| ACL-02 | 同校其他教师读取未发布导入草稿 | 拒绝；管理员同校可查，确认后的题库按约定共享 | 部分验证 — 合成 HTTP 用例验证同校非所有者教师不能在列表发现或读取未发布 task/item/source/preview/export/asset/review，受保护写入返回 403 且数据库不变；管理员例外与已确认题库共享未覆盖。证据 `tests.test_document_http.DocumentHTTPTests.test_document_task_reads_and_writes_are_limited_to_owner_and_school`。 |
| ACL-03 | 学生访问原卷、答案图片、草稿、solution=1 ZIP | 拒绝；不通过源码/网络响应泄漏答案 | 通过（隔离 HTTP）— 已发布测评学生只能读取其题目所需题图；增加 `solution=1` 返回 403。原卷 source/preview、导入资源、草稿页、单题/整卷及含答案 ZIP 对学生均返回 403；响应无试卷/答案文本。证据 `tests.test_document_http.DocumentHTTPTests.test_teacher_can_publish_editable_question_and_download_offline_image_zip`；日志 `access-export-security-tests-final-20260929.log`。 |
| ACL-04 | 学生访问其他学生未授权测评的题图 | 拒绝，不能只依学校授权 | 通过（隔离 HTTP）— 指定班学生发布后读取题图为 200；同校但未参加该测评的另一班学生读取相同 snapshot/asset 返回 403。证据同 ACL-03 HTTP 用例及 `access-export-security-tests-20260929.log`。 |
| ACL-05 | 恶意跨站写请求、失效会话 | 被拒绝，用户状态检查未绕过 | 通过（隔离 HTTP）— 跨 Origin 与无效会话写入返回 403；`must_change_password=1` 的既有会话写文档入口返回 409 `password_change_required`。`tests.test_document_http.DocumentHTTPTests.test_document_mutations_reject_cross_origin_and_students`；日志 `access-export-security-tests-final-20260929.log`。 |

## 3. 上传与后台任务

| ID | 场景和步骤 | 通过条件 | 状态/证据 |
| --- | --- | --- | --- |
| UP-01 | 普通教师选择中文名 DOCX/PDF | 一次选择即可上传，原件字节与本地哈希相同 | 通过（隔离环境）— 普通教师测试会话从浏览器选择字节与原件 SHA 相同的 DOCX B；非生产 SSO。m0-docx-b-browser-worker.json |
| UP-02 | 分片中断、刷新、重新选同文件续传 | 已接收分片复用，不重复创建任务 | 通过（隔离真实浏览器）— Chrome 实际选择 9,147,057 字节 Word B DOCX；模拟第 1 个分片已由服务端确认后网络中断，刷新并重选原件，恢复标题和同一 upload/request key，续传请求从分片 1 开始，分片 0 未重发，最终导航到复核页；SQLite 中仅一条本次上传记录。证据 `upload-resume-browser-20260929/upload-resume-browser-e2e.json` |
| UP-03 | 重发相同序号相同/不同字节 | 相同幂等成功，不同返回 409 | 通过（API）— `test_upload_is_chunked_idempotent_and_source_is_private` 验证相同字节重复返回 200、不同字节重复同一分片返回 409；没有重复分片行或覆盖已接收内容。 |
| UP-04 | complete 双击和超时后重试 | 相同 document/task ID，仅一份逻辑导入 | 通过（API）— complete 重试返回同一 task_id；tests.test_document_http |
| UP-05 | 缺分片、全文件哈希不符、伪装扩展名 | 不创建转换成功任务，提示具体原因 | 通过（API）— `test_upload_incomplete_hash_mismatch_and_wrong_container_fail_closed` 分别断言缺分片 409、整文件哈希不符 400、DOCX 扩展名伪装 422；均未产生解析任务或文件。 |
| UP-06 | 50 MiB 边界、超限、无 Content-Length、非法编码 | 按明确限额响应，无无限 read 或大内存占用 | 通过（隔离真实浏览器边界上传，不含解析）— Chrome 从教师导入页选择精确 50 MiB 的 PDF C 原卷副本（在原 PDF 字节后补空格至上限），100/100 分片完成；累计大小、存储大小和 SHA-256 一致，任务停留在 queued，明确未启动解析。原始套接字缺 `Content-Length` 400、超出 API 请求上限即使不发送 body 也返回 413、非法 UTF-8 400，以及元数据超限返回 413 的回归均通过。证据 `upload-50m-browser-transfer-evidence.json`、`upload-50m-real-pdf-fixture.json`、`upload-50m-real-pdf-selected-browser.png`、`upload-50m-real-pdf-transfer-browser.png`、`test_document_json_requests_require_bounded_valid_utf8_body`、`test_upload_size_limit_accepts_exact_50_mib_and_rejects_above`。 |
| UP-07 | assembling 进程退出，租约回收后恢复 | 旧 token 无法提交，恢复任务不重复创建 | 通过（隔离 HTTP）— 真实 SIGKILL 后由第二次 complete 回收过期租约，使用固定 upload→document ID，仅一份 document/task/batch；并发旧请求测试确认 loser 返回赢家 task/file ID，旧 token 无重复建行。证据 `m1-storage-orphan-recovery-20260929.json`、`test_sigkill_after_original_rename_leaves_no_published_reference_and_recovers`、`test_expired_upload_assembly_lease_is_reclaimed_without_duplicate_rows`。 |
| WK-01 | 两 worker 同时领取一个任务 | 仅一个取得有效租约，不双重入库 | 通过（隔离 SQLite 并发回归）— 两个独立 worker 线程通过 barrier 同时 claim 同一 queued task；恰有一个获得 lease token，另一个返回 `None`，数据库仅一次 attempt，任务 generation=1 且 token 与胜者一致。证明竞争领取不会双重转换/入库；不是生产 worker 并发演练。证据 `tests.test_document_worker.DocumentWorkerFlowTests.test_two_worker_instances_racing_for_one_task_create_only_one_lease`。 |
| WK-02 | 转换中重启 worker/服务 | 可恢复，原件保留，任务不永久 running | 通过（隔离 SQLite/文件系统）— 子进程在 conversion 文件目录 fsync/rename 后 SIGKILL，数据库尚无 conversion/candidate 行；过期租约回收使 generation 增至 2，第二次 worker 转换成功，原件保留，只有一份有效 conversion 与候选；旧孤儿目录被 GC 回收。未在生产 worker/service 上重启。证据 `test_sigkill_after_conversion_files_are_saved_recovers_and_collects_stale_output`、`m1-storage-orphan-recovery-20260929.json`。 |
| WK-03 | 旧租约任务在新任务之后返回 | fencing 拒绝旧成果提交 | 通过（隔离 worker）— generation 1 租约回收后 generation 2 完成；随后续跑旧 token 得到 `discarded_stale_output`，数据库只有 generation 2 的一份 conversion 与候选。证据 `tests.test_document_worker.DocumentWorkerFlowTests.test_expired_worker_lease_recovers_with_new_generation_and_discards_stale_result`。 |
| WK-04 | 超时、取消、超过重试次数 | 子进程回收、终态明确，没有无限循环 | 通过（隔离 worker 与子进程回归）— MinerU 超时和取消测试均确认子进程被终止并回收；真实 worker 取消请求到达活跃 converter 后，任务/batch 进入 cancelled、租约清除且没有部分 conversion；过期 lease 超过 attempt limit 后进入 terminal failure。`tests.test_document_adapters.DocumentAdapterTests.test_mineru_timeout_terminates_and_reaps_the_converter_process`、`test_mineru_cancellation_terminates_and_reaps_the_converter_process`、`tests.test_document_worker.DocumentWorkerFlowTests.test_worker_cancel_request_reaches_converter_and_leaves_no_partial_conversion`、`test_expired_worker_lease_after_attempt_limit_becomes_terminal_failure`。未在生产 systemd worker 上演练 |
| WK-05 | 模型不可用、转换命令失败 | dependency/model/conversion 错误区分，无假进度 | 通过（隔离 worker 故障注入）— 缺模型、缺依赖、转换命令失败与超时返回各自 error code 和安全可见消息；任务/batch 为失败终态、没有写 conversion/candidate，也没有伪造进度或泄漏 adapter 私有错误。生产当前模型配置与真实线上转换仍不因此通过。`tests.test_document_worker.DocumentWorkerFlowTests.test_worker_reports_missing_server_model_without_fake_progress`、`test_worker_reports_missing_server_dependency_without_fake_progress`、`test_worker_reports_conversion_failure_without_fake_progress`、`test_worker_reports_conversion_timeout_without_fake_progress` |
| WK-06 | 成功并编辑过的任务重新转换 | 新任务/产物，旧编辑与已发布版本保留 | 待验收 |
| WK-07 | OCR 运行同时进行正常学生请求 | 正常服务不被数据库长事务或 HTTP 转换阻塞 | 待验收 |

## 4. 真实转换与拆题

| ID | 场景和步骤 | 通过条件 | 状态/证据 |
| --- | --- | --- | --- |
| CV-01 | 第一次周测 Word A | 嵌入公式没有变成空白，图文位置可追溯，疑点可复核 | 阻塞 — 最新拆分为 15 个候选题号 1–15，前 10 题 A–D，后 5 题小问标签完整，当前候选未检出答案/解析标记；19 个答题卡块、158 个答案区块、3 个题型标题和 1 个卷首块仍待映射。原卷页 7–9 已确认三个 WPS 矩形是第 13–15 题答题区外框，不是题目示意图，但几何尚未进入编辑复核页。当前 Chrome 中 14/14 候选图加载、95 个 KaTeX 节点无错误；6 个同行题号边界需教师复核，268 个 LaTeX 公式仍需物理语义核对，PDF C 和传统 DOC 未验。不得以公式预览图替代正文。证据 `m0-word-a-current-conversion.json`、`m0-word-a-split-diagnostic-current.json`、`m0-word-a-full-browser-review.json`、`m0-word-a-source-page7-lo.png`、`m0-word-a-source-page8-lo.png`、`m0-word-a-source-page9-lo.png`、`m0-real-word-current-browser-check.json` |
| CV-01a | Word A MathType 源公式提取 | 每个 OLE 对象有可编辑公式正文及可追溯原预览 | 通过（仅源流解析与资产关联）— 268/268 MTEF 源流转换，268/268 预览关联；没有用图片作为正文。抽样公式还需物理语义复核，见 CV-01。证据 `m0-word-a-mtef-conversion.json` |
| CV-02 | 第二次周测 Word B | OMML、上下标、分式及图片完整，编辑后渲染一致 | 阻塞 — 当前适配器 1.3.0 与拆题规则重跑：279 个正文块、15 个候选（题号 1–15）、142 个公式待语义复核、21 个资产、187 个未分配块（181 答案区、4 题型标题、2 卷首）、无答案标记和占位选项。Chrome 静态页 15 个编辑器/预览、17/17 图加载、35 个 KaTeX 节点无错误；Q12 不再混入下一节标题。另在隔离教师应用中对照原卷核验并保存、批量发布 Q1，验证实际任务 ZIP 离线导出；其余 14 题、142 个公式和未分配块仍待人工核对，不代表全卷通过。证据 `m0-word-b-current-conversion.json`、`m0-real-word-current-browser-check.json`、`m0-word-b-current-app-e2e.json`；旧版 30 候选报告不代表当前拆分 |
| CV-03 | 联考扫描 PDF C | 六页均处理，识别正文而非仅截整页，来源页正确 | 部分通过（转换/可编辑显示；语义阻塞）— 当前产品适配器在生产主机独立 `/tmp` 配置和模型缓存中实际运行 MinerU pipeline 3.4.0：6/6 页、86 个含来源页的正文/图块、23 个独立图像资源、9,045 字符 Markdown；没有把整页截图或占位选项当正文。当前适配器对精确保存的 content-list 与图像 payload 重放后，得到题号 1–15；前 10 题结构化为 A–D，后 5 题保留 3 个小问，第 3 题四张图映射到 A–D，第 5/8 题粘连标记拆为 A–D。原卷目视对照发现 Q1 方程箭头丢失/错识别、Q4 选项 `√2` 识别为 `J√2`、Q14 的 `v₀` 前出现错误 nabla/dot；现增加数学定界符兜底复核，公式 issue 从 16 增至 31，Q1 新增 1 项待复核。另为 Q1 方程箭头、Q4 `\sqrt{2}`、Q14 `v_0` 制作来源块对应的可编辑 Markdown 纠错建议，尚未写入任务/数据库，也未获教师签字。实际导入任务 UI 的隔离 Chrome 页面现显示原 PDF、15 个 Markdown 编辑器/预览、102 个 KaTeX 节点/0 语法错误；原卷预览路由返回原 PDF 200、`application/pdf`，Chrome PDF 查看器显示 6 页。勾选 Q1 后批量入库被未解决的 `formula_ocr_requires_review` 阻止；候选保留 `needs_review`，publication 仍为 0。KaTeX 成功只证明语法可渲染，不能证明公式语义正确。仍有 5 个图片位置事项、全题 OCR/公式及答案映射待教师复核；PDF C 整卷离线导出未验。2026-09-30 另由 Chrome 实际选择原始 PDF C 文件上传至隔离教师页，完整经过当前分片上传、worker、adapter 与 splitter，复核页仍是可编辑正文而非截图/占位内容；15 个候选均未发布。此轮使用保存的真实远端 MinerU 输出重放，明确不算新 OCR。证据 `m0-pdf-c-source-review.json`、`m0-pdf-c-formula-review-guard.json`、`m0-pdf-c-current-split.json`、`m0-pdf-c-current-browser-review.json`、`m0-pdf-c-current-app-e2e.json`、`m0-pdf-c-app-task-review-batch-blocked.png`、`m0-pdf-c-app-task-original-pdf.png`、`pdf-c-mineru-ocr-final/ocr/`；Vision 低置信度报告仅作历史诊断。
| CV-04 | 传统 DOC 与文本 PDF | 经系统内置路径成功转换，原文件不变 | 部分验证 — 以 Word B 原 DOCX 派生 MS Word 97 二进制 `.doc` 格式控制样本（原 DOCX SHA 保持不变），实际调用 LibreOffice 26.2.4.2 的 `convert_legacy_doc` 生成 23 页 PDF；PDF 有可提取文字并检测到题号 1–15，Chrome 实际显示首页题干、选项、火车图和电路图。该文件不是用户提供的原生 `.doc`，且没有完成 MinerU/OCR→Markdown 识别，CV-04 仍待验收。证据 `legacy-doc-control-word-b-20260929/legacy-doc-conversion-result.json`、`legacy-doc-control-word-b-20260929/chrome-legacy-doc-preview.png` |
| CV-05 | 加密/损坏/无题目文档 | 明确失败或待处理，不显示“导入成功 0 题” | 待验收 |
| CV-06 | 图片引用含重复文件名，清理转换临时目录 | 独立资产已持久化，所有图片仍可显示 | 待验收 |
| SP-01 | 1. / 1． / 1、 / 自动编号 / 无空格 | 题号均正确识别，不漏全角题号 | 待验收（部分通过）— parser 回归覆盖全角/ASCII/无空格年份前缀；真实 PDF C 第二次当前产品适配结果为连续题号 1–15，现无漏题/重号。Word A/B 的自动编号、无空格边界仍需整卷人工核对。证据 `tests.test_question_splitter`、`m0-pdf-c-mineru-adapter-run.json` |
| SP-02 | 小数、年份、答案区重新编号 | 不误拆题，不混入答案或页脚 | 待验收（部分通过）— 回归测试与真实 OCR 拆题不再把 `0 . 8`、`0 . 6` 小数误切为题号，`1.2026年` 边界识别正确；真实 PDF C 仍需核对跨页/页脚，Word A/B 答题区重新编号尚未全验。证据 `tests.test_question_splitter`、`m0-pdf-c-mineru-adapter-run.json` |
| SP-03 | 同行选项、多行选项、图片选项 | 选项次序和归属正确，公式/图不丢失 | 待验收（结构拆分通过子项）— 当前真实 PDF C 重放结果对前 10 道选择题全部生成有序 A–D，未生成占位选项；第 3 题 A–D 标签与四张真实图块逐项对齐，题干图没有挂入 D。第 5/8 题的粘连选项标记已解析。浏览器确认第 3/5/8 题均显示四个结构化选项；选项文本/公式语义仍需逐项对照原卷后才能正式通过。证据 `m0-pdf-c-current-split.json`、`m0-pdf-c-current-browser-review.json`、`m0-pdf-c-question3-browser-review.png` |
| SP-04 | 多栏与跨页题，含续页选项 | 完整合并，source_spans 指向全部相应块 | 待验收 |
| SP-05 | 第 11/12 题公共条件与多个空 | 完整大题保留，作答项有稳定 child_key | 待验收 |
| SP-06 | 独立答案文件、重复题号歧义 | 唯一关联才确认，歧义明确待复核 | 阻塞（已验证子项）— 真实配套解析 DOCX 在隔离数据库对 Word B 原卷 1–15 题得到 15/15 唯一建议，未应用答案；隔离教师浏览器现已实点“生成题号匹配预览”，显示 2 条 Markdown 答案建议，未勾选、应用按钮禁用，SQLite 仍为 missing / 0 publication。新增 HTTP 回归构造两个同题号候选，预览将该号标为 `question_match_not_unique`，不提供写入建议；另两个唯一题号仍可预览。原有合成 HTTP 测试继续验证待复核状态、不发布、不覆盖、事务回滚和幂等重试。答案正文公式的物理语义与生产 SSO 工作流仍未确认。证据 `m0-real-word-b-answer-match-preview.json`、`m0-answer-match-preview-browser.json`、`m0-answer-match-preview-browser.png`、`tests/test_document_http.py`
| SP-07 | 主观大题不在结果制题型范围 | 仍可保存和导出，不被静默丢弃或自动判分 | 待验收 |
| SP-08 | OCR 丢图/公式/正文块无法归属 | issue 定位具体页/块，blocking 不得确认 | 待验收 |

### 真实样本逐题核对模板

每个完整大题一行；必要时为跨页/小问单独补行。所有正式发布题必须完成核对，不能只抽查第一题。

| 样本 SHA | 原题号 | 来源页/块 | 候选 ID | 题干 | 选项 | 公式 | 插图归属 | 小问/公共条件 | 答案状态 | 修订说明 | 复核者/时间 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 待填 | | | | | | | | | | | |

自动首遍质量另行统计：完整大题总数、首遍可用数、漏题/重题、公式失败数、孤立图数、人工修改次数、总复核时间。报告实际数据，不把最终人工核对通过率当作 OCR 原始准确率。

## 5. 编辑、发布、展示和导出

| ID | 场景和步骤 | 通过条件 | 状态/证据 |
| --- | --- | --- | --- |
| UI-01 | 教师从工作台完成真实整卷流程 | 不需要先生成 JSON、手工裁题或逐题重录 | 部分通过（真实文件选择→转换→拆题→编辑预览子链路）— Chrome 实际选择原始 PDF C（SHA `2570576f…ba968`）并经上传流程到达教师复核页；当前 worker/adapter/splitter 显示 15 个候选与 15 个可编辑 Markdown 编辑框，102 个 KaTeX 节点、0 个渲染错误；Q3 的 5/5 个行内图片在滚动进入视口后加载。临时改动可刷新预览并恢复而不保存；数据库确认 15 项均为 `needs_review`、36 个待核 issue、0 个 publication。此浏览器任务回放的是字节相同的已保存远端 MinerU 3.4 OCR 输出，不是本次新 OCR；使用隔离合成教师 session，不证明生产 SSO。因 31 项公式与 5 项图位问题尚未复核，没有 PDF C 批量发布或正式整卷导出。证据 `m0-pdf-c-real-file-selection-browser-20260930.json`、`m0-pdf-c-file-select-browser-edit-preview-20260930.png`、`m0-pdf-c-file-select-browser-question3-20260930.png`、`m0-word-b-current-app-e2e.json`。
| UI-02 | 点击题目、插图、警告 | 对应原卷页/块定位准确，不是按文件名猜位置 | 部分验证 — 隔离 Chrome 实点 PDF C 第 12 题的“跳转到第 5 页”，原卷 PDF 查看器页码由 1/6 变为 5/6，显示第 12 题正文及电桥图；Word/PDF 全部候选的页/块映射仍未逐题核验。证据 `m4-source-page-jump-browser.json`、`m4-source-page-jump-pdf-c.png` |
| UI-03 | 合并、拆分、改题号、调序、移动图片 | 来源和图片引用保持完整；旧候选可追溯 | 阻塞（部分实现）— 草稿调序、题号修改、来源块重分配、图片插入、拆分/合并与合成题批量发布已由隔离合成浏览器/API 验证；真实 Word B Q1 原卷映射和隔离发布通过，其他候选的真实来源对应与全卷复核仍缺。m4-candidate-reorder-browser.json、m4-source-image-browser.json、m4-split-merge-browser.json、m4-batch-publish-synthetic.json、m0-word-b-current-app-e2e.json |
| UI-04 | Markdown 编辑切预览再保存 | 换行、LaTeX、表格、图片位置无损 | 部分通过（编辑与预览）— 合成用例验证编辑/保存/刷新；真实 Word B Q1 当前 Markdown 保存后 SHA-256 不变，渲染预览保留原卷火车图；真实 PDF C 原文件 UI 流程中 15 个编辑框可用，102 个 KaTeX 节点无错误，临时插入的正文探针可显示于预览，移除后重新渲染且没有持久化；Q3 的 5 张行内图加载。PDF C 的教师修改保存/刷新和整卷图位/公式语义仍未验。证据 `browser-review-save-after.png`、`m0-word-b-current-app-e2e.json`、`m0-pdf-c-real-file-selection-browser-20260930.json`、`m0-pdf-c-file-select-browser-edit-preview-20260930.png`、`m0-pdf-c-file-select-browser-question3-20260930.png`。
| UI-05 | 整题模式破坏章节/稳定 ID | 有具体错误，不覆盖服务端规范对象 | 通过（单元测试）— 固定章节和稳定 ID 损坏会拒绝；tests.test_document_models |
| UI-06 | 两窗口同时编辑，断网后重试 | CAS 冲突可见，本地输入保留，不覆盖他人修改 | 待验收 |
| UI-07 | 390px 工作区和操作按钮 | 可切换原卷/编辑/预览，无不可操作区域 | 通过（Chrome 仿真）— 390×844 视口、内容宽度 390、编辑器/操作按钮可见、长公式限制在内部横向滚动；未做实体设备测试。browser-review-mobile-390.png |
| PB-01 | 所选第 3 道题写入故障 | 本次所选集合全部回滚，无半批入库 | 通过（合成事务）— 现有批次校验失败回滚测试外，新增图片投影中途注入意外异常；已插入的内容组、修订、结果题、资源引用、publication 和幂等键均回滚，连接退出事务态，同一 request key 重试后完整成功。证据 `tests.test_document_http.DocumentHTTPTests.test_teacher_can_publish_editable_question_and_download_offline_image_zip` |
| PB-02 | confirm 双击和换 key 重试同候选 | 不重复建题，返回已有 publication 或冲突 | 通过（合成幂等）— 重复 confirm 返回已发布记录；tests.test_document_worker |
| PB-03 | 正文核实但答案缺失 | 可入内容库，不能创建自动判对错测评 | 待验收 |
| PB-04 | 发布新题后经旧 question/update 接口修改 | 跳转新流程或拒绝，不产生两份冲突正文 | 待验收 |
| RD-01 | 题库、教师考试、学生错题、练习选项、解析、学习单 | 使用同一 renderer，格式及图一致 | 待验收 |
| RD-02 | 已建测评后编辑题库新版本 | 历史题干、评分规则和图片仍是绑定版本 | 通过（合成快照）— 新修订不改变已有测评绑定版本；tests.test_document_worker |
| RD-03 | 原始 HTML、脚本 URL、恶意数学宏、任意图片路径 | 不执行脚本、不请求任意资源，给出合法内容 | 通过（测试）— 原始 HTML、危险链接和不受控图片引用按安全渲染器规则处理；tests.test_document_models |
| RD-04 | /physics/ 与直连路径、数学字体 | 两种路径均无 404/字体错载/图片断链 | 部分通过（生产公开入口）— 15ff0293 部署后真实 Chromium 访问导题入口正确 303 到 `/physics/login`；登录 CSS/JS 与 document-import、question-rendering、KaTeX 资源均 200，浏览器请求失败 0。生产教师登录后的题目图文渲染仍待本人实际使用复核。证据 `production-browser-check-proxy-fix-20260930.json`、`remote-strict-release-check-proxy-fix-20260930.log` |
| EX-01 | 导出含图/公式单题 ZIP 后离线打开 | 相对路径完整，正文/图片哈希与版本一致 | 通过（合成单题 + 一道真实样本题）— Word B Q1 的实际任务包解压后含可编辑题干、A-D 选项、相对图片链接；离线资源存在且 SHA-256 匹配 manifest，无 API URL/占位文本。synthetic-offline-question.json/.zip、m0-word-b-current-app-e2e.json、m0-word-b-real-sample-task-export.zip |
| EX-02 | 导出某个填空小问 | 包含父题公共条件和必要图片，当前小问清楚 | 通过（合成小问）— 子题导出含父题公共条件；tests.test_question_export |
| EX-03 | 整卷 ZIP，多个同名图片 | 题序正确，无覆盖，paper.md 与分题文件引用均可解析 | 通过（合成整卷）— 多题次序、共享同名资源和相对路径经测试；tests.test_question_export |
| EX-04 | 打包时图片缺失/权限不足/超限 | 明确失败，不下载残缺成功包 | 通过（单元 + 隔离 HTTP）— 将已发布题引用图片模拟为缺失/完整性失败、文件权限错误，以及将 ZIP 限额压到 1 字节；失败请求均返回 `422 export_failed` JSON 且不是 ZIP；学生请求含答案导出返回 403。导出层的 MIME、哈希、大小上限另有单元覆盖。证据 `tests.test_document_http.DocumentHTTPTests.test_teacher_can_publish_editable_question_and_download_offline_image_zip`、`tests.test_question_export`；日志 `access-export-security-tests-final-20260929.log`。 |
| EX-05 | 无答案与含答案导出 | 按角色和选项裁剪，元数据无答案/学生信息旁漏 | 通过（合成权限）— 学生不能导出答案；tests.test_question_export |
| EX-06 | 未审核候选校对稿 ZIP | 正文来自当前候选 Markdown；明确携带审核状态、issues 与处理依据；图像相对链接及哈希完整；仅教师可读 | 通过（真实 PDF C OCR 结果重放 + 隔离 Chrome）— 上传原 PDF C 字节，在隔离 SQLite/存储中经当前 adapter/splitter 重放已保存 MinerU 3.4 OCR 包，Chrome 显示 15 个候选/编辑器/预览、102 个 KaTeX 节点无语法错误、23/23 图片加载；可见链接实际下载 15 个 Markdown 题文件与 23 张图片。ZIP 相对图片引用、SHA-256 与 manifest 一致，无占位选项/API URL；manifest 保留 31 条公式、5 条图位未解决事项并明确标注未审核。点击导出时会先自动保存未保存的 Markdown 或 issue 核验勾选；ZIP 同时保留处理后的复核说明、reviewer 与时间。隔离 DB publication 为 0。此为教师校对包，不代表 EX-03 正式已发布整卷导出或语义验收通过。证据 `m2-pdf-c-review-draft-autosave.json`、`m2-pdf-c-review-export-issue-autosave-probe.json`、`m2-pdf-c-review-draft-autosave-browser.png`、`m2-pdf-c-review-export-issue-autosave-probe.png`、ZIP 下载目录 `m2-pdf-c-review-draft-download-autosave/`、`m2-pdf-c-review-export-issue-autosave-download/`、`tests.test_document_http.DocumentHTTPTests.test_teacher_can_publish_editable_question_and_download_offline_image_zip`。 |

## 6. 旧内容修复和发布

| ID | 场景和步骤 | 通过条件 | 状态/证据 |
| --- | --- | --- | --- |
| MG-01 | 对两个考试逐题生成旧→新映射 | 有原题号/来源/哈希/复核者，不靠顺序猜测 | 部分完成（真实目标定位）— 生产数据库只读备份中，已按错误资产 SHA-256 唯一定位联考第 1—4 题及 8 个历史快照；源卷第 1 页与旧图内容也完成目视核对。目标修订仍缺教师逐题核验，第一次周测及其余旧题尚未映射。证据 `m6-production-copy-real-target-audit.json` |
| MG-02 | 联考第 1—4 题纠错 | 展示确实为相应原题；第 1 题不再显示第 5—7 题 | 阻塞（目标已定位，未应用）— 精确生产 v11 副本确认 4 道旧题共 8 个快照，涉及两场测评、312 条首次作答、144 条错题记录、20 条题目标签及两份答题卡模板关联；错误图片哈希与原卷页对照确认旧图实际显示第 5—7 题。PDF C 候选仍有公式/图位复核项，且没有教师确认的新修订版本，因此未运行正式纠错预览、未应用或撤销任何记录。`m6-production-copy-real-target-audit.json` |
| MG-03 | 预览后原库/映射文件发生变化 | apply 拒绝陈旧预览，不应用到错误快照 | 通过（合成工具）— 预览哈希过期时 apply 拒绝且不写入；tests.test_content_corrections |
| MG-04 | 相同 migration_key 重复执行 | 幂等；不同内容同 key 冲突 | 通过（合成副本）— 相同 mapping 重放返回同一 correction ID 并标记 `idempotent=true`；同 key 改变 `reason` 后的映射被拒绝，事务回滚，原有单条 active 记录及 mapping hash 不变。`tests.test_content_corrections.HistoricalContentCorrectionTests.test_preview_pins_mapping_and_baseline_apply_is_idempotent_and_reversible`；定向日志 `m6-migration-key-idempotency-test.log` |
| MG-05 | 应用前后比较保护记录 | 既有学生身份、首次答案/对错、标签、答题卡、练习历史不变 | 通过（实际生产数据副本）— 实际生产 v11 数据复制到临时文件后迁移；所有 64 张旧表和 10,338 行按旧列生成的类型化 SHA-256 指纹迁移前后相同，包含 identity_accounts、student_responses、question_tags、answer_card_templates、exam_assets、wrong_questions、redo_attempts。live 库未迁移。证据 `m1-production-copy-migration.json` |
| MG-06 | 纠错发现参考答案改变 | 不静默改判，独立复核处理 | 阻塞 — 未发现或复核任何真实参考答案变化 |
| MG-07 | 迁移后新增作答，再撤销本次纠错 | 新作答保留，只撤销显示修订 | 阻塞 — 合成纠错撤销验证通过；生产新增作答后撤销未执行。tests.test_content_corrections |
| OP-01 | 备份恢复副本 | SQLite 完整，原件/资源齐全，引用校验通过 | 待验收 |
| OP-02 | 发布维护期间处理 auto-update timer | 无半更新启动，结束后恢复原状态 | 待验收 |
| OP-03 | 更新后检查 HTTP 与 worker | 均运行目标提交，任务恢复及页面正常 | 待验收 |
| OP-04 | 严格远端 release gate | 全通过，保存实际日志，不用本地结果代替 | 通过（部署/未认证入口子项）— 2026-09-30 `REQUIRE_REMOTE_HEAD_MATCH=1` 通过；本机/GitHub/远端均为 `15ff029356c7fc63bb0d4444ce271a2323d928c7`，app 和 worker active，schema/runtime/HTTP 通过。另用真实 Chromium 验了 `/physics/documents` 登录重定向及静态资源。真实教师 SSO、文件转换和发布验收不属于此 gate。证据 `remote-strict-release-check-proxy-fix-20260930.log`、`production-browser-check-proxy-fix-20260930.json` |
| OP-05 | 真实教师上传到离线导出全链路 | 实际远端文件、页面、ZIP 有对应证据 | 待教师实用验收 — 正式代码已上线，但没有代入真实教师凭据、生产上传或发布；MinerU pipeline 模型尚缺配置，扫描 PDF 识别当前不可用。待教师 SSO、真实文件草稿、Markdown/图文预览及离线 ZIP 全链路反馈后逐项验收 |
| OP-06 | v12 兼容回退演练 | 旧功能及已发布新内容仍可读，schema 未降级 | 通过（隔离合成副本）— 基线应用在已发布新题副本实启；旧学生练习页与旧图片路由均返回 200，核心 schema 未降级，功能迁移账本和已发布新内容保留。未在生产 live 回退。证据 `m1-op06-baseline-published-content-rehearsal.json` |
| SSO-01 | 生产统一登录链路 | 登录资源可加载，SSO 端点跳转 IdP 并完成现有教师会话认证 | 部分通过（未认证入口）— `/physics/login` 返回 200 并正确加载资源；导题页未认证访问按预期重定向到该页；未输入凭据或完成 IdP callback，真实教师 session 和导题授权仍待用户实用验收。定向路径回归 `tests.test_document_http.test_sso_login_redirect_keeps_proxy_prefix_in_callback_uri`；线上证据 `production-browser-check-proxy-fix-20260930.json` |

## 7. 完成判定

上述强制场景全部通过且真实样本每一道发布题已核对，才能声明本轮施工完成。以下情况不能宣布完成：

- 只有文件按钮，后台仍要求 source_text 或人工制作整理包。
- 只转换 Word，扫描 PDF 没有实际运行；或传统 DOC 被忽略。
- 题干变成截图、公式缺失、选项占位、导出仍依赖线上图片。
- 新导入成功，但两个历史考试仍有未核实错误内容。
- 测试仅管理员操作，普通教师没有走过实际入口。
- 只有模型/库安装状态，没有真实转换质量证据。
- HEAD 一致但 worker 仍运行旧代码，或 release gate 存在失败。

## 8. 最终交付报告模板

```text
施工版本 / 提交：
schema 与兼容回退版本：
实际部署 URL / 验证时间：

已完成用户流程：
真实样本结果（逐份，大题数与作答项数分开）：
公式与插图核对结果：
单题 / 整卷 ZIP 路径及 SHA-256：
离线打开验证环境与结果：

历史修复范围 / mapping hash / migration key：
保护记录比较结果：
备份恢复与撤销演练结果：

自动化测试结果及日志：
严格远端 release gate 日志：
浏览器与屏幕尺寸证据：
物理设备验证情况：

未通过项 / 已知限制 / 下一步：
```

### 继续现场核查：扫描 PDF C 与配套解析文件（2026-09-29）

- 按要求再次运行恢复脚本：本机 `main`、`origin/main`、GitHub `main` 均为基线 `6c79ab00fd6b54048c6cb9840ea930c2089c39c5`；生产 SSH 在 key exchange 阶段 `Connection reset by peer`，没有生产 checkout、schema 或 service 运行版本证据，也没有部署。
- PDF C 真实 SHA-256 为 `2570576fe207be04b7aec2ee2034d9d0694ee4e4e300e39d8a74dc22ec8ba968`，6 页、3,362,261 字节，无文本层。第 1、4 页源图已保存为 `m0-pdf-c-page-1.png` 和 `m0-pdf-c-page-4.png`。当前 adapter 对原文件实际调用返回 `dependency_missing`（MinerU 不可用）；没有 OCR 输出、没有创建候选，CV-03 继续阻塞。
- 配套 Word B 解析 DOCX SHA-256 为 `5af4a7daf46c4a72577593625a314638312a2bb0b1af7d54ba09273b99a141d6`。当前 `docx-native 1.3.0` 实际转换得到 278 个块、19 个资源和 142 个 `formula_requires_review` issue；编号预拆为 1–15。当前工作树没有计划所列 `attach-answers` 路由或教师 UI，因此没有把解析写入题卷候选，也没有发布答案。这个结果只证明 Word adapter 可转换，不能证明答案匹配。
- 本条证据和状态已写入 `m0-pdf-c-and-answer-docx-current.json`。M0/M3 仍部分完成；SP-06 与 CV-03 阻塞，验收清单未整体通过。

- 本次复跑 release gate：本机本地目标 exit 0，288 tests 通过（54.408 秒），compileall、Node 与 `git diff --check` 通过；HTTP smoke 未启用，runtime readiness 仍报告 MinerU/PaddleOCR/MarkItDown/Playwright/Authlib 缺失或未配置。严格 `REQUIRE_REMOTE_HEAD_MATCH=1` 门禁再次 exit 255，SSH `10.50.159.62:22` 握手被重置，无部署。`current-release-gate-rerun.json`。

### 本轮答案附件界面与真实题号匹配续验（2026-09-29）

- 在隔离教师应用增加答案/解析文件上传、同卷任务选择、题号匹配预览与逐条勾选写入。答案内容仍以 Markdown 保存为待复核状态，不发布、不判分；已有答案、已入库题目和映射版本变化会阻止覆盖。写入事务具有请求键幂等性，部分失败整体回滚。
- 配套解析 DOCX（SHA-256 `5af4a7daf46c4a72577593625a314638312a2bb0b1af7d54ba09273b99a141d6`）与真实 Word B 题卷在隔离 SQLite 内经当前 adapter 转换后，预览给出题号 1–15 的 15/15 唯一匹配，0 条冲突/歧义。本次只查看预览，没有应用答案或向生产写入。浏览器实际打开隔离教师复核页，确认同卷答案任务控件、原卷 PDF、15 个 Markdown 编辑器与预览可见；用户切换浏览器焦点期间未完成答案控件点击流程，因此答案附件 UI 写入依赖 HTTP 集成测试验证。
- 定向 HTTP 测试覆盖答案任务上传与列出、唯一匹配预览、勾选应用一题、事务回滚、相同请求幂等、冲突重放、答案待复核状态及不自动发布。完整本机 gate：289 项测试通过（57.793 秒）、compileall、Node 与 `git diff --check` 通过；HTTP smoke 未启用。runtime readiness 仍显示 MinerU、PaddleOCR、MarkItDown、Playwright PDF、Authlib 缺失或未配置。
- 严格远端 gate 再次退出 255（`10.50.159.62:22` SSH 握手被重置）；没有生产 schema/备份/SSO/runtime 新证据，没有部署。CV-03 继续阻塞；M0/M3 仍部分通过；SP-06 只通过真实样本唯一题号预览和合成安全写入路径，重复题号专门样例、答案语义审查及生产流程待验；M7 阻塞，整体未通过验收。证据：`m0-real-word-b-answer-match-preview.json`、`current-release-gate-after-answer-attachment.log`、`remote-release-check-after-answer-attachment.log`、`current-release-gate-after-answer-attachment.json`。

### 答案匹配真实浏览器预览与扫描件识别质量诊断（2026-09-29）

- 本机隔离 SQLite 教师应用在 Chrome 实际操作同卷任务选择、读取答案任务并生成匹配预览。页面显示两条可编辑 Markdown 答案建议；未勾选、确认按钮保持禁用。数据库复查两题仍为 `answer_state=missing`、复核版本 1、0 publication。此浏览器夹具是合成任务；真实 Word B/配套答案 DOCX 的 15/15 题号匹配证据仍是隔离库 API 预览，不声称它们在同一浏览器页面实测。
- 新增专门 HTTP 集成覆盖重复题号：两个题卷候选都编号 1 时，匹配预览返回 `question_match_not_unique` 并排除该答案；题号 2、3 的唯一建议仍可用。将其与题号 3 的陈旧复核版本放进同一批写入时，整批回滚；幂等重放和不覆盖断言继续通过。定向测试 `tests.test_document_http.DocumentHTTPTests.test_teacher_can_preview_and_attach_answers_without_publishing_or_overwriting` 通过。
- 为诊断真实扫描 PDF C（SHA-256 `2570576fe207be04b7aec2ee2034d9d0694ee4e4e300e39d8a74dc22ec8ba968`），本机 macOS Vision 对源第 1、4 页图像给出 88/78 行，平均行置信度 0.3227/0.3026；大部分行低于 0.8，中文和物理符号有明显误识。此诊断不是产品 adapter，未生成、保存或发布候选；扫描 PDF 识别验收仍失败。完整输出 `m0-pdf-c-vision-ocr-diagnostic.txt` 与摘要 `m0-pdf-c-vision-ocr-diagnostic.json`。
- 当前本机门禁通过：289 项测试（56.490 秒）、compileall、Node、runtime readiness、`git diff --check` 通过；HTTP smoke 未启用。runtime readiness 仍显示 MinerU、PaddleOCR、MarkItDown、Playwright PDF 和 Authlib 缺失或未配置。严格远端门禁退出 255，SSH 握手重置，未部署。证据 `current-release-gate-after-duplicate-answer-test.log`、`remote-release-check-after-duplicate-answer-test.capture.log`、`current-release-gate-after-duplicate-answer-test.json`。
- 本节只更新已取得证据的子项。M0/M3、SP-06、CV-03 仍阻塞或部分通过；M7 仍待严格远端检查与生产运行态证据。不得据此宣布整体验收完成。

### 历史修复 migration_key 重放与冲突验收（2026-09-29）

- 对 `apply_preview` 增加合成回归：先应用映射并重放同 key/同内容，确认返回相同 correction ID、`idempotent=true`；再以同 key 更改映射 `reason`，确认 `migration_key reused with different mapping` 冲突、事务回滚，原 correction 仍是唯一 active 行，原 mapping hash 未变。
- 定向测试通过（1 项）；完整本机门禁通过 289 项测试（56.901 秒）、compileall、Node、runtime readiness 与 `git diff --check`，HTTP smoke 未启用。依赖 readiness 仍显示 MinerU、PaddleOCR、MarkItDown、Playwright PDF 与 Authlib 缺失或未配置。证据 `m6-migration-key-idempotency-test.log`、`current-release-gate-after-mg04-idempotency.log`。
- MG-04 仅按合成副本证据标为通过；生产数据库、真实历史映射与撤销后新增作答保留仍未验。M0—M7 整体保持未完成。


### ACL 范围回归与当前远端只读状态（2026-09-29）

- 新增 HTTP 集成测试为实际 worker 生成候选与 PNG 资源，确认所有者能读取；同校非所有者教师请求 task list/detail/items/source/preview/export/asset/review 以及 cancel/attach-answers/item-preview/item-save 均返回 403，外校教师对应请求均返回 404。两者的 task list 都不包含该任务；测试后 review revision、answer Markdown、publication 数量和 task status 均未改变。
- 该用例只验证未发布导入任务。已发布 revision 按题库共享策略读取、管理员同校例外、学生访问边界和生产账号实际授权仍未验证，因此 ACL-01/02 记录为部分验证，不标通过。定向证据：`m6-acl-task-owner-scope-test.log`；实现测试：`tests.test_document_http.DocumentHTTPTests.test_document_task_reads_and_writes_are_limited_to_owner_and_school`。
- 本机 release gate 通过：290 项测试（62.643 秒）、compileall、Node、runtime readiness 与 `git diff --check` 通过；HTTP smoke 未启用。runtime readiness 仍缺少或未配置 MinerU、PaddleOCR、MarkItDown、Playwright PDF 与 Authlib。日志与摘要：`current-release-gate-after-acl-scope-test.log`、`current-release-gate-after-acl-scope-test.json`。
- 当前严格远端 release gate 再次退出 255，SSH `10.50.159.62:22` key exchange 返回 `Connection reset by peer`，未部署且生产 schema 仍未知。最新公开只读 HTTP 检查（未认证、未写入）显示 `/physics/login` 200、`/physics/assets/app.css` 与 `app.js` 500、新的 `document-import.css` 与 `question-rendering.css` 404。证据：`remote-release-check-after-acl-scope-test.capture.log`、`current-production-readonly-http.json`。
- 本机 OCR runtime 盘点只找到 PDF 渲染工具 `pdftoppm`；Tesseract/OCRmyPDF 与 Python OCR 包（MinerU、PaddleOCR、EasyOCR、RapidOCR 等）均不可用。盘点不是识别结果，PDF C 仍没有候选和可编辑 OCR 正文。证据：`m0-local-ocr-runtime-availability.json`。
- MG-04 的同 key 相同映射重放幂等、同 key 不同内容冲突回滚仅在合成 SQLite 验证；历史生产题映射应用、生产撤销与新增作答保留未验。M0—M7 整体仍未通过验收。


### 生产 v11 副本迁移、SSO 重定向与 MinerU 模型配置核查（2026-09-29）

- 恢复脚本重新连通生产主机：远端 checkout 与 GitHub main 均为 `6c79ab00fd6b54048c6b9840ea930c2089c39c5`，auto-update timer enabled/active，应用进程在线，HTTP root 200；生产 schema 只读确认为 v11。证据 `production-state-current-20260929.json`。
- 使用 SQLite online backup API 从生产 `data/school.sqlite3` 只读创建远端临时副本，以当前工作树迁移代码升至 v12；备份前后指纹一致，64 张旧表及 10,338 行在迁移后关闭并重开仍逐表相同，包含身份、首次作答/判分、题目标签、答题卡模板和扫描图像资产、错题与练习记录。integrity_check=ok，外键错误 0。源库仍是 v11；临时副本、迁移代码及 PDF 暂存目录均已清理。证据 `m1-production-copy-migration.json`。
- 最新公开只读入口为 login 200、旧版 app.css/app.js 200；新功能 document-import.css/question-rendering.css 仍 404，说明当前工作树实施未部署。一次未认证 SSO GET 返回 303 到现有 school-platform IdP；未输入凭据，未验证教师 session/回调完成。证据 `current-production-readonly-http.json`。
- 生产 Python 环境装有 MinerU 3.4.0 等包，但进程没有 MinerU 配置环境，`~/mineru.json` 不存在，也没有 pipeline model directory 配置。因此安装可导入不等于 OCR 可运行；adapter 要求模型配置，会 fail-closed，PDF C 尚未产生 OCR 输出。证据 `m0-production-mineru-model-config-audit.json`。后续模型试验使用独立临时目录，不写 systemd 或生产配置。
- 本轮本机完整 gate 的 291 项测试、compileall、Node 与 `git diff --check` 通过；HTTP smoke 未启用。新增文件副本迁移用例覆盖全部 v11 表与持久化重开。严格远端 gate 尚未针对最终实现重跑。


### PDF C 当前产品 OCR、图形选项映射、真实浏览器预览与最终本轮门禁（2026-09-29）

- 已先运行 `scripts/hsp_recover_context.sh` 并阅读仓库 `AGENTS.md`，按现场确认本机 `main`、`origin/main`、GitHub `main` 和生产 checkout 均为基线 `6c79ab00fd6b54048c6b9840ea930c2089c39c5`；实现代码仍在 dirty worktree。生产 `data/school.sqlite3` schema v11。auto-update timer 与应用进程 active，根 HTTP 200。
- 用生产数据库 SQLite online backup API 复制到临时文件，再由当前迁移代码升级该副本至 v12。关闭重开后 64 张旧表、10,338 行的旧列类型化指纹完全一致，包含身份、首次作答、标签、答题卡和练习历史；integrity_check=ok，foreign_key_check=0。live 数据库仍为 v11，未迁移。证据 `m1-production-copy-migration.json`。
- 真实 PDF C（SHA-256 `2570576fe207be04b7aec2ee2034d9d0694ee4e4e300e39d8a74dc22ec8ba968`）通过当前产品 `convert_document`/MinerU pipeline 3.4.0 完整运行，OCR 临时 config、缓存和模型位于生产主机独立 `/tmp`，未写 service config、数据库或发布目录。六页均处理，86 个正文/图块、23 个图像资产、102 个公式片段，产生 9,045 字符可编辑 Markdown；source markdown SHA-256 `75b97e4b…`。完整产品 OCR 原件保存于 `pdf-c-mineru-ocr-final/ocr/`。
- 修复后的当前拆题器对完整转换输出重放（source Markdown、content-list 和 23 个图像 payload 与真实完整 adapter run 哈希相同）：识别题号 1–15；第 1–10 题为 A–D，第 11–15 题各保留 3 个小问；无占位项。第 3 题根据原卷四个独立图块下方的 A–D 标签与版面框关系，得到四个真正 Markdown 图形选项；铜管/磁铁示意图留在题干。第 5 题的 D 标记紧跟数字、第 8 题比值尾数与 D 粘连，均能拆出完整 A–D。当前详细结构与 asset/source spans 见 `m0-pdf-c-current-split.json`；5 个选项后的图片定位均显式产生教师复核 issue，避免把未标记图片自动塞进 D 项。
- 本地 loopback 预览用 Chrome 154/Playwright 打开真实输出：15 个 Markdown 编辑器及产品 renderer 预览，KaTeX 102 个、0 错误，23/23 图像加载。浏览器实际更改第 3 题 Markdown 并更新预览，临时文字出现在渲染视图；reload 后恢复，未保存、入库或发布。第 3 题截图显示题干示意图独立于 A–D 四张选项图；第 3/5/8 题浏览器结构均为四个选项；额外图块位置 issue 在复核清单中可见。证据 `m0-pdf-c-current-browser-review.json`、`m0-pdf-c-current-browser-review.png`、`m0-pdf-c-question3-browser-review.png`。
- 仍有 16 个公式语义事项及全题 OCR 文本、公式和选项内容需要教师对照原卷；例如第 5 题存在“A/D、B/D”文字粘连。该产品样本尚未在实际导入任务 UI 保存、批量入库或整卷导出，未发布任何 PDF C 候选。传统 DOC、失败/重启/限额流程、生产模型配置及生产教师 SSO 仍未验。
- 最终本机项目 release gate 使用 `/tmp/hsp-doc-test-env/bin/python` 通过：296 项测试（56.885 秒）、compileall、Node、runtime readiness 和 `git diff --check`；HTTP smoke 未启用。日志 `local-release-check-final.log`。先前系统 Python 缺少 `cryptography` 的失败是运行环境选择错误，不计作产品代码结果。
- 严格 `REQUIRE_REMOTE_HEAD_MATCH=1` gate 通过基线检查：本机/远端/GitHub ref 一致，服务进程、根与登录页健康；它不包含本次未提交代码。生产当前 `/physics/login` 200、旧 app CSS/JS 200、新导入/渲染资源 404；未输入生产凭据，SSO 仅观察到 303 跳转。未部署。证据 `remote-release-check-final.log`、`current-production-readonly-http.json`。
- M0、M2、M3、M4、M5、M6、M7 仍有未完成的强制验收；尤其传统 DOC、Word A/B 逐题核对、PDF C 公式/文字语义签字和实际整卷入库/导出、迁移回退、生产教师 SSO、旧题生产映射与撤销、目标代码远端运行证据。不得标记整体验收通过。

### M6 联考旧图真实目标副本审计（2026-09-29）

- 从生产 v11 数据库只读打开源库，使用 SQLite online backup API 在远端创建 mode-0600 临时副本；核对 64 张表的逐表行数与源库一致、总行数 10,339、`integrity_check=ok`，在副本重开后查询并删除临时文件。生产源库没有被修改。审计记录不含学生姓名或原始账号 ID，只保存记录 ID 的 SHA-256 与聚合计数。
- 按设计文档所载的错误图片 SHA-256 `4cd8b0bf2288b245295e15612b5d1e777819607508a396cdb3232b3f91dbbe8a`，副本中唯一匹配到第 1—4 题各一条旧题记录；每题在两场测评中各有一个历史快照。四题均有 5 个旧题标签；两场测评分别有 51/28 名参与者、204/108 条受影响首次作答、92/52 条受影响错题记录，均关联答题卡模板；当前对应重做记录为 0。每个旧题与快照内容均已记录哈希，见 `m6-production-copy-real-target-audit.json`。
- 视觉核对原始 PDF（SHA-256 `2570576fe207be04b7aec2ee2034d9d0694ee4e4e300e39d8a74dc22ec8ba968`）第一页可见第 1—4 题；对应旧 `paper-left.png`（SHA-256 `4cd8…be8a`）实际显示第 5—7 题。此证据证明旧图错配，并不能替代新题全文核验。当前 PDF C OCR 候选仍有 16 个公式及 5 个选项后图位需复核，未有教师签字的 verified 新修订，因此没有生成可应用的真实 mapping，也没有调用 apply/rollback。
- 新增 fail-closed 回归：将目标修订置为 `draft` 后，`build_preview` 必须报告 `target revision must be verified`，且纠错记录数保持为 0。定向测试 2 项通过，完整当前本机门禁 297 项通过（56.785 秒）；`m6-unverified-revision-preview-test.log`、`local-release-check-after-m6-revision-guard.log`。本地 runtime readiness 仍缺 MinerU/PaddleOCR/MarkItDown/Playwright/Authlib；HTTP smoke 未启用。
- 状态：MG-01 仅真实目标定位部分通过；MG-02 阻塞待逐题内容/答案复核与正式预览。MG-03/04 的陈旧预览、幂等冲突和撤销能力仍只有合成测试证据；MG-05 的精确生产副本迁移/历史指纹验证单独通过，生产 live 库未迁移。


### 本轮 schema 账本、旧版回退和发布门禁（2026-09-29）

- 为避免基线 v11 初始化把较新库的 `PRAGMA user_version` 从 12 写回 11，整卷导入 schema 改为独立账本：核心 `user_version` 保持 11，`app_schema_migrations` 记录 `document_ingestion=12`。空库、v11 副本、故障回滚和重复初始化的测试通过。
- 重新从生产只读打开 `school.sqlite3`，SQLite online backup 到远端临时文件后迁移并关闭重开：源库未改动；64 张旧表、10,339 行类型指纹一致；核心版本 11、功能版本 12、完整性 `ok`、外键错误 0。临时包和副本已清理。证据 `m1-production-copy-migration.json`。
- 扩充新题发布兼容投影：`questions.stem`、`options_json` 与解析保留旧版可读纯文本；学生可见 Markdown 图片同时生成兼容 PNG 资产，答案/解析专用图片不会混入学生题图集合。当前 renderer 继续使用完整 Markdown 修订。
- 在含已发布 Markdown 题、学生首次作答和练习记录的隔离副本启动基线 `6c79ab00`。旧版学生练习页显示题干、选项文本/公式和图题标记，`exam-media` 返回有效 PNG；前后核心版本 11、功能账本 12、文档/修订/题目数量及 SQLite 完整性不变。旧版把公式留作 LaTeX 文本、把图片放在旧题图区域，不保留新 renderer 的精确内联排版；未在生产库回退。证据 `m1-op06-baseline-published-content-rehearsal.json`。
- 为保证意外转换或存储异常也会撤销整批，发布事务增加统一异常回滚兜底。HTTP 集成在题目、内容修订已写入但 PNG 投影故障时注入异常，核对所有发布相关表及幂等键保持原值、SQLite 连接退出事务态，再用同一 request key 成功重试。
- 本地发布门禁通过：298 tests（56.910 秒）、compileall、Node、runtime readiness、`git diff --check` 通过；HTTP smoke 未启用。严格远端门禁通过，但本机 HEAD、远端 checkout、origin/main、GitHub main 都仍是基线 `6c79ab00fd6b54048c6b9840ea930c2089c39c5`，故门禁验证的是现有部署，不是当前 dirty 实现。日志 `local-release-check-after-transaction-rollback.log`、`remote-release-check-after-transaction-rollback.log`。
- M0/M2/M3/M4/M5/M6/M7 仍有未完成项：真实样本全题语义/公式审校、旧 DOC、扫描卷整卷 UI 入库与离线导出、生产教师 SSO 流程、旧题完整生产映射及撤销、当前实现提交/部署后的远端验收均未完成。只将本轮实际通过的 DB-06、OP-06 等子项标为通过，不代表整体验收完成。

### PDF 原件预览回退、隔离任务浏览器验收与门禁复跑（2026-09-29）

- 修复受保护任务预览路由：PDF 尚无转换器生成预览文件时，直接 inline 返回已上传原 PDF；其他文件类型继续使用转换预览。扩展分片上传 HTTP 测试，确认转换前预览返回原始字节、`application/pdf` 和 inline disposition。定向测试通过。
- 当前 PDF C 候选任务页在 Chrome 实际显示 15 个可编辑 Markdown 编辑器及独立预览、102 个 KaTeX 节点/0 个语法错误、原卷 PDF 以及 23 项图片资源。预览 endpoint HTTP 200，返回 3,362,261 字节、PDF header `%PDF-1.7`；Chrome 查看器显示真实 6 页原件，页面截图可见第 1 页题目正文和图形。
- 在页面仅勾选 Q1、保持“已对照原卷核对”未勾选后点击批量入库。页面显示 `Resolve or explicitly review all open items before publishing`；SQLite 中 Q1 仍为 `needs_review`，保留 1 条 `formula_ocr_requires_review`，任务 publication 数为 0。批量按钮先保存草稿，修订号从 1 升到 2；没有发布题目或写生产数据。此处用于验证未解决事项阻断，不能视为内容审校。
- 本地 strict release gate 通过：298 项测试（56.129 秒）、compileall、Node、runtime readiness 和 `git diff --check` 均通过；临时测试 venv 未安装 OCR/SSO/PDF 运行依赖，HTTP smoke 未启用。`REQUIRE_REMOTE_HEAD_MATCH=1` 远端 gate 通过，但本机 HEAD、远端 checkout、origin/main、GitHub main 都仍是部署基线 `6c79ab00fd6b54048c6b9840ea930c2089c39c5`；未提交、推送或部署当前实现，新功能运行态没有生产证据。
- 证据：`m0-pdf-c-current-app-e2e.json`、`m0-pdf-c-app-task-review-batch-blocked.png`、`m0-pdf-c-app-task-original-pdf.png`、`local-release-check-after-pdf-source-preview.log`、`remote-release-check-after-pdf-source-preview.log`。M0/M4 的 PDF C 全卷发布与离线导出、教师逐题语义核对以及 M7 新实现上线仍阻塞；不将整体验收标记为通过。

### PDF C 来源页直达与当前基线门禁（2026-09-29）

- PDF C 真实任务复核卡片增加来源页链接。浏览器实测发现仅改变当前 PDF iframe 的 `#page` 片段不会让 Chrome 内置 PDF 阅读器切页；链接改为同时带不同的 `jump_page` 查询参数和 `#page` 片段后，点击 Q12“跳转到第 5 页”使原卷查看器显示 `5 / 6`，页面内容为第 12 题及其电桥图。此记录只覆盖一个定位样例；所有样本的页/块映射仍须逐题核对。截图 `m4-source-page-jump-pdf-c.png`，结构证据 `m4-source-page-jump-browser.json`。
- 为来源链接新增 HTTP 断言；定向用例 `tests.test_document_http.DocumentHTTPTests.test_document_task_reads_and_writes_are_limited_to_owner_and_school` 通过。当前本机完整门禁通过 299 项测试（59.569 秒）、compileall、Node、runtime readiness 与 `git diff --check`；测试 venv 仍缺 MinerU、PaddleOCR、MarkItDown、Playwright 和 Authlib，HTTP smoke 未启用。日志 `local-release-check-after-source-page-jump.log`。
- 严格远端门禁通过：本机 `main`、`origin/main`、GitHub `main`、生产 checkout 均为 `5c14e9b002ff3de13c11578c071105f807bd7391`；auto-update timer active，最近一轮 fetch 与健康检查成功；服务、root、公开登录和 production runtime readiness 通过。生产 `user_version=11`、`integrity_check=ok`，未发现 `app_schema_migrations`；`/physics/assets/document-import.css` 和 `/physics/assets/question-rendering.css` 仍为 404，SSO 实际教师会话未验证。严格门禁只覆盖已部署基线，当前导入实现仍是未提交工作树内容。日志 `remote-release-check-after-source-page-jump.log`。
- M0—M7 均不整体通过。PDF C 公式/文字语义、五个图位和全题复核、Word A/B 逐题复核、传统 DOC、整卷真实发布和离线导出、生产教师 SSO、历史题逐题映射/正式预览/可撤销修复，以及当前实现的部署后验证仍未达标。

### M0 派生 DOC 格式控制与运行版本复核（2026-09-29）

- 用户提供的 Word B 原件 `2027届高三物理周测2.docx` SHA-256 仍为 `19ceac89048f0d6c42e23ae34c9672b45996d0605d2694102bac98adf7986b47`。为推进缺失的旧格式路径，复制样本后用 LibreOffice 26.2.4.2 导出 MS Word 97 二进制 `.doc`；文件头为 Compound File magic `d0cf11e0a1b11ae1`。此为 DOCX 派生控制样本，不冒充原生 `.doc` 输入。
- 实际调用 `convert_legacy_doc` 将该二进制 DOC 转为 23 页 PDF（SHA-256 `3addcf86c021c659b60bc36e343bdb2e657ecdd6801dbd7d8c359831c9c02f34`）；源 DOC 转换前后 SHA 相同。PDF 文字层 9,894 字符，检测到题号 1–15；Chrome PDF 查看器显示第 1/23 页的标题、Q1 文字选项、火车图及 Q2 电路图。没有对该 DOC 运行 MinerU，也没有得到可编辑 Markdown 候选，因此 CV-04 仍未通过。新增路由/来源追溯回归测试 `test_legacy_doc_routes_through_pdf_recognition_and_keeps_source_provenance` 通过。证据目录 `legacy-doc-control-word-b-20260929/`，摘要 `legacy-doc-conversion-result.json`，浏览器截图 `chrome-legacy-doc-preview.png`。
- 本机完整 release gate 通过 300 项测试（58.975 秒）、compileall、Node、runtime readiness 与 `git diff --check`；本机 test venv 的 OCR/SSO/PDF 依赖仍缺失。日志 `local-release-check-after-legacy-doc-control.log`。
- 当前本机 `main`、`origin/main`、GitHub `main` 和远端 checkout 均为 `a5ab6670dce154ce8bd6c58e3bd41df278754320`。严格远端 gate ref/process/HTTP/runtime 检查通过，日志 `remote-release-check-after-legacy-doc-control.log`。但生产服务 PID 115814 于 16:42:52 启动，基线提交时间为 16:43:32；17:24:11 auto-update 成功 fetch 后仍报 app service 已运行且 PID 未变，故无法证明进程加载的是 `a5ab6670`。生产 `school.sqlite3` 只读检查为核心 v11、完整性 `ok`、无 `app_schema_migrations`；新导入/渲染资源仍 404。没有生产写入、重启或部署。
- M0—M7 均继续保持未完成；原生 `.doc` 样本、legacy DOC 的完整 OCR/Markdown 识别、Word/PDF 语义签字、生产教师 SSO、PDF C 整卷发布/离线导出、旧题正式映射与撤销、以及当前实现的部署和运行版本证明仍缺。

### M0 PDF C 来源纠错建议复核（2026-09-29）

- 将原卷高清局部图与 byte-identical MinerU content-list 的来源块对照，为 Q1 两条衰变方程箭头、Q4 选项 A 的 `\sqrt{2}` 和 Q14 小问 (1) 的 `v_0` 生成可编辑 Markdown 建议，均附题号、页码、块号和原 OCR 片段。第二条衰变方程保留原卷字面 `X`，未推测核素或粒子语义。
- 建议保存在 `output/document-ingestion-evidence-20260929/m0-pdf-c-source-correction-proposals.json` 与 `.md`；没有写入导入任务、数据库或生产环境，没有解除任何复核项。执行者是实现 QA，教师签字仍缺。公式复核总数保持 31，5 个图位问题和全卷语义复核仍在。
- 本项只推进可审阅的正文纠错材料，不改变 CV-03/M0 状态，也不允许批量入库或宣称 PDF C 语义通过。

### UP-02 浏览器断点续传与 UP-03 分片幂等（2026-09-29）

- 改造主试卷上传页：上传开始前检查 50 MiB 上限；将教师作用域、文件名/大小/SHA、标题、角色和 request key 的短期恢复信息存入 `sessionStorage`；刷新后重选相同文件会调用相同幂等键，服务端返回已收分片，前端跳过已确认部分。收到所有分片并成功完成后清除恢复记录；恢复记录按教师隔离，保留 24 小时、最多两条。
- Chrome 本地隔离应用真实选择 9,147,057 字节 Word B DOCX；浏览器故障注入让服务端确认分片 0 后、分片 1 请求发送前中断。刷新后重选相同原件，标题恢复，上传沿用同一 request key 和 upload ID，只提交分片 1—17，没有重发分片 0，最终打开复核页。SQLite 有且仅有一条本次上传记录；数据目录与生产隔离。结构化证据 `output/document-ingestion-evidence-20260929/upload-resume-browser-20260929/upload-resume-browser-e2e.json`，截图记录中断和续传结果。
- API 回归扩展覆盖相同分片重复请求幂等返回 200、相同序号不同内容返回 409，以及重放相同 create request key 后收到已存分片。两项定向 HTTP 测试通过，Node 语法检查、compileall 和 `git diff --check` 通过；完整本地 gate 待本轮收尾后运行。
- 文件完整性失败关闭覆盖缺分片、整文件 SHA-256 不符和以 `.docx` 命名的 PDF 字节，分别返回 409/400/422，并断言没有创建任务或文件。上传限制覆盖 50 MiB 精确值可建上传记录、超 1 字节返回 413；原始 HTTP 请求缺 `Content-Length` 返回 400；超出 API 800 KiB 长度的请求只发 header、不发 body，服务器立即返回 413；非法 UTF-8 JSON 返回 400。四项定向测试通过；UP-06 仍待真实 50 MiB 浏览器选择验证。
- 新一轮 `hsp_recover_context.sh` 读到生产 `user_version=11`、`integrity_check=ok`、无迁移账本；应用 PID 仍为 115814、服务/root HTTP 200、runtime readiness 通过。生产 timer active，但本次 GitHub HTTPS fetch 超时并保留现有 checkout；未迁移、重启、写入或部署生产。

### 上传失败关闭边界与本轮 release gate（2026-09-29）

- UP-05 定向 HTTP 测试通过：缺分片 409、整文件 SHA 不符 400、`.docx` 扩展名对应错误容器 422；每个失败后均无解析任务和文件。分片相同内容重放 200、不同内容重放 409、同 create request key 恢复已有分片与 UP-02 的真实 Chrome 断点续传一起覆盖。
- UP-06 增加原始套接字覆盖：缺 `Content-Length` 400、只声明超出 800 KiB 上限而不发送正文时立即 413、非法 UTF-8 JSON 400；50 MiB 上传元数据精确边界接受、超 1 字节 413，浏览器上传前检查文件大小。在该次检查时，真实 50 MiB 浏览器选取/传输尚未验。
- 定向失败/边界 HTTP 测试 4 项通过；临时依赖环境的全量本机门禁通过 303 tests（59.789 秒）、compileall、Node 与 `git diff --check`，日志 `local-release-check-upload-resume-boundaries-final-20260929.log`。无 HTTP smoke；本机精简依赖的 runtime readiness 标记 MinerU、Playwright PDF、Authlib 缺失。
- 严格远端 gate 本轮 exit 0，基线 refs、服务进程、HTTP 与 runtime readiness 均通过，日志 `remote-release-check-after-upload-resume-boundaries-20260929.log`。之后只读重查生产：HEAD 仍是 `a5ab6670dce154ce8bd6c58e3bd41df278754320`，进程 PID 115814 自 16:42:51 运行，v11 `integrity_check=ok` 且没有新功能迁移账本；登录 200、SSO 303 到原 IdP，新导入和渲染 JS 仍 404。详见 `production-schema-current-after-upload-resume-20260929.json`、`production-public-routes-current-after-upload-resume-20260929.json`。当前功能未提交/部署，加载版本与生产教师 SSO、M0—M7 全流程仍未验；没有生产写入、重启或部署。

### EX-04 离线导出失败关闭（2026-09-29）

- 修复已发布题离线导出时的资源读取错误：`DocumentStoreError`（缺文件、读取限制或 SHA 校验失败）会转成明确的 `QuestionExportError`，HTTP 返回 `422 export_failed` JSON。
- 隔离 HTTP 测试实际创建并发布含图题目后，注入存储文件缺失，确认响应为 JSON 422、不是 ZIP；再将导出上限限制为 1 字节，确认同样失败关闭。已有学生请求含答案导出用例继续返回 403。单元测试覆盖不支持 MIME、哈希不符和超限。6 项相关测试通过，日志 `output/document-ingestion-evidence-20260929/export-fail-closed-tests-20260929.log`。

### ACL-03—05 学生题图与文档入口隔离（2026-09-29）

- 在隔离 HTTP 测试中创建并发布含图题、绑定到一个班级的测评。所属学生仅在发布后可读题图；同校另一班学生对同一 snapshot/asset 返回 403，证明授权同时检查具体测评参与关系。
- 对所属学生，强制追加 `solution=1` 读取题图返回 403；原卷 source/preview、导入资产、复核草稿、单题/整卷及含答案导出都返回 403，响应不含测试卷正文或答案。跨 Origin 请求与无效 session 写入返回 403；有 `must_change_password` 状态的会话返回 409 并要求先改密。
- 定向 HTTP 回归 `test_teacher_can_publish_editable_question_and_download_offline_image_zip` 与 `test_document_mutations_reject_cross_origin_and_students` 均通过；合并日志 `access-export-security-tests-final-20260929.log`。只验证隔离账号/数据库，不代表生产 SSO 账号已验。
- EX-04/ACL 定向组 7 项通过；其后补做 ST-03 路径与压缩比回归，存储用例共 8 项通过。最新完整本机门禁通过 305 tests（57.978 秒）、compileall、Node、runtime readiness 与 `git diff --check`，日志 `local-release-check-storage-paths-final-20260929.log`。严格 `REQUIRE_REMOTE_HEAD_MATCH=1` exit 0，但 refs 仍为基线，日志 `remote-release-check-storage-paths-final-20260929.log`；当前代码未提交或部署。


### 用户范围补充与最新门禁（2026-09-29）

- 解析质量验收允许使用远端服务器环境中的真实解析产物；不要求本机安装 MarkItDown、MinerU 或配置生产模型，也不把本机精简测试环境的 `missing_dependency` 判为解析失败。PDF C 已在远端服务器隔离 `/tmp` 配置/模型下，通过当前产品 adapter 实际运行 MinerU 3.4.0，生成可编辑 Markdown、页/块来源和图像资产；这是识别证据。该次运行没有改生产服务配置。
- 生产应用服务仍缺 MinerU 模型配置，功能代码未部署；这是运行态/部署限制，不能与上述识别结果混为一项。M3 仍部分完成，理由是 OCR 语义与 Word A/B 公式映射尚待教师逐题核验、原生 DOC 未测、失败/重启行为及整卷质量未验。
- EX-04 增加真实 HTTP 权限异常注入：图片存储读抛 `PermissionError` 时也返回 `422 export_failed` JSON，不返回 ZIP。EX-04/ACL-03—05 定向组 7 项通过，日志 `access-export-security-tests-final-20260929.log`。
- 最新本机门禁 303 tests（59.456 秒）、compileall、Node、runtime readiness、`git diff --check` 通过；日志 `local-release-check-acl-export-final2-20260929.log`。严格远端门禁 exit 0，日志 `remote-release-check-acl-export-final2-20260929.log`；所有 ref 仍为基线，当前实施代码尚未提交或部署，因此该门禁不证明新功能线上运行。


### M1 DOCX 容器路径边界续验（2026-09-29）

- 检查发现 DOCX 上传前 ZIP 容器校验将反斜杠归一化，但 POSIX `Path.parts` 会丢弃 `.` 路径段，且未识别 Windows 盘符路径。现改为对规范化后的原始 `/` 分段检查 `.`/`..`，并拒绝 `C:/...` 与 `C:...` drive 路径。
- 新回归覆盖 `../outside`、多层与反斜杠穿越、`.` 路径段、Windows drive 路径和 200:1 以上压缩比。存储模块 8 项通过；全量本机门禁 305 项通过。该项只证明上传前 DOCX 容器边界，不代表磁盘耗尽/原子写故障及所有归档组合已覆盖。
- 严格远端门禁 exit 0，refs 为基线且新代码未提交/部署；见 `remote-release-check-storage-paths-final-20260929.log`。


### M1 存储 I/O 异常失败关闭（2026-09-29）

- 文件同步触发 ENOSPC 时，原子写删除临时文件且不发布目标对象。分片写入遇 `PermissionError` 返回 503 `storage_unavailable`、数据库无分片行，可重试成功；完成上传遇 ENOSPC 返回 507 `storage_full`、上传状态恢复为 `uploading`、无 document/task 行，释放故障后重试进入 queued。图片损坏仍由解码校验拒绝。
- 定向失败用例 3 项通过。最新本机门禁 308 tests（63.225 秒）、compileall、Node、runtime readiness 与 `git diff --check` 通过；严格远端门禁 exit 0，但核对的是未包含 dirty 功能代码的基线。实际磁盘满和真实只读文件系统未现场演练。

### M1 上传崩溃一致性与孤立对象回收（2026-09-29）

- 在隔离 HTTP/SQLite 环境中让独立上传子进程完成原件 `rename` 后立即收到 POSIX `SIGKILL`，准确落在原件持久化与 document/task 数据库事务之间。进程退出码为 `-SIGKILL`；检查确认原件存在、upload 保持 `assembling`，`document_files` 与 `document_parse_tasks` 均无该上传记录。将租约置为过期后重新调用 complete，系统恢复并复用确定性 document ID，只生成一份 document、task 与 batch。另一个故障注入用例验证事务异常回滚后同样可重试。
- 新增 `document_maintenance.collect_orphaned_objects` 与 `scripts/hsp_document_gc.py`。CLI 默认以只读数据库连接生成 JSON dry-run 清单，只有显式 `--apply` 才回收对象；应用时以 SQLite 写事务固定引用快照，并先围栏过期上传。清理遵守保留期，保留数据库已引用文件、未完成上传、有效 assembly 租约及任何处于 running 状态的 worker 目录/未知 conversion 产物；测试验证可预览、活跃对象保护及任务 fencing 后对旧孤儿的回收。生产存储未运行该清理器。
- 定向恢复/清理测试通过，包括 GC 删除目录遇 `PermissionError` 后上传仍为 `expired`、文件可后续重试；本机完整门禁 320 tests（62.868 秒），compileall、Node、runtime readiness 与 `git diff --check` 均通过，日志 `local-release-check-orphan-recovery-final-20260929.log`。严格远端门禁 exit 0，日志 `remote-release-check-orphan-recovery-final-20260929.log`；检查的是基线引用、现存服务、HTTP/runtime readiness，dirty 功能代码仍未提交/部署，生产 v11/schema、PID 与新资源 404 状态未改变。
- ST-01、UP-07、WK-02 与 WK-03 已按隔离环境通过更新子项状态。并发 worker 领取、超时/取消/缺模型边界，以及 M0 的教师语义复核和整卷导入/导出仍未验；M0—M7 整体继续保持未完成。

### M3 worker SIGKILL 后恢复与孤立转换产物回收（2026-09-29）

- 隔离 worker 子进程使用确定性合成 converter，完成 conversion 目录的原子持久化后立即 SIGKILL；数据库仍只有 running task，没有 conversion 或 parsed candidates。确认进程退出码 `-SIGKILL`，再令租约过期并调用 recovery，任务 generation 从 1 增至 2、attempts 从 1 增至 2。清理器的删除失败回归也确认目录权限异常不会令 upload 重新激活。
- 任务重新排队后，对过保留期的未引用 conversion 目录运行隔离 GC 并回收旧产物；随后第二代 worker 成功，原始 document 保留，数据库只有一份有效 conversion 和候选。此前租约顺序测试继续证明旧 generation 结果被拒绝。
- 此项证明本地 worker 状态机、数据库租约和文件目录的隔离故障恢复；未实际重启生产 systemd worker 或应用服务，M3 仍因样本语义/原生 DOC/运行限额/生产部署等事项部分完成。

### M1 ST-02 并发 HTTP 分片与 M2 PDF C 校对稿导出（2026-09-29）

- 新增 HTTP 端点级竞态回归：同一上传的同字节分片在 barrier 同步后并发请求，两请求均 200，数据库仅一条分片记录且落盘字节/hash 一致；不同字节同时争用同一上传序号，恰有一个 200、一个 409，数据库 winner 与磁盘字节/hash 一致。M1 ST-02 仍部分通过，因为尚无两个转换任务通过 HTTP 同时复用同一图片资产的全链路竞态，也未在生产存储演练。
- 新增教师专用 `/api/documents/tasks/{id}/review-export.zip`。它导出当前未发布的题目 Markdown、相对路径图片和来源清单；包内 `REVIEW-STATUS.txt`、`paper.md` 和 `source.json` 明示“未审核”，保留每题复核版本与未解决 issues。只导出持久化的候选内容；前端发现正文未保存时先逐题调用现有版本校验保存接口，成功后才开始下载。发布导出仍要求正式 publication；学生角色无权访问校对包。
- 在真实 PDF C 原卷字节和已保存完整 MinerU 3.4 OCR/content-list/图片载荷的隔离任务上，Chrome 经浏览器点击校对稿下载；这次重放使用当前 adapter/splitter，但未声称重新识别或完成教师语义审签。页面显示 15 个可编辑候选/独立预览、102 个 KaTeX 节点且 0 错误、23/23 图片加载。浏览器先留有一个未保存题干编辑，再点击下载；系统将 Q1 复核版本从 1 提升为 2，ZIP 中含该保存内容。解压 15 份题目 Markdown、23 张图片，所有相对图链和 SHA-256 匹配；没有占位选项、API URL；36 条未解决事项（31 公式、5 图位）完整保留，publication 仍为 0。证据 `m2-pdf-c-review-draft-autosave.json`、`m2-pdf-c-review-draft-export.json`、`m2-pdf-c-review-draft-autosave-browser.png` 和 ZIP 下载目录 `m2-pdf-c-review-draft-download-autosave/`。
- 另一隔离 Chrome 复验只更改 Q1 的 issue 核验勾选及备注、不修改题目 Markdown，然后点击同一链接；新 JS 判断到 review state 未保存并先提交，导出清单带上 issue 的 `resolution_note`、`reviewed_by`、`reviewed_at`，Q1 revision=2、publication=0。备注明确写为“自动保存状态测试”，不是语义签字；该临时包单独保存在 `m2-pdf-c-review-export-issue-autosave-download/`，不用于 CV-03 内容验收。测试 `test_teacher_can_publish_editable_question_and_download_offline_image_zip` 也断言 issue 核验依据出现在包清单中。
- 测试 `tests.test_document_http.DocumentHTTPTests.test_teacher_can_publish_editable_question_and_download_offline_image_zip` 与 `test_concurrent_http_upload_parts_are_idempotent_and_conflicts_do_not_overwrite` 通过。完整本机 gate 通过 321 tests（60.796 秒）、compileall、Node、runtime readiness、`git diff --check`；本机 gate 的 HTTP smoke 未启用。日志 `m1-m2-final-local-release-check-20260929.log`。`REQUIRE_REMOTE_HEAD_MATCH=1` gate exit 0 并检查 refs、服务、HTTP 与 runtime，但本机 HEAD、远端 checkout、origin/main、GitHub main 都是既有基线 `a5ab6670dce154ce8bd6c58e3bd41df278754320`；本轮实现仍为未提交工作树内容，所以远端 gate不证明新功能线上运行。日志 `m1-m2-final-remote-release-check-20260929.log`。没有生产写入、部署或重启。
- 补入复核元数据保留后，再次全量 gate 仍通过 321 tests（66.050 秒）、compileall、Node、runtime readiness 与 `git diff --check`，日志 `final2-local-release-check-20260929.log`。严格远端 gate exit 0，但 HEAD/origin/GitHub/生产 checkout 仍是 `a5ab6670`，新实现为 dirty 工作树，日志 `final2-remote-release-check-20260929.log`。恢复脚本和只读 SSH 复核确认生产 service/timer active、PID 115814 未变、SQLite v11/64 tables/integrity ok/no migration ledger；四个新页面资源仍 404，详见 `production-live-readonly-final-20260929.log`、`production-state-current-final-20260929.json`。本轮没有生产写入/部署/重启；GitHub HTTPS auto-update fetch 曾超时。
- 未关闭的主要验收项包括 M0 教师逐题语义/公式签字、原生 DOC OCR、M1 剩余磁盘/存储和资产并发现场场景、M2 已发布 PDF C 正式整卷包、M3 生产 worker 重启/限额、M4 PDF C 全卷逐题映射与事务发布、M5 生产教师 SSO、M6 历史题真实映射/撤销、M7 当前实现提交部署后的实际进程版本证明。因此 M0—M7 均不整体通过。

### UP-06 真实浏览器 50 MiB 分片传输（2026-09-30）

- 在隔离 SQLite、临时 document root 和教师测试会话中，Chrome 从导入页选择恰好 52,428,800 字节的 PDF 文件。文件以真实 PDF C 原件（SHA-256 `2570576fe207be04b7aec2ee2034d9d0694ee4e4e300e39d8a74dc22ec8ba968`）为前缀，追加 49,066,539 个空格至上传上限；它只用于大小边界与传输完整性，不用于识别质量验收。
- 页面实际发送 100 个分片；数据库中分片序号连续、分片字节合计 52,428,800，原件落盘大小相同，存储 SHA 与上传文档及 fixture SHA 一致。完成上传后任务仍为 `queued`，没有 worker 领取或 OCR；生产服务、数据库和存储均未触碰。
- 结构化证据 `upload-50m-browser-transfer-evidence.json` 与 `upload-50m-real-pdf-fixture.json`；Chrome 页面证据 `upload-50m-real-pdf-selected-browser.png`、`upload-50m-real-pdf-transfer-browser.png`。UP-06 的浏览器边界传输子项已通过，不代表 M0 识别或 M7 生产浏览器验收通过。

### M1 ST-02 双任务 HTTP→worker 图像复用（2026-09-30）

- 新回归先通过实际 HTTP create/part/complete 为两个不同 PDF 任务排队。第一个 worker 转换进入后阻塞；第二次 `run_once` 返回 `None`，证明现行全局租约维持单槽、不允许并行转换。释放第一个任务后，两个 worker 任务依次使用相同的已解码 PNG；资产 id/key/SHA 均一致，数据库仅一条 `document_assets`，两个 conversion 各自有正确的 `conversion_asset_refs`，两个候选正文均引用同一图像，原件校验通过，publication 为 0。
- 该 HTTP/worker 回归使用合成图像，只验证并发/复用/引用完整性，不作为真实题目内容证据。并行 adapter 资产写仍由 `tests.test_document_store.DocumentStoreTests.test_concurrent_same_school_asset_store_reuses_one_immutable_object` 验证。由于当前 worker 主动串行，两个转换任务同时运行的竞态不属于现行生产执行路径；ST-02 仍部分通过，剩余项为生产存储演练。定向日志：`m1-cross-task-worker-asset-reuse-20260930.log`。
- 随后本机完整 release check 通过 322 tests（67.248 秒）、compileall、Node 与 `git diff --check`；HTTP smoke 未启用。临时本机 QA venv 不含 MinerU、Playwright PDF、Authlib，这不构成解析质量失败；本机不承担生产解析验收。日志 `m1-cross-task-final-local-release-check-20260930.log`。

### M0 PDF C 教师语义复核工作单（2026-09-30）

- 新增可填写工作单 `m0-pdf-c-teacher-review-workbook-20260930.md`，从隔离任务抽取 15 题覆盖、原卷页、ZIP 内可编辑 Markdown 路径、31 个公式 issue 的原 OCR 块及 5 个图位待核问题；关联原 PDF C 与未审核校对 ZIP。它不修改候选、不自动关闭 issue，也不视为教师签字。
- 等待教师逐题对照原卷填写保留/更正结论后，才能生成并浏览器核验新的修订版本，再继续 PDF C 批量发布和后续内容修复。Word A/B 语义复核、原生 DOC OCR 也仍待完成。

### M7 前端脚本门禁补充与 2026-09-30 复验

- `scripts/hsp_release_check.sh` 的本地 Node 检查现覆盖既有 `app.js` 与本次新增 `document-import.js`、`question-rendering.js`；门禁单测同步断言三者，避免导入/题目 renderer 脚本语法错误漏检。
- 本地完整 gate 322 项通过（63.114 秒，1 skipped），compileall、三份前端脚本语法、runtime readiness、`git diff --check` 通过；HTTP smoke 未启用。此前第一次全量运行因原门禁单测仍期待旧的单文件命令而失败，更新断言后复跑通过。证据 `m1-node-assets-release-check-final-20260930.log`。
- 再跑严格远端 gate：本地 `main`、`origin/main`、GitHub `main`、生产 checkout 均为 `a5ab6670dce154ce8bd6c58e3bd41df278754320`；服务进程、`/`、`/physics/login` 与远端 runtime readiness 通过。该门禁验证的是已部署基线，当前实施仍是未提交工作树内容；不能据此声称功能已发布、进程加载了新代码或生产浏览器/SSO 验收通过。证据 `remote-strict-release-check-continuation-20260930.log`。

### M1 WK-01 双 worker 竞争领取（2026-09-30）

- 新增并通过并发回归：两个线程用独立数据库连接模拟 worker 实例，通过 barrier 同时领取同一 queued task；只有一个返回 lease token，另一个得到 `None`。复核数据库仅增加一次 attempt，状态为 running、generation=1，数据库 token 等于胜者 token。
- 该测试证明隔离 SQLite 中的竞争领取和单槽租约行为，不等价于生产 systemd worker 实机并发运行。定向测试 `tests.test_document_worker.DocumentWorkerFlowTests.test_two_worker_instances_racing_for_one_task_create_only_one_lease` 单项通过；随后完整本地 gate 通过 323 项测试（76.815 秒，1 skipped），compileall、三份前端脚本语法、runtime readiness 和 `git diff --check` 通过，HTTP smoke 未启用。日志 `m1-wk01-local-gate-20260930.log`。

### M0 原始 PDF 浏览器选取与 M3 worker 边界回归（2026-09-30）

- Chrome 在隔离教师页面实际选取原始 PDF C 文件（SHA-256 `2570576fe207be04b7aec2ee2034d9d0694ee4e4e300e39d8a74dc22ec8ba968`），通过当前分片上传、worker、adapter、splitter 和复核页面。生成 15 个完整候选与 15 个可编辑 Markdown 编辑框；KaTeX 102 个节点、0 个语法错误。滚动到第 3 题后，题干和 A–D 五个行内图像均成功加载。编辑正文探针可更新预览，恢复后未保存；任务 DB 核对探针未持久化。
- 识别来源明确为此前真实 PDF C 的远端 MinerU 3.4.0 OCR 产物回放：本次浏览器工作流证明原始文件的产品上传、队列、worker、拆题与预览路径，不是一次新 OCR 调用。15 个候选均为 `needs_review`，仍有 31 个公式和 5 个图位事项，publication 为 0；没有将截图/占位选项替代正文，也未以合成教师会话宣称生产 SSO。证据 `m0-pdf-c-real-file-selection-browser-20260930.json`、`m0-pdf-c-file-select-browser-edit-preview-20260930.png`、`m0-pdf-c-file-select-browser-question3-20260930.png`。隔离服务已停止且浏览器测试会话已注销。
- WK-04/WK-05 隔离边界测试现通过：超时/取消会终止并回收子进程；worker 取消到达活跃 converter 后不留下 conversion；超过 attempt limit 进入终态。缺模型、缺依赖、转换失败、超时各有独立错误码/安全消息，不落 conversion/candidate 或伪进度。细项见第 3 节 WK-04/WK-05。
- 最新项目 venv 本地 release gate 通过 330 项测试（63.731 秒，1 skipped），compileall、三份前端脚本 Node 语法、runtime readiness 与 `git diff --check` 通过；HTTP smoke 未启用。日志 `m1-wk04-wk05-server-copy-local-gate-venv-20260930.log`。首次系统 `python3` 尝试因缺 `cryptography` 失败，单独保留日志，不作为最终结果。
- 最新 `REQUIRE_REMOTE_HEAD_MATCH=1` gate exit 0，refs、服务、HTTP 和远端 runtime readiness 均通过；远端、GitHub、origin 与本地 HEAD 仍为基线 `a5ab6670dce154ce8bd6c58e3bd41df278754320`。工作树改动未提交/部署，新页面资源仍未在生产提供，进程加载版本未证明；没有生产写入、迁移、部署或重启。日志 `remote-strict-release-check-wk04-wk05-20260930.log`。M0—M7 仍不整体通过。

### PDF C 教师勘误复核稿与上线验收延期（2026-09-30）

- 根据教师反馈修订离线复核包 `output/document-ingestion-evidence-20260929/pdf-c-visual-review/`：Tc 上下角标/衰变箭头、Q3 完整 O 图、Q4 √2、Q6 分母、Q7 图注与题型说明、Q10 F合公式与分节、Q11—Q13 答题下划线、Q14 的 d₁/d₂ 和 v₀。Markdown 为可编辑源，`index.html` 可直接在 Chrome 对照原 PDF 预览，无需手工编译。Q2、Q5、Q8、Q9 按教师反馈保持不变；原生 DOC 按用户意见暂缓。
- Chrome 实际打开该 HTML：15 个题目卡片、23 张题图全部加载，108 个 KaTeX 节点、0 个排版错误，两个题型说明作为独立区块显示。截图和逐项核对摘要见同目录 `browser-render-qa.json` 与 `browser-*-review.png`。这证明本轮修改已渲染且资源齐全，不等于全部内容语义签审或正式题库发布。
- 用户要求 M4 PDF C 整卷入库/离线导出、M5 生产教师 SSO、M6 历史题真实映射/可撤销修复和 M7 新代码生产加载版本验收等到系统正式上线后再验收；本轮均不标为通过，也没有做生产迁移、写库、发布或重启。MinerU 安装/模型配置与生产进程、SSO 核查边界见 `output/document-ingestion-evidence-20260929/document-ingestion-go-live-gates-20260930.md`。
- 本轮以项目 QA 虚拟环境执行 `PYTHON_BIN=/tmp/hsp-doc-test-env/bin/python VERIFY_TARGET=local bash scripts/hsp_release_check.sh`：330 项测试通过、1 项跳过，compileall、Node 语法与 `git diff --check` 通过；HTTP smoke 未启用。日志 `output/document-ingestion-evidence-20260929/local-release-check-after-corrections-20260930.log`。本机精简 QA 环境未安装 MinerU/MarkItDown/Playwright/Authlib；该 readiness 缺项不替代也不否定之前隔离生产环境的真实 PDF C OCR 记录，更不证明生产模型配置可用。

### M7 正式发布、生产入口复验与 MinerU 配置状态（2026-09-30）

- 功能代码提交 `15ff029356c7fc63bb0d4444ce271a2323d928c7` 已推送并部署；后续只增加验收记录。该代码提交修复了生产 Nginx `proxy_redirect`/`sub_filter` 与应用同时添加 `/physics` 前缀导致的重复路径。生产 SSH 用户没有免密 sudo，故未改动 Nginx 系统配置；应用按当前代理契约输出根路径，由代理统一添加公开前缀。
- 最终部署后真实 Chromium 未认证打开 `http://10.50.159.62/physics/documents`：303 后到 `/physics/login` 并返回 200；登录页 CSS/JS 使用正确 `/physics/assets/...` 路径。document-import、question-rendering、KaTeX 等 7 个目标资源状态均为 200，浏览器 request failed/console/page errors 均为 0。最终证据 `production-browser-check-final-20260930.json`、`production-browser-final-20260930.png`。未提交生产 SSO 凭据，也未登录真实教师会话。
- `REQUIRE_REMOTE_HEAD_MATCH=1 bash scripts/hsp_release_check.sh` 在最终同步后通过：本机、origin、GitHub 与生产 SHA 一致；app 与 worker active；生产核心 schema v11、文档账本 v12、DB integrity 及外键检查通过；新资源 HTTP 200。证据 `remote-strict-release-check-final-20260930.log`。本机 release check 332 tests 通过、1 skipped，证据 `local-release-check-proxy-fix-20260930.log`。
- 发布前已对生产 SQLite 做在线备份，路径 `/home/yub/Documents/trae_projects/HighSchoolPhysics/data/backups/school.sqlite3-before-document-ingestion-20260930T041059Z.sqlite3`、SHA-256 `27b4e7c79644873d9b408adcf5467d47d8a759ba4d051a1efd9fa4b8ff0fda00`。部署迁移后，64 张既有表的 schema/data 指纹逐表均一致，`integrity_check=ok`、外键错误 0；没有发布题目或改写学生记录。机器快照 `production-live-predeploy-snapshot-20260930.json`、`production-live-postdeploy-data-integrity-20260930.json`。
- 当前教师可从 `http://10.50.159.62/physics/login` 进入既有统一平台登录；登录后打开教师工作台的“整份试卷导入”。可先上传文件副本，查看转换、拆题、Markdown 正文及预览，不要在未审签的 PDF C 上批量入库。DOCX 可以进入真实教师试用；扫描 PDF OCR 需先配 MinerU 模型，当前会因缺配置而失败关闭。
- MinerU 配置边界：生产已安装 MinerU 3.4.0，但不是独立 `mineru-api` 服务；document worker 是本地 systemd 服务，调用 `mineru` CLI 的 `pipeline` backend。unit 已设置 `HSP_MINERU_TOOLS_CONFIG_JSON=/home/yub/mineru.json`、`MINERU_MODEL_SOURCE=local`、`HSP_MINERU_THREADS=1`。在服务器服务用户 `yub` 下先检查空间 `df -h /home/yub`，再用 3.x 官方 CLI 下载 pipeline 模型：`mineru-models-download -s modelscope -m pipeline`。下载器会写入 `~/mineru.json`；需确认其 `models-dir.pipeline` 为绝对模型根目录且存在 `models/Layout/PP-DocLayoutV2`、`models/MFR/unimernet_hf_small_2503`、`models/OCR/paddleocr_torch`、表格模型及 `models/MFR/pp_formulanet_plus_m`。worker 环境固定 `MINERU_MODEL_SOURCE=local`，避免任务执行时联网取模型。随后以相同 worker 环境运行 runtime check，期望 `mineru-local=configured`；重启 document worker 后，还必须用真实 PDF 经过产品上传/worker/Markdown/图文预览检查，才算识别成功。
- 该服务器为 2 vCPU/15 GiB，CPU pipeline 可用但要评估处理时间和磁盘增长；当前未下载模型，避免未经确认触发大体积联网下载。若改走独立 GPU MinerU API，当前 document-ingestion adapter 尚未连接 API provider；仅在管理设置里填 API 地址不能使本导题 worker 改走远端。
- 未认证入口修复与部署已通过；M4 PDF C 整卷正式发布/离线导出、M5 生产教师 SSO 工作流、M6 历史题真实映射及可撤销修复仍依用户此前约定等待正式上线后验收。原生 `.doc` 按用户意见暂缓。整体不宣称完成。
