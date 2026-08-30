import pytest
from pydantic import ValidationError

from by_qa.qa.common.operation_registry import (
    OPERATION_REGISTRY,
    DslGuideInput,
    GlobInput,
    ListDirInput,
    ListDirItem,
    OperationType,
    ReadFileInput,
    SearchInput,
)


def test_operation_type_values():
    assert OperationType.KNOWLEDGE_SEARCH.value == "knowledgeSearch"
    assert OperationType.LIST_DIR.value == "listDir"
    assert OperationType.GLOB.value == "glob"
    assert OperationType.READ_FILE.value == "readFile"
    assert OperationType.DSL_GUIDE.value == "dslGuide"


def test_registry_has_all_operation_types():
    assert set(OPERATION_REGISTRY.keys()) == set(OperationType)


def test_registry_tool_names():
    assert (
        OPERATION_REGISTRY[OperationType.KNOWLEDGE_SEARCH].tool_name
        == "search_knowledge"
    )
    assert OPERATION_REGISTRY[OperationType.LIST_DIR].tool_name == "list_directory"
    assert OPERATION_REGISTRY[OperationType.GLOB].tool_name == "glob_search"
    assert OPERATION_REGISTRY[OperationType.READ_FILE].tool_name == "read_file"
    assert OPERATION_REGISTRY[OperationType.DSL_GUIDE].tool_name == "get_dsl_guide"


def test_search_input_accepts_camel_alias():
    obj = SearchInput.model_validate({"query": "q", "knCodeList": ["kb1"]})
    assert obj.kn_code_list == ["kb1"]


def test_list_dir_input_accepts_camel_alias():
    obj = ListDirInput.model_validate(
        {
            "knCode": "kb1",
            "directoryPath": "/src",
            "metadataFieldList": ["owner", "updatedAt"],
            "pageNum": 2,
            "pageSize": 10,
        }
    )
    assert obj.kn_code == "kb1"
    assert obj.directory_path == "/src"
    assert obj.metadata_field_list == ["owner", "updatedAt"]
    assert obj.model_dump(by_alias=True, exclude_none=True) == {
        "knCode": "kb1",
        "directoryPath": "/src",
        "metadataFieldList": ["owner", "updatedAt"],
        "pageNum": 2,
        "pageSize": 10,
    }


def test_glob_input_accepts_camel_alias():
    obj = GlobInput.model_validate(
        {
            "knCode": "kb1",
            "pathRule": "**/*.py",
            "metadataFieldList": ["owner"],
        }
    )
    assert obj.path_rule == "**/*.py"
    assert obj.metadata_field_list == ["owner"]


def test_browse_inputs_default_to_no_metadata_and_no_pagination():
    list_input = ListDirInput.model_validate({"knCode": "kb1", "directoryPath": "/src"})
    glob_input = GlobInput.model_validate({"knCode": "kb1", "pathRule": "/*.md"})
    assert list_input.metadata_field_list is None
    assert glob_input.metadata_field_list is None
    assert list_input.page_num is None
    assert glob_input.page_size is None


def test_browse_inputs_reject_page_num_without_page_size():
    with pytest.raises(ValidationError, match="pageNum requires pageSize"):
        ListDirInput.model_validate(
            {"knCode": "kb1", "directoryPath": "/src", "pageNum": 2}
        )


def test_list_dir_item_preserves_browse_metadata_fields():
    item = ListDirItem.model_validate(
        {
            "knCode": "kb1",
            "name": "/src/a.md",
            "type": "file",
            "size": 12,
            "updatedAt": "2026-08-30T10:00:00+08:00",
            "buildStatus": "complete",
            "buildCurrentStep": "complete",
            "metadata": {"owner": {"valueType": "string", "value": "Alice"}},
        }
    )
    assert item.model_dump(by_alias=True) == {
        "knCode": "kb1",
        "name": "/src/a.md",
        "type": "file",
        "size": 12,
        "updatedAt": "2026-08-30T10:00:00+08:00",
        "buildStatus": "complete",
        "buildCurrentStep": "complete",
        "metadata": {"owner": {"valueType": "string", "value": "Alice"}},
    }


def test_read_file_input_accepts_camel_alias():
    obj = ReadFileInput.model_validate(
        {"knCode": "kb1", "filePath": "/src/main.py", "startLine": 1, "endLine": 10}
    )
    assert obj.file_path == "/src/main.py"
    assert obj.start_line == 1
    assert obj.end_line == 10


def test_read_file_input_optional_lines():
    obj = ReadFileInput.model_validate({"knCode": "kb1", "filePath": "/src/main.py"})
    assert obj.start_line is None
    assert obj.end_line is None


def test_search_input_optional_kn_code_list():
    obj = SearchInput.model_validate({"query": "q"})
    assert obj.kn_code_list is None


def test_search_input_kn_code_list_json_string():
    obj = SearchInput.model_validate({"query": "q", "knCodeList": '["kb1", "kb2"]'})
    assert obj.kn_code_list == ["kb1", "kb2"]


def test_search_input_kn_code_list_bare_string():
    obj = SearchInput.model_validate({"query": "q", "knCodeList": "kb1"})
    assert obj.kn_code_list == ["kb1"]


def test_dsl_guide_input_basic_construction():
    inp = DslGuideInput.model_validate({})
    assert inp.model_dump() == {}


def test_search_input_hides_where_and_accepts_metadata_field_list():
    inp = SearchInput.model_validate(
        {
            "query": "test",
            "where": {"eq": {"fieldName": "status", "value": "active"}},
            "metadataFieldList": ["status", "tags"],
        }
    )
    assert inp.query == "test"
    assert "where" not in SearchInput.model_fields
    assert "where" not in inp.model_dump()
    assert inp.metadata_field_list == ["status", "tags"]


def test_search_input_metadata_field_list_defaults_to_none():
    inp = SearchInput.model_validate({"query": "test"})
    assert inp.metadata_field_list is None
