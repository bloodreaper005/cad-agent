"""Run the interface validators headlessly against a real built model.

Gated on MECH_DESIGN_FREECADCMD, like the other FreeCAD-dependent suites.

These two validators imported FreeCADGui and drove `activeView()`, which tied
them to a GUI session an agent was steering and left them unreachable from the
product: packaged, digest-pinned, and callable by nothing. The geometry work
was always Part/OCCT and headless-capable; only the PNG render needed the GUI,
and visual review is a separate step of the validation workflow anyway.

So the point of this suite is not that the numbers are plausible. It is that
these files now run under FreeCADCmd through the pinned runner at all, and that
they still fail the things they are supposed to fail once they do.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from mech_cad_design_agent.gear_sizing import (
    GearDriveInput,
    size_gear_drive,
    to_json,
)
from mech_cad_design_agent.package_resources import freecad_scripts_directory


FREECADCMD = os.environ.get("MECH_DESIGN_FREECADCMD", "").strip()

# Named rather than derived from the script name: the report stem is singular
# where the script name is plural, and a derivation that silently produces the
# wrong path turns a real failure into a missing file.
_REPORT_NAMES = {
    "validate_mechanical_interfaces.py": "mechanical-interface-validation.json",
    "validate_fastener_interfaces.py": "fastener-interface-validation.json",
}


def _sizing() -> dict:
    result = size_gear_drive(
        GearDriveInput(
            power_kw=7.5,
            pinion_speed_rpm=1450.0,
            gear_speed_rpm=480.0,
            pinion_material="20MnCr5_carburised_G2",
            gear_material="20MnCr5_carburised_G2",
            duty="moderate",
            life_hours=20000.0,
            safety_factor=1.5,
        )
    )
    assert result.status == "sized"
    return json.loads(to_json(result))


@unittest.skipUnless(
    FREECADCMD,
    "MECH_DESIGN_FREECADCMD is not configured; live interface validation skipped",
)
class InterfaceValidationTests(unittest.TestCase):
    def _freecadcmd(self) -> Path:
        return Path(FREECADCMD).expanduser().resolve(strict=True)

    def _build_pair(self, workspace: Path) -> Path:
        """Build the same verified gear pair the geometry suite builds."""
        model = workspace / "model.FCStd"
        sizing_path = workspace / "sizing.json"
        sizing_path.write_text(json.dumps(_sizing()), encoding="utf-8")
        with freecad_scripts_directory() as scripts:
            builder = str(scripts / "create_gear_pair.py")
            seed = "\n".join(
                [
                    "import FreeCAD as App",
                    "document = App.newDocument('Seed')",
                    "document.saveAs(" + repr(str(model)) + ")",
                    "App.closeDocument(document.Name)",
                    "namespace = dict()",
                    "source = open(" + repr(builder) + ", 'rb').read()",
                    "exec(compile(source, 'create_gear_pair.py', 'exec'), namespace)",
                    "namespace['build']("
                    + repr(str(model))
                    + ", "
                    + repr(str(sizing_path))
                    + ")",
                ]
            )
            built = subprocess.run(
                [str(self._freecadcmd()), "-c", seed],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        self.assertEqual(
            built.returncode, 0, f"gear build failed: {(built.stderr or '')[-1500:]}"
        )
        return model

    def _validate(
        self, script_name: str, model: Path, specification: dict, out: Path
    ) -> dict | None:
        spec_path = out.parent / f"{script_name}-spec.json"
        spec_path.write_text(json.dumps(specification), encoding="utf-8")
        with freecad_scripts_directory() as scripts:
            script = str(scripts / script_name)
            driver = "\n".join(
                [
                    "import sys",
                    "sys.argv = ["
                    + ", ".join(
                        repr(value)
                        for value in (script, str(model), str(spec_path), str(out))
                    )
                    + "]",
                    "source = open(" + repr(script) + ", 'rb').read()",
                    "exec(compile(source, "
                    + repr(script_name)
                    + ", 'exec'), {'__name__': '__main__'})",
                ]
            )
            completed = subprocess.run(
                [str(self._freecadcmd()), "-c", driver],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            f"{script_name} failed headlessly: {(completed.stderr or '')[-1500:]}",
        )
        report = out / _REPORT_NAMES[script_name]
        if not report.is_file():
            return None
        return json.loads(report.read_text(encoding="utf-8"))

    def test_mechanical_interfaces_run_without_a_gui_and_measure_the_mesh(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mech-iface-") as raw:
            workspace = Path(raw)
            model = self._build_pair(workspace)
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            report = self._validate(
                "validate_mechanical_interfaces.py",
                model,
                {
                    "schema_version": "MechanicalInterfaceSpec/v1",
                    "working_sha256": digest,
                    "spatial_interfaces": [
                        {
                            "id": "mesh",
                            "a": "Pinion",
                            "b": "Gear",
                            "maximum_common_volume_mm3": 1.0,
                            "minimum_distance_mm": 0.0,
                        }
                    ],
                },
                workspace / "out",
            )
            assert report is not None
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["working_sha256"], digest)
            # No render, and the report says so rather than naming a PNG that
            # was never written.
            self.assertIsNone(report["render"])
            self.assertEqual(sorted(report["artifacts"]), ["json", "markdown"])
            mesh = report["interfaces"][0]
            self.assertEqual(mesh["status"], "passed")
            self.assertLess(mesh["common_volume_mm3"], 1.0)
            self.assertGreater(mesh["distance_mm"], 0.0)

    def test_a_stale_specification_fails_the_hash_check(self) -> None:
        """The port must not have dropped the binding to the model it measures."""
        with tempfile.TemporaryDirectory(prefix="mech-iface-") as raw:
            workspace = Path(raw)
            model = self._build_pair(workspace)
            report = self._validate(
                "validate_mechanical_interfaces.py",
                model,
                {
                    "schema_version": "MechanicalInterfaceSpec/v1",
                    "working_sha256": "0" * 64,
                    "spatial_interfaces": [],
                },
                workspace / "out",
            )
            assert report is not None
            self.assertEqual(report["status"], "failed")
            failed = {
                item["id"] for item in report["checks"] if item["status"] == "failed"
            }
            self.assertIn("model.hash", failed)

    def test_a_demanded_contact_the_mesh_cannot_meet_fails(self) -> None:
        """A real geometric requirement the built model genuinely violates."""
        with tempfile.TemporaryDirectory(prefix="mech-iface-") as raw:
            workspace = Path(raw)
            model = self._build_pair(workspace)
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            report = self._validate(
                "validate_mechanical_interfaces.py",
                model,
                {
                    "schema_version": "MechanicalInterfaceSpec/v1",
                    "working_sha256": digest,
                    "spatial_interfaces": [
                        {
                            "id": "mesh",
                            "a": "Pinion",
                            "b": "Gear",
                            "maximum_distance_mm": 0.001,
                        }
                    ],
                },
                workspace / "out",
            )
            assert report is not None
            self.assertEqual(report["status"], "failed")
            failed = {
                item["id"] for item in report["checks"] if item["status"] == "failed"
            }
            self.assertIn("mesh.maximum-distance", failed)

    def test_a_missing_object_is_reported_rather_than_crashing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mech-iface-") as raw:
            workspace = Path(raw)
            model = self._build_pair(workspace)
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            report = self._validate(
                "validate_mechanical_interfaces.py",
                model,
                {
                    "schema_version": "MechanicalInterfaceSpec/v1",
                    "working_sha256": digest,
                    "spatial_interfaces": [
                        {"id": "ghost", "a": "Pinion", "b": "NoSuchPart"}
                    ],
                },
                workspace / "out",
            )
            assert report is not None
            self.assertEqual(report["status"], "failed")
            failed = {
                item["id"] for item in report["checks"] if item["status"] == "failed"
            }
            self.assertIn("ghost.objects", failed)

    def test_fastener_interfaces_also_run_without_a_gui(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mech-iface-") as raw:
            workspace = Path(raw)
            model = self._build_pair(workspace)
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            report = self._validate(
                "validate_fastener_interfaces.py",
                model,
                {
                    "schema_version": "FastenerInterfaceSpec/v1",
                    "working_sha256": digest,
                    "interfaces": [],
                },
                workspace / "out",
            )
            assert report is not None
            self.assertEqual(report["status"], "passed")
            self.assertIsNone(report["render"])

    def test_validation_leaves_the_model_byte_identical(self) -> None:
        """A validator that edits what it measures is worse than none."""
        with tempfile.TemporaryDirectory(prefix="mech-iface-") as raw:
            workspace = Path(raw)
            model = self._build_pair(workspace)
            before = hashlib.sha256(model.read_bytes()).hexdigest()
            self._validate(
                "validate_mechanical_interfaces.py",
                model,
                {
                    "schema_version": "MechanicalInterfaceSpec/v1",
                    "working_sha256": before,
                    "spatial_interfaces": [
                        {"id": "mesh", "a": "Pinion", "b": "Gear"}
                    ],
                },
                workspace / "out",
            )
            self.assertEqual(hashlib.sha256(model.read_bytes()).hexdigest(), before)
