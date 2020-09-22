import json
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional

from pipx.commands.inject import inject
from pipx.commands.install import install
from pipx.constants import LOCAL_BIN_DIR
from pipx.emojies import sleep
from pipx.package_specifier import parse_pip_freeze_specifier
from pipx.pipx_metadata_file import JsonEncoderHandlesPath, PipxMetadata
from pipx.util import PipxError
from pipx.venv import Venv, VenvContainer

# TODO: exit code accurate


# Based on reinstall-all without the uninstall
# TODO: install frozen versions
def _install_from_metadata(
    venv_metadata: PipxMetadata,
    venv_container: VenvContainer,
    python: str,
    freeze_data: Optional[Dict[str, str]],
    force: bool,
    verbose: bool,
):
    if venv_metadata.main_package.package_or_url is None:
        # TODO: handle this better
        raise PipxError("Internal Error with pipx.")

    venv_dir = venv_metadata.venv_dir
    venv = Venv(venv_dir)

    # install main package first
    if freeze_data is not None:
        pass

    install(
        venv_dir=venv_dir,
        package_name=None,  # TODO: delete this if install is updated
        package_spec=venv_metadata.main_package.package_or_url,
        local_bin_dir=LOCAL_BIN_DIR,
        python=python,
        pip_args=venv_metadata.main_package.pip_args,
        venv_args=venv_metadata.venv_args,
        verbose=verbose,
        force=force,
        include_dependencies=venv_metadata.main_package.include_dependencies,
        suffix=venv_metadata.main_package.suffix,
    )

    # now install injected packages
    for (
        injected_name,
        injected_package,
    ) in venv.pipx_metadata.injected_packages.items():
        if injected_package.package_or_url is None:
            # This should never happen, but package_or_url is type
            #   Optional[str] so mypy thinks it could be None
            raise PipxError(
                f"Internal Error injecting package {injected_package} into {venv_dir.name}"
            )
        inject(
            venv_dir,
            injected_name,
            injected_package.package_or_url,
            injected_package.pip_args,
            verbose=verbose,
            include_apps=injected_package.include_apps,
            include_dependencies=injected_package.include_dependencies,
            force=True,
        )


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


# TODO: how to handle json python mismatch with python argument
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
