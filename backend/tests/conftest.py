"""Test isolation for state that lives outside the process.

`api` binds `_session_store` to `settings.session_db_path` at import time, and
that path is the real `.runtime/wildfire-sessions.sqlite3`. Any test that
exercises a session endpoint therefore wrote to - and in one case emptied - the
database a running server was serving. The suite was green while doing it.

This redirects the store to a per-run temporary file before any test touches it.
It is autouse and session-scoped because the damage happens on the first call,
not on the first assertion.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_session_store(tmp_path_factory: pytest.TempPathFactory):
    from wildfire_agent import api
    from wildfire_agent.session_store import SessionStore

    store = SessionStore(tmp_path_factory.mktemp("sessions") / "sessions.sqlite3")
    api._session_store = store
    api._sessions = store.ids()
    yield
