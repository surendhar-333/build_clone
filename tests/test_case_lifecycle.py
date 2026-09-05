import hashlib
from src.case_lifecycle import case_key, case_id, next_status


def test_case_key():
    bd = "2023-10-01"
    txn = "TXN-12345"
    expected_str = "2023-10-01|TXN-12345"
    expected_hash = hashlib.sha256(expected_str.encode("utf-8")).hexdigest()

    assert case_key(bd, txn) == expected_hash
    # Test determinism
    assert case_key(bd, txn) == case_key(bd, txn)


def test_case_id():
    key = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
    expected_id = "CASE-A1B2C3D4E5F6"
    assert case_id(key) == expected_id
    # Test optional legacy signature
    assert case_id("2023-10-01", key) == expected_id


def test_next_status_new_case():
    assert next_status(None, "AUTO") == "AUTO_RESOLVED"
    assert next_status("", "AUTO") == "AUTO_RESOLVED"
    assert next_status(None, "MANUAL") == "OPEN"
    assert next_status("", "MANUAL") == "OPEN"
    assert next_status(None, "SOMETHING_ELSE") == "OPEN"


def test_next_status_preservation_when_present():
    # Analyst states preserved
    assert next_status("MANUAL_REVIEW", "AUTO") == "MANUAL_REVIEW"
    assert next_status("MANUAL_REVIEW", "MANUAL") == "MANUAL_REVIEW"
    assert next_status("CLOSED", "AUTO") == "CLOSED"
    assert next_status("CLOSED", "MANUAL") == "CLOSED"


def test_next_status_system_state_recomputation():
    # System states recomputed based on disposition when present_in_latest=True
    assert next_status("OPEN", "AUTO") == "AUTO_RESOLVED"
    assert next_status("OPEN", "MANUAL") == "OPEN"
    assert next_status("AUTO_RESOLVED", "AUTO") == "AUTO_RESOLVED"
    assert next_status("AUTO_RESOLVED", "MANUAL") == "OPEN"


def test_next_status_disappearance():
    # Cases missing from latest recon
    # Preserves CLOSED variants
    assert next_status("CLOSED", "AUTO", present_in_latest=False) == "CLOSED"
    assert next_status("CLOSED", "MANUAL", present_in_latest=False) == "CLOSED"
    assert (
        next_status("CLOSED_DISAPPEARED", "AUTO", present_in_latest=False) == "CLOSED_DISAPPEARED"
    )

    # Transitions others to CLOSED_DISAPPEARED
    assert next_status(None, "AUTO", present_in_latest=False) == "CLOSED_DISAPPEARED"
    assert next_status("OPEN", "AUTO", present_in_latest=False) == "CLOSED_DISAPPEARED"
    assert next_status("OPEN", "MANUAL", present_in_latest=False) == "CLOSED_DISAPPEARED"
    assert next_status("AUTO_RESOLVED", "MANUAL", present_in_latest=False) == "CLOSED_DISAPPEARED"
    assert next_status("MANUAL_REVIEW", "MANUAL", present_in_latest=False) == "CLOSED_DISAPPEARED"
