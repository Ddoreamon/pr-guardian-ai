"""In-memory review store with human-approval state.

Deliberately simple: this is a demo service, not a database-backed product.
Reviews are capped so a long-running public instance cannot grow without bound.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from pr_guardian.models import ReviewReport

MAX_STORED_REVIEWS = 100
DECISIONS = {"pending", "accepted", "rejected"}


class ReviewNotFound(KeyError):
    pass


class InvalidDecision(ValueError):
    pass


@dataclass
class StoredReview:
    report: ReviewReport
    approvals: dict[str, str] = field(default_factory=dict)
    case_id: str = ""
    case_title: str = ""

    def approval_of(self, finding_id: str) -> str:
        return self.approvals.get(finding_id, "pending")


class ReviewStore:
    def __init__(self, max_reviews: int = MAX_STORED_REVIEWS) -> None:
        self._lock = threading.Lock()
        self._reviews: OrderedDict[str, StoredReview] = OrderedDict()
        self._max = max_reviews
        self._live_llm_calls = 0

    # ---- reviews ---------------------------------------------------------
    def add(self, review: StoredReview) -> None:
        with self._lock:
            self._reviews[review.report.review_id] = review
            while len(self._reviews) > self._max:
                self._reviews.popitem(last=False)

    def get(self, review_id: str) -> StoredReview:
        with self._lock:
            try:
                return self._reviews[review_id]
            except KeyError as exc:
                raise ReviewNotFound(review_id) from exc

    def set_decision(self, review_id: str, finding_id: str, decision: str) -> str:
        if decision not in DECISIONS:
            raise InvalidDecision(decision)
        with self._lock:
            review = self._reviews.get(review_id)
            if review is None:
                raise ReviewNotFound(review_id)
            known = {f.id for f in review.report.final_findings}
            if finding_id not in known:
                raise ReviewNotFound(finding_id)
            review.approvals[finding_id] = decision
            return decision

    # ---- live LLM budget -------------------------------------------------
    def try_consume_llm_budget(self, limit: int) -> bool:
        """Reserve one live model call. False when the instance budget is spent."""
        with self._lock:
            if self._live_llm_calls >= limit:
                return False
            self._live_llm_calls += 1
            return True

    def llm_calls_used(self) -> int:
        with self._lock:
            return self._live_llm_calls


store = ReviewStore()
# Guards the review pipeline so concurrent requests cannot race on git worktrees.
review_lock = threading.Lock()
