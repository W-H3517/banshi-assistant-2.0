# 办事助手：制度知识库与 Agent 聊天

本地试用程序：简易登录、个人会话隔离、共享 PDF 制度知识库、RAG 检索和 Agent 流式问答。前端使用 Vue 3 + TypeScript + Vite + Element Plus，后端使用 FastAPI、Pydantic AI 和 SQLite。

## 先选启动方式

| 用途 | 需要启动 | 浏览器地址 | 前端修改后 |
| --- | --- | --- | --- |
| **日常开发，支持热更新** | 后端（带 `--reload`）＋ Vite 前端，两个终端 | **http://127.0.0.1:5173/** | 保存源码后自动更新，通常无需手动刷新或构建。 |
| **使用构建好的版本** | 只启动后端 | **http://127.0.0.1:8000/** | 重新构建前端，再刷新页面。 |

以下命令均在项目根目录的 PowerShell 中执行。两种方式共用一个 8000 后端，不要重复启动；终端内按 `Ctrl+C` 停止服务。

## 首次准备

```powershell
cd 'D:\公司文件\banshi-assistant-2.0'
uv sync --locked
npm --prefix frontend ci
# 首次下载中文嵌入模型并验证本地 CPU 推理；已准备好后无需重复执行
uv run --locked python -m backend.prepare
```

嵌入模型为 `BAAI/bge-small-zh-v1.5`，通过 FastEmbed／ONNX Runtime 在本地 CPU 运行，模型权重约 90 MB，保存在 `data/models/`。首次准备需要联网访问 Hugging Face；正常后端启动只读本地缓存，未准备模型时请先执行上述命令。无需 GPU、嵌入 API 密钥或独立向量数据库服务。Windows 的缓存软链接提示不影响使用，无需为此开启管理员模式。

本地 `.env`：

```dotenv
DEEPSEEK_API_KEY=填写你的密钥
DEEPSEEK_MODEL=deepseek-flash
```

缺少密钥时，登录和知识库仍可使用，聊天会显示未配置提示。默认模型关闭思考模式。模型配置改变后请新建会话，不自动接续不同模型的历史。

## 开发启动：前后端自动更新（推荐开发时使用）

**终端一：启动后端，开启代码重载和详细日志。**

```powershell
uv run --locked python -m backend --reload --log-mode detailed
```

只需要简单日志时，将上面的命令替换为：

```powershell
uv run --locked python -m backend --reload --log-mode simple
```

**终端二：启动 Vite 前端。**

```powershell
npm --prefix frontend run dev
```

**浏览器打开 http://127.0.0.1:5173/ 。开发时从这个地址进入，不要打开 8000 的旧构建页面。**

- 修改 `frontend/src` 中的 Vue、TypeScript 或 CSS，保存后 Vite 自动更新页面；部分改动会触发整页刷新。
- 修改 `backend` 中的 Python 文件，Uvicorn 自动重启后端，并保留选定的日志模式；正在生成的聊天可能被中断。
- 修改 `.env` 或安装新依赖后，手动重启对应服务。
- Windows 下请在普通终端前台运行 `--reload`，保留控制台输出；本机验证中，无控制台后台运行或将输出重定向到文件会使 Uvicorn 重载卡住。程序已自动写入日志文件，无需额外使用 `>` 重定向。
- 开发启动无需运行 `npm run build`。如果 5173 被占用，以 Vite 终端实际显示的 Local 地址为准。

前端通过 `/api` 代理连接 8000 后端。代理显式设置 `changeOrigin: false`，保留原始 Host，使浏览器 Origin 与 Host 一致，修复从 5173 登录时“不允许跨站请求”的问题；后端仍拒绝外部来源的写请求。旧 Vite 进程若未自动应用配置，按 `Ctrl+C` 后重新运行前端启动命令。

## 构建后试用：只启动后端

首次使用或前端源码修改后，先构建：

```powershell
npm --prefix frontend run build
```

然后启动后端，不需要 Vite：

```powershell
uv run --locked python -m backend --log-mode simple
```

打开 http://127.0.0.1:8000/ 。后端同时提供 `frontend/dist` 中的页面和 `/api` 接口；此方式不支持前端热更新。需要详细日志时把 `simple` 改为 `detailed`。当前命令默认仅监听本机，正式部署另行配置。

