"""Tests for distribution: packaged resources stay byte-identical to
the repository canon (drift guard), init scaffolds a workspace
without ever overwriting, and the version flag reports the package."""

from pathlib import Path


_SYNC = [
    ("definitions/ma_touch_trend.toml",
     "src/strategy_verifier/product_resources/definitions/ma_touch_trend.toml"),
    ("definitions/wick_reversal.toml",
     "src/strategy_verifier/product_resources/definitions/wick_reversal.toml"),
    ("definitions/claims/example_squeeze_claim.toml",
     "src/strategy_verifier/product_resources/claims/example_squeeze_claim.toml"),
    ("docs/USER_GUIDE.md",
     "src/strategy_verifier/product_resources/docs/USER_GUIDE.md"),
    ("docs/DISCLAIMER.md",
     "src/strategy_verifier/product_resources/docs/DISCLAIMER.md"),
    ("docs/PRODUCT_CHARTER.md",
     "src/strategy_verifier/product_resources/docs/PRODUCT_CHARTER.md"),
    ("docs/COMPLIANCE.md",
     "src/strategy_verifier/product_resources/docs/COMPLIANCE.md"),
    ("definitions/demo_quickstart.toml",
     "src/strategy_verifier/product_resources/definitions/demo_quickstart.toml"),
    ("LICENSE", "src/strategy_verifier/product_resources/LICENSE"),
]


def test_packaged_resources_match_canon():
    for canon, packaged in _SYNC:
        assert Path(canon).read_bytes() == Path(packaged).read_bytes(), (
            f"resource drift: {packaged} differs from {canon}"
        )


def test_init_scaffolds_and_never_overwrites(tmp_path, capsys):
    from strategy_verifier import cli

    target = tmp_path / "work"
    assert cli.main(["init", "--dir", str(target)]) == 0
    first = capsys.readouterr().out
    assert "Verifier workspace initialized" in first
    assert (target / "docs" / "USER_GUIDE.md").exists()
    assert (target / "definitions" / "ma_touch_trend.toml").exists()
    assert (target / "claims" / "example_squeeze_claim.toml").exists()
    assert (target / "LICENSE").exists()
    for empty in ("data/captures", "reports", "state"):
        assert (target / empty).is_dir()
    assert (target / "data" / "captures"
            / "demo-900s-1.jsonl").exists()

    marker = target / "docs" / "USER_GUIDE.md"
    marker.write_text("사용자 수정본", encoding="utf-8")
    assert cli.main(["init", "--dir", str(target)]) == 0
    second = capsys.readouterr().out
    assert "written: 0" in second
    assert marker.read_text(encoding="utf-8") == "사용자 수정본"

    triage_exit = cli.main([
        "verify-triage",
        "--claim", str(target / "claims" / "example_squeeze_claim.toml"),
    ])
    assert triage_exit == 0
    assert "admissible: yes" in capsys.readouterr().out


def test_version_flag(capsys):
    from strategy_verifier import cli

    assert cli.main(["--version"]) == 0
    assert "verifier" in capsys.readouterr().out


def test_demo_pair_measures_out_of_the_box(tmp_path, capsys):
    """A fresh init carries a capture that the demo definition measures
    to completion - the thirty-second walk needs no outside data."""
    import hashlib
    from strategy_verifier import cli

    packaged = Path(
        "src/strategy_verifier/product_resources/data/captures/"
        "demo-900s-1.jsonl")
    digest = hashlib.sha256(packaged.read_bytes()).hexdigest()
    assert digest == "c2788fc25595278eb47cf250fdb83ad3e4780e4d89acb3d934d7a80e54e7fa73", "demo capture drifted"

    target = tmp_path / "work"
    assert cli.main(["init", "--dir", str(target)]) == 0
    assert "try the demo pair" in capsys.readouterr().out
    exit_code = cli.main([
        "verify-run",
        "--definition", str(target / "definitions"
                            / "demo_quickstart.toml"),
        "--input", str(target / "data" / "captures"
                       / "demo-900s-1.jsonl"),
        "--input-session-mode", "single",
    ])
    assert exit_code == 0
    assert "equation:" in capsys.readouterr().out
