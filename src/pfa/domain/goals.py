from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class GoalType(StrEnum):
    EMERGENCY_FUND = "emergency_fund"
    SAVINGS_TARGET = "savings_target"
    INVESTMENT_CONTRIBUTION = "investment_contribution"
    DEBT_REDUCTION = "debt_reduction"
    DISCRETIONARY_LIMIT = "discretionary_limit"


@dataclass(frozen=True, slots=True)
class Goal:
    name: str
    goal_type: GoalType
    target_minor: int
    current_minor: int = 0
    target_date: date | None = None
