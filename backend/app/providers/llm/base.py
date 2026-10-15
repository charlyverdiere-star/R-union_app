"""Interface commune aux modèles de langage utilisés pour l'analyse."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LlmError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens


@dataclass
class LlmResult:
    parsed: BaseModel
    usage: Usage = field(default_factory=Usage)


class LlmProvider(ABC):
    name: str = "abstrait"
    model: str = ""

    @abstractmethod
    def structured(
        self,
        *,
        instructions: str,
        request: str,
        schema: type[T],
        cached_context: str | None = None,
        max_tokens: int = 16000,
    ) -> LlmResult:
        """Retourne une réponse validée contre `schema`."""

    def describe(self) -> str:
        return f"{self.name}/{self.model}" if self.model else self.name
