from financial_os.cli import main


def test_version_cli(capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert "LedgerRing" in out
    assert "0." in out  # versioned LedgerRing x.y.z
