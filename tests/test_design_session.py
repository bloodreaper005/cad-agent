from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest

from mech_cad_design_agent.config import DesignSettings
from mech_cad_design_agent.hashing import file_sha256
from mech_cad_design_agent.design_session import (
    DesignSessionService,
    HostValidationError,
)
from mech_cad_design_agent.secure_fs import (
    FileIdentity,
    ManagedPath,
    same_managed_path,
)




def _host_validation(model: Path, nonce: str) -> dict[str, object]:
    """Stand in for the packaged validator, which needs a real FreeCAD.

    Returns what validate_model.py returns for a healthy model, so the nonce and
    digest binding record_result performs is exercised rather than bypassed.
    """
    return {
        "schema_version": "MechanicalDesignModelValidation/v1",
        "status": "valid",
        "nonce": nonce,
        "sha256": file_sha256(model),
        "size_bytes": model.stat().st_size,
        "document_name": model.stem,
        "object_count": 1,
        "recomputed": True,
    }


def _png_bytes(width: int = 640, height: int = 480) -> bytes:
    """A minimal but structurally real PNG, so evidence checks see a render."""
    import struct as _struct, zlib as _zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            _struct.pack(">I", len(data))
            + body
            + _struct.pack(">I", _zlib.crc32(body) & 0xFFFFFFFF)
        )

    header = _struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IEND", b"")


def _fcstd(*, object_name: str | None = None) -> bytes:
    object_xml = (
        f'<Object type="Part::Feature" name="{object_name}"/>'
        if object_name is not None
        else ""
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Document SchemaVersion="4" ProgramVersion="1.1.3">'
        f"<ObjectData>{object_xml}</ObjectData>"
        "</Document>"
    ).encode("utf-8")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Document.xml", document)
    return output.getvalue()


def _settings(tmp_path: Path) -> DesignSettings:
    return DesignSettings(
        workspace=tmp_path,
        package_root=tmp_path / "package",
        design_root=tmp_path / "designs",
        freecadcmd=tmp_path / "FreeCADCmd",
        freecadcmd_sha256="a" * 64,
        freecadcmd_identity=FileIdentity(volume=1, file_index=2),
        freecadcmd_version="1.1.3",
    )


def _service(
    tmp_path: Path, *, model_validator: object | None = None
) -> DesignSessionService:
    def create_seed(destination: Path) -> None:
        destination.write_bytes(_fcstd())

    def normalize_source(source: Path, destination: Path) -> None:
        del source
        destination.write_bytes(_fcstd(object_name="NormalizedSource"))

    return DesignSessionService(
        _settings(tmp_path),
        seed_creator=create_seed,
        source_normalizer=normalize_source,
        model_validator=model_validator or _host_validation,
    )


def _start(service: DesignSessionService, **overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "design_id": "basketball-carrier",
        "title": "Basketball Carrier",
        "model_classification": "new_design",
        "requirements": {"capacity": 4, "units": "mm"},
        "proposal_summary": "One-piece PLA carrier",
        "approval_text": "approved",
        "source_path": None,
    }
    arguments.update(overrides)
    return service.start(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("approval_text", "state"),
    [("no", "REJECT"), ("maybe", "UNCLEAR")],
)
def test_reject_and_unclear_do_not_create_state(
    tmp_path: Path, approval_text: str, state: str
) -> None:
    service = _service(tmp_path)

    result = _start(service, approval_text=approval_text)

    assert result["approval_state"] == state
    assert result["status"] == "not_started"
    assert not (tmp_path / "designs").exists()


