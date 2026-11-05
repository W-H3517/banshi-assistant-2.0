import uvicorn

from .app import configure_logging, create_app
from .config import Settings

settings = Settings.load()
configure_logging(settings)
uvicorn.run(create_app(settings), host="127.0.0.1", port=8000)
