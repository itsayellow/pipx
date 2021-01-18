from typing import Tuple

from packaging import version
from pypi_simple import PyPISimple


# TODO: handle git+ URLs
def get_latest_version(
    package_name: str, index_url: str = "https://pypi.org/simple/"
) -> Tuple[str, version]:
    with PyPISimple(index_url) as client:
        requests_page = client.get_project_page(package_name)

    package_latest_version_str = requests_page.packages[-1].version
    package_latest_version = version.parse(package_latest_version_str)

    return package_latest_version_str, package_latest_version
