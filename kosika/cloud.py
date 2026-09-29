"""
Kosika Cloud Execution Module

This module provides the foundation for remote Kosika program execution,
program distribution, and cloud-based agent orchestration.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ProgramManifest:
    """Manifest for a Kosika program package."""

    name: str
    version: str
    author: str
    capabilities: List[str]
    requires: List[str]
    main_file: str
    modules: List[str]
    hash: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    license: str = "MIT"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "capabilities": self.capabilities,
            "requires": self.requires,
            "main_file": self.main_file,
            "modules": self.modules,
            "hash": self.hash,
            "description": self.description,
            "tags": self.tags,
            "license": self.license,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProgramManifest":
        return cls(
            name=data["name"],
            version=data["version"],
            author=data["author"],
            capabilities=data["capabilities"],
            requires=data["requires"],
            main_file=data["main_file"],
            modules=data["modules"],
            hash=data["hash"],
            description=data.get("description", ""),
            tags=data.get("tags", []),
            license=data.get("license", "MIT"),
        )


@dataclass
class ProgramPackage:
    """Complete Kosika program package."""

    manifest: ProgramManifest
    source_files: Dict[str, str]  # filename -> source code
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "source_files": self.source_files,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProgramPackage":
        return cls(
            manifest=ProgramManifest.from_dict(data["manifest"]),
            source_files=data["source_files"],
            metadata=data.get("metadata", {}),
        )

    def compute_hash(self) -> str:
        """Compute SHA-256 hash of the package."""
        canonical = json.dumps(
            {"manifest": self.manifest.to_dict(), "source_files": self.source_files},
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class ProgramRegistry:
    """Registry for storing and retrieving Kosika programs."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or Path.home() / ".kosika" / "registry"
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._index_file = self.storage_path / "index.json"
        self._index = self._load_index()

    def _load_index(self) -> Dict[str, Any]:
        """Load the program index."""
        if self._index_file.exists():
            with open(self._index_file, "r") as f:
                return json.load(f)
        return {"programs": {}, "versions": {}}

    def _save_index(self):
        """Save the program index."""
        with open(self._index_file, "w") as f:
            json.dump(self._index, f, indent=2)

    def register(self, package: ProgramPackage) -> str:
        """Register a program package."""
        program_id = f"{package.manifest.author}/{package.manifest.name}"
        version_key = f"{program_id}@{package.manifest.version}"

        # Store package files
        package_dir = self.storage_path / program_id / package.manifest.version
        package_dir.mkdir(parents=True, exist_ok=True)

        # Save package manifest
        manifest_file = package_dir / "package.json"
        with open(manifest_file, "w") as f:
            json.dump(package.to_dict(), f, indent=2)

        # Save source files
        for filename, source in package.source_files.items():
            source_file = package_dir / filename
            source_file.write_text(source)

        # Update index
        if program_id not in self._index["programs"]:
            self._index["programs"][program_id] = {
                "name": package.manifest.name,
                "author": package.manifest.author,
                "versions": [],
                "latest": package.manifest.version,
            }

        if (
            package.manifest.version
            not in self._index["programs"][program_id]["versions"]
        ):
            self._index["programs"][program_id]["versions"].append(
                package.manifest.version
            )
            self._index["programs"][program_id]["latest"] = package.manifest.version

        self._index["versions"][version_key] = {
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "hash": package.manifest.hash,
            "capabilities": package.manifest.capabilities,
        }

        self._save_index()
        return program_id

    def get(
        self, program_id: str, version: Optional[str] = None
    ) -> Optional[ProgramPackage]:
        """Retrieve a program package."""
        if program_id not in self._index["programs"]:
            return None

        version = version or self._index["programs"][program_id]["latest"]
        package_dir = self.storage_path / program_id / version
        manifest_file = package_dir / "package.json"

        if not manifest_file.exists():
            return None

        with open(manifest_file, "r") as f:
            package_data = json.load(f)

        return ProgramPackage.from_dict(package_data)

    def list(self, author: Optional[str] = None) -> List[Dict[str, Any]]:
        """List available programs."""
        programs = []
        for program_id, info in self._index["programs"].items():
            if author and info["author"] != author:
                continue
            programs.append(
                {
                    "id": program_id,
                    "name": info["name"],
                    "author": info["author"],
                    "versions": info["versions"],
                    "latest": info["latest"],
                }
            )
        return programs

    def search(self, query: str) -> List[Dict[str, Any]]:
        """Search programs by name, description, or tags."""
        results = []
        query_lower = query.lower()

        for program_id, info in self._index["programs"].items():
            latest_version = info["latest"]
            version_key = f"{program_id}@{latest_version}"

            if version_key in self._index["versions"]:
                version_info = self._index["versions"][version_key]
                capabilities = " ".join(version_info["capabilities"]).lower()

                if query_lower in info["name"].lower() or query_lower in capabilities:
                    results.append(
                        {
                            "id": program_id,
                            "name": info["name"],
                            "author": info["author"],
                            "version": latest_version,
                            "capabilities": version_info["capabilities"],
                        }
                    )

        return results


