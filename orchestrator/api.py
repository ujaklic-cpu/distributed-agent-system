from __future__ import annotations

import uvicorn

from orchestrator.config import settings


def main() -> None:
    uvicorn.run(
        "orchestrator.app:app",
        host="0.0.0.0",
        port=8080,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
