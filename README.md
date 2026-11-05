# 办事助手：第一阶段

Python 脚本验证对话、历史、资料工具和 Agent 循环；无前端、HTTP 服务、数据库或账号管理。

## 启动

在项目目录执行：

```powershell
uv sync --locked
uv run python -m agent_demo
```

本地 `.env` 配置：

```dotenv
DEEPSEEK_API_KEY=填写你的密钥
DEEPSEEK_MODEL=deepseek-flash
```

默认模型为 `deepseek-flash`，思考模式关闭。只验证此模型；修改模型配置后可新建会话，跨模型历史接续需要另行确认和验证。

| 命令 | 功能 |
| --- | --- |
| `/new` | 新建空会话，不改变测试用户 ID。 |
| `/sessions` | 列出已保存的会话 ID、更新时间和模型。 |
| `/load <会话ID>` | 加载历史，继续对话。 |
| `/exit` | 退出。 |
| Ctrl+C | 中断当前操作，不保存未完成轮次。 |

示例问题：`外地培训回来报销需要什么材料？请查询相关制度。`

## 数据与日志

- `data/fixtures/`：受 Git 管理的模拟制度，可按关键词搜索并读取原文；不能作为真实办事依据。
- `data/runtime/identity.json`：首次运行生成的匿名 UUID；每次模型请求通过 `extra_body.user_id` 传递，包括工具续轮。
- `data/runtime/sessions/`：完整成功轮次的 JSON 历史，保留工具调用与结果；失败和中断不覆盖此前历史。
- `logs/`：每次进程运行生成 UTF-8 `.txt` 日志，包含 SDK 请求信息、工具状态、耗时、用量与错误类型，不输出密钥或完整请求体。
- `.env`、运行数据和日志均被 Git 忽略。不要在日志或提交中复制密钥。

测试身份不是账号体系。DeepSeek 的 `user_id` 用于服务端内容安全、KVCache 与调度隔离，不能代替第二阶段的访问权限。身份文件损坏时停止启动，不自动替换；需要重置时先确认旧数据处置方式。

## 验证与故障

```powershell
uv run pytest -q
uv run ruff check .
```

真实 API 验证（会产生调用费用，结果保存在 `data/runtime/live-verification.json`）：

```powershell
uv run python -m agent_demo.verify
```

密钥缺失时不发起请求。API 失败显示错误类型和 HTTP 状态；检查本地密钥、余额、网络和模型权限后重新提问。每轮最多 8 次模型请求，每次超时 120 秒；不自动切换模型、删除历史或额外循环重试。

实际验证记录见 `验证记录.md`；开发范围及后续工作见 `开发计划.md`。

## 官方参考

- [DeepSeek user_id 与隔离](https://api-docs.deepseek.com/quick_start/rate_limit/)
- [Pydantic AI DeepSeek 接入](https://pydantic.dev/docs/ai/models/deepseek/)
- [Pydantic AI Agent 与事件流](https://pydantic.dev/docs/ai/core-concepts/agent/)
