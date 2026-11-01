# 文档导入验收证据索引

仓库根目录的 `output/` 和 `tmp/` 是本地生成的验收证据与转换工作目录，已加入 `.gitignore`，不会随应用代码提交。原始试卷、教师校对稿和生产数据库副本仍需按来源、哈希和用途单独保存，不应把整个工作目录当作发布包。

## 如何阅读

- `output/document-ingestion-evidence-20260929/document-ingestion-go-live-gates-20260930.md` 是上线前历史快照，日期和结论不能直接代表当前状态。
- 同目录中以 `remote-`, `production-`, `current-` 开头的日志和 JSON 是后续只读检查或浏览器证据，应结合文件名日期、提交号和其中记录的 `source_sha256` 一起判断。
- `pdf-c-visual-review/`、`m0-pdf-c-source-correction-proposals.md` 和教师复核工作单属于人工校对成果。它们明确标记为“待教师复核”或“未发布”前，不得当作已入库题目。
- `tmp/` 下的 PDF 裁剪、LibreOffice 配置、SQLite WAL 文件和临时服务是可重建缓存；只保留它们用于追溯时确实需要的副本。

## 证据边界

脚本通过、静态页面可渲染或 OCR 产物存在，都不能单独证明题目语义正确。公式、图位、题目边界和答案仍需对照原卷逐题确认；生产验收还必须同时核对远端提交、进程、HTTP 资源、数据库完整性和真实教师流程。
