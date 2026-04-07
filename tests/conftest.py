"""
Pytest configuration for the Supplier Trust Engine test suite.

pytest_configure runs before any test module is imported, so env vars
set here are visible to module-level code in test files (e.g. the
os.environ assignments in test_admin_api.py that must precede the
`from api.main import app` import).

Using setdefault so that values already injected by a CI workflow or
a local .env are not overwritten.
"""
import os


def pytest_configure(config):
    # Prevent auth.py from raising ValueError at import time
    os.environ.setdefault("ADMIN_TOKEN", "ci-test-admin-token")

    # Force every test that triggers init_db() to use an in-memory DB
    # unless the caller explicitly overrides (e.g. integration tests)
    os.environ.setdefault("DB_PATH", ":memory:")
