import argparse
import os

import uvicorn

from .config import ROOT


def main():
    parser = argparse.ArgumentParser(description="办事助手本地服务")
    parser.add_argument("--log-mode", choices=("simple", "detailed"), default="simple",
                        help="日志模式：simple 摘要（默认）；detailed 模型和工具输入输出")
    parser.add_argument("--reload", action="store_true", help="开发模式：后端 Python 修改后自动重启")
    args = parser.parse_args()
    # Uvicorn 重载使用子进程；通过进程环境继承启动参数，不修改本地 .env。
    os.environ["BANSHI_LOG_MODE"] = args.log_mode
    uvicorn.run(
        "backend.app:create_app", factory=True, host="127.0.0.1", port=8000,
        reload=args.reload, reload_dirs=[str(ROOT / "backend")] if args.reload else None,
    )


if __name__ == "__main__":
    main()
