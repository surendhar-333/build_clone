import hashlib
from typing import Optional


def case_key(business_date: str, txn_id: str) -> str:
    """
    Returns the SHA256 hex digest of the string `{business_date}|{txn_id}`.
    """
    key_str = f"{business_date}|{txn_id}"
    return hashlib.sha256(key_str.encode("utf-8")).hexdigest()


def case_id(arg1: str, arg2: Optional[str] = None) -> str:
    """
    Returns the Case ID format given a case key.
    Format: 'CASE-' followed by the first 12 characters of the case key in uppercase.
    Supports either `case_id(case_key)` or `case_id(business_date, case_key)` signatures.
    """
    key = arg2 if arg2 is not None else arg1
    return f"CASE-{key[:12].upper()}"


def next_status(current: Optional[str], disposition: str, present_in_latest: bool = True) -> str:
    """
    Determine the next status of a case based on its current status, disposition,
    and whether it is present in the latest reconciliation run.
    """
    if not present_in_latest:
        if current in ("CLOSED", "CLOSED_DISAPPEARED"):
            return current
        return "CLOSED_DISAPPEARED"

    # State preservation for analyst states
    if current in ("MANUAL_REVIEW", "CLOSED"):
        return current

    # Recompute system states (or initial state)
    if disposition == "AUTO":
        return "AUTO_RESOLVED"
    return "OPEN"
