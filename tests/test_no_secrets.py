"""Guards against committing secrets or API keys to the repository.

Scans git-tracked files for provider credential formats, private keys, and
hardcoded credential assignments. The scraped corpus and database files are
excluded — they hold third-party data, not authored code.
"""

import re
import subprocess
import unittest

from ailandscape import config

# This file is excluded from the scan — it necessarily contains the
# detection patterns themselves.
_SELF = "tests/test_no_secrets.py"

# High-confidence credential formats — specific provider key shapes and
# private-key blocks.
_STRONG_PATTERNS = {
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36}\b"),
    "GitHub fine-grained PAT": re.compile(r"\bgithub_pat_[0-9A-Za-z_]{50,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    "Anthropic API key": re.compile(r"\bsk-ant-[0-9A-Za-z_\-]{20,}\b"),
    "OpenAI-style API key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
}

# Heuristic: a credential-named variable assigned a non-placeholder literal.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(api[_-]?key | secret | password | passwd
       | client[_-]?secret | access[_-]?token | auth[_-]?token)\b
    \s* [:=] \s*
    ['"] ([^'"]{6,}) ['"]
    """
)
# Values that are obviously not real secrets.
_SAFE_VALUE = re.compile(
    r"(?i)(example|placeholder|changeme|dummy|your[_-]|sample"
    r"|^x+$|<.+>|\$\{?.+|^test$|^none$|^null$|^\.\.\.)"
)


def _tracked_files():
    """Return repo-relative paths of all git-tracked files, or None."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=str(config.ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


class NoSecretsTest(unittest.TestCase):
    def setUp(self):
        files = _tracked_files()
        if not files:
            self.skipTest("not a git repository or git unavailable")
        self.files = [f for f in files if f != _SELF]

    def _read(self, rel_path):
        try:
            return (config.ROOT / rel_path).read_text(
                encoding="utf-8", errors="ignore"
            )
        except OSError:
            return ""

    def test_no_api_keys_or_private_keys(self):
        hits = []
        for rel_path in self.files:
            # Exclude the scraped corpus and any database files — only
            # authored code and config are checked here.
            if rel_path.startswith(("corpus/", "data/")):
                continue
            text = self._read(rel_path)
            for label, pattern in _STRONG_PATTERNS.items():
                if pattern.search(text):
                    hits.append("%s: possible %s" % (rel_path, label))
        self.assertEqual(
            hits, [], "potential secrets in tracked files:\n" + "\n".join(hits)
        )

    def test_no_hardcoded_credentials(self):
        hits = []
        for rel_path in self.files:
            # The corpus is scraped third-party prose, not authored code.
            if rel_path.startswith("corpus/"):
                continue
            for match in _ASSIGNMENT.finditer(self._read(rel_path)):
                name, value = match.group(1), match.group(2)
                if _SAFE_VALUE.search(value):
                    continue
                hits.append("%s: '%s' assigned %r..." % (rel_path, name, value[:3]))
        self.assertEqual(
            hits,
            [],
            "potential hardcoded credentials:\n" + "\n".join(hits),
        )


if __name__ == "__main__":
    unittest.main()
