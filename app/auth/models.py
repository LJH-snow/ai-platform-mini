from dataclasses import dataclass


@dataclass(frozen=True)
class APIKey:
    key: str
    name: str
