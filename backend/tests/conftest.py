import pytest

from app.api.routes import registry


@pytest.fixture(autouse=True)
def isolated_registry_state(monkeypatch, tmp_path):
    """Keep tests from writing the durable registry into the working tree."""
    monkeypatch.setattr(
        registry.settings, "registry_database_path", str(tmp_path / "registry.sqlite3")
    )
    monkeypatch.setattr(registry.settings, "registry_object_directory", str(tmp_path / "objects"))
    registry.get_registry_store.cache_clear()
    yield
    registry.get_registry_store.cache_clear()
