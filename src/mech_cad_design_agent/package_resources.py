from __future__ import annotations

from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
from typing import Iterator


# The reviewed SHA-256 of every FreeCAD script this package ships.
#
# These digests existed only in the test suite until now, which pinned them in
# CI and left the runtime free to execute whatever happened to be on disk. The
# executable was verified twice per run and the code it executed not once. This
# is the manifest `freecad_runner` checks before handing a script to FreeCAD,
# and the packaging test reads it from here rather than holding a second copy.
#
# A deliberate change to a packaged script updates its digest here in the same
# commit. That is the point: the value should be inconvenient to change by
# accident and trivial to change on purpose.
PACKAGED_SCRIPT_DIGESTS: dict[str, str] = {
    "create_empty_model.py": "f0bf474d56ff1652786a0e53c168ba83beadc8369af867322d3a4cdaf892a062",
    "create_gear_pair.py": "db9af785e87f6653c6f4b16561d55d717a28362badbda4f6397ea701fb70d4e6",
    "extract_model_manifest.py": "cc63c6d6a9281259bb238c5c8d118115f3fb99c03b6a3ea09863bbe0ecfb267d",
    "normalize_model.py": "295de05c0f86a0fafd69df4911e101e3aaa326be86b999816c8a461f74a39a04",
    "validate_external_step.py": "f069b4c32b82c3a9016ba95e6dc59ceee4749c0b0501087c2992410d717ec7cd",
    "validate_fastener_interfaces.py": "f447d6eb53b5715bceb056a38d64f0e539d35bc6adfb48a8c9b8d661fadfb6a1",
    "validate_mechanical_interfaces.py": "5bc857162ceeae569f17b02cb4db4c90e7c44cdc99f2a54f3add90003d923473",
    "validate_model.py": "e1ac0a683f15cf5c960476a33e7c29358057dd7272c618a6b3a06125baa01f96",
}


@contextmanager
def _resource_directory(name: str) -> Iterator[Path]:
    resource = files("mech_cad_design_agent").joinpath("resources", name)
    with as_file(resource) as root:
        yield root


@contextmanager
def validation_resources_directory() -> Iterator[Path]:
    with _resource_directory("validation") as root:
        yield root


@contextmanager
def freecad_scripts_directory() -> Iterator[Path]:
    with _resource_directory("freecad") as root:
        yield root


@contextmanager
def standard_part_provider_config() -> Iterator[Path]:
    with _resource_directory("config") as root:
        yield root / "standard_part_providers.json"
