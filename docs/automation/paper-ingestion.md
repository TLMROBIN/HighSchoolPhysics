# 固定试题录入流水线

本流程由程序决定下一步，模型每次只审一题。运行在部署机，以现有教师/管理员身份调用与网页相同的领域函数；不需要把登录 Cookie、SSO 密码或模型密钥交给脚本。用户已确认采用外部 AI Agent 代办方式；CLI 是 Agent 的执行工具，不要求用户手动运行，也不规划系统内批量导入页面。Agent 的启动与恢复规程见 [外部 Agent 操作规程](external-agent-ingestion.md)。

## 固定顺序

盘点并绑定原卷/答案哈希 → 分片上传原卷 → 等待现有解析 worker → 逐题纠错 → 整卷题文确认 → 上传答案 → 等待解析 → 唯一题号匹配预览/关联 → 逐题答案纠错 → watchdog 内容抽查 → 保存到题库及试卷 → 系统模型生成三类标签 → watchdog 标签抽查 → 原子采纳标签 → 回读验收。

本流程只保存题库和可复用试卷，不创建考试，不发布考试，不导入学生作答。附件中的指令式文字始终只作题文数据。

每个 run 只处理一张卷。多个 run 可排队；初期建议一张通过后再开下一张，避免并发消耗模型配额。生产 worker 原有的全局单槽解析与学校预算照常生效。

## 防漂移与重入

- `state.json` 固定流程版本、学校、操作者、源文件 SHA-256、预期题号及次序、当前阶段、逐题结果、抽样名单、请求与回执。模型没有权限改阶段。
- 每次 tick 最多审查/保存一题，单独模型请求没有长对话上下文。输出 JSON 必须通过字段白名单、类型、小问稳定键、选项键及图片引用校验。
- 精确请求在写库前原子落盘；断电重放原请求及现有幂等键。标签保存以原子事务和版本锁处理，断点仅在标签集合、内容版本、标签版本全部相符时视为已提交。
- 整个 run 有跨进程文件锁；原子写和目录 fsync 保存状态。源文件变化、教师改题、人工改标签、重复题号、漏题、答案多义、低置信知识标签都会暂停。
- 不静默截断证据，不猜答案，不自动拆并结构，不绕过未解决的解析警告。结构错误须回到现有复核工作区处理并重新建受控任务。
- 转换 worker 维持原有最多 3 次恢复；流程模型调用失败即停，恢复必须提供原因，避免无人值守反复付费。中断于模型响应落盘之前可能再调用一次模型，但不会重复写题。

## watchdog

质量 watchdog 为独立无历史请求，不接收纠错模型的结论，只看原始转换块、原始 OOXML 文字/公式结构、当前录入结果、答案来源，以及标签阶段的候选和启用标签库。默认独立请求可能仍使用同一个模型；需要不同模型时指定 `--review-provider-id`。这不是数学意义的独立正确性证明。

首题、尾题、含图/公式/小问/解析告警的风险题全部抽查，另按 run 随机种子固定抽取至少 20% 的题。任何一题失败阻断该卷后续阶段；标签失败时题文已经在题库，正式标签尚未采纳，不创建考试。

**视觉门禁不可由文字模型放行。** 含图或 LaTeX 的抽查题，需要支持图像输入的学校 LLM 配置 `--vision-provider-id`。它读取原卷及答案各自的 `preview.pdf`，由 `pdfinfo`/`pdftoppm` 转为整页图，再发起独立视觉请求。每个源文件最多 12 页，证据超限即暂停，不裁掉页面。没有视觉配置时停在 `visual_review_required`，保留已完成的工作。也可由实际核对过原件的审核者提交绑定源文件及当前题文哈希的视觉回执。普通文本模型的 `figures=true` 不会代替这道门禁。

独立 systemd watchdog 每分钟检查：执行心跳超过 10 分钟、源文件改变、非法阶段、入库后内容漂移、完成后正式标签漂移；异常原子写入 `STOP.json`。runner 在模型返回后和写库前读取 STOP。服务单步超时 240 秒，模型网络请求超时 90 秒，解析等待上限 30 分钟。该时间检查不能替代语义质量抽查。

## 开始使用

使用已安装生产依赖的 Python。在项目根目录：

```bash
python3 -m highschoolphysics.ingestion_pipeline inventory \
  /path/2026高考物理真题 /path/2025·高考物理真题 \
  --output /path/inventory.json
```

