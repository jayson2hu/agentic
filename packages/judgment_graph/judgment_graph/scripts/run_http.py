from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "judgment_graph.http_api:app",
        host=os.getenv("L2_HTTP_HOST", "127.0.0.1"),
        port=int(os.getenv("L2_HTTP_PORT", "8200")),
        reload=False,
    )


if __name__ == "__main__":
    main()
