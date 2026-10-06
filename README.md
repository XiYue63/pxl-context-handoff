# context-handoff-pxl

把一个项目下的**多次不同对话**汇总成一份项目上下文，让新对话不必从零开始。

## 它做什么

- 合并三个来源：Codex 的项目归属表（`~/.codex/.codex-global-state.json`）、
  `~/.codex/state_5.sqlite` 的 `threads` 表、rollout 里的 `session_meta.cwd`
- **项目文件夹改名或移动后依然找得到旧对话**：先按路径解析出 `projectId`，再取该
  项目下的全部对话，而不是只比当前路径
- 归档到 `~/.codex/archived_sessions` 的对话也能收录（位置记录在 `state_5.sqlite`）
- 按会话 ID 去重，剔除环境注入、权限说明、附件头这类非真人内容
- 先输出对话索引（日期 / 会话 ID / 轮次 / 标题 / 用户诉求）
- 再提炼成 `PROJECT-CONTEXT.md`：已确认事实、已做决定、约定、未决问题
- 在文件末尾维护「已收录对话」清单，看清哪些对话已经收录

## 安装

把本目录放到你的 skills 目录之一，例如：

- `~/.agents/skills/context-handoff-pxl`
- `~/.codex/skills/context-handoff-pxl`

## 用法

在 Codex 对话里直接说：

- “用 context-handoff-pxl 汇总这个项目里所有的对话”
- “把这个项目的对话上下文整理一下”
- “接上这个项目的上下文”

也可以单独跑脚本：

```bash
python scripts/scan_project_conversations.py --project <项目根目录>
```

### 脚本参数

| 参数 | 作用 |
|---|---|
| `--project <path>` | 项目目录，默认当前目录 |
| `--list` | 只输出紧凑清单（日期 / 会话 ID / 轮次 / 标题） |
| `--explain` | 清单里额外显示归属依据（项目归属表 / 工作目录） |
| `--alias <old-path>` | 同一项目的旧路径，可重复；用于文件夹改名或移动后的补充匹配 |
| `--format json` | 结构化输出 |
| `--session <id>` | 展开某一次对话的完整内容 |
| `--include-archived` | 按路径扫描时也包含 `~/.codex/archived_sessions` |
| `--no-desktop-state` | 不使用归属表，只按 rollout 里的 cwd 匹配 |
| `--limit <n>` | 最多输出多少个会话，默认 50 |
| `--max-user-chars <n>` | 每条用户消息截断长度，默认 300 |
| `--out <file>` | 写入文件而不是打印 |

## 边界

- 只读会话存储，不修改或删除任何记录
- 只识别 Codex 自己的会话；Claude Code、ChatGPT 网页版等其他工具的记录不在范围内
- 一个 Codex 项目可以绑定多个根目录，这类项目的对话数会多于单个文件夹里的数量，
  属于正常情况
- 生成的上下文文件只保留“三个月后仍然有效”的信息，不做流水账式追加