def test_new_design_creates_local_session_without_database(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = _start(service)

    root = tmp_path / "designs" / "basketball-carrier"
    state = json.loads((root / "design.json").read_text(encoding="utf-8"))
    assert result["status"] == "approved"
    assert same_managed_path(Path(str(result["model_path"])), root / "model.FCStd")
    assert state["schema_version"] == "DesignSession/v1"
    assert state["model_classification"] == "new_design"
    assert state["model_status"] == "approved"
    assert state["direction_approval"] == {
        "state": "APPROVE",
        "text": "approved",
    }
    assert state["final_confirmation"]["state"] == "not_confirmed"
    assert state["lesson_review"]["status"] == "not_evaluated"
    assert state["knowledge"]["status"] == "not_executed"
    assert state["model"]["seed_sha256"] == file_sha256(root / "model.FCStd")
    assert (root / "validation").is_dir()
    assert (root / "output").is_dir()


def test_design_root_containment_uses_backend_canonical_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    canonical_workspace = tmp_path / "canonical-workspace"
    canonical_root = canonical_workspace / "designs"

    monkeypatch.setattr(
        "mech_cad_design_agent.design_session.validate_managed_path",
        lambda path, allow_missing_leaf: ManagedPath(
            canonical_workspace,
            FileIdentity(volume=1, file_index=10),
            canonical_workspace,
        ),
    )

    def relative(path: Path, root: Path, *, allow_missing_leaf: bool) -> Path:
        assert path == tmp_path / "designs"
        assert root == canonical_workspace
        assert allow_missing_leaf is True
        return Path("designs")

    monkeypatch.setattr(
        "mech_cad_design_agent.design_session.relative_managed_path",
        relative,
    )
    monkeypatch.setattr(
        "mech_cad_design_agent.design_session.ensure_managed_directory",
        lambda path, parents, exist_ok: ManagedPath(
            canonical_root,
            FileIdentity(volume=1, file_index=11),
            canonical_workspace,
        ),
    )

    assert service._ensure_root() == canonical_root


def test_start_is_idempotent_for_matching_design(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _start(service)

    second = _start(service, approval_text="yes")

    assert second["resumed"] is True
    assert second["model_path"] == first["model_path"]
    state = json.loads(
        (tmp_path / "designs" / "basketball-carrier" / "design.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["direction_approval"]["text"] == "approved"


def test_start_rejects_an_incompatible_existing_design(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _start(service)

    with pytest.raises(ValueError, match="different design intent"):
        _start(service, requirements={"capacity": 6, "units": "mm"})


@pytest.mark.parametrize("design_id", ["../escape", "bad/id", "", "空白"])
def test_start_rejects_unsafe_design_ids(tmp_path: Path, design_id: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        _start(_service(tmp_path), design_id=design_id)


def test_unicode_title_and_requirements_round_trip(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _start(
        service,
        design_id="carrier-01",
        title="篮球收纳框",
        requirements={"说明": "四个篮球", "路径": "有 空格"},
        approval_text="同意",
    )

    state = service.get("carrier-01")

    assert state["title"] == "篮球收纳框"
    assert state["requirements"]["说明"] == "四个篮球"


def test_new_design_can_reuse_only_a_shape_free_fcstd_seed(tmp_path: Path) -> None:
    empty = tmp_path / "empty.FCStd"
    empty.write_bytes(_fcstd())
    service = _service(tmp_path)

    _start(service, source_path=str(empty))

    state = service.get("basketball-carrier")
    assert state["model"]["seed_sha256"] == file_sha256(empty)
    assert state["model"]["source_relative_path"] is None

    # Real FreeCAD stores a part's geometry as a nonempty BREP member, so a
    # seed that already carries a part looks like this on disk.
    shaped = tmp_path / "shaped.FCStd"
    shaped.write_bytes(
        _fcstd_with_breps({"ExistingPart.Shape.brp": b"DBRep_DrawableShape\n"})
    )
    with pytest.raises(ValueError, match="shape-free"):
        _start(
            service,
            design_id="another-design",
            source_path=str(shaped),
        )


def test_existing_fcstd_is_snapshotted_and_never_edited(tmp_path: Path) -> None:
    source = tmp_path / "Original Model.FCStd"
    source.write_bytes(_fcstd(object_name="OriginalPart"))
    original = source.read_bytes()
    service = _service(tmp_path)

    result = _start(
        service,
        design_id="existing-model",
        model_classification="existing_model",
        source_path=str(source),
    )

    root = tmp_path / "designs" / "existing-model"
    state = service.get("existing-model")
    snapshot = root / str(state["model"]["source_relative_path"])
    assert result["status"] == "approved"
    assert snapshot.read_bytes() == original
    assert source.read_bytes() == original
    assert (root / "model.FCStd").read_bytes() == original
    assert state["model"]["source_sha256"] == file_sha256(source)


def test_existing_step_uses_normalizer_and_preserves_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;ENDSEC;END-ISO-10303-21;", encoding="ascii")
    service = _service(tmp_path)

    _start(
        service,
        design_id="step-design",
        model_classification="existing_model",
        source_path=str(source),
    )

    root = tmp_path / "designs" / "step-design"
    state = service.get("step-design")
    assert (root / str(state["model"]["source_relative_path"])).read_text(
        encoding="ascii"
    ) == source.read_text(encoding="ascii")
    assert (root / "model.FCStd").read_bytes() == _fcstd(
        object_name="NormalizedSource"
    )


def _write_validation(
    root: Path,
    *,
    working_sha256: str,
    status: str = "passed",
    include_artifacts: bool = True,
) -> tuple[Path, list[Path]]:
    report = root / "validation" / "model_validation.json"
    report.write_text(
        json.dumps(
            {
                "status": status,
                "schema_version": 1,
                "validator": "freecad-model-validation",
                "working_sha256": working_sha256,
                "checks": [
                    {
                        "id": _check_id,
                        "validator": "freecad-model-validation",
                        "status": "passed",
                        "message": "ok",
                        "mandatory": True,
                    }
                    for _check_id in (
                        "file.exists",
                        "document.open",
                        "document.recompute",
                        "document.geometry",
                    )
                ] + [
                    {
                        "id": "shape-validity",
                        "validator": "freecad-model-validation",
                        "status": "passed" if status == "passed" else "failed",
                        "message": "shape is valid" if status == "passed" else "invalid",
                        "mandatory": True,
                    }
                ],
                "fastener_inventory": [],
                "summary": {
                    "total": 5,
                    "passed": 5 if status == "passed" else 4,
                    "failed": 0 if status == "passed" else 1,
                    "warnings": 0,
                    "fasteners_detected": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    markdown = root / "validation" / "model_validation.md"
    image = root / "validation" / "model_validation.png"
    if include_artifacts:
        markdown.write_text("# Validation\n", encoding="utf-8")
        image.write_bytes(_png_bytes())
    return report, [markdown, image]


def test_passed_same_hash_validation_completes_session(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = _start(service)
    model = Path(str(result["model_path"]))
    model.write_bytes(_fcstd(object_name="Carrier"))
    report, evidence = _write_validation(
        model.parent, working_sha256=file_sha256(model)
    )

    recorded = service.record_result(
        design_id="basketball-carrier",
        model_path=str(model),
        validation_report_path=str(report),
        evidence_paths=[str(path) for path in evidence],
    )

    assert recorded["status"] == "completed"
    state = service.get("basketball-carrier")
    assert state["model"]["sha256"] == file_sha256(model)
    assert state["validation"]["status"] == "passed"


def test_host_validation_is_bound_to_the_recorded_model(tmp_path: Path) -> None:
    """The nonce and digest the host issued must come back unchanged."""
    seen: list[str] = []

    def validator(model: Path, nonce: str) -> dict[str, object]:
        seen.append(nonce)
        return _host_validation(model, nonce)

    service = _service(tmp_path, model_validator=validator)
    result = _start(service)
    model = Path(str(result["model_path"]))
    model.write_bytes(_fcstd(object_name="Carrier"))
    report, evidence = _write_validation(
        model.parent, working_sha256=file_sha256(model)
    )

    recorded = service.record_result(
        design_id="basketball-carrier",
        model_path=str(model),
        validation_report_path=str(report),
        evidence_paths=[str(path) for path in evidence],
    )

    assert recorded["status"] == "completed"
    assert len(seen) == 1 and len(seen[0]) >= 32
    evidence_recorded = service.get("basketball-carrier")["validation"]["host_evidence"]
    assert evidence_recorded["nonce"] == seen[0]
    assert evidence_recorded["sha256"] == file_sha256(model)


@pytest.mark.parametrize(
    "breakage, expected_warning",
    [
        ("nonce", "nonce mismatch"),
        ("digest", "different model bytes"),
        ("unavailable", "did not confirm the model"),
    ],
)
def test_a_passing_report_still_fails_without_host_evidence(
    tmp_path: Path, breakage: str, expected_warning: str
) -> None:
    """An agent-written report that passes cannot complete on its own.

    Each case is a host validator that declines to corroborate the report: one
    that answers a different nonce, one that describes different bytes, and one
    that cannot run at all.
    """

    def validator(model: Path, nonce: str) -> dict[str, object]:
        if breakage == "unavailable":
            raise HostValidationError("FreeCAD is not available")
        payload = _host_validation(model, nonce)
        if breakage == "nonce":
            payload["nonce"] = "0" * 64
        else:
            payload["sha256"] = "0" * 64
        return payload

    service = _service(tmp_path, model_validator=validator)
    result = _start(service)
    model = Path(str(result["model_path"]))
    model.write_bytes(_fcstd(object_name="Carrier"))
    report, evidence = _write_validation(
        model.parent, working_sha256=file_sha256(model)
    )

    recorded = service.record_result(
        design_id="basketball-carrier",
        model_path=str(model),
        validation_report_path=str(report),
        evidence_paths=[str(path) for path in evidence],
    )

    assert recorded["status"] == "needs_attention"
    state = service.get("basketball-carrier")
    assert state["validation"]["status"] == "incomplete"
    assert expected_warning in state["validation"]["warning"]
    assert state["validation"]["host_evidence"] is None


@pytest.mark.parametrize("failure", ["failed", "stale", "missing-evidence"])
def test_invalid_validation_sets_needs_attention(
    tmp_path: Path, failure: str
) -> None:
    service = _service(tmp_path)
    result = _start(service)
    model = Path(str(result["model_path"]))
    model.write_bytes(_fcstd(object_name="Carrier"))
    report, evidence = _write_validation(
        model.parent,
        working_sha256=("0" * 64 if failure == "stale" else file_sha256(model)),
        status=("failed" if failure == "failed" else "passed"),
        include_artifacts=failure != "missing-evidence",
    )

    recorded = service.record_result(
        design_id="basketball-carrier",
        model_path=str(model),
        validation_report_path=str(report),
        evidence_paths=[str(path) for path in evidence],
    )

    assert recorded["status"] == "needs_attention"
    assert service.get("basketball-carrier")["model_status"] == "needs_attention"


def test_result_paths_must_remain_in_the_session(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = _start(service)
    model = Path(str(result["model_path"]))
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the design session"):
        service.record_result(
            design_id="basketball-carrier",
            model_path=str(model),
            validation_report_path=str(outside),
            evidence_paths=[],
        )


def test_changed_model_invalidates_a_completed_session(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = _start(service)
    model = Path(str(result["model_path"]))
    model.write_bytes(_fcstd(object_name="CarrierV1"))
    report, evidence = _write_validation(
        model.parent, working_sha256=file_sha256(model)
    )
    service.record_result(
        design_id="basketball-carrier",
        model_path=str(model),
        validation_report_path=str(report),
        evidence_paths=[str(path) for path in evidence],
    )

    model.write_bytes(_fcstd(object_name="CarrierV2"))
    state = service.get("basketball-carrier")

    assert state["model_status"] == "needs_attention"
    assert state["validation"]["status"] == "stale"


def _fcstd_with_breps(members: dict[str, bytes]) -> bytes:
    """Build an FCStd whose objects own the given BREP shape payloads."""
    objects = "".join(
        f'<Object type="Part::Feature" name="{name.split(".")[0]}"/>' for name in members
    )
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "Document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Document SchemaVersion="4"><Objects>'
            f"{objects}</Objects></Document>",
        )
        for name, payload in members.items():
            archive.writestr(name, payload)
    return output.getvalue()


def test_a_metadata_only_seed_is_shape_free() -> None:
    """The packaged seed creator adds one shapeless audit object on purpose.

    FreeCAD writes an empty BREP member for an object with no shape, so a seed
    that carries only metadata must still count as shape-free.
    """
    from mech_cad_design_agent.design_session import _shape_free_fcstd

    seed = _fcstd_with_breps({"DesignAudit.Shape.brp": b""})
    assert _shape_free_fcstd(seed) is True


def test_real_geometry_is_not_shape_free() -> None:
    from mech_cad_design_agent.design_session import _shape_free_fcstd

    modelled = _fcstd_with_breps({"Block.Shape.brp": b"DBRep_DrawableShape\n\n"})
    assert _shape_free_fcstd(modelled) is False


def test_geometry_is_detected_beside_a_shapeless_object() -> None:
    from mech_cad_design_agent.design_session import _shape_free_fcstd

    mixed = _fcstd_with_breps(
        {"DesignAudit.Shape.brp": b"", "Block.Shape.brp": b"DBRep_DrawableShape\n"}
    )
    assert _shape_free_fcstd(mixed) is False


def test_new_design_rejects_a_seed_creator_that_writes_geometry(
    tmp_path: Path,
) -> None:
    service = DesignSessionService(
        _settings(tmp_path),
        seed_creator=lambda destination: destination.write_bytes(
            _fcstd_with_breps({"Block.Shape.brp": b"DBRep_DrawableShape\n"})
        ),
    )
    with pytest.raises(ValueError, match="shape-free"):
        service.start(
            design_id="seeded",
            title="Seeded",
            model_classification="new_design",
            requirements={},
            proposal_summary="p",
            approval_text="yes",
        )
