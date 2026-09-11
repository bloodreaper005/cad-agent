"""Build a real gear pair in FreeCAD and check the geometry is sound.

Gated on MECH_DESIGN_FREECADCMD, like the other FreeCAD-dependent suites, so
it skips where no reviewed FreeCADCmd is configured.

This covers the one thing the pure-Python gear tests cannot: that the packaged
`create_gear_pair.py` turns a sizing result into geometry that is actually
valid and actually meshes. Two defects found during development were invisible
to every other check -- a mesh phase that put a tooth where a space belonged,
and flanks with no backlash -- and both produced a model that looked correct
while interfering by hundreds of cubic millimetres at the correct centre
distance. Only measuring the built solids catches that class of error.
"""

from __future__ import annotations

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

# Interference at the correct centre distance should be zero. The tolerance
# leaves room for the polygonal approximation of the involute flank without
# admitting a real tooth clash, which runs to hundreds of cubic millimetres.
MAX_INTERFERENCE_MM3 = 1.0

_PROBE = """
import json
import FreeCAD as App

document = App.openDocument({model!r})
pinion = document.getObject("Pinion")
gear = document.getObject("Gear")
common = pinion.Shape.common(gear.Shape)
print("GEARPROBE " + json.dumps({{
    "pinion_solids": len(pinion.Shape.Solids),
    "gear_solids": len(gear.Shape.Solids),
    "pinion_valid": bool(pinion.Shape.isValid()),
    "gear_valid": bool(gear.Shape.isValid()),
    "pinion_volume": float(pinion.Shape.Volume),
    "gear_volume": float(gear.Shape.Volume),
    "interference_mm3": float(common.Volume),
    "pinion_module": float(pinion.Module),
    "pinion_teeth": float(pinion.Teeth),
    "gear_teeth": float(gear.Teeth),
    "centre_distance": float(gear.CentreDistanceMM),
    "gear_placement_x": float(gear.Placement.Base.x),
}}))
App.closeDocument(document.Name)
"""


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
    "MECH_DESIGN_FREECADCMD is not configured; live gear geometry test skipped",
)
class GearGeometryTests(unittest.TestCase):
    def _build(self, sizing: dict) -> dict:
        freecadcmd = Path(FREECADCMD).expanduser().resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="mech-gear-geometry-") as raw:
            workspace = Path(raw)
            model = workspace / "model.FCStd"
            sizing_path = workspace / "sizing.json"
            sizing_path.write_text(json.dumps(sizing), encoding="utf-8")

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
                    [str(freecadcmd), "-c", seed],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                )
            self.assertEqual(
                built.returncode,
                0,
                f"gear build failed: {(built.stderr or '')[-1500:]}",
            )
            self.assertTrue(model.is_file(), "the build produced no model")

            probe = subprocess.run(
                [str(freecadcmd), "-c", _PROBE.format(model=str(model))],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr[-1500:])
            for line in (probe.stdout or "").splitlines():
                if line.startswith("GEARPROBE "):
                    return json.loads(line[len("GEARPROBE ") :])
            self.fail(f"probe produced no measurement: {probe.stdout[-1500:]}")

    def test_built_pair_is_two_valid_solids_that_mesh(self) -> None:
        sizing = _sizing()
        measured = self._build(sizing)

        self.assertEqual(measured["pinion_solids"], 1)
        self.assertEqual(measured["gear_solids"], 1)
        self.assertTrue(measured["pinion_valid"])
        self.assertTrue(measured["gear_valid"])
        self.assertGreater(measured["pinion_volume"], 0.0)
        self.assertGreater(measured["gear_volume"], 0.0)
        self.assertLess(measured["gear_volume"], 1.0e9)

        # The gear must carry more material than the pinion; a swapped tooth
        # count would otherwise pass every other assertion here.
        self.assertGreater(measured["gear_volume"], measured["pinion_volume"])

    def test_teeth_mesh_rather_than_clash_at_the_sized_centre_distance(self) -> None:
        """The check that catches a wrong mesh phase or missing backlash."""
        measured = self._build(_sizing())
        self.assertLess(
            measured["interference_mm3"],
            MAX_INTERFERENCE_MM3,
            "the teeth overlap at the centre distance the sizing chose; "
            "a wrong mesh phase or missing backlash will show up here",
        )

    def test_geometry_carries_the_values_it_was_sized_from(self) -> None:
        sizing = _sizing()
        measured = self._build(sizing)
        geometry = sizing["derived"]["geometry"]
        teeth = sizing["derived"]["teeth"]

        self.assertAlmostEqual(
            measured["pinion_module"], geometry["module_mm"], places=6
        )
        self.assertEqual(int(measured["pinion_teeth"]), teeth["pinion"])
        self.assertEqual(int(measured["gear_teeth"]), teeth["gear"])
        self.assertAlmostEqual(
            measured["centre_distance"], geometry["centre_distance_mm"], places=6
        )
        self.assertAlmostEqual(
            measured["gear_placement_x"], geometry["centre_distance_mm"], places=6
        )

    def test_a_clashing_centre_distance_is_actually_detectable(self) -> None:
        """Prove the interference measurement can fail, not just pass.

        Without this, a probe that always reported zero would make the meshing
        test above look healthy while checking nothing.
        """
        sizing = _sizing()
        correct = sizing["derived"]["geometry"]["centre_distance_mm"]
        sizing["derived"]["geometry"]["centre_distance_mm"] = correct - 6.0
        measured = self._build(sizing)
        self.assertGreater(
            measured["interference_mm3"],
            MAX_INTERFERENCE_MM3,
            "pulling the gears together must drive the teeth into one another",
        )


if __name__ == "__main__":
    unittest.main()
