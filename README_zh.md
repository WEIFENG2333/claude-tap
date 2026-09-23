# claude-tap

[English](README.md)

追踪 AI 编程 CLI 实际发给模型 API 的内容。

`claude-tap` 会把 Claude Code、Codex CLI、DeepSeek Harness、Gemini CLI、Grok Build、
MiniMax Code、Antigravity CLI、Kimi Code、MiMo Code、OpenClaw、opencode、Pi、Oh My Pi 等工具
放到本地代理后面运行，记录请求、流式响应、工具列表、token 用量和 system prompt，
并生成一个可以直接打开的 HTML trace。

适合用来回答这些问题：

- 这个 CLI 实际发了什么 system prompt？
- 模型看到了哪些工具？
- 请求打到了 Anthropic、OpenAI、Gemini，还是你的自定义 relay？
- 两个 CLI 版本之间 prompt 改了什么？
- 一次 coding-agent 运行里到底发生了什么？

这个 fork 目前从 GitHub 安装。PyPI 上的 `claude-tap` 还是旧项目，还不包含这次重写。

## 安装

需要 Python 3.11+ 和 `git`。

```bash
uv tool install git+https://github.com/WEIFENG2333/claude-tap.git@main
```

`@main` 会安装这个 fork 的 `main` 分支在当时的最新提交。安装后不会自动更新。

验证：

```bash
claude-tap --version
```

如果找不到 `claude-tap`，检查 uv tool 的 bin 目录是否在 `PATH` 里。也可以直接从
GitHub 运行：

```bash
uv tool run --from git+https://github.com/WEIFENG2333/claude-tap.git@main claude-tap --version
```

升级这个 fork：

```bash
uv tool upgrade claude-tap
```

## 快速开始

在原本的 AI CLI 命令前面加上 `claude-tap`：

```bash
claude-tap claude -- -p "What is 2+2?"
claude-tap codex -- exec "Say hi"
claude-tap dsh -- --profile headless "Say hi"
claude-tap gemini -- -p "Explain async/await"
claude-tap grok -- --single "Explain async/await"
claude-tap kimi-code -- --prompt "Say hi"
```

表格里的 client 名都可以这样传。`--` 后面的参数会原样传给对应 CLI。

CLI 退出后，会输出类似这些文件：

```text
[claude-tap] summary:
  api_calls:    2
  tokens:       352 in / 15 out
  trace:        ./.traces/2026-05-06/trace_120137.jsonl
  log:          ./.traces/2026-05-06/trace_120137.log
  view:         ./.traces/2026-05-06/trace_120137.html
```

直接打开 HTML 文件就能看完整 trace，不需要启动服务器。

如果想在 CLI 运行时实时看 trace，加 `-L`：

```bash
claude-tap -L claude -- -p "Explain async/await"
```

## 按 Session 组织的 Viewer

新版 Viewer 会区分 **capture**（一个 JSONL 抓取文件）和逻辑 **session**（一次 CLI
对话）。可收起的左栏按标准化 Session 身份归类，但不把原始 ID 暴露在列表里；中间每一行
就是一次 LLM 请求。稳定的两行活动预览用一致节奏展示模型文字和直接工具调用，并保留本地
时间、模型、耗时以及紧凑的单请求输入/输出用量；常规 2xx 状态和 Session 聚合 Token 不再
重复占位。右侧阅读区默认打开 Messages，只渲染本次请求体里的消息序列：源数组中的每一项
对应一个 role 区段，每个 content block 按原顺序直接展示，并保留协议类型、tool-call id 和
tool-result id，不编号、不重组，也不跨请求配对数据。System 是一整块连续内容；Tools 只
展示本次请求挂载的定义，并统一格式化 Anthropic、OpenAI Chat/Responses、Gemini 和 Codex
namespace 的 Schema。
每个标签只有一个主纵向阅读滚动区；请求、响应、请求头和完整 Trace 的 JSON 使用按分支
懒展开的折叠树，并兼容 DeepSeek Harness 的悬浮、复制、右键菜单和键盘操作，不再整份截断。
即使 HTTP 状态是 200，流式响应里的真实错误也会明确显示。宽版 Inspector 可以调节大小，
相邻请求会在后台预取；折叠详情、切换请求或标签时会保留原来的标签和滚动位置。移动端会把
会话列表变成抽屉，把请求详情变成全屏阅读区。

Thread、Sub-agent、Turn 等层级在客户端提供时仍会保留，但不影响通用分组，完整信息可在
原始请求数据中查看。

