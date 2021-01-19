from functools import partial
from pathlib import Path
from typing import Callable, Collection, Optional

from packaging.version import InvalidVersion, Version
from pypi_simple import PyPISimple  # type: ignore

from pipx import constants
from pipx.colors import bold
from pipx.commands.common import VenvProblems, get_package_summary
from pipx.constants import EXIT_CODE_LIST_PROBLEM, EXIT_CODE_OK, ExitCode
from pipx.emojies import sleep
from pipx.package_specifier import _parse_specifier
from pipx.util import PipxError
from pipx.venv import Venv, VenvContainer

Pool: Optional[Callable]
try:
    import multiprocessing.synchronize  # noqa: F401
    from multiprocessing import Pool
except ImportError:
    Pool = None


# TODO: handle git+ URLs
def latest_version_from_index(
    package_name: str, index_url: str = "https://pypi.org/simple/"
) -> Optional[Version]:
    """Returns None if latest version cannot be determined."""
    package_latest_version: Optional[Version]

    with PyPISimple(index_url) as client:
        requests_page = client.get_project_page(package_name)

    if requests_page is None:
        return None

    # TODO: is last package in packages guaranteed to be latest version?

    package_latest_version_str = requests_page.packages[-1].version

    if package_latest_version_str is None:
        return None

    try:
        package_latest_version = Version(package_latest_version_str)
    except InvalidVersion:
        print("Latest Version is invalid: {package_latest_version_str}")
        package_latest_version = None

    return package_latest_version


def get_latest_version(package_metadata) -> Optional[Version]:
    if package_metadata.package_or_url is None:
        # This should never happen, but package_or_url is type
        #   Optional[str] so mypy thinks it could be None
        raise PipxError("Internal Error with pipx metadata.")
    parsed_specifier = _parse_specifier(package_metadata.package_or_url)
    # print(f"package_metadata.package={package_metadata.package}")
    # print(f"    package_metadata.package_or_url={package_metadata.package_or_url}")
    # print(f"    package_metadata.package_version={package_metadata.package_version}")
    # print(f"    parsed_specifier.valid_pep508={parsed_specifier.valid_pep508}")
    # print(f"    parsed_specifier.valid_url={parsed_specifier.valid_url}")
    # print(f"    parsed_specifier.valid_local_path={parsed_specifier.valid_local_path}")
    # if parsed_specifier.valid_pep508 is not None:
    #     print(
    #         f"    parsed_specifier.valid_pep508.url={parsed_specifier.valid_pep508.url}"
    #     )
    if (
        parsed_specifier.valid_url
        or parsed_specifier.valid_local_path
        or (
            parsed_specifier.valid_pep508 is not None
            and parsed_specifier.valid_pep508.url is not None
        )
    ):
        return None
    pip_args = package_metadata.pip_args
    if "--index-url" in pip_args:
        custom_index_url = pip_args[pip_args.index("--index_url") + 1]
        print("Using custom index-url: {custom_index_url}")
        latest_version = latest_version_from_index(
            package_metadata.package, custom_index_url
        )
    else:
        latest_version = latest_version_from_index(package_metadata.package)

    return latest_version


def list_packages(dirs, all_venv_problems, include_injected):
    if Pool:
        p = Pool()
        try:
            for package_summary, venv_problems in p.map(
                partial(get_package_summary, include_injected=include_injected), dirs
            ):
                print(package_summary)
                all_venv_problems.or_(venv_problems)
        finally:
            p.close()
            p.join()
    else:
        for package_summary, venv_problems in map(
            partial(get_package_summary, include_injected=include_injected), dirs
        ):
            print(package_summary)
            all_venv_problems.or_(venv_problems)

    return all_venv_problems


def list_command(
    venv_container: VenvContainer, include_injected: bool, only_outdated: bool
) -> ExitCode:
    """Returns pipx exit code."""
    dirs: Collection[Path] = sorted(venv_container.iter_venv_dirs())

    if not dirs:
        print(f"nothing has been installed with pipx {sleep}")
        return EXIT_CODE_OK

    print(f"venvs are in {bold(str(venv_container))}")
    print(f"apps are exposed on your $PATH at {bold(str(constants.LOCAL_BIN_DIR))}")

    if only_outdated:
        dirs_version_unknown = []
        dirs_version_outdated = []
        for venv_dir in dirs:
            venv = Venv(venv_dir)
            current_version = Version(
                venv.package_metadata[venv.main_package_name].package_version
            )
            latest_version = get_latest_version(
                venv.package_metadata[venv.main_package_name]
            )
            if latest_version is None:
                dirs_version_unknown.append(venv_dir)
            elif latest_version > current_version:
                dirs_version_outdated.append(venv_dir)
        if not dirs_version_unknown and not dirs_version_outdated:
            print(f"No out-of-date pipx packages {sleep}")
            # TODO: what exit code?
            return EXIT_CODE_OK

    venv_container.verify_shared_libs()

    all_venv_problems = VenvProblems()
    if not only_outdated:
        all_venv_problems = list_packages(dirs, all_venv_problems, include_injected)
    else:
        print("\nOutdated packages:")
        if not dirs_version_outdated:
            print("    No verified out-of-date packages have been installed with pipx")
        else:
            all_venv_problems = list_packages(
                dirs_version_outdated, all_venv_problems, include_injected
            )
        if dirs_version_unknown:
            print("\nPackages with unknown latest version:")
            all_venv_problems = list_packages(
                dirs_version_unknown, all_venv_problems, include_injected
            )

    if all_venv_problems.bad_venv_name:
        print(
            "\nOne or more packages contain out-of-date internal data installed from a\n"
            "previous pipx version and need to be updated.\n"
            "    To fix, execute: pipx reinstall-all"
        )
    if all_venv_problems.invalid_interpreter:
        print(
            "\nOne or more packages have a missing python interpreter.\n"
            "    To fix, execute: pipx reinstall-all"
        )
    if all_venv_problems.missing_metadata:
        print(
            "\nOne or more packages have a missing internal pipx metadata.\n"
            "   They were likely installed using a pipx version before 0.15.0.0.\n"
            "   Please uninstall and install these package(s) to fix."
        )
    if all_venv_problems.not_installed:
        print(
            "\nOne or more packages are not installed properly.\n"
            "   Please uninstall and install these package(s) to fix."
        )

    if all_venv_problems.any_():
        print()
        return EXIT_CODE_LIST_PROBLEM

    return EXIT_CODE_OK
