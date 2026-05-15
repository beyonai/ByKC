"""Validate Agent DSL where clauses for structural and semantic correctness."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from by_qa.knowledge_base.dsl.errors import DslValidationDetail, DslValidationError

BOOLEAN_OPERATORS = {"and", "or", "not"}
LEAF_OPERATORS = {"eq", "ne", "in", "contains", "exists", "gt", "gte", "lt", "lte"}
COMPARISON_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte"}
ORDER_OPERATORS = {"gt", "gte", "lt", "lte"}
ORDER_VALUE_TYPES = {"number", "datetime"}
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
        return

    value_type = known_fields[field_name]
    has_value_key = "value" in body
    value = body.get("value")

    if operator == "exists":
        if has_value_key:
            errors.append(
                DslValidationDetail(
                    path=f"{path}.exists.value",
                    code="INVALID_FIELD_VALUE_TYPE",
                    message="'exists' must not carry a value",
                )
            )
        return

    if not has_value_key:
        errors.append(
            DslValidationDetail(
                path=f"{path}.{operator}.value",
                code="INVALID_FIELD_VALUE_TYPE",
                message=f"'{operator}' requires a value",
            )
        )
        return

    if operator == "contains":
        if value_type != "stringList":
            errors.append(
                DslValidationDetail(
                    path=f"{path}.contains.fieldName",
                    code="INVALID_FIELD_VALUE_TYPE",
                    message=(
                        f"'contains' is only valid for stringList fields; "
                        f"'{field_name}' is {value_type}"
                    ),
                )
            )
            return
        if not isinstance(value, str):
            errors.append(
                DslValidationDetail(
                    path=f"{path}.contains.value",
                    code="INVALID_FIELD_VALUE_TYPE",
                    message="'contains' value must be a string",
                )
            )
        return

    if operator == "in":
        if value_type == "stringList":
            errors.append(
                DslValidationDetail(
                    path=f"{path}.in.fieldName",
                    code="INVALID_FIELD_VALUE_TYPE",
                    message=(
                        "'in' is not supported for stringList fields; use 'contains'"
                    ),
                )
            )
            return
        if not isinstance(value, list) or not value:
            errors.append(
                DslValidationDetail(
                    path=f"{path}.in.value",
                    code="INVALID_FIELD_VALUE_TYPE",
                    message="'in' value must be a non-empty array",
                )
            )
            return
        for i, item in enumerate(value):
            if not _value_matches_type(item, value_type):
                errors.append(
                    DslValidationDetail(
                        path=f"{path}.in.value[{i}]",
                        code="INVALID_FIELD_VALUE_TYPE",
                        message=(
                            f"value at index {i} does not match field type {value_type}"
                        ),
                    )
                )
                return
        return

    if operator in COMPARISON_OPERATORS:
        if value_type == "stringList":
            errors.append(
                DslValidationDetail(
                    path=f"{path}.{operator}.fieldName",
                    code="INVALID_FIELD_VALUE_TYPE",
                    message=(f"'{operator}' is not supported for stringList fields"),
                )
            )
            return
        if operator in ORDER_OPERATORS and value_type not in ORDER_VALUE_TYPES:
            errors.append(
                DslValidationDetail(
                    path=f"{path}.{operator}.fieldName",
                    code="INVALID_FIELD_VALUE_TYPE",
                    message=(
                        f"'{operator}' requires number or datetime field; "
                        f"'{field_name}' is {value_type}"
                    ),
                )
            )
            return
        if not _value_matches_type(value, value_type):
            errors.append(
                DslValidationDetail(
                    path=f"{path}.{operator}.value",
                    code="INVALID_FIELD_VALUE_TYPE",
                    message=(f"value does not match field type {value_type}"),
                )
            )
        return


def _value_matches_type(value: Any, value_type: str) -> bool:
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "number":
        # bool is a subclass of int in Python; reject it explicitly.
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "datetime":
        if not isinstance(value, str):
            return False
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        try:
            datetime.fromisoformat(normalized)
        except ValueError:
            return False
        return True
    return False
