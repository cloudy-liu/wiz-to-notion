from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class CleanupPlanningTests(unittest.TestCase):
    def test_build_expected_child_page_counts_tracks_folders_notes_and_title_collisions(self) -> None:
        from wiz_to_notion.cleanup import build_expected_child_page_counts

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Android").mkdir()
            (root / "Android" / "One.md").write_text("# One", encoding="utf-8")
            (root / "Android" / "Sub").mkdir()
            (root / "Android" / "Sub" / "Two.md").write_text("# Two", encoding="utf-8")
            (root / "微信收藏").mkdir()
            (root / "微信收藏" / "极客时间 - AI技术内参.md").write_text("# 极客时间 | AI技术内参", encoding="utf-8")
            (root / "微信收藏" / "极客时间 - AI技术内参--copy.md").write_text("# 极客时间 | AI技术内参", encoding="utf-8")

            counts = build_expected_child_page_counts(root)

        self.assertEqual(1, counts[("", "Android")])
        self.assertEqual(1, counts[("Android", "One")])
        self.assertEqual(1, counts[("Android", "Sub")])
        self.assertEqual(1, counts[("Android/Sub", "Two")])
        self.assertEqual(1, counts[("", "微信收藏")])
        self.assertEqual(2, counts[("微信收藏", "极客时间 | AI技术内参")])

    def test_plan_duplicate_cleanup_prefers_state_ids_and_only_removes_excess(self) -> None:
        from wiz_to_notion.cleanup import ExistingChildPage, plan_duplicate_cleanup

        actual_pages = (
            ExistingChildPage(page_id="keep-state", title="Python", parent_path="语言/Python", path="语言/Python/Python", created_time="2026-04-26T00:00:00.000Z", last_edited_time="2026-04-26T00:00:00.000Z"),
            ExistingChildPage(page_id="old-1", title="Python", parent_path="语言/Python", path="语言/Python/Python", created_time="2026-04-25T00:00:00.000Z", last_edited_time="2026-04-25T00:00:00.000Z"),
            ExistingChildPage(page_id="old-2", title="Python", parent_path="语言/Python", path="语言/Python/Python", created_time="2026-04-24T00:00:00.000Z", last_edited_time="2026-04-24T00:00:00.000Z"),
            ExistingChildPage(page_id="wx-1", title="极客时间 | AI技术内参", parent_path="微信收藏", path="微信收藏/极客时间 | AI技术内参", created_time="2026-04-25T00:00:00.000Z", last_edited_time="2026-04-25T00:00:00.000Z"),
            ExistingChildPage(page_id="wx-2", title="极客时间 | AI技术内参", parent_path="微信收藏", path="微信收藏/极客时间 | AI技术内参", created_time="2026-04-26T00:00:00.000Z", last_edited_time="2026-04-26T00:00:00.000Z"),
            ExistingChildPage(page_id="manual", title="Manual", parent_path="语言/Python", path="语言/Python/Manual", created_time="2026-04-26T00:00:00.000Z", last_edited_time="2026-04-26T00:00:00.000Z"),
        )
        expected_counts = {
            ("语言/Python", "Python"): 1,
            ("微信收藏", "极客时间 | AI技术内参"): 2,
        }
        state_page_groups = {
            ("语言/Python", "Python"): ("keep-state",),
        }

        plan = plan_duplicate_cleanup(
            expected_counts=expected_counts,
            state_page_groups=state_page_groups,
            actual_pages=actual_pages,
        )

        self.assertEqual(1, len(plan.groups))
        self.assertEqual(("old-1", "old-2"), tuple(plan.groups[0].remove_ids))
        self.assertEqual(("keep-state",), tuple(plan.groups[0].keep_ids))
        self.assertEqual(2, plan.groups[0].redundant_count)
        self.assertEqual({"old-1", "old-2"}, set(plan.page_ids_to_trash))


if __name__ == "__main__":
    unittest.main()
