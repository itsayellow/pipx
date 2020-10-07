import logging
from pathlib import Path
from typing import List

from packaging.utils import canonicalize_name

from pipx.colors import bold
from pipx.emojies import hazard, stars
from pipx.util import PipxError
from pipx.venv import Venv


def uninject_dep(venv: Venv, package_name: str, *, verbose: bool,) -> bool:
    package_name = canonicalize_name(package_name)

    if package_name == venv.pipx_metadata.main_package.package:
        logging.warning(
            f"{hazard}  {package_name} is the main package of {venv.root.name} "
            "venv.  Use `pipx uninstall` to uninstall instead of uninject."
        )
        return False
    if package_name not in venv.pipx_metadata.injected_packages:
        logging.warning(
            f"{hazard}  {package_name} is not in the {venv.root.name} venv.  Skipping."
        )
        return False

    venv.uninstall_package(package=package_name,)

    # TODO: remove symlinks (Unix, macOS) or copies (Windows) for removed
    #       injected packages as in uninstall() if `--include_apps`, `--include_deps`

    print(
        f"uninjected package {bold(package_name)} from venv {bold(venv.root.name)} {stars}"
    )
    return True


def uninject(venv_dir: Path, dependencies: List[str], *, verbose: bool,) -> int:
    """Returns pipx exit code"""

    if not venv_dir.exists() or not next(venv_dir.iterdir()):
        raise PipxError(f"Virtual environment {venv_dir.name} does not exist.")

    venv = Venv(venv_dir, verbose=verbose)

    if not venv.package_metadata:
        raise PipxError(
            f"Can't uninject from Virtual Environment {venv_dir.name!r}.\n"
            f"    {venv_dir.name!r} has missing internal pipx metadata.\n"
            "    It was likely installed using a pipx version before 0.15.0.0.\n"
            f"    Please uninstall and install {venv_dir.name!r} manually to fix."
        )

    all_success = True
    for dep in dependencies:
        all_success &= uninject_dep(venv, dep, verbose=verbose)

    if all_success:
        return 0
    else:
        return 1
