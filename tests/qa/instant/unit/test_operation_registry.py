from by_qa.qa.instant.runtime.operation_registry import (
    OPERATION_REGISTRY,
    GlobInput,
    ListDirInput,
    OperationType,
    ReadFileInput,
    SearchInput,
)


def test_operation_type_values():
    assert OperationType.SEARCH.value == "search"
    assert OperationType.LIST_DIR.value == "listDir"
    assert OperationType.GLOB.value == "glob"
    assert OperationType.READ_FILE.value == "readFile"


def test_registry_has_all_operation_types():
    assert set(OPERATION_REGISTRY.keys()) == set(OperationType)


def test_registry_tool_names():
    assert OPERATION_REGISTRY[OperationType.SEARCH].tool_name == "search_knowledge"
    assert OPERATION_REGISTRY[OperationType.LIST_DIR].tool_name == "list_directory"
    assert OPERATION_REGISTRY[OperationType.GLOB].tool_name == "glob_search"
    assert OPERATION_REGISTRY[OperationType.READ_FILE].tool_name == "read_file"


def test_search_input_accepts_camel_alias():
    obj = SearchInput.model_validate({"query": "q", "knCodeList": ["kb1"]})
    assert obj.kn_code_list == ["kb1"]


def test_list_dir_input_accepts_camel_alias():
    obj = ListDirInput.model_validate({"knCode": "kb1", "directoryPath": "/src"})
    assert obj.kn_code == "kb1"
    assert obj.directory_path == "/src"


def test_glob_input_accepts_camel_alias():
    obj = GlobInput.model_validate({"knCode": "kb1", "pathRule": "**/*.py"})
    assert obj.path_rule == "**/*.py"


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
