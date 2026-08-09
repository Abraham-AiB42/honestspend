from financial_os.cli import main


def test_version_cli(capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert "Floatpile" in out
    assert "1." in out or "0." in out  # versioned x.y.z
