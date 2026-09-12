from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import sys

from .bootstrap_diagnostics import DiagnosticGateError, blocked_response
from .freecad_bridge_security import inspect_bridge_settings
from .config import (
    POSTGRES_BACKEND,
    SQLITE_BACKEND,
    DesignSettings,
    KnowledgeSettings,
    StandardPartSettings,
    default_sqlite_path,
    is_postgres_url,
    normalize_backend,
)
from .freecad_discovery import (
    FreeCADCandidate,
    FreeCADDiscoveryError,
    run_freecad_version,
    validate_freecadcmd,
    validate_local_freecadcmd,
)
from .secure_fs import FileIdentity
from .standard_part_configuration import (
    load_standard_part_provider_catalog,
    load_standard_part_sources,
)
from .workspace_bootstrap import (
    BootstrapFailure,
    parse_selected_env_file,
    read_workspace_manifest,
)


@dataclass(frozen=True)
class BootstrapRuntime:
    cwd: Path
    environ: Mapping[str, str]
    workspace_override: Path | None = None
    freecad_command_override: Path | None = None
    freecad_sha256_override: str = ""

    @classmethod
    def from_process(
        cls,
        *,
        cwd: Path,
        environ: Mapping[str, str],
        workspace: str | Path | None = None,
        freecad_command: str | Path | None = None,
        freecad_sha256: str = "",
        **_: object,
    ) -> "BootstrapRuntime":
        effective_environment = dict(environ)
        selected_env = parse_selected_env_file(
            None, effective_environment, Path(cwd).resolve()
        )
        if selected_env is not None:
            for key, entry in selected_env.values.items():
                effective_environment.setdefault(key, entry.value)
        return cls(
            cwd=Path(cwd).resolve(),
            environ=effective_environment,
            workspace_override=(
                Path(workspace).expanduser().resolve() if workspace else None
            ),
            freecad_command_override=(
                Path(freecad_command).expanduser().resolve()
                if freecad_command
                else None
            ),
            freecad_sha256_override=freecad_sha256.strip().lower(),
        )

    def _workspace(self) -> Path:
        if self.workspace_override is not None:
            return self.workspace_override
        configured = str(self.environ.get("MECH_DESIGN_WORKSPACE", "")).strip()
        if configured:
            requested = Path(configured).expanduser()
            return (
                requested if requested.is_absolute() else self.cwd / requested
            ).resolve()
        for candidate in (self.cwd, *self.cwd.parents):
            if (candidate / "config" / "mechanical_design.json").is_file():
                return candidate
        return self.cwd

    def _manifest(self):
        try:
            return read_workspace_manifest(self._workspace())
        except (BootstrapFailure, OSError) as exc:
            response = blocked_response(
                capability="workspace",
                code=getattr(exc, "code", "WORKSPACE_NOT_INITIALIZED"),
                message="initialize a mechanical design workspace",
                diagnostics=self.status(include_freecad=False),
            )
            raise DiagnosticGateError(response) from None

    def _freecad_candidate(self, manifest: object) -> FreeCADCandidate:
        command_value = (
            self.freecad_command_override
            or (
                Path(str(self.environ["MECH_DESIGN_FREECADCMD"]))
                if str(self.environ.get("MECH_DESIGN_FREECADCMD", "")).strip()
                else None
            )
            or (Path(manifest.freecad_command) if manifest.freecad_command else None)
        )
        digest = (
            self.freecad_sha256_override
            or str(self.environ.get("MECH_DESIGN_FREECADCMD_SHA256", "")).strip().lower()
            or str(manifest.freecad_sha256 or "").lower()
        )
        if command_value is None or len(digest) != 64:
            raise FreeCADDiscoveryError(
                "FREECADCMD_NOT_CONFIGURED",
                "configure FreeCADCmd and its SHA-256",
            )
        validator = validate_freecadcmd if sys.platform == "win32" else validate_local_freecadcmd
        return validator(
            Path(command_value).expanduser().resolve(),
            source="configured",
            run_version=run_freecad_version,
            expected_sha256=digest,
        )

    def _knowledge_component(self, workspace: Path) -> dict[str, str]:
        try:
            backend, _url, sqlite_path = self.knowledge_backend(workspace)
        except (RuntimeError, ValueError) as exc:
            return {
                "name": "knowledge",
                "status": "setup_required",
                "code": "KNOWLEDGE_BACKEND_INVALID",
                "message": str(exc),
            }
        if backend == POSTGRES_BACKEND:
            return {
                "name": "knowledge",
                "status": "ok",
                "code": "KNOWLEDGE_CONFIGURED",
                "message": "PostgreSQL knowledge services are configured",
            }
        if sqlite_path.is_file():
            return {
                "name": "knowledge",
                "status": "ok",
                "code": "KNOWLEDGE_SQLITE_READY",
                "message": f"local SQLite knowledge store at {sqlite_path}",
            }
        return {
            "name": "knowledge",
            "status": "warning",
            "code": "KNOWLEDGE_SQLITE_NOT_INITIALIZED",
            "message": (
                "run 'mech-cad-design knowledge bootstrap' to create "
                f"{sqlite_path}; CAD can continue without it"
            ),
        }

    def status(self, *, include_freecad: bool = True) -> dict[str, object]:
        components: list[dict[str, str]] = []
        try:
            manifest = read_workspace_manifest(self._workspace())
        except Exception as exc:
            code = (
                "WORKSPACE_NOT_INITIALIZED"
                if not (
                    self._workspace() / "config" / "mechanical_design.json"
                ).is_file()
                else getattr(exc, "code", "WORKSPACE_INVALID")
            )
            components.append(
                {
                    "name": "workspace",
                    "status": "setup_required",
                    "code": code,
                    "message": "workspace is not initialized",
                }
            )
            return {
                "schema_version": "MechanicalDesignSystemStatus/v1",
                "status": {"overall": "setup_required"},
                "components": components,
            }
        components.append(
            {
                "name": "workspace",
                "status": "ok",
                "code": "WORKSPACE_READY",
                "message": str(manifest.workspace),
            }
        )
        if include_freecad:
            try:
                candidate = self._freecad_candidate(manifest)
                components.append(
                    {
                        "name": "freecadcmd",
                        "status": "ok",
                        "code": "FREECADCMD_READY",
                        "message": f"FreeCAD {candidate.version}",
                    }
                )
            except Exception as exc:
                components.append(
                    {
                        "name": "freecadcmd",
                        "status": "setup_required",
                        "code": getattr(exc, "code", "FREECADCMD_NOT_CONFIGURED"),
                        "message": "FreeCADCmd is not ready",
                    }
                )
            components.append(inspect_bridge_settings().as_component())
        components.append(self._knowledge_component(manifest.workspace))
        overall = (
            "setup_required"
            if any(item["status"] == "setup_required" for item in components)
            else "warning"
            if any(item["status"] == "warning" for item in components)
            else "ok"
        )
        return {
            "schema_version": "MechanicalDesignSystemStatus/v1",
            "status": {"overall": overall},
            "components": components,
        }

    def design_settings(self) -> DesignSettings:
        manifest = self._manifest()
        try:
            candidate = self._freecad_candidate(manifest)
        except Exception as exc:
            raise DiagnosticGateError(
                blocked_response(
                    capability="design",
                    code=getattr(exc, "code", "FREECADCMD_NOT_CONFIGURED"),
                    message="FreeCADCmd is required for design modeling",
                    diagnostics=self.status(),
                )
            ) from None
        bridge = inspect_bridge_settings()
        if not bridge.safe_to_proceed:
            raise DiagnosticGateError(
                blocked_response(
                    capability="design",
                    code=bridge.code,
                    message=bridge.message,
                    diagnostics=self.status(),
                )
            )
        return DesignSettings(
            workspace=manifest.workspace,
            package_root=manifest.workspace,
            design_root=manifest.workspace / "designs",
            freecadcmd=candidate.path,
            freecadcmd_sha256=candidate.sha256,
            freecadcmd_identity=candidate.identity,
            freecadcmd_version=candidate.version,
        )

    def design_inspection_settings(self) -> DesignSettings:
        """Build settings for read-only design inspection.

        Listing, reading, and resuming design jobs never launch FreeCAD, so the
        FreeCAD fields are deliberately unset placeholders. Never pass these
        settings to an operation that runs a FreeCAD script.
        """
        manifest = self._manifest()
        return DesignSettings(
            workspace=manifest.workspace,
            package_root=manifest.workspace,
            design_root=manifest.workspace / "designs",
            freecadcmd=manifest.workspace / ".freecad-not-configured",
            freecadcmd_sha256="",
            freecadcmd_identity=FileIdentity(0, 0),
            freecadcmd_version="",
        )

    def design_knowledge_scope(self) -> dict[str, str]:
        manifest = self._manifest()
        identity = manifest.raw.get("identity")
        if not isinstance(identity, Mapping):
            raise RuntimeError("knowledge scope is not configured")
        organization_id = str(identity.get("organization_id") or "").strip()
        design_group_id = str(identity.get("design_group_id") or "").strip()
        if not organization_id or not design_group_id:
            raise RuntimeError("knowledge scope is not configured")
        return {
            "organization_id": organization_id,
            "design_group_id": design_group_id,
        }

    def knowledge_backend(self, workspace: Path) -> tuple[str, str, Path]:
        """Resolve the knowledge backend, its URL, and its SQLite path."""
        database_url = str(self.environ.get("MECH_DESIGN_DATABASE_URL", "")).strip()
        requested = str(self.environ.get("MECH_DESIGN_KNOWLEDGE_BACKEND", "")).strip()
        backend = (
            normalize_backend(requested, "MECH_DESIGN_KNOWLEDGE_BACKEND")
            if requested
            else (POSTGRES_BACKEND if is_postgres_url(database_url) else SQLITE_BACKEND)
        )
        configured_path = str(self.environ.get("MECH_DESIGN_SQLITE_PATH", "")).strip()
        if configured_path:
            requested_path = Path(configured_path).expanduser()
            sqlite_path = (
                requested_path
                if requested_path.is_absolute()
                else (workspace / requested_path)
            ).resolve()
        else:
            sqlite_path = default_sqlite_path(workspace)
        if backend == POSTGRES_BACKEND and not database_url:
            raise RuntimeError(
                "MECH_DESIGN_KNOWLEDGE_BACKEND=postgres requires MECH_DESIGN_DATABASE_URL"
            )
        return backend, database_url, sqlite_path

    def knowledge_settings(self) -> KnowledgeSettings:
        manifest = self._manifest()
        scope = self.design_knowledge_scope()
        backend, database_url, sqlite_path = self.knowledge_backend(manifest.workspace)
        return KnowledgeSettings(
            workspace=manifest.workspace,
            database_url=database_url,
            backend=backend,
            sqlite_path=sqlite_path,
            neo4j_uri=str(
                self.environ.get("MECH_DESIGN_NEO4J_URI", "bolt://127.0.0.1:57687")
            ),
            neo4j_user=str(self.environ.get("MECH_DESIGN_NEO4J_USER", "neo4j")),
            neo4j_password=str(self.environ.get("MECH_DESIGN_NEO4J_PASSWORD", "")),
            organization_id=scope["organization_id"],
            design_group_id=scope["design_group_id"],
        )

    @staticmethod
    def standard_part_providers(category: str = "") -> dict[str, object]:
        return load_standard_part_provider_catalog().as_dict(category)

    def standard_part_sources_status(self) -> dict[str, object]:
        try:
            manifest = self._manifest()
            sources = load_standard_part_sources(manifest)
            return {
                "schema_version": "StandardPartConfigurationResult/v1",
                "status": sources.status,
                "code": sources.code,
                "message": sources.message,
                "catalog": sources.catalog_dict(),
            }
        except DiagnosticGateError as exc:
            return exc.response
        except BootstrapFailure as exc:
            return exc.as_dict()

    def standard_part_settings(self) -> StandardPartSettings:
        manifest = self._manifest()
        sources = load_standard_part_sources(manifest)
        return StandardPartSettings(
            workspace=manifest.workspace,
            catalog_root=sources.effective_root,
        )


__all__ = ["BootstrapRuntime", "run_freecad_version"]
