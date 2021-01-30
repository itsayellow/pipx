import json
import time
from functools import partial
from pathlib import Path
from typing import Any, Callable, Collection, Dict, List, Optional, Tuple

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pypi_simple import PyPISimple  # type: ignore
from requests.exceptions import ReadTimeout

from pipx import constants
from pipx.colors import bold
from pipx.commands.common import VenvProblems, get_package_summary
from pipx.constants import EXIT_CODE_LIST_PROBLEM, EXIT_CODE_OK, ExitCode
from pipx.emojies import sleep
from pipx.interpreter import DEFAULT_PYTHON
from pipx.package_specifier import _parse_specifier
from pipx.util import PipxError, get_pip_config, run_subprocess
from pipx.venv import Venv, VenvContainer

Pool: Optional[Callable]
try:
    import multiprocessing.synchronize  # noqa: F401
    from multiprocessing import Pool
except ImportError:
    Pool = None

DEFAULT_PYPI_SIMPLE_URL = "https://pypi.org/simple/"


# TODO: handle git+ URLs
def latest_version_from_index(
    package_name: str, index_url: str = DEFAULT_PYPI_SIMPLE_URL
) -> Optional[Version]:
    """Returns None if latest version cannot be determined."""
    package_latest_version: Optional[Version]

    print(f"PyPISimple using: {index_url}")
    time_start = time.time()
    try:
        with PyPISimple(index_url) as client:
            requests_page = client.get_project_page(package_name, timeout=10.0)
    except ReadTimeout:
        return None
    print(f"PyPISimple elapsed: {time.time()-time_start}")

    if requests_page is None:
        return None

    package_versions = []
    for package_instance in requests_page.packages:
        try:
            package_versions.append(Version(package_instance.version))
        except InvalidVersion:
            pass

    if package_versions:
        return max(package_versions)
    else:
        return None


def indexes_from_pip_config(python: str) -> Tuple[str, List[str]]:
    pip_config = get_pip_config(python)
    pip_config_index_url = DEFAULT_PYPI_SIMPLE_URL
    pip_config_extra_index_urls = []

    if ":env:.index-url" in pip_config:
        pip_config_index_url = pip_config[":env:.index-url"][0]
    elif "global.index-url" in pip_config:
        pip_config_index_url = pip_config["global.index-url"][0]

    if ":env:.extra-index-url" in pip_config:
        pip_config_extra_index_urls = pip_config[":env:.extra-index-url"]
    elif "global.extra-index-url" in pip_config:
        pip_config_extra_index_urls = pip_config["global.extra-index-url"]

    return (pip_config_index_url, pip_config_extra_index_urls)


# TODO: add logging messages
# TODO: type package_metadata
# Typically takes ~0.06s
def get_latest_version(
    package_metadata, pip_config_index_url: str, pip_config_extra_index_urls: List[str]
) -> Optional[Version]:
    index_url = DEFAULT_PYPI_SIMPLE_URL
    extra_index_urls: List[str] = []

    if package_metadata.package_or_url is None:
        # This should never happen, but package_or_url is type
        #   Optional[str] so mypy thinks it could be None
        raise PipxError("Internal Error with pipx metadata.")

    parsed_specifier = _parse_specifier(package_metadata.package_or_url)
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
        index_url = pip_args[pip_args.index("--index_url") + 1]
    else:
        index_url = pip_config_index_url

    # latest_version = latest_version_from_index(package_metadata.package, index_url)
    for index_url in [index_url] + extra_index_urls:
        latest_version = latest_version_from_index(package_metadata.package, index_url)
        if latest_version is not None:
            break

    return latest_version


# Typically takes ~2.00s
def get_latest_version2(package_metadata, python_path: Path) -> Optional[Version]:
    if package_metadata.package_or_url is None:
        # This should never happen, but package_or_url is type
        #   Optional[str] so mypy thinks it could be None
        raise PipxError("Internal Error with pipx metadata.")

    package_name = package_metadata.package

    parsed_specifier = _parse_specifier(package_metadata.package_or_url)
    if (
        parsed_specifier.valid_url
        or parsed_specifier.valid_local_path
        or (
            parsed_specifier.valid_pep508 is not None
            and parsed_specifier.valid_pep508.url is not None
        )
    ):
        return None

    pip_list_subprocess = run_subprocess(
        [python_path, "-m", "pip", "list", "--outdated", "--format", "json"]
    )

    pip_list = json.loads(pip_list_subprocess.stdout)
    for package_info in pip_list:
        if canonicalize_name(package_info["name"]) == package_name:
            try:
                latest_version = Version(package_info["latest_version"])
            except InvalidVersion:
                return None
            return latest_version

    return None


def list_packages(
    dirs: Collection[Path],
    all_venv_problems: VenvProblems,
    include_injected: bool,
    extra_info: Optional[Dict[str, Any]] = None,
) -> VenvProblems:
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
    all_venv_problems = VenvProblems()

    if only_outdated:
        time_start = time.time()
        (pip_config_index_url, pip_config_extra_index_urls) = indexes_from_pip_config(
            DEFAULT_PYTHON
        )
        print(f"get_pip_config elapsed: {time.time()-time_start}")
        # TODO: check injected packages also if include_injected
        dirs_version_unknown = []
        dirs_version_outdated = []
        extra_info: Dict[str, Any] = {}
        for venv_dir in dirs:
            venv = Venv(venv_dir)

            extra_info[str(venv_dir)] = {}

            current_version = Version(
                venv.package_metadata[venv.main_package_name].package_version
            )

            # print(f"venv.main_package_name = {venv.main_package_name}")
            # start_time = time.time()
            # latest_version = get_latest_version2(
            #     venv.package_metadata[venv.main_package_name], venv.python_path
            # )
            # print(f"get_latest_version2: {time.time()-start_time:.3f}s")

            start_time = time.time()
            latest_version = get_latest_version(
                venv.package_metadata[venv.main_package_name],
                pip_config_index_url,
                pip_config_extra_index_urls,
            )
            print(f"get_latest_version: {time.time()-start_time:.3f}s")

            extra_info[str(venv_dir)][venv.main_package_name] = {}
            extra_info[str(venv_dir)][venv.main_package_name]["latest_version"] = (
                str(latest_version) if latest_version is not None else None
            )
            if latest_version is None:
                dirs_version_unknown.append(venv_dir)
            elif latest_version > current_version:
                dirs_version_outdated.append(venv_dir)

        # NOTE: pip currently only checks pypi, and can't find packages
        #       installed from URL, effectively ignoring them for "outdated"
        #       purposes.  By listing unknown latest versions we are being more
        #       conservative.  pip doesn't list these at all
        # To actually verify version of URL-based packages, we'd probably
        #   have to install them to a temp directory to verify their version
        if dirs_version_unknown:
            # TODO: this may just be annoying (put it in help instead?)
            print("\n(Not checking git- or URL-based packages.)")
        else:
            print("\n")

        if not dirs_version_outdated:
            print(f"No out-of-date pipx packages {sleep}")
            return EXIT_CODE_OK

        print("Outdated packages:")
        all_venv_problems = list_packages(
            dirs_version_outdated, all_venv_problems, include_injected, extra_info
        )

    else:
        all_venv_problems = list_packages(dirs, all_venv_problems, include_injected)

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
