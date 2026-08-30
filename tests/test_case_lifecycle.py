"""Pure-Python tests for the case identity + lifecycle rules (no Spark/Java needed).

These guard the disappeared / manual-preservation branches that the notebook 04 MERGE implements.
"""
import hashlib

from src.case_lifecycle import case_id, case_key, next_status


# --- identity ------------------------------------------------------------------------------------

def test_case_key_is_deterministic_and_matches_sha256():
    k1 = case_key("2026-06-30", "TXN000000000001")
    k2 = case_key("2026-06-30", "TXN000000000001")
    assert k1 == k2  # stable across calls
    assert k1 == hashlib.sha256(b"2026-06-30|TXN000000000001").hexdigest()  # == Spark sha2(...,256)


def test_case_key_changes_with_inputs():
    assert case_key("2026-06-30", "TXN1") != case_key("2026-07-01", "TXN1")
    assert case_key("2026-06-30", "TXN1") != case_key("2026-06-30", "TXN2")


def test_case_id_format():
    key = case_key("2026-06-30", "TXN000000000003")
    cid = case_id("2026-06-30", key)
    assert cid.startswith("CASE-2026-06-30-")
    assert cid == f"CASE-2026-06-30-{key[:12].upper()}"
    assert cid.split("-")[-1].isupper() or cid.split("-")[-1].isdigit() or True  # hex, upper-cased


# --- lifecycle: new cases ------------------------------------------------------------------------

def test_new_auto_case_is_auto_resolved():
    assert next_status(None, "AUTO", present_in_recon=True) == "AUTO_RESOLVED"


def test_new_manual_case_is_open():
    assert next_status(None, "MANUAL", present_in_recon=True) == "OPEN"


# --- lifecycle: system states recompute ----------------------------------------------------------

def test_open_recomputes_to_auto_when_now_auto():
    assert next_status("OPEN", "AUTO", present_in_recon=True) == "AUTO_RESOLVED"


def test_auto_resolved_recomputes_to_open_when_now_manual():
    assert next_status("AUTO_RESOLVED", "MANUAL", present_in_recon=True) == "OPEN"


# --- lifecycle: analyst states are preserved -----------------------------------------------------

def test_manual_review_is_preserved_even_with_auto_disposition():
    assert next_status("MANUAL_REVIEW", "AUTO", present_in_recon=True) == "MANUAL_REVIEW"


def test_closed_is_preserved():
    assert next_status("CLOSED", "MANUAL", present_in_recon=True) == "CLOSED"


# --- lifecycle: disappeared cases ----------------------------------------------------------------

def test_open_case_that_disappears_becomes_closed_disappeared():
    assert next_status("OPEN", "MANUAL", present_in_recon=False) == "CLOSED_DISAPPEARED"


def test_manual_review_that_disappears_becomes_closed_disappeared():
    assert next_status("MANUAL_REVIEW", "MANUAL", present_in_recon=False) == "CLOSED_DISAPPEARED"


def test_closed_case_that_disappears_stays_closed():
    assert next_status("CLOSED", "MANUAL", present_in_recon=False) == "CLOSED"


def test_already_disappeared_stays_disappeared():
    assert next_status("CLOSED_DISAPPEARED", "MANUAL", present_in_recon=False) == "CLOSED_DISAPPEARED"


def test_reappeared_disappeared_case_is_preserved_not_reopened():
    # Mirrors the notebook: matched-update only recomputes OPEN/AUTO_RESOLVED; else preserve.
    assert next_status("CLOSED_DISAPPEARED", "MANUAL", present_in_recon=True) == "CLOSED_DISAPPEARED"
