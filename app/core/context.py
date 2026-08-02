from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    api_key: str | None = None
    api_key_name: str | None = None

    def with_auth(self, api_key: str, api_key_name: str) -> "RequestContext":
        return RequestContext(
            request_id=self.request_id,
            api_key=api_key,
            api_key_name=api_key_name,
        )
