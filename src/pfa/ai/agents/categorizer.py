from __future__ import annotations

from pydantic_ai import Agent

from pfa.ai.models import local_model
from pfa.ai.schemas import TransactionClassification
from pfa.config import Settings
from pfa.ingestion.categorizer import Classification
from pfa.observability import TimedOperation


class LocalTransactionClassifier:
    """Optional model-backed classifier. Failures return no classification."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.agent = Agent(
            local_model(settings),
            output_type=TransactionClassification,
            system_prompt=(
                "Classify one personal finance transaction. Use unknown when evidence is weak. "
                "Return a concise reason. Confidence is diagnostic, not a probability."
            ),
            retries=settings.agent_retries,
        )

    def classify(self, description: str, amount_minor: int) -> Classification | None:
        try:
            with TimedOperation("classifier_inference", model=self.settings.model):
                result = self.agent.run_sync(
                    f"Description: {description}\nAmount in minor units: {amount_minor}"
                ).output
        except Exception:
            return None
        if result.kind.value == "unknown":
            return None
        return Classification(
            kind=result.kind,
            category=result.category,
            transfer_purpose=result.transfer_purpose,
            source="ai",
            confidence=result.confidence,
            reason=result.reason,
        )
