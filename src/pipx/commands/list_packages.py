import logging
import time
from functools import partial
from pathlib import Path
from typing import Any, Callable, Collection, Dict, List, Optional

from packaging.version import Version

from pipx import constants
from pipx.colors import bold
from pipx.commands.common import VenvProblems, get_package_summary
from pipx.constants import EXIT_CODE_LIST_PROBLEM, EXIT_CODE_OK, ExitCode
from pipx.emojies import sleep
from pipx.interpreter import DEFAULT_PYTHON
from pipx.package_specifier import _parse_specifier
from pipx.pipx_metadata_file import PackageInfo
from pipx.simple_interface import (
    get_indexes,
    indexes_from_pip_config,
    latest_version_from_index,
)
from pipx.util import PipxError
from pipx.venv import Venv, VenvContainer

Pool: Optional[Callable]
try:
    import multiprocessing.synchronize  # noqa: F401
    from multiprocessing import Pool
except ImportError:
    Pool = None

logger = logging.getLogger(__name__)


def get_latest_version(
    package_info: PackageInfo,
    pip_config_index_url: str,
    pip_config_extra_index_urls: List[str],
) -> Optional[Version]:
    if package_info.package_or_url is None or package_info.package is None:
        # This should never happen, but check these Optional variables
        raise PipxError("Internal Error with pipx metadata.")

    # Specifically ignore VCS- or URL-based packages.
    #   pip currently only checks indexes, and can't find URL-based
    #       packages there, effectively ignoring them for "outdated" purposes.
    #   To actually verify version of URL-based packages, we'd probably
    #       have to install them to a temp directory to verify their version
    #       which would take too long.
    parsed_specifier = _parse_specifier(package_info.package_or_url)
    if (
        parsed_specifier.valid_url
        or parsed_specifier.valid_local_path
        or (
            parsed_specifier.valid_pep508 is not None
            and parsed_specifier.valid_pep508.url is not None
        )
    ):
        return None

    index_urls = get_indexes(
        package_info.pip_args, pip_config_index_url, pip_config_extra_index_urls
    )
    logger.info(f"index_urls = {index_urls}")

    for index_url in index_urls:
        latest_version = latest_version_from_index(package_info.package, index_url)
        if latest_version is not None:
            break

    return latest_version


def list_packages(
    dirs: Collection[Path],
    include_injected: bool,
    extra_info: Optional[Dict[str, Any]] = None,
) -> VenvProblems:
    all_venv_problems = VenvProblems()

    if Pool:
        p = Pool()
        try:
            for package_summary, venv_problems in p.map(
                partial(
                    get_package_summary,
                    include_injected=include_injected,
                    extra_info=extra_info,
                ),
                dirs,
            ):
                print(package_summary)
                all_venv_problems.or_(venv_problems)
        finally:
            p.close()
            p.join()
    else:
        for package_summary, venv_problems in map(
            partial(
                get_package_summary,
                include_injected=include_injected,
                extra_info=extra_info,
            ),
            dirs,
        ):
            print(package_summary)
            all_venv_problems.or_(venv_problems)

    return all_venv_problems


def list_outdated_packages(dirs: Collection[Path], include_injected: bool):
    time_start = time.time()
    (pip_config_index_url, pip_config_extra_index_urls) = indexes_from_pip_config(
        DEFAULT_PYTHON
    )
    logger.info(f"get_pip_config elapsed: {time.time()-time_start}")
    logger.info(f"pip_config_index_url = {pip_config_index_url}")
    logger.info(f"pip_config_extra_index_urls = {pip_config_extra_index_urls}")

    if include_injected:
        print("TODO: need to implement --include-injected with --outdated")

    dirs_version_unknown = []
    dirs_version_outdated = []
    extra_info: Dict[str, Any] = {}
    for venv_dir in dirs:
        venv = Venv(venv_dir)

        extra_info[str(venv_dir)] = {}

        current_version = Version(
            venv.package_metadata[venv.main_package_name].package_version
        )

        start_time = time.time()
        latest_version = get_latest_version(
            venv.package_metadata[venv.main_package_name],
            pip_config_index_url,
            pip_config_extra_index_urls,
        )
        logger.info(f"get_latest_version: {time.time()-start_time:.3f}s")

        extra_info[str(venv_dir)][venv.main_package_name] = {}
        extra_info[str(venv_dir)][venv.main_package_name]["latest_version"] = (
            str(latest_version) if latest_version is not None else None
        )
        if latest_version is None:
            dirs_version_unknown.append(venv_dir)
        elif latest_version > current_version:
            dirs_version_outdated.append(venv_dir)

    if not dirs_version_outdated:
        print(f"No out-of-date pipx packages {sleep}")
        return EXIT_CODE_OK

    all_venv_problems = list_packages(
        dirs_version_outdated, include_injected, extra_info
    )

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

    venv_container.verify_shared_libs()

    if only_outdated:
        all_venv_problems = list_outdated_packages(dirs, include_injected)
    else:
        all_venv_problems = list_packages(dirs, include_injected)

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