class ProgramLoader:
    """Load Kosika programs from various sources."""

    @staticmethod
    def from_directory(directory: Path) -> ProgramPackage:
        """Load a program from a directory."""
        if not directory.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")

        # Look for kosika.json or kos files
        syn_files = list(directory.glob("*.kos"))

        if not syn_files:
            raise ValueError(f"No .kos files found in {directory}")

        # Create manifest
        manifest = ProgramManifest(
            name=directory.name,
            version="1.0.0",
            author="local",
            capabilities=[],
            requires=[],
            main_file=syn_files[0].name,
            modules=[f.name for f in syn_files],
            hash="",
        )

        # Load source files
        source_files = {}
        for syn_file in syn_files:
            source_files[syn_file.name] = syn_file.read_text()

        package = ProgramPackage(manifest=manifest, source_files=source_files)
        manifest.hash = package.compute_hash()

        return package

    @staticmethod
    def from_url(url: str) -> ProgramPackage:
        """Load a program from a URL (placeholder for HTTP implementation)."""
        # TODO: Implement HTTP fetching
        raise NotImplementedError("URL loading not yet implemented")

    @staticmethod
    def from_github(repo: str, path: str = "") -> ProgramPackage:
        """Load a program from a GitHub repository (placeholder)."""
        # TODO: Implement GitHub API integration
        raise NotImplementedError("GitHub loading not yet implemented")


class ExecutionRequest:
    """Request for remote program execution."""

    def __init__(
        self,
        program_id: str,
        version: Optional[str] = None,
        input_data: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
    ):
        self.program_id = program_id
        self.version = version
        self.input_data = input_data or {}
        self.options = options or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "program_id": self.program_id,
            "version": self.version,
            "input": self.input_data,
            "options": self.options,
        }


class ExecutionResult:
    """Result of program execution."""

    def __init__(
        self,
        execution_id: str,
        status: str,
        output: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        trace: Optional[str] = None,
    ):
        self.execution_id = execution_id
        self.status = status  # "running", "completed", "failed"
        self.output = output
        self.error = error
        self.trace = trace

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "trace": self.trace,
        }


class CloudExecutor:
    """Execute Kosika programs in the cloud."""

    def __init__(self, api_endpoint: str, api_key: str):
        self.api_endpoint = api_endpoint
        self.api_key = api_key

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute a program request (placeholder for HTTP client)."""
        # TODO: Implement HTTP client for cloud execution
        raise NotImplementedError("Cloud execution not yet implemented")

    def get_status(self, execution_id: str) -> ExecutionResult:
        """Get execution status (placeholder)."""
        # TODO: Implement status check
        raise NotImplementedError("Status check not yet implemented")


# Convenience functions
def create_package(directory: Path) -> ProgramPackage:
    """Create a program package from a directory."""
    loader = ProgramLoader()
    return loader.from_directory(directory)


def register_package(
    package: ProgramPackage, registry: Optional[ProgramRegistry] = None
) -> str:
    """Register a package in the registry."""
    registry = registry or ProgramRegistry()
    return registry.register(package)


def find_program(
    query: str, registry: Optional[ProgramRegistry] = None
) -> List[Dict[str, Any]]:
    """Find programs in the registry."""
    registry = registry or ProgramRegistry()
    return registry.search(query)
