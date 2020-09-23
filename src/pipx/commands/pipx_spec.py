import json
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional

from pipx.commands.inject import inject
from pipx.commands.install import install
from pipx.constants import LOCAL_BIN_DIR
from pipx.emojies import sleep
from pipx.package_specifier import (
    parse_pip_freeze_specifier,
    parse_specifier_for_install,
)
from pipx.pipx_metadata_file import JsonEncoderHandlesPath, PackageInfo, PipxMetadata
from pipx.util import PipxError
from pipx.venv import Venv, VenvContainer

# TODO: exit code accurate
# TODO: refuse to install_spec on frozen spec if pip_args differ between
#       main package and injected
# TODO: how to handle local paths on install


def _package_info_modify_package_or_url_(
    original_package_info: PackageInfo, package_or_url
) -> PackageInfo:
    # restore original package_or_url in metadata if freeze_data after install
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
    venv_metadata: PipxMetadata, freeze_data: Optional[Dict[str, str]], verbose: bool,
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
                freeze_data[package], []
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

    for (_injected_name, injected_package,) in venv_metadata.injected_packages.items():
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


def _unfreeze_install_deps(
    venv: Venv, venv_metadata: PipxMetadata, freeze_data: Dict[str, str], force: bool
) -> None:
    user_installed_packages = []
    user_installed_packages.append(venv_metadata.main_package.package)
    for _injected_name, injected_package in venv_metadata.injected_packages.items():
        if injected_package.package is None:
            # TODO: handle this better
            raise PipxError("Internal Error with pipx.")
        user_installed_packages.append(injected_package.package)
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

        # TODO: determine proper pip_args for each dep!  How??
        #       In the meantime just use main_package pip_args
        for (_package_name, package_spec) in freeze_dep_data.items():
            cmd = ["install"] + venv_metadata.main_package.pip_args + [package_spec]
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
# TODO: pip freeze will provide a bogus git address specification if package is a
#       local path and/or editable.  Attempt to use full local path instead.
def _install_from_metadata(
    venv_metadata: PipxMetadata,
    venv_container: VenvContainer,
    python: str,
    freeze_data: Optional[Dict[str, str]],
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
        main_package_or_url = freeze_data[venv_metadata.main_package.package]
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
            injected_package_or_url = freeze_data[injected_package.package]
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
) -> int:
    input_file = Path(in_filename)
    with open(input_file, "r") as pipx_spec_fh:
        spec_metadata = json.load(pipx_spec_fh)
    for venv_name in spec_metadata:
        # if venv_name in venv_container:
        #   continue
        venv_dir = venv_container.get_venv_dir(venv_name)
        venv_metadata = PipxMetadata(venv_dir, read=False)
        venv_metadata.from_dict(spec_metadata[venv_name]["metadata"])
        _install_from_metadata(
            venv_metadata,
            venv_container,
            python,
            spec_metadata[venv_name].get("pip_freeze", None),
            force,
            verbose,
        )

    return 0


# TODO: handle venvs with no metadata
# TODO: handle venvs with different version metadata
def export_spec(
    out_filename: str,
    venv_container: VenvContainer,
    skip_list: List[str],
    include_list: Optional[List[str]],
    freeze: bool,
    verbose: bool,
) -> int:
    dirs: Collection[Path] = sorted(venv_container.iter_venv_dirs())
    if not dirs:
        print(f"nothing has been installed with pipx {sleep}")
        return 0

    venv_container.verify_shared_libs()
    spec_metadata: Dict[str, Any] = {}

    for venv_dir in sorted(venv_container.iter_venv_dirs()):
        if venv_dir.name in skip_list:
            continue
        if include_list is not None and venv_dir.name not in include_list:
            continue
        spec_metadata[venv_dir.name] = {}
        venv_metadata = PipxMetadata(venv_dir).to_dict()
        spec_metadata[venv_dir.name]["metadata"] = venv_metadata
        if freeze:
            venv = Venv(venv_dir)
            pip_freeze_dict = {}
            for specifier in venv.pip_freeze():
                package = parse_pip_freeze_specifier(specifier)
                pip_freeze_dict[package] = specifier
            spec_metadata[venv_dir.name]["pip_freeze"] = pip_freeze_dict

    with open(out_filename, "w") as pipx_export_fh:
        json.dump(
            spec_metadata,
            pipx_export_fh,
            indent=4,
            sort_keys=True,
            cls=JsonEncoderHandlesPath,
        )

    return 0
