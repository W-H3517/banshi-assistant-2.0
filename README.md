# 办事助手：制度知识库与 Agent 聊天

第二阶段本地试用程序：简易登录、个人会话隔离、共享 Markdown 制度知识库、Agent 流式问答。前端使用 Vue 3 + TypeScript + Vite + Element Plus，后端使用 FastAPI、Pydantic AI 和 SQLite。

## 快速启动

首次安装和构建，在项目根目录执行：

```powershell
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
uv run --locked python -m backend
```

以后可直接执行最后一条命令。浏览器打开 http://127.0.0.1:8000/ 。后端同时提供构建后的页面和 API，默认只监听本机，使用单个进程。

本地 `.env`：

```dotenv
DEEPSEEK_API_KEY=填写你的密钥
DEEPSEEK_MODEL=deepseek-flash
```

缺少密钥时，登录和知识库仍可使用，聊天会显示未配置提示。默认模型关闭思考模式。模型配置改变后请新建会话，不自动接续不同模型的历史。

## 测试账号

| 账号 | 初始密码 | 权限 |
| --- | --- | --- |
| admin | demo12345 | 上传制度、查看共享知识库、使用自己的聊天。 |
| alice | demo12345 | 查看共享知识库、使用自己的聊天。 |
| bob | demo12345 | 查看共享知识库、使用自己的聊天。 |

账号仅用于本地原型。登录使用随机 HttpOnly Cookie，服务端保存令牌摘要；密码使用带盐 PBKDF2 哈希保存。管理员也不能读取其他人的聊天。制度按部门共享，不按上传者隔离。当前不提供注册、账号管理和密码修改页面。

## 试用方式

1. 用 admin 登录，在「制度知识库」上传 UTF-8 `.md` 文件，单份上限 256 KB；可直接使用已导入的三份模拟制度。
2. 在「办事助手」提问，例如「外地培训回来报销需要什么材料？」。
3. Agent 自主决定直接回答或调用工具，页面显示工具状态和流式正文。
4. 需要信息时，问问题工具显示问题卡片；选择选项或输入自由文本，提交后继续执行。等待问题可跨页面刷新和服务重启恢复。
5. 点击回答中的制度引用或「查阅资料」打开原文；左侧历史可继续聊天。
6. 退出后切换 alice 或 bob，检查个人会话隔离。

初期只有两个 Agent 工具：

- `ask_question`：提出问题，可附选项；使用 Pydantic AI DeferredToolRequests / DeferredToolResults 暂停并恢复工具循环。
- `search_documents`：关键词查询已上传制度，最多返回五份命中文档的完整原文；模型可自行补查。

没有固定的工具调用顺序。单轮连同追问续接最多 8 次模型请求，每次超时 120 秒。停止或失败不把未完成的工具历史写入成功历史，页面允许重新发送；等待问题期间失败仍保留原问题。

## 数据与范围

- `backend/`：后端、认证、数据库、检索和 Agent。
- `frontend/`：前端源码与 npm 锁文件。
- `tests_stage2/`：第二阶段测试。
- `data/fixtures/`：三份受 Git 管理的模拟制度，不是正式办事依据。
- `data/app/banshi.sqlite3`：账号、登录令牌摘要、制度原文、会话与待答问题。
- `data/app/logs/`：UTF-8 `.txt` 日志；不记录完整模型请求或密钥。

首次启动直接创建表并初始化账号和模拟制度，不引入数据库迁移。仅首次创建账号时导入模拟制度；修改 fixtures 不会自动覆盖库中内容。

当前支持新增制度，不做编辑、删除、版本、发布、审核、经验、PDF 或向量检索。手机只保留基本适配，暂不精修。

备份时先停止服务，再复制 `data/app`；恢复时在停止状态下放回同一目录。现阶段只支持单进程运行；后续结构不兼容必须先确认兼容或舍弃，不自动重建数据库。

第一阶段旧源码、测试、匿名身份、JSON 历史和日志已由用户手动删除，并完成目录核实及 Git 清理。当前使用第二阶段入口和数据目录。

## 开发和验证

后端开发：

```powershell
uv run uvicorn backend.app:create_app --factory --reload --host 127.0.0.1 --port 8000
```

另开终端运行前端开发服务：

```powershell
npm --prefix frontend run dev
```

Vite 将 `/api` 转发到后端。生产构建由后端静态托管；前端改动后需重新构建、刷新页面。

```powershell
uv run --locked python -m pytest -q
uv run --locked ruff check backend tests_stage2
npm --prefix frontend run build
git diff --check
```

测试使用临时数据库和模拟模型请求，不调用真实 API。真实模型验证通过网页完成，会产生调用费用。

当前状态见 [开发计划.md](开发计划.md)、[验证记录.md](验证记录.md)。工具暂停恢复参考 [Pydantic AI 官方文档](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)。
