# 可信作答升级第一批实施与验收记录

配套：[升级方案](2026-10-01-response-evidence-upgrade-plan.md)。2026-10-01，按 U0—U2 工程范围实施，后续教学试点和 U3/U4 仍按门禁推进。

## 已实现行为

- 继续使用“考试与作答”。CSV 导入提供“学生答案”和“已核对结果”两种方式；后者要求来源名称与核对依据。服务层同时接收结构化 records，按同一协议验证。
- 预览逐条显示原答、规范答案、规则建议、外部结论及异常；确认保存绑定批次、内容、名单、题目内容/规则和现有作答的版本。相同请求重试不重复写入。
- 外部结果与规则冲突、规则无法确定、非法选项等均进入待复核。教师从作答行展开复核，填写核对答案、结果和依据。尚有缺失或 pending 时不能发布。
- “查看证据与判定历史”显示导入来源、原始答案、行号和全部判定过程；关联原答题图须属于同学校、同测评、同学生。
- 发布后从作答行展开“更正作答或结果”，先预览影响，再确认；首次记录、测评快照和练习历史保留，有效答案/结果与标签统计同步变化。
- 撤销误判来源采用 is_active 标记，不删除错题或练习。学生不再从该来源开始练习；同题的其他有效错误来源继续生效。新认定错误从修订生效次日安排，避免制造历史逾期。
- 原有单选、多选、填空答案与数值容差匹配抽到独立无分数服务。明确空白保持独立记录；单位/符号等无法确定的非空答案仍 pending。

## 模型与兼容

新增 feature migration `response_evidence=14`，核心 user_version 仍为 11；文档导入和标签任务各自的版本账本保持原值。

新增批次、证据、判定、复核、发布五张表。证据和判定使用数据库触发器禁止修改/删除。student_responses 的 outcome/final_answer 是兼容现有页面的统一有效投影，initial_answer 继续受原不可变保护。

历史记录追加 legacy 证据/判定，保留已有结论，不补造识别置信度。migration 幂等并有事务回滚；首次迁移应在生产数据库备份后执行。

直接调用旧 CSV 保存接口的客户端需要先预览并带回 preview_token、request_key；现有浏览器入口已更新。旧有分数写接口继续禁用。标准答案与身份错误不通过个别结果更正入口静默修改；检测到判定依据变化会阻止发布，需进入专门核对流程。

## 当前验收证据

- 生产迁移前只读备份：`data/backups/school-before-response-evidence-20261001T024754Z.sqlite3`，文件权限 600；生产备份未改写学生数据。
- 私有生产副本：3148 条 student_responses、1480 条 wrong_questions；新增 legacy 证据和判定各 3148 条。79 张既有业务表的原列数据指纹一致，integrity_check=ok，foreign_key_check=0。摘要 `/tmp/hsp-response-shadow-summary.json`，不将数据库或身份信息提交仓库。
- 定向领域/HTTP测试：新增导入、复核、更正、权限、重复提交、并发发布、判定依据变化、迁移故障回滚和物理边界用例；最终全量与独立版本验证见本节后续记录。
- Chrome 隔离演示数据库通过产品页面完成：导入外部冲突 → 保存 → 人工复核 → 发布 → 预览更正 → 确认更正。有效答案 B、首次 A、教师更正说明可见；学生无教师证据/更正按钮。
- 390×844 的教师/学生页面 document scrollWidth=390，无整页横向溢出。截图为隔离演示数据，非生产学生。
- 发现一处既有空白语义回归并修复：明确空白不依赖答案规则已确认；非空不确定判定仍 pending。随后定向回归通过。

## 待验收与后续门禁

真实教师 SSO、实际 Android 壳上传/返回/断网重试、真实班级连续两轮周测与 2—3 周复习观察仍需现场证据；浏览器模拟不等同于课堂/真机验收。

