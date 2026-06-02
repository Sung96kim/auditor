"""Application entrypoint."""

import uvicorn
from fastapi import FastAPI

from app.api import routes

app = FastAPI(title="Pulse")
app.include_router(routes.router)


def start():
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except Exception:
        pass


if __name__ == "__main__":
    start()
