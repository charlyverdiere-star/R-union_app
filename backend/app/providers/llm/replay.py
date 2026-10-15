"""Fournisseur de rejeu — RÉSERVÉ AUX TESTS AUTOMATISÉS.

Relit des réponses enregistrées sur le disque au lieu d'appeler un modèle.
Cela permet de tester l'enchainement complet (extraction, fusion, rédaction,
vérification, génération des documents) de manière deterministe et sans coût.
Les réponses rejouees sont validees contre le même schema Pydantic que les
réponses reelles : une évolution du schema casse le test, comme il se doit.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .base import LlmError, LlmProvider, LlmResult, T, Usage


class ReplayLlmProvider(LlmProvider):
    name = "test-replay"
    model = "test-replay"

    def __init__(self, directory: Path | None):
        if not directory:
            raise LlmError("REPLAY_LLM_DIR n'est pas renseigné.")
        self._dir = Path(directory)
        self._counter: dict[str, int] = {}

    def structured(
        self,
        *,
        instructions: str,
        request: str,
        schema: type[T],
        cached_context: str | None = None,
        max_tokens: int = 16000,
    ) -> LlmResult:
        kind = schema.__name__
        index = self._counter.get(kind, 0)
        self._counter[kind] = index + 1

        candidates = [
            self._dir / f"{kind}-{index:02d}.json",
            self._dir / f"{kind}.json",
        ]
        for path in candidates:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return LlmResult(parsed=schema.model_validate(data), usage=Usage())

        digest = hashlib.sha256(request.encode()).hexdigest()[:8]
        raise LlmError(
            f"Aucune réponse de rejeu pour {kind} (index {index}, requête {digest}). "
            f"Fichiers attendus : {', '.join(p.name for p in candidates)}",
            retryable=False,
        )
