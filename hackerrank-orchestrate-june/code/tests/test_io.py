"""I/O: byte-exact echo of the 4 input fields (';', '|', Hinglish), writer column
order + quoting, the csvdict round-trip, and reading the real dataset."""
import csv

from src.config import load_config
from src.io.reader import read_claims, read_evidence_rules, read_history, read_sample_labels
from src.io.writer import write_output
from src.pipeline import _csvdict_to_kwargs, safe_default_row
from src.schema import OUTPUT_COLUMNS, OutputRow


def _row(**kw):
    base = dict(user_id="u1", image_paths="images/test/case_1/img_1.jpg;images/test/case_1/img_2.jpg",
                user_claim="Customer: front bumper | Support: kis type? | Customer: scratch hai.",
                claim_object="car", evidence_standard_met=True, evidence_standard_met_reason="r",
                risk_flags=["claim_mismatch", "user_history_risk"], issue_type="scratch", object_part="front_bumper",
                claim_status="contradicted", claim_status_justification="j", supporting_image_ids=["img_1"],
                valid_image=False, severity="low")
    base.update(kw)
    return OutputRow(**base)


def test_writer_header_and_byte_exact_echo(tmp_path):
    rows = [_row(), _row(user_id="u2", claim_object="laptop", object_part="hinge",
                         issue_type="broken_part", claim_status="supported", valid_image=True,
                         risk_flags=["none"], severity="medium")]
    out = tmp_path / "out.csv"
    write_output(rows, out, expected_count=2)

    with open(out, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == list(OUTPUT_COLUMNS)
        got = list(reader)
    # the 4 input fields survive verbatim (semicolons + pipes + Hinglish)
    assert got[0]["image_paths"] == rows[0].image_paths
    assert got[0]["user_claim"] == rows[0].user_claim
    assert got[0]["risk_flags"] == "claim_mismatch;user_history_risk"
    assert got[1]["risk_flags"] == "none"
    assert got[0]["valid_image"] == "false" and got[1]["valid_image"] == "true"


def test_writer_is_quote_all(tmp_path):
    out = tmp_path / "o.csv"
    write_output([_row()], out)
    first_data_line = out.read_text(encoding="utf-8").splitlines()[1]
    assert first_data_line.startswith('"') and first_data_line.endswith('"')


def test_csvdict_roundtrip():
    r = _row()
    rebuilt = OutputRow(**_csvdict_to_kwargs(r.to_csv_dict()))
    assert rebuilt.to_csv_dict() == r.to_csv_dict()


def test_safe_default_row_is_valid():
    from src.io.reader import ClaimInput
    c = ClaimInput(user_id="u", image_paths="images/test/case_1/img_1.jpg", user_claim="x", claim_object="package")
    r = safe_default_row(c, "boom")
    d = r.to_csv_dict()
    assert d["claim_status"] == "not_enough_information" and d["supporting_image_ids"] == "none"
    assert d["valid_image"] == "false" and d["severity"] == "unknown"
    assert "manual_review_required" in d["risk_flags"]


def test_claim_id_unique_even_when_case_id_collides(tmp_path):
    # Two rows with NO 'case_NNN' in the path and the SAME user_id collide on case_id()
    # (== user_id). read_claims must still assign distinct uid()s so their cached facts /
    # checkpoint rows can't overwrite each other.
    p = tmp_path / "claims.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["user_id", "image_paths", "user_claim", "claim_object"])
        w.writerow(["u1", "images/a.jpg", "c1", "car"])
        w.writerow(["u1", "images/b.jpg", "c2", "car"])
    claims = read_claims(p)
    assert claims[0].case_id() == claims[1].case_id() == "u1"   # old key would collide
    assert claims[0].uid() != claims[1].uid()                   # new identity is unique
    assert len({c.uid() for c in claims}) == 2


def test_claim_id_keeps_case_label_verbatim_for_cache_compat(tmp_path):
    # When the path carries a unique 'case_NNN', uid() must equal that label verbatim so
    # existing 'case_NNN.json' cache files stay valid (no re-run forced).
    p = tmp_path / "claims.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["user_id", "image_paths", "user_claim", "claim_object"])
        w.writerow(["u1", "images/test/case_001/img_1.jpg", "c1", "car"])
        w.writerow(["u2", "images/test/case_002/img_1.jpg", "c2", "laptop"])
    claims = read_claims(p)
    assert claims[0].uid() == "case_001" and claims[1].uid() == "case_002"


def test_reads_bundled_dataset():
    # Validates the bundled demo dataset loads end to end. The repo ships small
    # synthetic rows (the real challenge data is not redistributable), so this
    # asserts shape/round-trip rather than fixed row counts.
    cfg = load_config()
    claims = read_claims(cfg.test_csv)
    sample = read_claims(cfg.sample_csv)
    labels = read_sample_labels(cfg.sample_csv)
    assert claims and all(c.claim_object in {"car", "laptop", "package"} for c in claims)
    assert sample and len(labels) == len(sample)   # one label row per sample claim
    assert read_history(cfg.history_csv)            # history present
    assert read_evidence_rules(cfg.evidence_csv)    # rulebook present
    # a multi-image claim's ids round-trip in order
    multi = [c for c in claims if len(c.image_ids()) >= 2]
    assert multi and multi[0].image_ids()[:2] == ["img_1", "img_2"]
