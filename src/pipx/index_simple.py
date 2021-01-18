from typing import Optional

from packaging.version import InvalidVersion, Version
from pypi_simple import PyPISimple  # type: ignore


# TODO: handle git+ URLs
def get_latest_version(
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