## 日志模式

通过启动命令选择，不需要修改 `.env` 或数据库。切换模式需停止当前后端再启动。

```powershell
# 简单模式：默认值，不加参数也一样
uv run --locked python -m backend --log-mode simple

# 详细模式：记录每次模型调用和工具调用的输入、结果
uv run --locked python -m backend --log-mode detailed
```

两种模式均输出控制台和 `data/app/logs/server-时间-模式.txt`（UTF-8），启动时显示具体路径。

| 模式 | 内容 |
| --- | --- |
| simple | 请求状态、会话完成与调用次数、错误类型；不记录对话、资料或工具正文。 |
| detailed | 简单日志，加上框架生成的每次模型请求的指令、历史消息、工具定义和输出，以及工具名称、调用 ID、参数和结果；包含追踪 ID、时间、会话关联和框架提供的用量。 |

详细模式使用 **Pydantic AI 原生 Instrumentation + OpenTelemetry SimpleSpanProcessor / ConsoleSpanExporter**。仅增加本地日志输出适配、中文格式化和密钥脱敏，不自行维护一套模型/工具事件采集逻辑，也不向 Logfire 或其他遥测服务上传。

详细记录在一次调用结束或报错后写入；流式模型输出由框架合并为该次调用结果，不逐个 token 刷日志。它记录的是框架的模型消息与工具调用结构，不是原始 HTTP 抓包，不采集登录密码、Cookie 或认证请求头。

阅读详细日志时：

- `chat 模型名`：`gen_ai.system_instructions`、`gen_ai.input.messages`、`gen_ai.output.messages` 分别对应指令、输入和输出。
- `execute_tool 工具名`：`gen_ai.tool.call.arguments` 和 `gen_ai.tool.call.result` 对应工具参数与结果。
- 问问题暂停时会记录 `CallDeferred` 状态；用户稍后回答的结果在续接请求的输入消息中，按工具调用 ID 关联。
- API 错误可能记录在同一 trace 的 `invoke_agent` 中，包含异常和 HTTP 状态；用 `trace_id` 对应相关模型调用。
- 跨轮及追问续接按 `gen_ai.conversation.id` 或 Agent `metadata.conversation_id` 查询。

详细日志包含聊天和检索片段，API 密钥会替换为 `[REDACTED]`；日志目录继续由 Git 忽略。原有日志保留，不因切换模式而删除。本地嵌入不调用外部 API；嵌入向量和 PDF 二进制不写入聊天追踪日志。

## 测试账号

| 账号 | 初始密码 | 权限 |
| --- | --- | --- |
| admin | demo12345 | 上传制度、查看共享知识库、使用自己的聊天。 |
| alice | demo12345 | 查看共享知识库、使用自己的聊天。 |
| bob | demo12345 | 查看共享知识库、使用自己的聊天。 |

账号仅用于本地原型。登录使用随机 HttpOnly Cookie，服务端保存令牌摘要；密码使用带盐 PBKDF2 哈希保存。管理员也不能读取其他人的聊天。制度按部门共享，不按上传者隔离。当前不提供注册、账号管理和密码修改页面。

## 试用方式

1. 用 admin 登录，在「制度知识库」上传可复制文字的 `.pdf`，单份上限 20 MB、200 页；可直接使用已导入的三份 PDF 模拟制度。暂不支持 Markdown 上传、扫描件 OCR、加密 PDF。
2. 上传期间页面显示正在解析和建立索引；完成后才能查询。相同文件改名后重复上传仍会提示已存在。索引失败不保留半成品资料。
3. 在「办事助手」提问，例如「外地培训回来报销需要什么材料？」。Agent 自主决定直接回答或调用工具，页面显示工具状态和流式正文。
4. 需要信息时，问问题工具显示问题卡片；选择选项或输入自由文本，提交后继续执行。等待问题可跨页面刷新和服务重启恢复。
5. 点击回答中的制度引用或「查阅资料」打开 PDF 对应页；可调整页码或在新窗口打开。引用采用 PDF 物理页码，从 1 开始，可能与纸面印刷页码不同。
6. 左侧历史可继续聊天；退出后切换 alice 或 bob，检查个人会话隔离。

初期只有两个 Agent 工具：

