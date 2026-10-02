"""Configurable standalone API entry point for desktop and LAN demos."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "backend.app.main:app",
        host=os.getenv("STATION_HOST", "127.0.0.1"),
        port=int(os.getenv("STATION_PORT", "8000")),
        proxy_headers=os.getenv("APP_ENV", "development").lower() == "production",
        forwarded_allow_ips=os.getenv("STATION_TRUSTED_PROXIES", "127.0.0.1"),
    )


if __name__ == "__main__":
    main()
