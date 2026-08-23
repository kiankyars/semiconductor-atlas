import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "generate_atlas.py"
SPEC = importlib.util.spec_from_file_location("generate_semiconductor_atlas", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class GenerateAtlasTests(unittest.TestCase):
    def _release_manifest(self, source: Path, **extra_files: object) -> dict[str, object]:
        raw = source.read_bytes()
        files = {
            "atlas.geojson": {
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            **extra_files,
        }
        return {"format": MODULE.RELEASE_FORMAT, "files": files}

    def _update_source_and_manifest(self, source: Path, **changes: object) -> None:
        document = json.loads(source.read_text(encoding="utf-8"))
        source.write_text(json.dumps({**document, **changes}), encoding="utf-8")
        manifest_path = source.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw = source.read_bytes()
        manifest["files"][source.name] = {
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def _write_transaction_marker(
        self,
        release_directory: Path,
        stage: Path,
        *,
        old_digest: str,
        new_digest: str,
    ) -> None:
        MODULE._transaction_path(release_directory).write_bytes(
            MODULE._transaction_bytes(
                release_directory,
                stage,
                old_manifest_sha256=old_digest,
                new_manifest_sha256=new_digest,
            )
        )

    def test_safe_embedding_cannot_close_script_element(self) -> None:
        data = {"type": "FeatureCollection", "features": [], "note": "</script><script>bad()</script>"}
        encoded = MODULE.safe_json(data)
        self.assertNotIn("</script>", encoded)
        self.assertIn("\\u003c", encoded)

    def test_generate_embeds_valid_geojson_and_updates_manifest(self) -> None:
        document = {
            "type": "FeatureCollection",
            "atlas_as_of": "2026-07-17",
            "features": [
                {
                    "type": "Feature",
                    "id": "one",
                    "geometry": None,
                    "properties": {
                        "name": "One",
                        "capabilities": [
                            {
                                "capability_type": "cleanroom_area",
                                "value": 123,
                                "unit": "m2",
                                "confidence": 0.91,
                                "valid_from": "2026-01-01",
                            }
                        ],
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "atlas.geojson"
            output = root / "atlas.html"
            source.write_text(json.dumps(document), encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(self._release_manifest(source)),
                encoding="utf-8",
            )
            MODULE.generate(source, output)
            rendered = output.read_text(encoding="utf-8")
            self.assertIn("Semiconductor Atlas", rendered)
            self.assertIn('"id":"one"', rendered)
            self.assertIn(
                'Object.prototype.hasOwnProperty.call(x, "confidence")',
                rendered,
            )
            self.assertIn(
                '${x.unit ? ` ${x.unit}` : ""} · ${Math.round(x.confidence * 100)}%',
                rendered,
            )
            self.assertIn(
                'if (scopeLifecycle.length) blocks.push(["Scope and lifecycle", scopeLifecycle])',
                rendered,
            )
            self.assertIn(
                'if (unknownValues.length) blocks.push(["Explicit unknowns", unknownValues])',
                rendered,
            )
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("atlas.html", manifest["files"])
            self.assertEqual(
                "standalone_atlas_html",
                manifest["files"]["atlas.html"]["role"],
            )

    def test_ai_critical_rejects_noncanonical_output_before_recovery_or_mutation(self) -> None:
        document = {"type": "FeatureCollection", "features": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "atlas.geojson"
            source.write_text(json.dumps(document), encoding="utf-8")
            release_template = root / "atlas-template.html"
            release_template.write_bytes(MODULE.TEMPLATE_PATH.read_bytes())
            manifest = self._release_manifest(
                source,
                **{
                    release_template.name: {
                        "bytes": release_template.stat().st_size,
                        "sha256": hashlib.sha256(release_template.read_bytes()).hexdigest(),
                    }
                },
            )
            template_digest = hashlib.sha256(release_template.read_bytes()).hexdigest()
            manifest["format"] = MODULE.AI_CRITICAL_RELEASE_FORMAT
            manifest["atlas_template_sha256"] = template_digest
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            output = root / "map.html"

            with mock.patch.object(MODULE, "_recover_interrupted_install") as recover:
                with self.assertRaisesRegex(ValueError, "must be named atlas.html"):
                    MODULE.generate(source, output)

            recover.assert_not_called()
            self.assertFalse(output.exists())
            self.assertEqual(
                before,
                {path.name: path.read_bytes() for path in root.iterdir()},
            )

    def test_rejects_non_feature_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "FeatureCollection"):
                MODULE.load_geojson(path)

    def test_generate_rejects_destructive_output_collisions(self) -> None:
        document = {"type": "FeatureCollection", "features": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "atlas.geojson"
            source.write_text(json.dumps(document), encoding="utf-8")
            original_source = source.read_bytes()

            with self.assertRaisesRegex(ValueError, "input GeoJSON"):
                MODULE.generate(source, source)
            self.assertEqual(original_source, source.read_bytes())

            manifest = root / "manifest.json"
            manifest.write_text('{"files": {}}\n', encoding="utf-8")
            original_manifest = manifest.read_bytes()
            with self.assertRaisesRegex(ValueError, "release manifest"):
                MODULE.generate(source, manifest)
            self.assertEqual(original_manifest, manifest.read_bytes())

            original_template = MODULE.TEMPLATE_PATH.read_bytes()
            with self.assertRaisesRegex(ValueError, "HTML template"):
                MODULE.generate(source, MODULE.TEMPLATE_PATH)
            self.assertEqual(original_template, MODULE.TEMPLATE_PATH.read_bytes())

            claims = root / "claims.jsonl"
            claims.write_text("claim\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"\.html"):
                MODULE.generate(source, claims)
            self.assertEqual("claim\n", claims.read_text(encoding="utf-8"))

    def test_generate_rejects_untracked_or_modified_release_geojson(self) -> None:
        document = {"type": "FeatureCollection", "features": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "atlas.geojson"
            output = root / "atlas.html"
            source.write_text(json.dumps(document), encoding="utf-8")
            manifest = self._release_manifest(source)
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            source.write_text(json.dumps({**document, "changed": True}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "does not match"):
                MODULE.generate(source, output)
            self.assertFalse(output.exists())

    def test_generate_rejects_dangling_output_and_manifest_symlinks_without_following(self) -> None:
        document = {"type": "FeatureCollection", "features": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "atlas.geojson"
            source.write_text(json.dumps(document), encoding="utf-8")
            actual_manifest = root / "actual-manifest.json"
            actual_manifest.write_text(
                json.dumps(self._release_manifest(source)),
                encoding="utf-8",
            )
            manifest = root / "manifest.json"
            manifest.symlink_to(actual_manifest.name)
            output = root / "atlas.html"

            manifest_before = actual_manifest.read_bytes()
            with self.assertRaisesRegex(ValueError, "regular file"):
                MODULE.generate(source, output)
            self.assertEqual(manifest_before, actual_manifest.read_bytes())
            self.assertFalse(output.exists())

            manifest.unlink()
            manifest.write_bytes(actual_manifest.read_bytes())
            victim = root / "victim.html"
            output.symlink_to(victim.name)
            with self.assertRaisesRegex(ValueError, "unmanaged HTML"):
                MODULE.generate(source, output)
            self.assertTrue(output.is_symlink())
            self.assertFalse(victim.exists())

    def test_staging_write_failure_preserves_the_previous_managed_pair(self) -> None:
        document = {"type": "FeatureCollection", "features": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "release"
            root.mkdir()
            source = root / "atlas.geojson"
            output = root / "atlas.html"
            source.write_text(json.dumps(document), encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(self._release_manifest(source)),
                encoding="utf-8",
            )
            MODULE.generate(source, output)
            self._update_source_and_manifest(source, revision="new")
            old_output = output.read_bytes()
            old_manifest = (root / "manifest.json").read_bytes()

            real_write = MODULE._write_durable

            def fail_manifest(path: Path, raw: bytes) -> None:
                if path.name == "manifest.json":
                    raise OSError("simulated disk full")
                real_write(path, raw)

            with mock.patch.object(MODULE, "_write_durable", side_effect=fail_manifest):
                with self.assertRaisesRegex(OSError, "disk full"):
                    MODULE.generate(source, output)

            self.assertEqual(old_output, output.read_bytes())
            self.assertEqual(old_manifest, (root / "manifest.json").read_bytes())
            self.assertFalse(MODULE._backup_path(root).exists())
            self.assertEqual(
                [],
                list(root.parent.glob(MODULE._stage_prefix(root) + "*")),
            )
            MODULE._verify_managed_bundle(root)

    def test_owner_marker_write_failure_removes_the_unowned_stage(self) -> None:
        document = {"type": "FeatureCollection", "features": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "release"
            root.mkdir()
            source = root / "atlas.geojson"
            output = root / "atlas.html"
            source.write_text(json.dumps(document), encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(self._release_manifest(source)),
                encoding="utf-8",
            )
            old_manifest = (root / "manifest.json").read_bytes()
            real_write = MODULE._write_durable

            def fail_owner(path: Path, raw: bytes) -> None:
                if path.name.endswith(MODULE.STAGE_OWNER_SUFFIX):
                    raise OSError("simulated owner marker failure")
                real_write(path, raw)

            with mock.patch.object(MODULE, "_write_durable", side_effect=fail_owner):
                with self.assertRaisesRegex(OSError, "owner marker failure"):
                    MODULE.generate(source, output)

            self.assertEqual(old_manifest, (root / "manifest.json").read_bytes())
            self.assertFalse(output.exists())
            self.assertEqual(
                [],
                list(root.parent.glob(MODULE._stage_prefix(root) + "*")),
            )

    def test_failed_directory_swap_rolls_back_the_previous_bundle(self) -> None:
        document = {"type": "FeatureCollection", "features": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "release"
            root.mkdir()
            source = root / "atlas.geojson"
            output = root / "atlas.html"
            source.write_text(json.dumps(document), encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(self._release_manifest(source)),
                encoding="utf-8",
            )
            MODULE.generate(source, output)
            self._update_source_and_manifest(source, revision="new")
            old_output = output.read_bytes()
            old_manifest = (root / "manifest.json").read_bytes()
            real_rename = MODULE._rename_path

            def fail_stage_install(source_path: Path, destination: Path) -> None:
                if (
                    source_path.name.startswith(MODULE._stage_prefix(root))
                    and destination == root
                ):
                    raise OSError("simulated rename failure")
                real_rename(source_path, destination)

            with mock.patch.object(MODULE, "_rename_path", side_effect=fail_stage_install):
                with self.assertRaisesRegex(OSError, "rename failure"):
                    MODULE.generate(source, output)

            self.assertEqual(old_output, output.read_bytes())
            self.assertEqual(old_manifest, (root / "manifest.json").read_bytes())
            self.assertFalse(MODULE._backup_path(root).exists())
            self.assertEqual(
                [],
                list(root.parent.glob(MODULE._stage_prefix(root) + "*")),
            )
            MODULE._verify_managed_bundle(root)

    def test_recovery_restores_backup_and_removes_owned_orphan_stage(self) -> None:
        document = {"type": "FeatureCollection", "features": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "release"
            root.mkdir()
            source = root / "atlas.geojson"
            output = root / "atlas.html"
            source.write_text(json.dumps(document), encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(self._release_manifest(source)),
                encoding="utf-8",
            )
            MODULE.generate(source, output)
            expected_output = output.read_bytes()
            expected_manifest = (root / "manifest.json").read_bytes()
            old_digest = MODULE._manifest_sha256(root)

            backup = MODULE._backup_path(root)
            root.rename(backup)
            stage = root.parent / (MODULE._stage_prefix(root) + "interrupted")
            stage.mkdir()
            (stage / "partial").write_text("partial", encoding="utf-8")
            owner_marker = MODULE._stage_owner_path(stage)
            owner_marker.write_text(str(root.absolute()), encoding="utf-8")
            self._write_transaction_marker(
                root,
                stage,
                old_digest=old_digest,
                new_digest="f" * 64,
            )

            MODULE._recover_interrupted_install(root)

            self.assertEqual(expected_output, output.read_bytes())
            self.assertEqual(expected_manifest, (root / "manifest.json").read_bytes())
            self.assertFalse(backup.exists())
            self.assertFalse(stage.exists())
            self.assertFalse(owner_marker.exists())
            MODULE._verify_managed_bundle(root)

    def test_recovery_commits_a_valid_current_bundle_but_preserves_ambiguous_state(self) -> None:
        document = {"type": "FeatureCollection", "features": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "release"
            root.mkdir()
            source = root / "atlas.geojson"
            output = root / "atlas.html"
            source.write_text(json.dumps(document), encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(self._release_manifest(source)),
                encoding="utf-8",
            )
            MODULE.generate(source, output)
            backup = MODULE._backup_path(root)
            shutil.copytree(root, backup)
            digest = MODULE._manifest_sha256(root)
            completed_stage = root.parent / (MODULE._stage_prefix(root) + "completed")
            self._write_transaction_marker(
                root,
                completed_stage,
                old_digest=digest,
                new_digest=digest,
            )

            MODULE._recover_interrupted_install(root)
            self.assertFalse(backup.exists())
            MODULE._verify_managed_bundle(root)

            shutil.copytree(root, backup)
            self._write_transaction_marker(
                root,
                completed_stage,
                old_digest=digest,
                new_digest=digest,
            )
            output.write_text("corrupt", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match"):
                MODULE._recover_interrupted_install(root)
            self.assertTrue(root.exists())
            self.assertTrue(backup.exists())


if __name__ == "__main__":
    unittest.main()