- `ask_question`：提出问题，可附选项；使用 Pydantic AI DeferredToolRequests / DeferredToolResults 暂停并恢复工具循环。
- `search_documents`：用具体问题或关键词混合检索制度，最多返回 6 个片段，每个不超过 1000 字符，附文档 ID、标题、物理页码和引用链接。可指定 `document_id` 缩小范围，或同时指定 `page` 补查某页。不会自动把整份长 PDF 交给模型。

没有固定的工具调用顺序。单轮连同追问续接最多 8 次模型请求，每次超时 120 秒。停止或失败不把未完成的工具历史写入成功历史，页面允许重新发送；等待问题期间失败仍保留原问题。

## PDF 与检索实现

- pdfplumber 提取文字并保留页码，按嵌入模型实际 tokenizer 分块，尽可能在句末或段末断开；每块最多 380 个正文 token、相邻块约 50 token 重叠，同时限制字符数。标题与正文一起嵌入前再次检查 512 token 上限。
- Jieba 中文分词＋BM25 关键词排名，与本地 BGE 向量余弦相似度排名用 RRF 融合。语义候选阈值为 0.40，用于召回筛选，不代表答案置信度；Agent 必须判断片段是否支持结论，无依据时明确说明。模型固定为 Qdrant ONNX 快照 `46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59`；后续更换嵌入模型或分块方案需要重新索引，不能直接混用旧向量。
- 原 PDF、片段和 float32 向量在同一 SQLite 事务中保存，重启直接读取，未改动资料不重新计算向量。当前采用精确向量扫描和内存 BM25，面向小规模本地试用；大量文档或高并发需要另行评测并升级索引服务。
- PDF 解析、分词排名及嵌入计算在工作线程中执行；上传排队串行处理，CPU 嵌入限制为 2 个线程。
- 正文排版、简单表格按 PDF 的文字顺序提取；复杂多栏、合并单元格、跨页表格和图片中的信息不保证完整还原，关键条款应打开原 PDF 核对。不做 OCR，也不进行模型微调。
- 本地嵌入不向外部服务发送制度；聊天仍使用已配置的 DeepSeek API，检索到的片段会随聊天上下文发送给该 API。正式内部资料的部署边界仍按需求文档另行确定。

## 数据与范围

- `backend/`：后端、认证、数据库、检索和 Agent。
- `frontend/`：前端源码与 npm 锁文件。
- `tests_stage2/`：登录、会话、日志、启动和 PDF／RAG 测试。
- `data/fixtures/`：三份受 Git 管理的 PDF 模拟制度，不是正式办事依据。
- `data/app/banshi.sqlite3`：账号、登录令牌摘要、原 PDF、片段、向量、会话与待答问题。
- `data/models/`：本地嵌入模型缓存，不纳入 Git。
- `data/app/logs/`：UTF-8 `.txt` 日志；简单模式记录摘要，详细模式记录模型和工具内容，两者均屏蔽 API 密钥。

首次启动直接创建表并初始化账号；启动时按文件 SHA-256 去重导入初始 PDF，不覆盖已有资料，不引入数据库迁移。请勿把修改 fixtures 当成制度版本更新方式。

当前支持新增 PDF 制度和混合检索，不做编辑、删除、版本、发布、审核、经验或 OCR。手机只保留基本适配，暂不精修。

备份时先停止服务，再复制 `data/app`；恢复时在停止状态下放回同一目录。现阶段只支持单进程运行；后续结构不兼容必须先确认兼容或舍弃，不自动重建数据库。

本次 PDF 升级经用户确认舍弃旧 Markdown 运行数据；用户已手动删除旧 `data/app`，助手核实后重新初始化。旧 Markdown 初始资料已转换为 PDF，旧上传与全文检索实现已移除，`.env` 保留。程序不会自动删除或迁移其他已有数据库。

## 验证命令

```powershell
uv run --locked python -m pytest -q
uv run --locked ruff check backend tests_stage2
npm --prefix frontend run build
git diff --check
```

测试使用临时数据库、实际本地嵌入模型和模拟聊天 API 响应；请先运行 `backend.prepare`。测试不调用真实聊天 API。真实模型验证通过网页完成，会产生调用费用。

当前状态见 [开发计划.md](开发计划.md)、[验证记录.md](验证记录.md)。工具暂停恢复参考 [Pydantic AI 官方文档](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)。
