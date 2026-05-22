import os
import tempfile
import unittest

from ailandscape import emailer
from ailandscape.storage_kg import KnowledgeGraphStore


class EmailerTest(unittest.TestCase):
    SMTP_ENV = ("AIL_SMTP_HOST", "AIL_SMTP_PORT", "AIL_SMTP_USER",
                "AIL_SMTP_PASSWORD", "AIL_SMTP_FROM")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.recipients_path = os.path.join(self.tmp, "recipients.txt")
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))
        self.kg.insert_node(
            "Pete Hegseth", "person", mention_count=5,
            last_seen="2026-05-20",
        )
        self.kg.commit()
        self._orig_env = {k: os.environ.get(k) for k in self.SMTP_ENV}
        for k in self.SMTP_ENV:
            os.environ.pop(k, None)

    def tearDown(self):
        self.kg.close()
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _write_recipients(self, *lines):
        with open(self.recipients_path, "w", encoding="utf-8") as h:
            h.write("\n".join(lines) + "\n")

    def _set_smtp(self):
        os.environ["AIL_SMTP_HOST"] = "smtp.example.test"
        os.environ["AIL_SMTP_USER"] = "tester"
        os.environ["AIL_SMTP_PASSWORD"] = "not-a-real-secret"
        os.environ["AIL_SMTP_FROM"] = "tester@example.test"

    def test_load_recipients_skips_comments_and_blanks(self):
        self._write_recipients("# comment", "  ", "a@example.test", "b@x")
        self.assertEqual(
            emailer.load_recipients(self.recipients_path),
            ["a@example.test", "b@x"],
        )

    def test_daily_digest_no_op_without_recipients(self):
        result = emailer.daily_digest([], self.kg, self.recipients_path)
        self.assertFalse(result["sent"])
        self.assertIn("recipient", result["reason"])

    def test_daily_digest_no_op_without_smtp(self):
        self._write_recipients("a@example.test")
        result = emailer.daily_digest([], self.kg, self.recipients_path)
        self.assertFalse(result["sent"])
        self.assertIn("SMTP", result["reason"])

    def test_daily_digest_sends_with_mocked_smtp(self):
        self._write_recipients("a@example.test", "b@example.test")
        self._set_smtp()
        captured = {}

        def fake_send(smtp, message):
            captured["smtp"] = smtp
            captured["to"] = message["To"]
            captured["subject"] = message["Subject"]
            captured["body"] = message.get_content()

        result = emailer.daily_digest(
            [], self.kg, self.recipients_path, sender_fn=fake_send
        )
        self.assertTrue(result["sent"])
        self.assertEqual(result["recipients"], 2)
        self.assertIn("a@example.test", captured["to"])
        self.assertIn("AI Landscape", captured["subject"])
        # The digest body carries both the briefing and the POC leads section.
        self.assertIn("BRIEFING", captured["body"])
        self.assertIn("POC LEADS", captured["body"])
        self.assertIn("Pete Hegseth", captured["body"])


if __name__ == "__main__":
    unittest.main()
