"""Model catalog: connectivity reporting and cost.

`list_all_models` is called on page load and iterates every provider in the
catalog. Two things matter: it must not do the same work twice per provider,
and it must not report a provider as disconnected just because that provider
happened to be unreachable when the list was built.
"""
from __future__ import annotations

import pytest

# backend.models -> backend.db -> backend.tools.registry -> backend.db is a
# cycle; importing backend.db first breaks it. conftest imports the app, so
# touch it before reaching for anything under ai_support.
import backend.db  # noqa: F401
from backend.ai_support import catalog


def test_connected_flag_is_explicit_not_inferred():
    """`note` is also set when a connected provider is unreachable.

    Inferring `connected` from the absence of `note` reported a working
    provider as disconnected whenever its model list failed to load, which
    made the picker look empty for a provider the user had configured.
    """
    import inspect

    src = inspect.getsource(catalog.list_provider_models)
    assert '"connected": auth is not None' in src


def test_provider_models_marks_unconnected_provider(client):
    import asyncio

    async def run():
        return await catalog.list_provider_models("definitely-not-a-provider")

    with pytest.raises(KeyError):
        asyncio.run(run())


@pytest.mark.parametrize("provider_id", ["groq", "openrouter", "openai"])
def test_list_provider_models_reports_a_boolean_connected(provider_id, client, monkeypatch):
    import asyncio

    async def no_auth(_provider_id):
        return None

    # `connected` is derived from resolve_runtime_auth. The old test called the
    # real resolver and assumed the developer's shell had no provider keys, so
    # an ambient OPENAI_API_KEY made it assert True is False. Supply the auth
    # result explicitly: the no-key case stays the same on every machine.
    monkeypatch.setattr(catalog, "resolve_runtime_auth", no_auth)
    row = asyncio.run(catalog.list_provider_models(provider_id))
    assert isinstance(row.get("connected"), bool)
    # No key, so it must be explicitly False rather than missing - callers
    # read this key directly.
    assert row["connected"] is False


def test_connected_flag_is_independent_of_ambient_keys(monkeypatch):
    """Regression for the baseline failure: a shell key must not flip this test.

    The old version read the real resolver, so exporting OPENAI_API_KEY in the
    developer's shell made `connected` True and turned a green suite red. The
    test now controls the resolver itself, so the result cannot depend on the
    environment it runs in.
    """
    import asyncio

    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key-must-not-leak")

    async def no_auth(_provider_id):
        return None

    monkeypatch.setattr(catalog, "resolve_runtime_auth", no_auth)
    row = asyncio.run(catalog.list_provider_models("openai"))
    assert row["connected"] is False


def test_list_all_models_does_not_resolve_auth_twice(monkeypatch, client):
    """The duplicate resolve doubled SQLite reads per provider per load."""
    import asyncio

    calls: list[str] = []
    real = catalog.resolve_runtime_auth

    async def counting(provider_id: str):
        calls.append(provider_id)
        return await real(provider_id)

    monkeypatch.setattr(catalog, "resolve_runtime_auth", counting)
    asyncio.run(catalog.list_all_models())

    from backend.ai_support.providers import providers_by_priority

    expected = len(providers_by_priority())
    assert len(calls) == expected, (
        f"resolve_runtime_auth called {len(calls)}x for {expected} providers; "
        "each provider should resolve auth exactly once"
    )
    assert len(calls) == len(set(calls)), "a provider resolved auth more than once"


def test_live_model_errors_redact_preview_credentials(monkeypatch):
    import asyncio

    async def fail_with_echoed_key(auth):
        raise RuntimeError(f"provider rejected credential {auth.api_key}")

    monkeypatch.setattr(catalog, "_fetch_openai_compatible", fail_with_echoed_key)

    result = asyncio.run(catalog.list_provider_models("groq", api_key="secret-preview-key"))

    assert result["connected"] is True
    assert result["live"] is False
    assert result["error"] == "provider rejected credential <REDACTED>"
    assert "secret-preview-key" not in repr(result)


def test_pinned_model_provider_lookup_is_cached(monkeypatch):
    """Routing a pinned model must not re-fetch every provider each turn."""
    import asyncio

    calls: list[int] = []

    async def fake_all_models():
        calls.append(1)
        return {"models": [{"id": "vendor/model:free", "provider_id": "openrouter"}]}

    monkeypatch.setattr(catalog, "list_all_models", fake_all_models)
    catalog.clear_model_index_cache()
    assert asyncio.run(catalog.find_model_provider("vendor/model:free")) == "openrouter"
    assert asyncio.run(catalog.find_model_provider("vendor/model:free")) == "openrouter"
    assert asyncio.run(catalog.find_model_provider("no-such-model")) is None
    assert len(calls) == 1