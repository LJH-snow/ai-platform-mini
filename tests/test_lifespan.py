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
                    conversation_storage="memory",
                    database_url=MagicMock(
                        get_secret_value=MagicMock(
                            return_value="postgresql+asyncpg://localhost/test"
                        )
                    ),
                    debug=False,
                    log_level="INFO",
                    log_format="json",
                    log_file=None,
                    telemetry_enabled=False,
                    telemetry_metrics_enabled=False,
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

    def test_init_db_failure_disposes_engine_internally(self) -> None:
        """When init_db's schema init fails, it should dispose the engine
        itself so the caller does not need to."""
        from app.db.init import get_engine, init_db

        async def _test() -> None:
            with patch("app.db.init.create_async_engine") as mock_create:
                mock_engine = AsyncMock()
                mock_engine.begin = MagicMock(
                    side_effect=RuntimeError("connection refused")
                )
                mock_engine.dispose = AsyncMock()
                mock_create.return_value = mock_engine

                with pytest.raises(RuntimeError, match="connection refused"):
                    await init_db(
                        "postgresql://localhost/test",
                        include_rag=False,
                    )

                # Engine should have been disposed internally
                mock_engine.dispose.assert_awaited_once()
                # Global engine should be cleared
                assert get_engine() is None

        import asyncio

        asyncio.run(_test())


