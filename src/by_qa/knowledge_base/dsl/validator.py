"""Validate Agent DSL where clauses for structural and semantic correctness."""

from __future__ import annotations

from typing import Any

from by_qa.knowledge_base.dsl.errors import DslValidationDetail, DslValidationError

BOOLEAN_OPERATORS = {"and", "or", "not"}
LEAF_OPERATORS = {"eq", "ne", "in", "contains", "exists", "gt", "gte", "lt", "lte"}
MAX_DEPTH = 3
MAX_LEAF_COUNT = 12


def validate_where_clause(
    where: dict[str, Any] | None,
    *,
    known_fields: dict[str, str],
) -> None:
    if where is None:
        return
    errors: list[DslValidationDetail] = []
    leaf_count = [0]
    _validate_node(where, known_fields, errors, leaf_count, path="where", depth=0)
    if errors:
        raise DslValidationError(error_list=errors)


def _validate_node(
    node: Any,
    known_fields: dict[str, str],
    errors: list[DslValidationDetail],
    leaf_count: list[int],
    *,
    path: str,
    depth: int,
) -> None:
    if not isinstance(node, dict) or len(node) != 1:
        errors.append(
            DslValidationDetail(
                path=path,
                code="INVALID_BOOLEAN_NODE",
                message="each node must be an object with exactly one operator key",
            )
        )
        return

    operator = next(iter(node))

    if operator in BOOLEAN_OPERATORS:
        if depth >= MAX_DEPTH:
            errors.append(
                DslValidationDetail(
                    path=path,
                    code="TOO_DEEP_BOOLEAN_NESTING",
                    message=f"boolean nesting depth exceeds limit {MAX_DEPTH}",
                )
            )
            return

        operand = node[operator]
        if operator == "not":
            if not isinstance(operand, dict):
                errors.append(
                    DslValidationDetail(
                        path=f"{path}.not",
                        code="INVALID_BOOLEAN_NODE",
                        message="'not' must wrap a single clause object",
                    )
                )
                return
            _validate_node(
                operand,
                known_fields,
                errors,
                leaf_count,
                path=f"{path}.not",
                depth=depth + 1,
            )
        else:
            if not isinstance(operand, list) or len(operand) == 0:
                errors.append(
                    DslValidationDetail(
                        path=f"{path}.{operator}",
                        code="INVALID_BOOLEAN_NODE",
                        message=f"'{operator}' must be a non-empty array",
                    )
                )
                return
            for i, child in enumerate(operand):
                _validate_node(
                    child,
                    known_fields,
                    errors,
                    leaf_count,
                    path=f"{path}.{operator}[{i}]",
                    depth=depth + 1,
                )

    elif operator in LEAF_OPERATORS:
        leaf_count[0] += 1
        if leaf_count[0] > MAX_LEAF_COUNT:
            errors.append(
                DslValidationDetail(
                    path=path,
                    code="TOO_MANY_CONDITIONS",
                    message=f"leaf condition count exceeds limit {MAX_LEAF_COUNT}",
                )
            )
            return
        _validate_leaf(node, operator, known_fields, errors, path=path)

    else:
        allowed = ", ".join(sorted(LEAF_OPERATORS | BOOLEAN_OPERATORS))
        errors.append(
            DslValidationDetail(
                path=path,
                code="UNSUPPORTED_OPERATOR",
                message=(
                    f"operator '{operator}' is not supported; "
                    f"allowed operators: {allowed}"
                ),
            )
        )


def _validate_leaf(
    node: dict[str, Any],
    operator: str,
    known_fields: dict[str, str],
    errors: list[DslValidationDetail],
    *,
    path: str,
) -> None:
    body = node[operator]
    if not isinstance(body, dict):
        errors.append(
            DslValidationDetail(
                path=f"{path}.{operator}",
                code="INVALID_BOOLEAN_NODE",
                message=f"'{operator}' body must be an object",
            )
        )
        return

    field_name = body.get("fieldName")
    if not field_name:
        errors.append(
            DslValidationDetail(
                path=f"{path}.{operator}.fieldName",
                code="UNKNOWN_FIELD",
                message="fieldName is required",
            )
        )
        return

    if field_name not in known_fields:
        errors.append(
            DslValidationDetail(
                path=f"{path}.{operator}.fieldName",
                code="UNKNOWN_FIELD",
                message=f"fieldName '{field_name}' is not defined",
            )
        )
