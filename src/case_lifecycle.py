"""Pure-Python reference for exception-case identity and lifecycle rules.

Notebook ``04_phase4_gold_reconciliation.py`` implements the SAME rules in Spark/Delta:

- ``case_key`` = ``F.sha2(concat_ws('|', business_date, txn_id), 256)`` — byte-identical to
  ``hashlib.sha256(f"{business_date}|{txn_id}").hexdigest()`` here.
- ``case_id`` = ``'CASE-' + business_date + '-' + upper(case_key[:12])``.
- the MERGE status-transition ``CASE`` mirrors :func:`next_status`.

Keeping the rules here makes them unit-testable with plain pytest (no Spark/Delta/Java), and documents
the contract the notebook MERGE must uphold.
"""
import hashlib

LIFECYCLE_STATES = {
    "OPEN",
    "AUTO_RESOLVED",
    "MANUAL_REVIEW",
    "CLOSED",
    "CLOSED_DISAPPEARED",
}

# States the system recomputes each run; anything else is analyst-managed and preserved.
_SYSTEM_STATES = {"OPEN", "AUTO_RESOLVED"}


def case_key(business_date: str, txn_id: str) -> str:
    """Stable exception identity — matches Spark ``sha2(concat_ws('|', business_date, txn_id), 256)``."""
    return hashlib.sha256(f"{business_date}|{txn_id}".encode("utf-8")).hexdigest()


def case_id(business_date: str, key: str) -> str:
    """Human-readable stable id — matches the notebook's ``CASE-<date>-<upper(first 12 hex)>``."""
    return f"CASE-{business_date}-{key[:12].upper()}"


def next_status(current_status, disposition: str, present_in_recon: bool = True) -> str:
    """Lifecycle status a case should hold after an upsert (mirrors the notebook MERGE).

    - Not present in the latest recon -> ``CLOSED_DISAPPEARED`` unless already CLOSED/CLOSED_DISAPPEARED
      (the case row is kept, never deleted).
    - New case (``current_status`` is None) -> system state from the disposition.
    - Currently a system state (OPEN/AUTO_RESOLVED) -> recompute from the disposition.
    - Analyst-managed states (MANUAL_REVIEW, CLOSED) and CLOSED_DISAPPEARED are preserved.
    """
    system_target = "AUTO_RESOLVED" if disposition == "AUTO" else "OPEN"

    if not present_in_recon:
        if current_status in ("CLOSED", "CLOSED_DISAPPEARED"):
            return current_status
        return "CLOSED_DISAPPEARED"

    if current_status is None or current_status in _SYSTEM_STATES:
        return system_target

    return current_status
