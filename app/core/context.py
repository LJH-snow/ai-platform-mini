from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.auth.identity import IdentityContext


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    api_key: str | None = None
    api_key_name: str | None = None
    identity: IdentityContext | None = None

    def with_auth(self, api_key: str, api_key_name: str) -> RequestContext:
        return RequestContext(
            request_id=self.request_id,
            api_key=api_key,
            api_key_name=api_key_name,
            identity=self.identity,
        )

    def with_identity(self, identity: IdentityContext) -> RequestContext:
        return RequestContext(
            request_id=self.request_id,
            api_key=self.api_key,
            api_key_name=self.api_key_name,
            identity=identity,
        )