class TestLifespanRAG:
    def test_rag_disabled_does_not_create_embedder(self) -> None:
        """When RAG_ENABLED=false, no embedder should be created."""
        with (
            patch(
                "app.main.get_settings",
                return_value=MagicMock(
                    auth_storage="memory",
                    conversation_storage="memory",
                    rag_enabled=False,
                    initial_api_key=MagicMock(
                        get_secret_value=MagicMock(return_value="")
                    ),
                    admin_api_keys=MagicMock(
                        get_secret_value=MagicMock(return_value="")
                    ),
                    log_level="INFO",
                    log_format="json",
                    log_file=None,
                    app_name="test",
                    debug=False,
                    telemetry_enabled=False,
                    telemetry_metrics_enabled=False,
                ),
            ),
            patch("app.main.provide_embedder") as mock_provide_embedder,
        ):
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/api/v1/health")
                assert response.status_code == 200

        mock_provide_embedder.assert_not_called()

    def test_rag_enabled_creates_embedder_and_init_db_with_rag(self) -> None:
        """When RAG_ENABLED=true, embedder is created and init_db
        is called with include_rag=True."""
        mock_provider = AsyncMock()
        mock_provider.close = AsyncMock()
        mock_embedder = AsyncMock()
        mock_embedder.close = AsyncMock()

        with (
            patch(
                "app.main.get_settings",
                return_value=MagicMock(
                    auth_storage="postgres",
                    conversation_storage="memory",
                    rag_enabled=True,
                    database_url=MagicMock(
                        get_secret_value=MagicMock(
                            return_value="postgresql+asyncpg://localhost/test"
                        )
                    ),
                    initial_api_key=MagicMock(
                        get_secret_value=MagicMock(return_value="")
                    ),
                    admin_api_keys=MagicMock(
                        get_secret_value=MagicMock(return_value="")
                    ),
                    log_level="INFO",
                    log_format="json",
                    log_file=None,
                    app_name="test",
                    debug=False,
                    telemetry_enabled=False,
                    telemetry_metrics_enabled=False,
                    rag_embedding_model="nomic-embed-text",
                    rag_embedding_dimensions=768,
                ),
            ),
            patch("app.main.provide_llm_provider", return_value=mock_provider),
            patch("app.main.provide_embedder", return_value=mock_embedder),
            patch("app.db.init.init_db", new_callable=AsyncMock) as mock_init_db,
            patch("app.db.init.dispose_db", new_callable=AsyncMock),
            patch("app.auth.dependencies.provide_api_key_service"),
            patch(
                "app.main.provide_rag_ingestion_queue",
                return_value=AsyncMock(),
            ),
        ):
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/api/v1/health")
                assert response.status_code == 200

        # init_db should be called with include_rag=True
        mock_init_db.assert_awaited_once()
        call_kwargs = mock_init_db.call_args[1]
        assert call_kwargs["include_rag"] is True
        # Embedder should be closed on shutdown
        mock_embedder.close.assert_awaited_once()

    def test_rag_enabled_closes_embedder_on_shutdown(self) -> None:
        """Embedder must be closed during lifespan shutdown."""
        mock_provider = AsyncMock()
        mock_provider.close = AsyncMock()
        mock_embedder = AsyncMock()
        mock_embedder.close = AsyncMock()

        with (
            patch(
                "app.main.get_settings",
                return_value=MagicMock(
                    auth_storage="postgres",
                    conversation_storage="memory",
                    rag_enabled=True,
                    database_url=MagicMock(
                        get_secret_value=MagicMock(
                            return_value="postgresql+asyncpg://localhost/test"
                        )
                    ),
                    initial_api_key=MagicMock(
                        get_secret_value=MagicMock(return_value="")
                    ),
                    admin_api_keys=MagicMock(
                        get_secret_value=MagicMock(return_value="")
                    ),
                    log_level="INFO",
                    log_format="json",
                    log_file=None,
                    app_name="test",
                    debug=False,
                    telemetry_enabled=False,
                    telemetry_metrics_enabled=False,
                    rag_embedding_model="nomic-embed-text",
                    rag_embedding_dimensions=768,
                ),
            ),
            patch("app.main.provide_llm_provider", return_value=mock_provider),
            patch("app.main.provide_embedder", return_value=mock_embedder),
            patch("app.db.init.init_db", new_callable=AsyncMock),
            patch("app.db.init.dispose_db", new_callable=AsyncMock),
            patch("app.auth.dependencies.provide_api_key_service"),
            patch(
                "app.main.provide_rag_ingestion_queue",
                return_value=AsyncMock(),
            ),
        ):
            from app.main import app

            with TestClient(app) as client:
                client.get("/api/v1/health")

        mock_embedder.close.assert_awaited_once()
        mock_provider.close.assert_awaited_once()

    def test_lifespan_catches_exception_group_from_provider_close(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.close = AsyncMock(
            side_effect=ExceptionGroup(
                "close failures",
                [RuntimeError("provider A failed"), RuntimeError("provider B failed")],
            )
        )
        with (
            patch(
                "app.main.provide_llm_provider",
                return_value=mock_provider,
            ),
        ):
            from app.main import app

            client = TestClient(app)
            with client:
                response = client.get("/api/v1/health")
                assert response.status_code == 200


class TestLifespanCacheIsolation:
    """Verify that consecutive lifespan iterations do not reuse
    stale cached objects after clear_container_cache()."""

    def test_second_lifespan_gets_new_provider(self) -> None:
        """After shutdown, the second lifespan must create a fresh
        LLMProvider, not reuse the closed one from the first."""
        providers: list[object] = []

        def _capture_provider() -> AsyncMock:
            p = AsyncMock()
            p.close = AsyncMock()
            providers.append(p)
            return p

        with patch("app.main.provide_llm_provider", side_effect=_capture_provider):
            # First lifespan
            from app.main import app

            with TestClient(app):
                pass
            # Second lifespan (same app object, caches should be cleared)
            with TestClient(app):
                pass

        # Two distinct provider instances created
        assert len(providers) == 2
        assert providers[0] is not providers[1]
        # Both were closed
        for p in providers:
            p.close.assert_awaited_once()  # type: ignore[attr-defined]

    def test_second_lifespan_gets_new_api_key_service(self) -> None:
        """After shutdown, the second lifespan must create a fresh
        APIKeyService, not reuse one bound to a disposed engine."""

        services: list[object] = []

        original_fn = __import__(
            "app.auth.dependencies", fromlist=["provide_api_key_service"]
        ).provide_api_key_service

        def _capturing_factory() -> object:
            svc = original_fn.__wrapped__()  # type: ignore[attr-defined]
            services.append(svc)
            return svc

        with patch(
            "app.auth.dependencies.provide_api_key_service",
            new=_capturing_factory,
        ):
            from app.main import app

            # First lifespan
            with TestClient(app):
                pass
            # Second lifespan — cache was cleared during shutdown
            with TestClient(app):
                pass

        # Two distinct APIKeyService instances
        assert len(services) == 2
        assert services[0] is not services[1]
