import asyncio
import logging
from collections.abc import Awaitable
from types import TracebackType
from typing import NoReturn, Self, TypeVar, cast

from app.exceptions.base import QuotaReservationError
from app.quota.models import QuotaReservation
from app.quota.service import QuotaService

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ReservationLifecycle:
    """Keeps a quota reservation active until it is settled or released."""

    def __init__(
        self,
        reservation: QuotaReservation | None,
        quota_service: QuotaService | None,
    ) -> None:
        self._reservation = reservation
        self._quota_service = quota_service
        self._renewal_failure: asyncio.Future[None] | None = None
        self._renewal_task: asyncio.Task[None] | None = None
        self._finalized = False

    async def __aenter__(self) -> Self:
        if self._reservation is None or self._quota_service is None:
            return self

        self._renewal_failure = asyncio.get_running_loop().create_future()
        self._renewal_task = asyncio.create_task(self._renew_reservation())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._stop_renewal()
        if not self._finalized:
            await self.release()

    async def run(
        self, operation: Awaitable[T], *, return_quota_failure_result: bool = False
    ) -> T:
        """Await an operation unless the reservation renewal fails first."""
        if self._renewal_failure is None:
            return await operation

        operation_task = asyncio.ensure_future(operation)
        waitables: set[asyncio.Future[object]] = {
            cast(asyncio.Future[object], operation_task),
            cast(asyncio.Future[object], self._renewal_failure),
        }
        try:
            done, _ = await asyncio.wait(
                waitables,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            await self._cancel_operation(operation_task)
            raise
        if self._renewal_failure in done:
            renewal_failure = self._renewal_failure.exception()
            if renewal_failure is None:
                raise RuntimeError(
                    "Quota renewal failure signal completed without an error."
                )
            await self._cancel_operation(operation_task, cancel_message=renewal_failure)
            if return_quota_failure_result and not operation_task.cancelled():
                return await operation_task
            raise renewal_failure
        return await operation_task

    async def settle(self) -> None:
        await self._stop_renewal()
        if self._finalized or self._reservation is None or self._quota_service is None:
            return
        await self._quota_service.settle(self._reservation.reservation_id)
        self._finalized = True

    async def release(self) -> None:
        await self._stop_renewal()
        if self._finalized or self._reservation is None or self._quota_service is None:
            return
        await self._quota_service.release(self._reservation.reservation_id)
        self._finalized = True

    async def _renew_reservation(self) -> None:
        assert self._quota_service is not None
        try:
            while True:
                await asyncio.sleep(self._quota_service.reservation_renewal_seconds)
                renewed = await self._quota_service.renew(self._reservation_id())
                if not renewed:
                    self._set_renewal_failure(
                        QuotaReservationError("Quota reservation could not be renewed.")
                    )
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "quota_reservation_renewal_failed reservation_id=%s",
                self._reservation_id(),
            )
            self._set_renewal_failure(
                QuotaReservationError("Quota reservation renewal failed.")
            )

    def _set_renewal_failure(self, error: QuotaReservationError) -> None:
        if self._renewal_failure is not None and not self._renewal_failure.done():
            self._renewal_failure.set_exception(error)

    async def _stop_renewal(self) -> None:
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            await asyncio.gather(self._renewal_task, return_exceptions=True)
            self._renewal_task = None

        if self._renewal_failure is not None:
            renewal_failure = self._renewal_failure
            self._renewal_failure = None
            if not renewal_failure.done():
                renewal_failure.cancel()
            else:
                try:
                    renewal_failure.result()
                except QuotaReservationError:
                    pass

    def _raise_renewal_failure(self) -> NoReturn:
        assert self._renewal_failure is not None
        self._renewal_failure.result()
        raise RuntimeError("Quota renewal failure signal completed without an error.")

    @staticmethod
    async def _cancel_operation(
        operation_task: asyncio.Future[T],
        *,
        cancel_message: object | None = None,
    ) -> None:
        if not operation_task.done():
            operation_task.cancel(cancel_message)
        await asyncio.gather(operation_task, return_exceptions=True)

    def _reservation_id(self) -> str:
        assert self._reservation is not None
        return self._reservation.reservation_id