U3 的固定答题卡识别尚未实施；需真实模板与独立真值样本。U4 单位换算、安全表达式、AI 建议尚未实施。直接 XLSX、第三方凭据开放、标准答案批量修订、主观题与细分诊断不作为本批已完成能力。

本批与现场另一批 DOCX/题目渲染改动隔离提交，并以独立检出运行发布回归。本批最终运行版本只包含作答升级。


## 最终工程发布记录（2026-10-01）

- 功能代码提交：`d6e5d901`（证据/导入/复核/更正）、`80cc3233`（手机卡片与中文来源）、`42116b41`（排除导入后改为缺考的作答）。其他导题内容工作保留在原工作树，不纳入本批最终运行版本。
- 在独立 managed worktree 上，最终 `42116b41` 通过 `PATH=/tmp/hsp-doc-test-env/bin:$PATH PYTHON_BIN=/tmp/hsp-doc-test-env/bin/python VERIFY_TARGET=local bash scripts/hsp_release_check.sh`：374 tests、1 skipped；compileall、四份脚本 Node 语法、runtime 报告、diff 检查通过。先前独立回归的 MinerU 模拟器失败来自子进程 PATH Python 缺 PIL；统一虚拟环境后单项和最终全量通过。
- 生产最终备份：`data/backups/school-response-predeploy-20261001T025846Z.sqlite3`，SHA-256 `32315f54b1e65cff7e5b6f0255a2ee3406ba63b3ed495b0686ac3234dcdc20b0`；文档/资源备份 `data/backups/response-resources-20261001T025846Z.tar.gz`，SHA-256 `6d4a10997f8190cb1c9e1953d95cc4828990f7fe13a8aacf6448bcb928f9e8fc`。二者权限 600。
- GitHub 推送成功。远端 HTTPS fetch 卡住时，临时停止 user-level 自动更新 timer/service，以已校验 Git bundle 快进到同一 GitHub 提交，重启 app 与 document worker 后恢复 timer；未改 Nginx、网络或 systemd 持久配置。
- 远端 app/worker active，timer enabled/active；`response_evidence=14`，核心及已有 feature 版本保留。生产迁移后 79 张原业务表的原列数据指纹全一致；3148 条作答、1480 条错题、0 条历史重做保持原数，3148 条 legacy 判定已建立，integrity=ok、外键错误 0。生产核验摘要：`data/backups/response-postdeploy-20261001.json`。
- `REQUIRE_REMOTE_HEAD_MATCH=1 bash scripts/hsp_release_check.sh` 通过：本机 HEAD、本机/远端 origin、GitHub main、远端 checkout 为同一功能版本；服务、正式入口、文档资源、feature v14、数据库完整性与学校库 runtime 检查通过。
- 正式 Chromium 未认证访问 `/physics/exams` 正常到 `/physics/login`；新 learning.js 与 learning-responses.css 均 HTTP 200，哈希等于独立发布版本；未登录访问证据 API 返回 401，页面脚本错误 0。生产真实教师 SSO 和实际课堂流程仍未验证。
- 独立版本再次在隔离数据库通过更正预览/确认及历史查看。教师手机展开证据后采用完整卡片，教师/学生均 390×844、scrollWidth=390，页面错误 0。它不是 Android 真机或教学效果证据。
- 本地日志、脱敏摘要和演示页面截图存于 `output/response-evidence-20261001/`；没有提交生产数据库、学生身份资料或测试会话凭据。

工程第一批已可使用，U2 的真实班级试用门禁仍未通过。U3/U4 及课堂观察不因这次发布自动标完成；后续班级与测评选择待教师明确。

- 提交范围修正：验收文档提交 `ec5600be` 曾带入并行任务的已暂存导题改动。远端当时仍为 `42116b41`，在自动部署前暂停 timer/service；通过追加修正提交恢复最终代码树，保留并行任务的工作文件及暂存内容，不强推、不覆盖该任务。最终范围应与 `42116b41` 的代码树一致，仅验收文档增加；随后重新恢复 timer 并验远端。
