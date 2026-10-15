"""Analyse et rédaction avec Claude.

Deux mécanismes sont utilisés systématiquement :

  - **sorties structurées** (`output_format`) : le modèle doit répondre selon
    un schema Pydantic. On ne depend donc pas d'un JSON improvise dans du
    texte libre, et une réponse non conforme est rejetee par le SDK ;
  - **mise en cache du prefixe** : les consignes et la transcription sont
    envoyees comme blocs système mis en cache. Lorsqu'on enchaîne plusieurs
    appels sur la même réunion (une rédaction par thème), seule la partie
    variable est refacturée au plein tarif.
"""

from __future__ import annotations

import anthropic

from .base import LlmError, LlmProvider, LlmResult, T, Usage


class AnthropicProvider(LlmProvider):
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-5-5",
        effort: str = "high",
        timeout: float = 900.0,
    ):
        if not api_key:
            raise LlmError("Clé API Anthropic manquante.")
        self.model = model
        self._effort = effort
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=4
        )

    def structured(
        self,
        *,
        instructions: str,
        request: str,
        schema: type[T],
        cached_context: str | None = None,
        max_tokens: int = 16000,
    ) -> LlmResult:
        system: list[dict] = [{"type": "text", "text": instructions}]
        if cached_context:
            system.append({"type": "text", "text": cached_context})
        # Le dernier bloc système porte le marqueur de cache : tout ce qui le
        # precede (consignes + transcription) constitue le prefixe réutilisé.
        system[-1]["cache_control"] = {"type": "ephemeral"}

        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                output_config={"effort": self._effort},
                output_format=schema,
                messages=[{"role": "user", "content": request}],
            )
        except anthropic.AuthenticationError as exc:
            raise LlmError("Clé API Anthropic invalide.", retryable=False) from exc
        except anthropic.PermissionDeniedError as exc:
            raise LlmError(
                "La clé API Anthropic n'a pas les droits nécessaires.", retryable=False
            ) from exc
        except anthropic.NotFoundError as exc:
            raise LlmError(
                f"Le modèle « {self.model} » est introuvable ou indisponible pour ce compte.",
                retryable=False,
            ) from exc
        except anthropic.RateLimitError as exc:
            raise LlmError(
                "Limite de débit atteinte chez Anthropic. Réessayez dans quelques minutes.",
                retryable=True,
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LlmError(
                f"Connexion à l'API Anthropic impossible : {exc}", retryable=True
            ) from exc
        except anthropic.APIStatusError as exc:
            raise LlmError(
                f"Erreur {exc.status_code} de l'API Anthropic : {exc.message}",
                retryable=exc.status_code >= 500,
            ) from exc

        if response.stop_reason == "refusal":
            raise LlmError(
                "Le modèle a refusé de traiter ce contenu. Vérifiez la nature de "
                "l'enregistrement, puis réessayez.",
                retryable=False,
            )
        if response.stop_reason == "max_tokens":
            raise LlmError(
                "La réponse du modèle a été tronquée : le passage analysé est trop "
                "volumineux. Réduisez CHUNK_TARGET_SECONDS puis relancez l'analyse.",
                retryable=False,
            )
        if response.parsed_output is None:
            raise LlmError(
                "Le modèle n'a pas renvoyé de réponse exploitable.", retryable=True
            )

        usage = Usage(
            input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        )
        return LlmResult(parsed=response.parsed_output, usage=usage)
