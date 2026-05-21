"""MetadataFieldsListOperation — parallel metadata fields query across knowledge bases."""

from __future__ import annotations

from typing import Any

from by_qa.core.exceptions import KnowledgeBaseNotFoundOrForbiddenError
from by_qa.core.logger import error, info
from by_qa.qa.common.config import KnowledgeBaseConfig
from by_qa.qa.common.context import QARuntimeContext
from by_qa.qa.common.operation_registry import OperationType
from by_qa.qa.tools.operations.base import (
    BaseOperation,
    DispatchRequest,
    _normalize_headers,
)


def _format_metadata_field_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "property_name": item.get("propertyName") or item.get("property_name", ""),
        "value_type": item.get("valueType") or item.get("value_type", ""),
        "description": item.get("description", ""),
        "source_type": "metadata_field",
    }


def _format_metadata_fields_error(
    *, service_name: str, path: str, exc: Exception
) -> dict[str, Any]:
    return {
        "property_name": "",
        "value_type": "",
        "description": "",
        "source_type": "metadata_field",
        "is_error": True,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "service_name": service_name,
        "path": path,
    }


def _format_metadata_fields_api_error(
    *, service_name: str, path: str, result_msg: str
) -> dict[str, Any]:
    return {
        "property_name": "",
        "value_type": "",
        "description": "",
        "source_type": "metadata_field",
        "is_error": True,
        "error": result_msg,
        "error_type": "ApiError",
        "service_name": service_name,
        "path": path,
    }


class MetadataFieldsListOperation(BaseOperation):
    """Parallel metadata fields list across multiple KBs grouped by service."""

    operation_type = OperationType.METADATA_FIELDS_LIST

    def build_requests(
        self,
        payload: dict[str, Any],
        kbs: list[KnowledgeBaseConfig],
        ctx: QARuntimeContext,
    ) -> tuple[list[DispatchRequest], list[dict[str, Any]]]:
        authorized_codes = {kb.kb_code for kb in kbs}
        kn_code_list: list[str] | None = payload.get("kn_code_list") or payload.get(
            "knCodeList"
        )

        pre_dispatch_errors: list[dict[str, Any]] = []
        if kn_code_list:
            unauthorized = [
                code for code in kn_code_list if code not in authorized_codes
            ]
            for code in unauthorized:
                exc = KnowledgeBaseNotFoundOrForbiddenError(
                    f"Knowledge base '{code}' not found or access not permitted."
                )
                error("[dispatcher] metadataFields: %s", exc)
                pre_dispatch_errors.append(
                    _format_metadata_fields_error(service_name="", path="", exc=exc)
                )
            kbs = [kb for kb in kbs if kb.kb_code in kn_code_list]

        # Group KBs by (service_name, path, base_url)
        grouped: dict[tuple[str, str, str | None], list[str]] = {}
        service_headers: dict[str, dict[str, str]] = {}
        for kb in kbs:
            path = kb.operations.get(OperationType.METADATA_FIELDS_LIST)
            if not path:
                continue
            normalized = _normalize_headers(kb.headers)
            if normalized:
                service_headers.setdefault(kb.service_name, {}).update(normalized)
            key = (kb.service_name, path, kb.base_url)
            grouped.setdefault(key, [])
            if kb.kb_code not in grouped[key]:
                grouped[key].append(kb.kb_code)

        if not grouped:
            return ([], pre_dispatch_errors)

        requests = [
            DispatchRequest(
                service_name=service_name,
                path=path,
                base_url=base_url,
                headers=service_headers.get(service_name),
                body={"knCodeList": kb_codes},
            )
            for (service_name, path, base_url), kb_codes in grouped.items()
        ]

        for r in requests:
            if r.base_url:
                info(
                    "[dispatcher] metadataFields: direct mode url=%s%s",
                    r.base_url.rstrip("/"),
                    "/" + r.path.lstrip("/"),
                )
            else:
                info(
                    "[dispatcher] metadataFields: discovery mode service=%s path=%s",
                    r.service_name,
                    r.path,
                )
        info("[dispatcher] metadataFields: dispatching %s requests", len(requests))

        return (requests, pre_dispatch_errors)

    def process_response(
        self, response: dict[str, Any], request: DispatchRequest
    ) -> list[dict[str, Any]]:
        return [
            _format_metadata_field_item(item)
            for item in response.get("resultObject", {}).get("data", [])
        ]

    def process_api_error(
        self, response: dict[str, Any], request: DispatchRequest
    ) -> list[dict[str, Any]]:
        result_msg = response.get("resultMsg", "unknown error")
        error(
            "[dispatcher] metadataFields API error: service=%s base_url=%s path=%s resultMsg=%s",
            request.service_name,
            request.base_url,
            request.path,
            result_msg,
        )
        return [
            _format_metadata_fields_api_error(
                service_name=request.service_name,
                path=request.path,
                result_msg=result_msg,
            )
        ]

    def process_error(
        self, exc: Exception, request: DispatchRequest
    ) -> list[dict[str, Any]]:
        error(
            "[dispatcher] metadataFields failed: service=%s base_url=%s path=%s error=%s",
            request.service_name,
            request.base_url,
            request.path,
            exc,
        )
        return [
            _format_metadata_fields_error(
                service_name=request.service_name, path=request.path, exc=exc
            )
        ]

    def aggregate(self, parts: list[Any]) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for part in parts:
            if isinstance(part, list):
                for item in part:
                    if item.get("is_error"):
                        errors.append(item)
                    else:
                        prop_name = item.get("property_name", "")
                        if prop_name and prop_name not in seen:
                            seen.add(prop_name)
                            results.append(item)
            elif isinstance(part, dict):
                if part.get("is_error"):
                    errors.append(part)
                else:
                    prop_name = part.get("property_name", "")
                    if prop_name and prop_name not in seen:
                        seen.add(prop_name)
                        results.append(part)
        results.sort(key=lambda r: r.get("property_name", ""))
        return errors + results


__all__ = ["MetadataFieldsListOperation"]
