from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import kb_job


class TestKBJobStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = (kb_job.ROOT, kb_job.JOBS_DIR, kb_job.JOBS_LOCK)
        kb_job.ROOT = self.root
        kb_job.JOBS_DIR = self.root / "rag" / "jobs"
        kb_job.JOBS_DIR.mkdir(parents=True)
        kb_job.JOBS_LOCK = kb_job.JOBS_DIR / ".jobs.lock"
        self.raw = self.root / "raw" / "consulting" / "demo.md"
        self.raw.parent.mkdir(parents=True)
        self.raw.write_text("# demo\nhello\n", encoding="utf-8")

    def tearDown(self):
        kb_job.ROOT, kb_job.JOBS_DIR, kb_job.JOBS_LOCK = self.old
        self.tmp.cleanup()

    def test_forward_stages_and_done(self):
        job = kb_job.create_job(self.raw, uploader="test")
        self.assertEqual(job["stage"], "queued")
        self.assertEqual(job["status"], "running")
        job = kb_job.update_by_raw(self.raw, "wiki_written")
        self.assertEqual(job["stage"], "wiki_written")
        self.assertEqual(job["stage_index"], kb_job.STAGE_INDEX["wiki_written"])
        kb_job.finish_active_jobs({"qmd": "done"})
        job = kb_job.get_job(self.raw)
        self.assertEqual(job["stage"], "done")
        self.assertEqual(job["status"], "done")
        self.assertIsNotNone(job["finished_at"])
        self.assertEqual(job["detail"]["qmd"], "done")

    def test_no_backward_advance(self):
        kb_job.create_job(self.raw)
        kb_job.update_by_raw(self.raw, "qmd_embed")
        job = kb_job.update_by_raw(self.raw, "wiki_written")
        self.assertEqual(job["stage"], "qmd_embed")

    def test_fail_and_retry(self):
        kb_job.create_job(self.raw)
        kb_job.update_by_raw(self.raw, "projection")
        job = kb_job.fail_by_raw(self.raw, "boom")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "boom")
        job = kb_job.update_by_raw(self.raw, "qmd_update")
        self.assertEqual(job["status"], "running")
        self.assertIsNone(job["error"])
        self.assertEqual(job["attempt"], 2)

    def test_create_job_requeues_failed_job(self):
        kb_job.create_job(self.raw)
        kb_job.update_by_raw(self.raw, "qmd_embed")
        kb_job.fail_by_raw(self.raw, "boom")
        job = kb_job.create_job(self.raw)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["stage"], "queued")
        self.assertIsNone(job["error"])
        self.assertEqual(job["attempt"], 2)

    def test_done_after_failure_updates_finished_at(self):
        kb_job.create_job(self.raw)
        kb_job.update_by_raw(self.raw, "qmd_embed")
        failed = kb_job.fail_by_raw(self.raw, "embed failed")
        failed_at = failed["finished_at"]
        time.sleep(1.05)
        kb_job.finish_by_raw(self.raw, {"qmd": "done"})
        job = kb_job.get_job(self.raw)
        self.assertEqual(job["status"], "done")
        self.assertNotEqual(job["finished_at"], failed_at)
        self.assertEqual(job["finished_at"], job["updated_at"])

    def test_batch_finish_does_not_touch_new_queued_job(self):
        first = kb_job.create_job(self.raw)
        second_raw = self.root / "raw" / "consulting" / "second.md"
        second_raw.write_text("# second\n", encoding="utf-8")
        second = kb_job.create_job(second_raw)
        kb_job.finish_active_jobs(job_ids={first["job_id"]})
        self.assertEqual(kb_job.get_job(self.raw)["status"], "done")
        self.assertEqual(kb_job.get_job(second_raw)["status"], "running")
        self.assertNotEqual(first["job_id"], second["job_id"])

    def test_new_content_creates_new_job(self):
        first = kb_job.create_job(self.raw)
        kb_job.finish_active_jobs()
        self.raw.write_text("# demo\nchanged\n", encoding="utf-8")
        second = kb_job.create_job(self.raw)
        self.assertNotEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(kb_job.list_jobs()), 2)


if __name__ == "__main__":
    unittest.main()
