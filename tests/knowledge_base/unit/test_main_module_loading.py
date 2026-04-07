"""Tests for dynamic API module loading in the app entrypoint."""

from types import SimpleNamespace

from by_qa.main import _detect_missing_packages, _register_api_modules


def test_detect_missing_packages_returns_only_unavailable_modules(monkeypatch):
    """Module detection should report only packages whose specs are missing."""

    def fake_find_spec(name):
        return None if name == "missing_package" else object()

    monkeypatch.setattr("by_qa.main.find_spec", fake_find_spec)

    assert _detect_missing_packages(("fastapi", "missing_package")) == [
        "missing_package"
    ]


def test_register_api_modules_skips_modules_with_missing_packages(monkeypatch):
    """Optional API modules should be skipped when their dependencies are absent."""
    app = SimpleNamespace()
    registered = []
    warnings = []

    module_definition = SimpleNamespace(
        name="knowledge_build",
        route_module="by_qa.knowledge_build.api.routes",
        register_function="register_routes",
        required_packages=("missing_package",),
        register_kwargs_factory=lambda: {"sentinel": "value"},
    )

    monkeypatch.setattr("by_qa.main.API_MODULES", (module_definition,))
    monkeypatch.setattr(
        "by_qa.main.logger.warning",
        lambda message, *args: warnings.append(message % args if args else message),
    )
    monkeypatch.setattr(
        "by_qa.main.import_module",
        lambda _: SimpleNamespace(
            register_routes=lambda *args, **kwargs: registered.append((args, kwargs))
        ),
    )
    monkeypatch.setattr("by_qa.main.find_spec", lambda _: None)

    loaded, skipped = _register_api_modules(app)

    assert loaded == []
    assert skipped == {"knowledge_build": ["missing_package"]}
    assert registered == []
    assert warnings == [
        "api module skipped: module=knowledge_build, missing_packages=missing_package"
    ]


def test_register_api_modules_registers_available_modules(monkeypatch):
    """Optional API modules should register routes when dependencies are present."""
    app = SimpleNamespace()
    infos = []
    registered = []

    module_definition = SimpleNamespace(
        name="knowledge_build",
        route_module="by_qa.knowledge_build.api.routes",
        register_function="register_routes",
        required_packages=("fastapi",),
        register_kwargs_factory=lambda: {"sentinel": "value"},
    )

    monkeypatch.setattr("by_qa.main.API_MODULES", (module_definition,))
    monkeypatch.setattr("by_qa.main.find_spec", lambda _: object())
    monkeypatch.setattr(
        "by_qa.main.import_module",
        lambda _: SimpleNamespace(
            register_routes=lambda *args, **kwargs: registered.append((args, kwargs))
        ),
    )
    monkeypatch.setattr(
        "by_qa.main.logger.info",
        lambda message, *args: infos.append(message % args if args else message),
    )

    loaded, skipped = _register_api_modules(app)

    assert loaded == ["knowledge_build"]
    assert skipped == {}
    assert registered == [((app,), {"sentinel": "value"})]
    assert infos == [
        "api module registered: module=knowledge_build, route_module=by_qa.knowledge_build.api.routes"
    ]
