---
name: pxl-context-handoff
license: MIT
description: >-
  Maintain one durable project context file so a new Codex conversation starts
  with what earlier conversations in the same project already established.
  Use when the user asks to 整理对话上下文, 整理一个项目里的多个对话, 汇总历史对话,
  上下文交接, 收尾, 归档本次对话, 接上上下文, or update PROJECT-CONTEXT.md.
  Scans Codex session rollouts belonging to the current project, distills verified
  facts, decisions, conventions, and open questions into one file, and can reload
  that context in a new session. Not for summarizing an article, paper, or
  document that carries no cross-session state.
metadata:
  version: 1.0.2
  repo: https://github.com/XiYue63/pxl-context-handoff
  short-description: 把一个项目下多次不同对话汇总成一份项目上下文
---

# Context Handoff

目标：让新对话不必从零开始。把同一个项目下**多次不同对话**中跨对话仍然成立的信息，
提炼进项目里唯一的上下文文件。原始会话记录保持不动。

## 数据来源

脚本合并三个来源并去重，按可靠性排序：

1. `~/.codex/.codex-global-state.json` 的 `thread-project-assignments`：Codex 自己
   记录的「对话 → 项目」归属表，**项目文件夹改名或移动后依然有效**。先按路径解析出
   `projectId`，再取该 projectId 下的全部对话。
2. `~/.codex/state_5.sqlite` 的 `threads` 表：给出每个对话的 rollout 路径、标题和
   归档状态。归档对话的 rollout 在 `archived_sessions` 目录，只有这张表知道位置。
3. rollout 文件第一行的 `session_meta.cwd`：兜底匹配，覆盖归属表里没有的老对话。

用脚本读取，不要手写解析：

```bash
python <skill-root>/scripts/scan_project_conversations.py --project <项目根目录>
```

常用参数：

- `--list`：紧凑清单（日期 / 会话 ID / 轮次 / 标题），先看有哪些对话。
- `--explain`：清单里额外显示归属依据（项目归属表 / 工作目录）。
- `--alias <旧路径>`：同一项目的旧路径，可重复；文件夹改名或移动后补充匹配。
- 默认输出 Markdown 索引报告，含每次对话的用户诉求和最后一条回复的截断。
- `--format json`：结构化输出。
- `--session <id>`：展开某一次对话的完整内容。
- `--include-archived`：按路径扫描时也包含归档会话（归属表命中的归档会话本来就会包含）。
- `--no-desktop-state`：不使用归属表，只按 rollout 里的 cwd 匹配。
- `--out <file>`：写入文件而不是打印。

脚本只读会话存储和状态库，不会修改或删除任何记录。这些文件在项目目录之外，读之前先
向用户说明来源。

注意：Codex 的一个项目可以绑定多个根目录（例如同时包含两个文件夹）。这类项目的对话数
会多于单个文件夹里的数量，这是正常的，按项目汇总时应全部计入。

## 上下文文件

按顺序查找，找到就沿用，不再新建平行文件：

1. `docs/PROJECT-CONTEXT.md`
2. `PROJECT-CONTEXT.md`
3. `docs/HANDOFF.md`、`HANDOFF.md`、`docs/CONTEXT.md`、`CONTEXT.md`
4. 都不存在时，在项目根目录新建 `PROJECT-CONTEXT.md`

已存在但结构不同，就沿用它的结构补充，不要推倒重写。

## 只写四类内容

| 类别 | 说明 |
|---|---|
| 已确认事实 | 已验证的、当前仍然成立的事实 |
| 已做决定 | 决定了什么、为什么、影响什么 |
| 约定 | 后续必须遵守的工作方式与边界 |
| 未决问题 | 还没有结论、下次需要继续的 |

判断标准：**这条信息三个月后还有效吗？**

- 临时试错、被推翻的方案、中间过程、寒暄：不写。
- 代码或文件的现状属于代码事实，只写结论和理由，不要抄大段内容。
- 每条尽量带日期；必要时标注来自哪次对话。

## 工作流

### 汇总一个项目的多个对话（主流程）

1. 确认项目根目录。
2. 跑脚本拿到对话清单，先向用户确认范围（数量、时间跨度），
   除非用户已明确说“全都整理”。
3. 逐个对话提炼四类内容。不同对话说法冲突时，列出冲突交给用户裁决，
   不要擅自选一个。
4. 合并进上下文文件：同一事实就地更新，不追加重复条目；被推翻的旧结论改写或删除，
   不要新旧两版并存。
5. 在文件末尾维护「已收录对话」小节，记录日期、标题、一句话结论、会话 ID。
   这样下次能看出哪些对话已经收录、哪些还没有。
6. 汇报：扫描到几个对话、收录了几个、改了哪几条、哪些待确认。

### 收尾（只看当前对话）

当前对话本身就是材料，不需要跑脚本。提炼四类内容后，按上面的合并规则更新上下文文件，
并把这个会话补进「已收录对话」。

### 接上

- 读上下文文件，给一份简短现状：项目是什么、最近的决定、当前未决、下一步。
- 标注明显过期或与当前工作区不符的条目。
- 文件不存在就直说，并问是否要先跑一次汇总。

## 文件结构

新建时用这个模板：

```markdown
# <项目名> 项目上下文

最后更新：YYYY-MM-DD

## 项目是什么
一句话。

## 已确认事实
- YYYY-MM-DD：...

## 已做决定
- YYYY-MM-DD：决定了 X。理由：Y。影响：Z。

## 约定
- ...

## 未决问题
- ...

## 已收录对话
- YYYY-MM-DD · <一句话结论> · <session_id>

## 更新记录
- YYYY-MM-DD：...
```

## 边界

- 不删除原始会话记录或任何用户文件；需要清理时先列清单等确认。
- 不把密钥、完整控制台规则、个人隐私写进上下文文件。
- 一次只维护一个权威上下文文件，其他位置只放指针。
- 上下文文件是开工速读材料，不是流水账；超过约 200 行时先压缩再追加。
- 不把“整理上下文”扩大成对整个仓库的重构、重命名或文档迁移。
