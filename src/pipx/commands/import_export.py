import json
from pathlib import Path
from typing import Callable, Collection, Optional

from pipx.commands.inject import inject
from pipx.commands.install import install
from pipx.constants import LOCAL_BIN_DIR
from pipx.emojies import sleep
from pipx.pipx_metadata_file import JsonEncoderHandlesPath, PipxMetadata
from pipx.util import PipxError
from pipx.venv import Venv, VenvContainer

Pool: Optional[Callable]
try:
    import multiprocessing.synchronize  # noqa: F401
    from multiprocessing import Pool
except ImportError:
    Pool = None

# TODO: skip venvs, specify venvs
# TODO: handle venvs with no metadata
# TODO: handle venvs with different version metadata
# TODO: exit code accurate
# TODO: optional --freeze switch for fully-frozen versions of all packages in venv


def _get_venv_info(venv_dir: Path):
    venv_metadata = PipxMetadata(venv_dir).to_dict()
    venv_name = venv_dir.name
    return venv_name, venv_metadata


def export_json(out_filename: str, venv_container: VenvContainer) -> int:
    dirs: Collection[Path] = sorted(venv_container.iter_venv_dirs())
    if not dirs:
        print(f"nothing has been installed with pipx {sleep}")
        return 0

    venv_container.verify_shared_libs()
    all_venv_metadata = {}
    if Pool:
        with Pool() as p:
            for (venv_name, venv_metadata) in p.map(_get_venv_info, dirs,):
                all_venv_metadata[venv_name] = venv_metadata
    else:
        for (venv_name, venv_metadata) in map(_get_venv_info, dirs,):
            all_venv_metadata[venv_name] = venv_metadata

    with open(out_filename, "w") as pipx_export_fh:
        json.dump(
            all_venv_metadata,
            pipx_export_fh,
            indent=4,
            sort_keys=True,
            cls=JsonEncoderHandlesPath,
        )

    return 0


# TODO: handle venv directories that collide (already exist and will be installed)


# Based on reinstall-all without the uninstall
def _install_from_metadata(
    venv_metadata: PipxMetadata,
    venv_container: VenvContainer,
    python: str,
    verbose: bool,
    force: bool,
):
    if venv_metadata.main_package.package_or_url is None:
        # TODO: handle this better
        raise PipxError("Internal Error with pipx.")

    venv_dir = venv_metadata.venv_dir
    venv = Venv(venv_dir)

    # install main package first
    install(
        venv_dir=venv_dir,
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


# TODO: how to handle json python mismatch with python argument
def install_json(
    in_filename: str,
    venv_container: VenvContainer,
    python: str,
    verbose: bool,
    force: bool,
) -> int:
    input_file = Path(in_filename)
    with open(input_file, "r") as pipx_spec_fh:
        install_config = json.load(pipx_spec_fh)
    for venv_name in install_config:
        # if venv_name in venv_container:
        #   continue
        venv_dir = venv_container.get_venv_dir(venv_name)
        venv_metadata = PipxMetadata(venv_dir, read=False)
        venv_metadata.from_dict(install_config[venv_name])
        _install_from_metadata(venv_metadata, venv_container, python, verbose, force)

    return 0
