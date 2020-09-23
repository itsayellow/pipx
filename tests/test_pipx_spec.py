from helpers import run_pipx_cli

# packages used in install, inject tests:
#   pycowsay
#   black
#   pylint
#
#   cloudtoken, awscli, ansible, shell-functools (slow)

# TODO: pin versions of test packages?


def test_export_spec(pipx_temp_env, monkeypatch, capsys):
    run_pipx_cli(["install", "pycowsay"])
    run_pipx_cli(["inject", "black"])
    run_pipx_cli(["inject", "pylint"])
    assert not run_pipx_cli(["export-spec", "test.json"])
