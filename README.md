# 办事助手：制度知识库与 Agent 聊天

本地试用程序：简易登录、个人会话隔离、共享 PDF 制度知识库、员工经验、RAG 检索和 Agent 流式问答。前端 Vue 3 + TypeScript + Vite + Element Plus，后端 FastAPI + Pydantic AI + SQLite。解析、OCR、嵌入都在本机 CPU 运行，只有聊天会调用所配置的模型服务。

实现细节见 [docs/实现说明.md](docs/实现说明.md)，进展见 [开发计划.md](开发计划.md)、[验证记录.md](验证记录.md)。以下命令均在项目根目录的 PowerShell 中执行。

## 首次准备

```powershell
uv sync --locked
npm --prefix frontend ci
uv run --locked python -m backend.prepare
```

`backend.prepare` 下载并校验本地模型（需联网访问 Hugging Face 与 ModelScope，已准备的只校验）：中文嵌入 `BAAI/bge-small-zh-v1.5` 约 90 MB，RapidOCR 识别模型约 32 MB，版面检测与表格结构模型约 15 MB，都保存在 `data/models/`。无需 GPU 或向量数据库服务。

在项目根目录新建 `.env`，按所用模型填写（改完重启后端并新建会话）：

```dotenv
# DeepSeek（默认）
MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=填写你的密钥
DEEPSEEK_MODEL=deepseek-flash

# 或 OpenAI：MODEL_PROVIDER=openai、OPENAI_API_KEY、OPENAI_MODEL，
#   可选 OPENAI_API=responses|chat_completions、OPENAI_REASONING_EFFORT=low 等
# 或本机 Ollama（无需密钥，需先启动 Ollama 并下载模型）：
#   MODEL_PROVIDER=ollama、OLLAMA_MODEL=qwen3.5:9b-q8_0、OLLAMA_BASE_URL=http://localhost:11434/v1
```

缺少密钥或 Ollama 未启动时，登录和知识库照常可用，只有聊天会提示未配置或调用失败。

## 启动

| 用途 | 启动什么 | 浏览器地址 |
| --- | --- | --- |
| 日常开发（前后端自动更新） | 后端 `--reload` ＋ Vite，两个终端 | http://127.0.0.1:5173/ |
| 使用构建好的页面 | 只启动后端 | http://127.0.0.1:8000/ |

**开发：**终端一启动后端，终端二启动前端，从 5173 进入：

```powershell
uv run --locked python -m backend --reload
```

```powershell
npm --prefix frontend run dev
```

**构建后使用：**前端源码有改动时先构建，再只启动后端，从 8000 进入：

```powershell
npm --prefix frontend run build
```

```powershell
uv run --locked python -m backend
```

后端只监听本机 8000 端口，`Ctrl+C` 停止。日志写入控制台和 `data/app/logs/server-时间-模式.txt`：默认 `--log-mode simple` 只记请求与调用次数；`--log-mode detailed` 另记每次模型请求和工具调用的输入输出（API 密钥脱敏）。Windows 下 `--reload` 请在普通终端前台运行，不要重定向输出。

## 启动自检与常见问题

后端启动前会自检，不通过就列出原因并中止，按提示处理后重新启动：

| 提示 | 处理 |
| --- | --- |
| 缺少依赖 | 先停止正在运行的后端，再 `uv sync --locked`（切换分支后常见） |
| 嵌入模型或 OCR、版面、表格识别模型未准备 | `uv run --locked python -m backend.prepare` |
| 检索索引与当前程序的索引版本不一致（列出文件） | 停止后端，运行 `uv run --locked python -m backend.reindex`，用库中保存的原 PDF 重建全部索引（文档 ID、账号、会话、经验保留；含扫描件时需几分钟），完成后再启动 |
| 端口 8000 已被占用 | 已有后端在运行：在原终端 `Ctrl+C`；不确定时 `netstat -ano \| findstr :8000` 查进程 |

其他情况：

- `uv sync` 报 `cv2.pyd` 拒绝访问：后端或重建进程占用着 OpenCV，先全部停止再同步。
- 8000 打开的页面是旧的：重新 `npm --prefix frontend run build`，或开发时改用 5173。
- 中文路径下 `npm run build` 直接退出（`-1073740791`）：Node 24.12.0 的已知问题，升级到 24.13.1 及以上。

## 测试账号

| 账号 | 初始密码 | 权限 |
| --- | --- | --- |
| admin | demo12345 | 上传、删除制度，审核经验，重置测试数据 |
| alice / bob | demo12345 | 查看知识库、记录经验、使用自己的聊天 |

仅供本地原型使用，没有注册与改密页面；管理员也看不到其他人的聊天。

## 功能概览

- **办事助手**：流式回答，可展开「检索过程」查看每步查询与结果；需要条件时会弹出追问卡片。回答引用打开右侧证据栏，在原 PDF 上高亮所查片段。
- **知识库 → 制度**：搜索与阅读制度，阅读器默认「切分预览」显示实际检索片段。管理员可上传文字或扫描版 PDF（≤20 MB、≤200 页），解析完成后才能检索；可删除制度。「重置测试数据」会清空全部制度和所有聊天，恢复三份模拟制度，不可撤销。
- **员工经验**：任何人可记录经验并提交审核；管理员在「管理工作台」调整、发布或下架；已发布经验可在知识库浏览，并参与助手检索。

## 目录与数据

- `backend/`：后端（`parsing/` PDF 解析与表格识别，`indexing/` 结构树与检索片段，`knowledge/` 资料维护、检索与查表）。
- `frontend/`：Vue 前端；`设计样稿/`：已确认的静态样稿，不参与构建。
- `tests/`：后端测试。`data/fixtures/`：三份模拟制度（非正式依据）。`测试素材/`：本地真实制度，Git 忽略。
- `data/app/banshi.sqlite3`：账号、原 PDF、检索索引、会话与经验；`data/models/`：本地模型；`data/app/logs/`：日志。三者都不纳入 Git。
- 备份：停止后端后复制 `data/app`；恢复时在停止状态放回原处。只支持单进程运行。

## 验证命令

```powershell
uv run --locked python -m pytest -q
uv run --locked ruff check backend tests
npm --prefix frontend run build
```

测试使用临时数据库、本地模型和模拟聊天响应，不调用真实聊天 API；运行前需已执行 `backend.prepare`。
