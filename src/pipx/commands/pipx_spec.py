import json
import logging
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional, Sequence

from pipx.commands.inject import inject
from pipx.commands.install import install
from pipx.constants import (
    EXIT_CODE_EXPORT_MISSING_METADATA,
    EXIT_CODE_OK,
    LOCAL_BIN_DIR,
    ExitCode,
)
from pipx.emojies import sleep
from pipx.package_specifier import (
    parse_pip_freeze_specifier,
    parse_specifier,
    parse_specifier_for_install,
)
from pipx.pipx_metadata_file import JsonEncoderHandlesPath, PackageInfo, PipxMetadata
from pipx.util import PipxError
from pipx.venv import Venv, VenvContainer

# TODO: exit code accurate

"""
install_spec impossible with (fail on install):
    Local path does not exist on this system

Freezing / Unfreezing is impossible with (warn on export_spec, fail on install_spec):
    Local paths (no way to verify "version")

Freezing / Unfreezing All is impossible with (warn on export_spec, fail on install_spec):
    pip_args different for main and injected packages (which pip_args to use
        with a particular dep?  We don't know where the dep came from.)
"""

PIPX_SPEC_VERSION = "0.1"


def _package_info_modify_package_or_url_(
    original_package_info: PackageInfo, package_or_url
) -> PackageInfo:
    return PackageInfo(
        package=original_package_info.package,
        package_or_url=package_or_url,
        pip_args=original_package_info.pip_args,
        include_apps=original_package_info.include_apps,
        include_dependencies=original_package_info.include_dependencies,
        apps=original_package_info.apps,
        app_paths=original_package_info.app_paths,
        apps_of_dependencies=original_package_info.apps_of_dependencies,
        app_paths_of_dependencies=original_package_info.app_paths_of_dependencies,
        package_version=original_package_info.package_version,
        suffix=original_package_info.suffix,
    )


def _venv_installable(
    venv_metadata: PipxMetadata, freeze_data: Optional[Dict[str, Any]], verbose: bool
) -> bool:
    """Return True if main, all injected packages, and freeze_data specifiers
    all have valid package specifiers.
    Usually returns False for invalid local path package specifier.
    """
    # Check all pip_freeze specs for uninstallable package_specs
    if freeze_data is None:
        freeze_data = {}
    for package in freeze_data:
        try:
            package_or_url, pip_args = parse_specifier_for_install(
                freeze_data[package]["specifier"], []
            )
        except PipxError:
            # Most probably it is a local path that is currently not valid
            return False

    # Check metadata for uninstallable package_specs
    if venv_metadata.main_package.package_or_url is None:
        return False
    try:
        package_or_url, pip_args = parse_specifier_for_install(
            venv_metadata.main_package.package_or_url,
            venv_metadata.main_package.pip_args,
        )
    except PipxError:
        # Most probably it is a local path that is currently not valid
        return False

    for (_injected_name, injected_package) in venv_metadata.injected_packages.items():
        if injected_package.package_or_url is None:
            return False
        try:
            package_or_url, pip_args = parse_specifier_for_install(
                injected_package.package_or_url, injected_package.pip_args
            )
        except PipxError:
            # Most probably it is a local path that is currently not valid
            return False

    return True


def _get_user_installed_packages(venv_metadata: PipxMetadata):
    user_installed_packages = []
    user_installed_packages.append(venv_metadata.main_package.package)
    for _, injected_package in venv_metadata.injected_packages.items():
        if injected_package.package is None:
            # TODO: handle this better
            raise PipxError("Internal Error with pipx.")
        user_installed_packages.append(injected_package.package)
    return user_installed_packages


def _pip_args_all_same(venv_metadata: PipxMetadata) -> bool:
    for _, injected_package in venv_metadata.injected_packages.items():
        if injected_package.pip_args != venv_metadata.main_package.pip_args:
            # TODO: do we need to check for same options just out of order?
            return False
    return True


def _unfreeze_install_deps(
    venv: Venv, venv_metadata: PipxMetadata, freeze_data: Dict[str, Any], force: bool
) -> None:
    user_installed_packages = _get_user_installed_packages(venv_metadata)
    freeze_dep_data = {
        x: y for (x, y) in freeze_data.items() if x not in user_installed_packages
    }

    if freeze_dep_data:
        # from install.py
        try:
            exists = venv.root.exists() and next(venv.root.iterdir())
        except StopIteration:
            exists = False

        if exists:
            if force:
                print(f"Installing to existing directory {str(venv.root)!r}")
            else:
                print(
                    f"{venv.root.name!r} already seems to be installed. "
                    f"Not modifying existing installation in {str(venv.root)!r}. "
                    "Pass '--force' to force installation."
                )
                return

        venv.create_venv(venv_metadata.venv_args, venv_metadata.main_package.pip_args)

        if not _pip_args_all_same(venv_metadata):
            logging.warning(
                "pip arguments for main package differ with pip "
                "arguments for one or more injected packages.  This "
                "may make restoring dependencies fail or behave "
                "unpredictably."
            )

        # use main_package pip_args for all dependencies because we don't know
        #   otherwise
        for (_, package_dict) in freeze_dep_data.items():
            cmd = (
                ["install"]
                + venv_metadata.main_package.pip_args
                + [package_dict["specifier"]]
            )
            venv._run_pip(cmd)


