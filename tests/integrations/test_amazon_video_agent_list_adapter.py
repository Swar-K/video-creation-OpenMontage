from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from integrations.amazon_video_agent.importer import _parse_scenes
from integrations.amazon_video_agent.list_adapter import _write_staged_list, _script_product_map
from scripts.render_piper_narrated_project import EDGE_VOICES, build_edit_decisions


class AmazonListAdapterTests(unittest.TestCase):
    def _make_list_project(self, root: Path) -> Path:
        projects = root / "projects"
        list_project = projects / "TOP3-EXAMPLE"
        list_project.mkdir(parents=True)
        config = {
            "title": "Top 3 sample products",
            "products": [
                {"id": "B000000001", "label": "Best overall"},
                {"id": "B000000002", "label": "Best compact"},
                {"id": "B000000003", "label": "Best for travel"},
            ],
            "order": "countdown",
        }
        (list_project / "list.yaml").write_text(
            yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
        )
        (list_project / "script.md").write_text(
            "# TITLE: Top 3 sample products\n"
            "## hook\n[1] Here are three options worth considering. | product: all | p: 1\n"
            "## item_3\n[2] This is the third pick for travel. | product: 3 | p: 1\n"
            "## item_2\n[3] This is the second pick for compact use. | product: 2 | p: 1\n"
            "## item_1\n[4] This is my top overall pick. | product: 1 | p: 1\n",
            encoding="utf-8",
        )
        for rank in range(1, 4):
            product = projects / f"B00000000{rank}" / "assets"
            product.mkdir(parents=True)
            (product / f"photo-{rank}.jpg").write_bytes(b"image-fixture")
            (projects / f"B00000000{rank}" / "facts.yaml").write_text(
                f"product_name: Product {rank}\n", encoding="utf-8"
            )
        return list_project

    def test_list_adapter_stages_product_specific_assets_and_scenes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._make_list_project(root)
            staged = _write_staged_list(source, root / "stage" / "source")
            scenes = _parse_scenes(staged / "scenes.md")
            self.assertEqual(len(scenes), 4)
            self.assertTrue(scenes[1]["refs"][0].startswith("P03__"))
            self.assertTrue(scenes[2]["refs"][0].startswith("P02__"))
            self.assertTrue(scenes[3]["refs"][0].startswith("P01__"))
            self.assertTrue((staged / "facts.yaml").is_file())
            self.assertTrue((staged / "script.md").is_file())
            self.assertEqual(len(list((staged / "assets").iterdir())), 3)
            self.assertTrue((source / "list.yaml").is_file())

    def test_list_script_product_metadata_is_parsed(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self._make_list_project(Path(temp))
            from integrations.amazon_video_agent.importer import _parse_script
            _, sections = _parse_script(source / "script.md")
            mapping = _script_product_map(source / "script.md", sections)
            self.assertIsNone(mapping["1"])
            self.assertEqual(mapping["2"], 3)
            self.assertEqual(mapping["3"], 2)
            self.assertEqual(mapping["4"], 1)

    def test_edge_voice_options_match_ad_agent_four_voice_set(self):
        self.assertEqual(EDGE_VOICES, {
            "edge_female": ("en-US-AvaMultilingualNeural", "female"),
            "edge_female_2": ("en-US-EmmaMultilingualNeural", "female"),
            "edge_male": ("en-US-AndrewMultilingualNeural", "male"),
            "edge_male_2": ("en-US-BrianMultilingualNeural", "male"),
        })


    def test_manual_workflow_exposes_list_and_four_edge_voice_options(self):
        workflow = (
            Path(__file__).resolve().parents[2]
            / ".github" / "workflows" / "amazon-video-agent-narrated.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("edge_female", workflow)
        self.assertIn("edge_female_2", workflow)
        self.assertIn("edge_male", workflow)
        self.assertIn("edge_male_2", workflow)
        self.assertIn('if [ -f "$SOURCE/list.yaml" ]', workflow)
        self.assertIn('VOICE: ${{ inputs.voice }}', workflow)
        self.assertEqual(workflow.count("default: false"), 2)

    def test_list_edit_uses_scene_product_image_not_round_robin_image(self):
        script = {
            "total_duration_seconds": 20.0,
            "sections": [{"id": "s02", "label": "item_2",
                          "start_seconds": 0.0, "end_seconds": 20.0}],
        }
        manifest = {"assets": [
            {"id": "p1", "type": "image", "path": "assets/images/P01__one.jpg"},
            {"id": "p2", "type": "image", "path": "assets/images/P02__two.jpg"},
        ]}
        plan = {"scenes": [{"script_section_id": "s02",
                            "required_assets": [{"type": "image", "description": "P02__two.jpg"}]}]}
        decisions = build_edit_decisions(script, manifest, plan, manifest["assets"], "TOP3")
        self.assertEqual(decisions["cuts"][0]["source"], "p2")
        self.assertEqual(decisions["overlays"][0]["text"], "Rank #2")


if __name__ == "__main__":
    unittest.main()
