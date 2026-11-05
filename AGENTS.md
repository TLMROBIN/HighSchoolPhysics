# HighSchoolPhysics - AI Agent 操作合约

本仓库以本机 `main` 为开发真相，以 `10.50.159.62` 上的部署服务为验收真相。所有阶段开发、修复、发布都必须先看现场，再改动，最后验证远端。

## 工作规则

- 优先延续现有阶段计划、README、`docs/superpowers/` 里的路线图和验收口径。
- 不要因为对话崩溃或上下文丢失而重新规划；先恢复现场。
- 不要清理或覆盖运行数据目录 `data/`，尤其是远端 `data/school.sqlite3`。
- 本机 `.mavis/`、`.opencode/`、`references/` 默认视为工具/参考目录，不要自动纳入发布提交。

## 只读恢复现场

用户说“继续”“刚才崩了继续”或任务状态不明时，先运行：

```bash
bash scripts/hsp_recover_context.sh
```

该脚本只读检查本机 Git/worktree、GitHub ref、远端 checkout、`highschoolphysics-auto-update.timer`、服务进程、HTTP `/` 和 runtime readiness。先用它确定当前阶段，不要凭记忆推进。

## 自动远端验证

做完会影响部署行为的代码、配置、数据结构、路由或发布脚本后，默认运行：

```bash
REQUIRE_REMOTE_HEAD_MATCH=1 bash scripts/hsp_release_check.sh
```

只有远端 checkout、本机 HEAD、远端 `origin/main`、GitHub `main`、服务进程、HTTP smoke 和 runtime readiness 都通过后，才能说“远端已验证”。

本机开发自测才使用：

```bash
VERIFY_TARGET=local bash scripts/hsp_release_check.sh
```

## 自动同步服务

远端部署机使用 user-level systemd：

- `highschoolphysics-auto-update.timer`
- `highschoolphysics-auto-update.service`

服务脚本为 `scripts/hsp_remote_auto_update.sh`，通过 GitHub HTTPS 拉取 `main`，更新 checkout，必要时重启 `python3 -m highschoolphysics.server`，并做 HTTP 健康检查。

## 外部 AI Agent 批量试题录入

用户已明确采用“提供文件夹和范围，由外部 AI Agent 代办”的工作方式，不要求新增系统内批量导入页面。涉及整卷批量入库、解析纠错、答案核对和标签采纳时，先读 `docs/automation/external-agent-ingestion.md`，再按现有持久流水线执行。

- 用户负责提供原卷/答案、范围与目标；Agent 负责配对、创建逐卷任务、纠错、抽查、恢复和汇总，不要求用户执行 CLI 或编辑状态文件。
- 完整批次指令覆盖其范围内正常步骤，不逐步重复索要确认；事实不明的题文、答案及图片归属保留待核对，不猜测放行。
- 从任务清单和逐卷状态恢复现场，不依赖长对话记忆，不用新增网页入口作为完成前提。
- 当前接入使用受控后台 CLI 与现有身份/权限。现有标签 Agent token 不能冒充上传、改题或答案纠错权限。