def _restore_metadata_after_unfreeze(venv: Venv, venv_metadata: PipxMetadata) -> None:
    # restore original package_or_url in metadata if freeze_data after install

    # update our venv's pipx_metadata after install changed written version
    venv.pipx_metadata.read()
    venv.pipx_metadata.main_package = _package_info_modify_package_or_url_(
        venv.pipx_metadata.main_package, venv_metadata.main_package.package_or_url
    )
    for (
        injected_name,
        injected_package,
    ) in venv.pipx_metadata.injected_packages.items():
        # restore original package_or_url in metadata if freeze_data after install

        # update our venv's pipx_metadata after inject changed written version
        venv.pipx_metadata.injected_packages[
            injected_name
        ] = _package_info_modify_package_or_url_(
            venv.pipx_metadata.injected_packages[injected_name],
            injected_package.package_or_url,
        )

    venv.pipx_metadata.write()


# Based on reinstall-all without the uninstall
# TODO: Refuse to install venv containing local paths?  Or try to resolve?
# TODO: if freeze or freeze-all with local path, issue warning that it may not
#       be same version.
def _install_from_metadata(
    venv_metadata: PipxMetadata,
    venv_container: VenvContainer,
    python: str,
    freeze_data: Optional[Dict[str, Any]],
    force: bool,
    verbose: bool,
):
    if (
        venv_metadata.main_package.package_or_url is None
        or venv_metadata.main_package.package is None
    ):
        # TODO: handle this better
        raise PipxError("Internal Error with pipx.")

    venv_dir = venv_metadata.venv_dir
    venv = Venv(venv_dir, python=python, verbose=verbose)

    if not _venv_installable(venv_metadata, freeze_data, verbose):
        print(f"Cannot install {venv_dir.name}")
        return 1

    if freeze_data is not None:
        # install using frozen version
        main_package_or_url = freeze_data[venv_metadata.main_package.package][
            "specifier"
        ]
        _unfreeze_install_deps(venv, venv_metadata, freeze_data, force)
        # install_force needs to be True because we already set up the venv
        install_force = True
    else:
        main_package_or_url = venv_metadata.main_package.package_or_url
        install_force = force

    # install main package
    install(
        venv_dir=venv_dir,
        package_name=None,  # TODO: delete this if install is updated
        package_spec=main_package_or_url,
        local_bin_dir=LOCAL_BIN_DIR,
        python=python,
        pip_args=venv_metadata.main_package.pip_args,
        venv_args=venv_metadata.venv_args,
        verbose=verbose,
        force=install_force,
        include_dependencies=venv_metadata.main_package.include_dependencies,
        suffix=venv_metadata.main_package.suffix,
    )

    # install injected packages
    for (
        injected_name,
        injected_package,
    ) in venv.pipx_metadata.injected_packages.items():
        if injected_package.package_or_url is None or injected_package.package is None:
            # This should never happen, but package_or_url is type
            #   Optional[str] so mypy thinks it could be None
            raise PipxError(
                f"Internal Error injecting package {injected_package} into {venv_dir.name}"
            )

        if freeze_data is not None:
            # install using frozen version
            injected_package_or_url = freeze_data[injected_package.package]["specifier"]
        else:
            injected_package_or_url = injected_package.package_or_url

        inject(
            venv_dir,
            injected_name,
            injected_package_or_url,
            injected_package.pip_args,
            verbose=verbose,
            include_apps=injected_package.include_apps,
            include_dependencies=injected_package.include_dependencies,
            force=force,
        )

    if freeze_data is not None:
        _restore_metadata_after_unfreeze(venv, venv_metadata)


# TODO: how to handle installing when original venv had
#       local path install
def install_spec(
    in_filename: str,
    venv_container: VenvContainer,
    python: str,
    force: bool,
    verbose: bool,
) -> ExitCode:
    input_file = Path(in_filename)
    with open(input_file, "r") as pipx_spec_fh:
        spec_metadata = json.load(pipx_spec_fh)

    for venv_name in spec_metadata["venvs"]:
        # if venv_name in venv_container:
        #   continue
        venv_dir = venv_container.get_venv_dir(venv_name)
        venv_metadata = PipxMetadata(venv_dir, read=False)
        venv_metadata.from_dict(spec_metadata["venvs"][venv_name]["metadata"])
        _install_from_metadata(
            venv_metadata,
            venv_container,
            python,
            spec_metadata["venvs"][venv_name].get("pip_freeze", None),
            force,
            verbose,
        )

    # If no PipxError (Exit Code 1) assume everything went ok
    return EXIT_CODE_OK