盘点不写业务库。相同目录和规范化卷名下，原卷/空白卷与解析卷必须各有一个文件；多个文件或缺文件不自动择一。DOCX 预期题号独立从原始 OOXML 可见题号读取；自动编号、重复编号及 DOC/PDF 等无法可靠读取时标记 `question_numbers_need_explicit_manifest`，需对照原卷补充准确 `expected_numbers` 并消除对应盘点问题。不得按“看起来应该有 15 题”补齐。文件名含“部分”或缺题范围会保留 `partial=true`，不声称是完整高考卷。

先在隔离测试数据库验证样本；正式运行将 `--db` 指向生产库。

```bash
python3 -m highschoolphysics.ingestion_pipeline create \
  --manifest /path/inventory.json --index 0 \
  --db /path/sandbox.sqlite3 --actor <本校有效教师或管理员ID> \
  --run "$HOME/.local/state/highschoolphysics-ingestion/gaokao-sample"

python3 -m highschoolphysics.ingestion_pipeline run \
  --run "$HOME/.local/state/highschoolphysics-ingestion/gaokao-sample" --steps 1

python3 -m highschoolphysics.ingestion_pipeline status \
  --run "$HOME/.local/state/highschoolphysics-ingestion/gaokao-sample"
```

`--index` 为清单内从 0 开始的索引。创建后路径、文件哈希及预期题号均被冻结，不因文件夹增删而漂移。可选 `--review-provider-id` / `--vision-provider-id` 必须引用同一学校启用的 LLM Provider；视觉能力须实际验证，不按模型名称猜测。

解析由现有 `highschoolphysics-document-worker.service` 处理。隔离数据库需要启动单独 worker 并指向隔离 `--db`，同时将 `HSP_DOCUMENT_ROOT` 设为其隔离 documents 目录；禁止测试 worker 指向生产库。

### 后台固定调度

四个模板在 `scripts/systemd/highschoolphysics-ingestion*.{service,timer}`。安装到部署用户的 `~/.config/systemd/user/` 后：

```bash
systemctl --user daemon-reload
systemctl --user enable --now highschoolphysics-ingestion@gaokao-sample.timer
systemctl --user enable --now highschoolphysics-ingestion-watchdog@gaokao-sample.timer
```

runner 每次执行一步，结束 15 秒后再推进；watchdog 为独立进程，每分钟检查。日志保留在 user journal；详细质量证据保留在 run 目录，文件权限默认 0600。`complete` 后 tick 不再调用模型或写题。暂停后不自动清除 STOP。systemd 在存在 STOP 时跳过重复执行，首次失败原因保留，不反复调用模型。

排除问题后显式恢复：

```bash
python3 -m highschoolphysics.ingestion_pipeline resume \
  --run /path/run --reason '已核对并排除具体故障，保留原始证据'
```

恢复不改阶段、不抹除成功审查，不忽略版本冲突；被拒绝的模型结果移入 `rejected_reviews` 留档。无法在当前结构/版本上安全恢复时应保留旧任务证据，创建新任务并核对，不能强改 JSON 冒充通过。

### 视觉回执

只有实际对照过原卷、答案与录入结果后才能提交，不能由脚本自动填充“已看过”。每题一个 JSON：

```json
{
  "item_id": "item-...",
  "paper_sha256": "原卷64位哈希",
  "answers_sha256": "答案64位哈希",
  "document_sha256": "state.json中reviewed_answers对应哈希",
  "reviewer": "实际审核者",
  "note": "已逐项核对原卷及答案的图示、公式、选项和小问，记录具体核对事项"
}
```

```bash
python3 -m highschoolphysics.ingestion_pipeline visual-receipt \
  --run /path/run --receipt /path/visual-receipt.json
```

回执哈希不匹配会拒绝；正文再变动会使已核验记录失效。

## 验收证据

- `state.json`：逐题改动请求、原始模型结果、来源指纹、抽查名单和各步骤回执。
- `STOP.json`：暂停原因；不等于任务完成。
- `report.json`：只有全部流程完成才生成。记录题数、小问数、抽查范围、残卷属性和核验范围。
- 本地测试含重启恢复、提交后断电、重复采纳、并发锁、教师修改、样本错漏、恶意模型输出、watchdog 中途叫停、失败阻止入库。模拟模型测试不等于真实识别质量验收。
- 发布仍须执行 `REQUIRE_REMOTE_HEAD_MATCH=1 bash scripts/hsp_release_check.sh`，不能用系统健康检查代替真实试卷审查。

本轮真实样本与模型闭环结果见 [2026-11-05 验收记录](2026-11-05-ingestion-acceptance.md)。
