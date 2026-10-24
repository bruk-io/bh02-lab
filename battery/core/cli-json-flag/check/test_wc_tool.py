import json

from wc_tool import main


def _file(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("one two\nthree\n")
    return str(path)


def test_plain_output_is_unchanged(tmp_path, capsys):
    path = _file(tmp_path)
    assert main([path]) == 0
    assert capsys.readouterr().out == f"2 3 {path}\n"


def test_json_output(tmp_path, capsys):
    path = _file(tmp_path)
    assert main(["--json", path]) == 0
    assert json.loads(capsys.readouterr().out) == {"path": path, "lines": 2, "words": 3}


def test_json_flag_after_path(tmp_path, capsys):
    path = _file(tmp_path)
    assert main([path, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["words"] == 3


def test_json_counts_are_integers(tmp_path, capsys):
    main(["--json", _file(tmp_path)])
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data["lines"], int) and isinstance(data["words"], int)