def _venvs_with_missing_metadata(venv_dirs: List[Path],) -> List[str]:
    venvs_no_metadata = []
    for venv_dir in venv_dirs:
        if PipxMetadata(venv_dir).main_package.package is None:
            venvs_no_metadata.append(venv_dir.name)
    return venvs_no_metadata


# TODO: this will not find local packages that are only dependencies and
#       not in PipxMetadata
def _local_package_path(package: str, venv_metadata: PipxMetadata) -> Optional[str]:
    """Return path to package if it is editable, None otherwise."""
    if package == venv_metadata.main_package.package:
        if venv_metadata.main_package.package_or_url is None:
            # TODO: handle this better
            raise PipxError("Internal Error with pipx.")
        if parse_specifier(venv_metadata.main_package.package_or_url).valid_local_path:
            return venv_metadata.main_package.package_or_url
        else:
            return None
    else:
        for package in venv_metadata.injected_packages:
            package_or_url = venv_metadata.injected_packages[package].package_or_url
            if package_or_url is None:
                # TODO: handle this better
                raise PipxError("Internal Error with pipx.")
            if parse_specifier(package_or_url).valid_local_path:
                return venv_metadata.main_package.package_or_url
            else:
                return None

    return None


def _check_for_freeze_problems(venv: Venv, freeze_all: bool):
    """Check for future or current problems with unfreezing / freezing

    Warn on export_spec, fail on install_spec:
        --freeze is impossible:
            Local paths in main, injected (no way to verify "version")

        --freeze-all is impossible:
            pip_args different for main and injected packages (which pip_args to use
                with a particular dep?  We don't know where the dep came from.)
            Local paths in a dep (TODO: how to detect?!?!)
    """
    problems: List[str] = []
    return problems


# TODO: handle venvs with different version metadata
# TODO: does non-editable local path need extra metadata note that it is local?
def export_spec(
    out_filename: str,
    venv_container: VenvContainer,
    skip_list: Sequence[str],
    include_list: Optional[List[str]],
    freeze: bool,
    freeze_all: bool,
    verbose: bool,
) -> ExitCode:
    dirs: Collection[Path] = sorted(venv_container.iter_venv_dirs())
    if not dirs:
        print(f"nothing has been installed with pipx {sleep}")
        return EXIT_CODE_OK

    # TODO: remove this?
    venv_container.verify_shared_libs()

    spec_metadata: Dict[str, Any] = {"spec_version": PIPX_SPEC_VERSION, "venvs": {}}

    venv_dirs_export: List[Path] = []
    for venv_dir in sorted(venv_container.iter_venv_dirs()):
        if venv_dir.name in skip_list:
            continue
        if include_list is not None and venv_dir.name not in include_list:
            continue
        venv_dirs_export.append(venv_dir)

    venvs_no_metadata = _venvs_with_missing_metadata(venv_dirs_export)
    if _venvs_with_missing_metadata(venv_dirs_export):
        print("Cannot export pipx spec.  The following venvs have missing metadata:\n")
        print("    ", end="")
        for venv_name in venvs_no_metadata:
            print(f"{venv_name}, ", end="")
        print("")
        print(
            "    Please uninstall and install each of these venvs, or reinstall-all to fix."
        )
        return EXIT_CODE_EXPORT_MISSING_METADATA

    for venv_dir in venv_dirs_export:
        spec_metadata["venvs"][venv_dir.name] = {}
        venv_metadata = PipxMetadata(venv_dir)
        spec_metadata["venvs"][venv_dir.name]["metadata"] = venv_metadata.to_dict()
        if freeze_all or freeze:
            venv = Venv(venv_dir)
            pip_freeze_dict: Dict[str, Any] = {}
            for specifier in venv.pip_freeze():
                package = parse_pip_freeze_specifier(specifier)
                package_path = _local_package_path(package, venv_metadata)
                if package_path is not None:
                    pip_freeze_dict[package]["specifier"] = package_path
                    pip_freeze_dict[package]["local"] = True
                else:
                    pip_freeze_dict[package]["specifier"] = specifier
                    pip_freeze_dict[package]["local"] = False
        if freeze_all:
            spec_metadata["venvs"][venv_dir.name]["pip_freeze"] = pip_freeze_dict
        elif freeze:
            user_installed_packages = _get_user_installed_packages(venv_metadata)
            spec_metadata["venvs"][venv_dir.name]["pip_freeze"] = {
                x: y for x, y in pip_freeze_dict.items() if x in user_installed_packages
            }

    with open(out_filename, "w") as pipx_export_fh:
        json.dump(
            spec_metadata,
            pipx_export_fh,
            indent=4,
            sort_keys=True,
            cls=JsonEncoderHandlesPath,
        )

    # If no PipxError (Exit Code 1) assume everything went ok
    return EXIT_CODE_OK
