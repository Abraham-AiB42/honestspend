"""Simple Home must not show IFPP / runway wording."""

from honestspend.services.home_simple import _plain_ifpp_warning


def test_ifpp_pool_warning_is_rewritten():
    out = _plain_ifpp_warning(
        "No checking accounts in IFPP pool — using other cash-flagged accounts."
    )
    assert out is not None
    assert "IFPP" not in out
    assert "runway" not in out.lower()


def test_runway_negative_is_rewritten():
    out = _plain_ifpp_warning("Spendable runway starts negative (-12) after buffer/tax vault — red now.")
    assert out is not None
    assert "runway" not in out.lower()
    assert "IFPP" not in out


def test_card_paid_runway_is_rewritten():
    out = _plain_ifpp_warning(
        "Skipped 2 card-paid bill(s) in cash runway (card path owns those — not double-counted against Safe to spend)."
    )
    assert out is not None
    assert "runway" not in out.lower()


def test_plain_buffer_warning_kept():
    msg = "Total cash buffer $1000.00 reserved."
    assert _plain_ifpp_warning(msg) == msg


def test_plain_alert_rewrites_runway_title():
    from honestspend.services.home_simple import _plain_alert

    out = _plain_alert(
        {
            "message": "Spendable runway starts negative (-12) after buffer/tax vault — red now.",
            "level": "critical",
            "code": "neg_check",
        }
    )
    assert "runway" not in out["title"].lower()
    assert "IFPP" not in out["title"]
