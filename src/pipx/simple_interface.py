import argparse
import time
from typing import List, Optional, Tuple

from packaging.version import InvalidVersion, Version
from pypi_simple import PyPISimple  # type: ignore
from requests.exceptions import ReadTimeout

from pipx.util import get_pip_config

DEFAULT_PYPI_SIMPLE_URL = "https://pypi.org/simple/"


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


def get_indexes(
    pip_args: List[str],
    pip_config_index_url: str,
    pip_config_extra_index_urls: List[str],
) -> List[str]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-url", "-i", action="store")
    parser.add_argument("--extra-index-url", action="store")
    parsed_pip_args, _ = parser.parse_known_args(pip_args)
    print(f"parsed_pip_args = {parsed_pip_args}")

    if parsed_pip_args.index_url is not None:
        index_url = parsed_pip_args.index_url
    else:
        index_url = pip_config_index_url

    if parsed_pip_args.extra_index_url is not None:
        extra_index_urls = parsed_pip_args.extra_index_url.split()
    else:
        extra_index_urls = []

    return [index_url] + extra_index_urls


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
