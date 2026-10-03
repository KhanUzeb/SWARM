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
def test_list_provider_models_reports_a_boolean_connected(provider_id, client):
    import asyncio

    row = asyncio.run(catalog.list_provider_models(provider_id))
    assert isinstance(row.get("connected"), bool)
    # No key in the test environment, so it must be explicitly False rather
    # than missing - callers read this key directly.
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