Session 适配来自实际请求流量，不会递归搜索所有叫 `sessionId` 的字段：

| 客户端 | Session 信号 | 可选层级 | 验证依据 |
| --- | --- | --- | --- |
| Claude Code | `X-Claude-Code-Session-Id` | `X-Claude-Code-Agent-Id`、`X-Claude-Code-Parent-Agent-Id` | capture-only 实测 2.1.234 |
| Codex CLI | 优先 `x-codex-turn-metadata.session_id`，再用 `session-id` | thread、turn、window、父 thread | capture-only 实测 0.144.1 |
| DeepSeek Harness | `X-DeepSeek-Harness-Session-Id` | — | capture-only 实测 0.1.0-rc.7 |
| Grok Build | `X-Grok-Session-Id` | agent、turn index | capture-only 实测 1.0.3 |
| Pi | `session_id` | `x-session-affinity` 兜底 | capture-only 实测 0.72.1 |
| Hermes Agent | `session_id` | — | capture-only 实测 0.0.0 agent build |
| opencode 兼容流量 | `X-Session-ID` | `X-Parent-Session-ID` | 已实现兼容；本地版本因 bootstrap 未能直接捕获 prompt |
| Gemini CLI | 模型请求中没有 Session ID | — | capture-only 实测 0.40.1 确认缺失 |

OpenAI Responses 请求存在显式顶层 `conversation` 时也会使用它；
`previous_response_id` 只作为链路信息保存，不会伪造成 Session。通用客户端还支持白名单内的
`x-session-id`、`session-id`、`session_id`、`x-conversation-id` 和对应顶层 body
字段。找不到可靠身份时（例如当前 Gemini CLI 和旧版 Claude Code），页面会明确标注为
capture 级兜底，而不是猜错分组。

Live Viewer schema v2 会先返回轻量元数据索引，再按 JSONL 字节偏移读取选中的完整请求，
并通过带类型的 `record.appended` 事件增量更新。选中项和相邻请求体使用有界 LRU 缓存，
同时合并重复的进行中请求，因此大 trace 不再一次性灌进浏览器内存。

## 导出 Prompt 快照

如果你只关心 system prompt / instructions / tools，不需要完整 viewer，可以用
`--export-prompt`：

```bash
claude-tap run claude --export-prompt claude.prompt.md --no-open -- -p hi
claude-tap run codex --export-prompt codex.prompt.md --no-open -- exec "hi"
claude-tap run dsh --export-prompt dsh.prompt.md --no-open -- --profile headless "hi"
claude-tap run gemini --export-prompt gemini.prompt.md --no-open -- -p hi
claude-tap run grok --export-prompt grok.prompt.md --no-open -- --single hi
claude-tap run agy --export-prompt antigravity.prompt.md --no-open -- --print hi --dangerously-skip-permissions
claude-tap run kimi-code --export-prompt kimi-code.prompt.md --no-open -- --prompt hi
claude-tap run omp --export-prompt omp.prompt.md --no-open -- --print --mode text --no-session hi
```

如果某个 CLI 自己还有子命令，把它的参数放在 `--` 后面：

```bash
claude-tap run openclaw --export-prompt openclaw.prompt.md --no-open -- agent --local --message hi --json
```

prompt 导出成功后，即使子进程后面以非 0 状态退出，`claude-tap` 也会把这次运行视为成功捕获。
这对只关心 prompt 的自动化任务很有用，例如按版本归档 prompt。

只捕获模式返回符合客户端协议的占位响应，包括 Antigravity 所需的 CloudCode 响应外层。
标记为 `requestType: "checkpoint"` 的 CloudCode 请求仍保留在原始 trace 中，但不会作为
prompt 快照候选。如果只捕获到了 checkpoint 请求，prompt 导出会失败。

也可以从已有 trace 里导出 prompt：

```bash
claude-tap export ./.traces/2026-05-06/trace_120137.jsonl --format prompt-md -o prompt.md
```

## 支持的 CLI

你需要先安装自己想追踪的 AI CLI。`claude-tap` 负责启动和代理这些 CLI，不负责安装它们。

