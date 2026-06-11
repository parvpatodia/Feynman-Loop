"""Verification tests: the evidence rule that makes any LLM's verdicts checkable in code.
This is the integrity layer under zero-key mode AND the accuracy check on the API judge."""

from feynman_loop.verification import evidence_supported, verified_status

_TEXT = "Backprop computes gradients of the loss via the chain rule, then the optimizer updates weights."


def test_verbatim_quote_is_supported():
    assert evidence_supported(_TEXT, "computes gradients of the loss")


def test_case_and_whitespace_do_not_matter():
    assert evidence_supported("Backprop  COMPUTES\ngradients of the loss.", "backprop computes gradients")


def test_fabricated_quote_is_rejected():
    assert not evidence_supported("the optimizer updates weights", "computes gradients via the chain rule")


def test_paraphrase_is_not_evidence():
    # same idea, different words: a quote must be the learner's words, not a restatement
    assert not evidence_supported(_TEXT, "derivatives are propagated backwards through layers")


def test_trivial_evidence_is_rejected():
    assert not evidence_supported(_TEXT, "the")


def test_single_char_drift_is_rescued():
    # a dropped apostrophe mid-quote is transcription noise, not fabrication
    text = "the model's weights are updated by a separate optimizer step"
    assert evidence_supported(text, "the models weights are updated by a separate optimizer")


def test_met_without_evidence_downgrades_to_partial():
    assert verified_status(status="met", evidence="words not present", text=_TEXT) == ("partial", False)


def test_partial_without_evidence_downgrades_to_missed():
    assert verified_status(status="partial", evidence="words not present", text=_TEXT) == ("missed", False)


def test_met_with_real_evidence_stands():
    assert verified_status(status="met", evidence="via the chain rule", text=_TEXT) == ("met", True)


def test_missed_needs_no_evidence():
    assert verified_status(status="missed", evidence="", text=_TEXT) == ("missed", True)


def test_unknown_status_cannot_inflate():
    status, ok = verified_status(status="excellent", evidence="via the chain rule", text=_TEXT)
    assert status == "missed" and not ok
