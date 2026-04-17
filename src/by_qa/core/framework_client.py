"""Helpers for calling discovered HTTP services through by-framework."""

from __future__ import annotations

from typing import Any

from by_framework.common.redis_client import init_redis
from by_framework.core.discovery import DiscoveryClient
from by_framework.util.discovery_http_client import DiscoveryHttpClient
from by_framework.util.http_client import RetryConfig

from by_qa.config import get_settings

DEFAULT_DISCOVERY_CACHE_INTERVAL = 5
DEFAULT_REMOTE_REQUEST_RETRY_ATTEMPTS = 3


def _build_discovery_client() -> DiscoveryClient:
    """Create a discovery client using repository settings."""
    settings = get_settings()
    redis_client = init_redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_database,
        password=settings.redis_password or None,
        username=settings.redis_username or None,
        decode_responses=True,
    )
    return DiscoveryClient(
        redis_client=redis_client,
        cache_interval=DEFAULT_DISCOVERY_CACHE_INTERVAL,
    )


def _build_retry_config() -> RetryConfig:
    """Create the retry config used for discovered HTTP requests."""
    return RetryConfig(
        max_attempts=DEFAULT_REMOTE_REQUEST_RETRY_ATTEMPTS,
        retry_on_status_codes=frozenset({502, 503, 504}),
    )


async def request_discovered_json(
    *,
    method: str,
    service_name: str,
    path: str,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Call a discovered HTTP service and return the parsed JSON body."""
    discovery_client = _build_discovery_client()
    try:
        async with DiscoveryHttpClient(
            discovery_client,
            retry_config=_build_retry_config(),
        ) as client:
            request_kwargs: dict[str, Any] = {
                "method": method,
                "service_name": service_name,
                "path": path,
                "json": json,
            }
            if headers:
                request_kwargs["headers"] = headers
            response = await client._request_with_discovery(
                **request_kwargs,
            )
        if not response.is_success:
            raise RuntimeError(
                f"service request failed: service_name={service_name}, "
                f"path={path}, status_code={response.status_code}"
            )
        if not isinstance(response.data, dict):
            raise ValueError("discovered service response body must be a JSON object")
        return response.data
    finally:
        await discovery_client.close()


async def post_discovered_json(
    *,
    service_name: str,
    path: str,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """POST JSON to a discovered service and return the parsed JSON body."""
    return await request_discovered_json(
        method="POST",
        service_name=service_name,
        path=path,
        json=json,
        headers=headers,
    )


__all__ = [
    "post_discovered_json",
    "request_discovered_json",
]
