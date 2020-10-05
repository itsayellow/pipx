import sys
from pathlib import Path
from typing import List

from pipx.colors import bold
from pipx.emojies import stars
from pipx.util import PipxError
from pipx.venv import Venv


def uninject_dep(venv: Venv, package_name: str, *, verbose: bool,) -> None:
    venv.uninstall_package(package=package_name,)

    print(f"  injected package {bold(package_name)} into venv {bold(venv.root.name)}")
    print(f"done! {stars}", file=sys.stderr)


def uninject(venv_dir: Path, dependencies: List[str], *, verbose: bool,) -> int:
    """Returns pipx exit code"""

    if not venv_dir.exists() or not next(venv_dir.iterdir()):
        raise PipxError(f"Virtual environment {venv_dir.name} does not exist.")
        return 1

    venv = Venv(venv_dir, verbose=verbose)

    if not venv.package_metadata:
        raise PipxError(
            f"Can't uninject from Virtual Environment {venv_dir.name!r}.\n"
            f"    {venv_dir.name!r} has missing internal pipx metadata.\n"
            "    It was likely installed using a pipx version before 0.15.0.0.\n"
            f"    Please uninstall and install {venv_dir.name!r} manually to fix."
        )

    for dep in dependencies:
        uninject_dep(venv, dep, verbose=verbose)
    return 0