| CLI | 命令 | 默认模式 | 状态 |
| --- | --- | --- | --- |
| Claude Code | `claude-tap claude` | reverse | verified |
| Codex CLI | `claude-tap codex` | reverse | verified |
| Codex App | `claude-tap codexapp` | forward | verified |
| DeepSeek Harness | `claude-tap dsh` | forward | prompt-export verified |
| Gemini CLI | `claude-tap gemini` | reverse | verified |
| Grok Build | `claude-tap grok` | reverse | prompt-export verified |
| MiniMax Code | `claude-tap minimax-code` | reverse | prompt-export verified¹ |
| Antigravity CLI | `claude-tap agy` | forward | prompt-export verified |
| Kimi Code | `claude-tap kimi-code` | forward | prompt-export verified |
| MiMo Code | `claude-tap mimo` | forward | prompt-export verified |
| OpenClaw | `claude-tap openclaw` | reverse | prompt-export verified |
| opencode | `claude-tap opencode` | forward | verified |
| Kimi CLI | `claude-tap kimi` | forward | prompt-export verified |
| Pi | `claude-tap pi` | forward | prompt-export verified |
| Oh My Pi | `claude-tap omp` | forward | prompt-export verified |
| Hermes Agent | `claude-tap hermes` | forward | prompt-export verified |
| iFlow CLI | `claude-tap iflow` | forward | verified |
| Cursor Agent | `claude-tap cursor` | reverse | wired |
| Qoder CLI | `claude-tap qoder` | reverse | wired |
| Devin CLI | `claude-tap devin` | forward | wired |

¹ MiniMax Code 目前发布的是桌面应用，并没有公开 CLI。这个客户端面向支持
`MINIMAX_CODE_BASE_URL` 的 headless runtime launcher；Phistory 会提供并验证该 launcher。

`verified` 表示做过真实 trace 捕获。`prompt-export verified` 表示真实 CLI 已经在
capture-only 模式下发出过包含 prompt 的请求。`wired` 表示代码路径已实现并有单测，
但完整真实运行可能还需要登录态、API key 或上游行为验证。

## 原理

`claude-tap` 会启动一个本地代理，再把选中的 CLI 作为子进程启动，并让这个子进程请求本地代理。

它有两种拦截模式：

| 模式 | 用于 | 做法 |
| --- | --- | --- |
| reverse | Claude Code、Codex、Gemini、Grok Build、MiniMax Code、OpenClaw、Cursor、Qoder | 设置 base URL、CLI 参数或临时子进程配置，让 CLI 请求 `127.0.0.1` |
| forward | Codex App、DeepSeek Harness、Antigravity、opencode、Kimi、Kimi Code、MiMo、Pi、Oh My Pi、Hermes、iFlow、Devin | 设置 `HTTPS_PROXY`，并用本地 CA 拦截 HTTPS |

两种模式都会尽量保留你原本配置的真实 upstream。如果你的 CLI 本来就走私有 relay 或区域 endpoint，
`claude-tap` 会继续转发到那里，而不是偷偷换成官方默认地址。

forward 模式第一次使用时会生成本地 CA。Node 和 Python 客户端通常会通过环境变量自动信任它。
如果某个 CLI 的 TLS 栈不认这些环境变量，可以运行：

```bash
claude-tap ca install
```

## 常用命令

```bash
# 追踪一次普通 CLI 运行
claude-tap claude -- -p "What is 2+2?"

# 运行后不要自动打开浏览器
claude-tap claude --no-open -- -p "hi"

# 指定真实上游，例如自己的 relay
claude-tap codex -t https://my-relay.example.com/v1

# 只启动代理，再让其他进程连进来
claude-tap proxy -p 8080
ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude

# 浏览历史 trace
claude-tap live

# 导出 trace
claude-tap export ./.traces/2026-05-06/trace_120137.jsonl -o report.md
claude-tap export ./.traces/2026-05-06/trace_120137.jsonl --format html
```

完整参数看：

```bash
claude-tap --help
claude-tap run --help
```

## 安全提醒

`claude-tap` 会记录子进程 CLI 发送和接收的内容。trace 里可能包含 prompt、文件路径、
工具结果、token 和 provider 元数据。公开分享前请先检查。

默认情况下代理只监听本机。`run` 默认绑定 `127.0.0.1`；如果确实需要让外部连接，
`proxy` 可以显式指定 `--host`。

## 开发

```bash
git clone https://github.com/WEIFENG2333/claude-tap.git
cd claude-tap
uv sync --extra dev
uv run claude-tap --version
```

架构和维护指南见 [`MAINTAINING.md`](MAINTAINING.md)，coding-agent 贡献规则见 [`AGENTS.md`](AGENTS.md)。

## License

[MIT](LICENSE)
