from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from semiconductor_atlas import milestone_benchmark as benchmark
from semiconductor_atlas.models import MilestoneStatus, MilestoneValue
from tests import test_milestone_benchmark as fixtures


class MilestoneBenchmarkReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MilestoneBenchmarkTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_resealed_vintage_cannot_substitute_a_different_expected_event(self):
        vintage = self.fixture._vintage()
        self.fixture._claim("hvm-review-probe", "project-a", MilestoneValue(
            "high_volume_production", MilestoneStatus.EXPECTED,
            "2026-04-01", None, "2026-04-30", "month", "April 2026"))
        self.fixture.connection.commit()
        tampered = copy.deepcopy(vintage)
        case = tampered["cases"][0]
        refs = [self.fixture._ref("hvm-review-probe")]
        case["input"]["input_claims"] = refs
        case["lineage"] = benchmark._graph(self.fixture.connection, refs,
            tampered["evidence_cutoff_at"], case["case"]["project_entity_id"])
        tampered.pop("sha256")
        tampered = benchmark._seal(tampered)
        with self.assertRaisesRegex(ValueError, "exact expected event"):
            benchmark.verify_vintage(self.fixture.connection, tampered)

    def test_directory_swap_cannot_report_publication_at_the_wrong_path(self):
        root = self.fixture.root
        for when in ("before", "after"):
            with self.subTest(when=when):
                directory = root / ("publish-" + when)
                directory.mkdir()
                moved = root / ("moved-" + when)
                output = directory / "artifact.json"
                sentinel = b"Another writer's file must survive."
                real_link = benchmark.os.link

                def swap(*args, **kwargs):
                    if when == "after":
                        real_link(*args, **kwargs)
                    directory.rename(moved)
                    directory.mkdir()
                    output.write_bytes(sentinel)
                    if when == "before":
                        real_link(*args, **kwargs)

                with patch.object(benchmark.os, "link", side_effect=swap):
                    with self.assertRaises((ValueError, OSError)):
                        benchmark.write_new(output, {"fixture": "not a real study"})
                self.assertEqual(sentinel, output.read_bytes())

    def test_resealed_vintage_cannot_change_the_source_native_project_key(self):
        vintage = self.fixture._vintage()
        tampered = copy.deepcopy(vintage)
        tampered["study"]["specification"]["roster"][0]["project_stable_key"] = "source-native:not-project-a"
        tampered["cases"][0]["case"]["project_stable_key"] = "source-native:not-project-a"
        tampered["study"].pop("sha256")
        tampered["study"] = benchmark._seal(tampered["study"])
        tampered["study_sha256"] = tampered["study"]["sha256"]
        tampered.pop("sha256")
        tampered = benchmark._seal(tampered)
        with self.assertRaisesRegex(ValueError, "exact source-native project identity"):
            benchmark.verify_vintage(self.fixture.connection, tampered)


if __name__ == "__main__":
    unittest.main()
