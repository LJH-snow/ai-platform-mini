from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestLifespanStartup:
    def test_app_starts_and_health_responds(self) -> None:
        from app.main import app

        with TestClient(app) as client:
            response = client.get("/api/v1/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

    def test_memory_mode_starts_without_postgres(self) -> None:
        from app.main import app

        with TestClient(app) as client:
            response = client.get("/api/v1/health")
            assert response.status_code == 200


class TestLifespanErrorHandling:
    def test_provider_closed_when_startup_fails(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.close = AsyncMock()
        with (
            patch(
                "app.main.provide_llm_provider",
                return_value=mock_provider,
            ),
            patch(
                "app.main._bootstrap_keys",
                side_effect=RuntimeError("bootstrap failure"),
            ),
        ):
            from app.main import app

            with pytest.raises(RuntimeError, match="bootstrap failure"):
                with TestClient(app):
                    pass

        mock_provider.close.assert_awaited_once()

    def test_db_init_failure_does_not_call_dispose(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.close = AsyncMock()
        with (
            patch(
                "app.main.provide_llm_provider",
                return_value=mock_provider,
            ),
            patch(
                "app.main.get_settings",
                return_value=MagicMock(
                    auth_storage="postgres",
                    database_url=MagicMock(
                        get_secret_value=MagicMock(
                            return_value="postgresql://localhost/test"
                        )
                    ),
                    debug=False,
                ),
            ),
            patch(
                "app.db.init.init_db",
                side_effect=RuntimeError("connection refused"),
            ),
            patch("app.db.init.dispose_db") as mock_dispose,
        ):
            from app.main import app

            with pytest.raises(RuntimeError, match="connection refused"):
                with TestClient(app):
                    pass

        mock_dispose.assert_not_called()
        mock_provider.close.assert_awaited_once()
