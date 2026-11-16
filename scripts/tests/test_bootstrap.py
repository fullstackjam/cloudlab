import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class BootstrapSourceTest(unittest.TestCase):
    def run_bootstrap(self, repository, branch):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "infra/flux/flux-system").mkdir(parents=True)
            shutil.copy(ROOT / "infra/bootstrap.sh", root / "infra/bootstrap.sh")
            shutil.copy(
                ROOT / "infra/flux/flux-system/gotk-sync.yaml",
                root / "infra/flux/flux-system/gotk-sync.yaml",
            )
            (root / "bin").mkdir()
            calls = root / "calls"
            for tool in ("flux", "kubectl", "sops"):
                mock = root / "bin" / tool
                mock.write_text(f'#!/bin/sh\necho {tool} >> "{calls}"\nexit 1\n')
                mock.chmod(0o755)
            env = dict(os.environ, PATH=f'{root / "bin"}:/usr/bin:/bin',
                       GITHUB_TOKEN="fixture", GITHUB_REPOSITORY=repository,
                       GITHUB_BRANCH=branch, KUBECONFIG="fixture", SOPS_AGE_KEY="fixture")
            env.pop("SOPS_AGE_KEY_FILE", None)
            result = subprocess.run(["bash", str(root / "infra/bootstrap.sh")],
                                    env=env, capture_output=True, text=True)
            return result, calls.exists()

    def test_different_branch_stops_before_cluster_or_credentials_access(self):
        result, called = self.run_bootstrap("fullstackjam/cloudlab", "recovery")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Committed Flux source does not match", result.stderr)
        self.assertFalse(called)

    def test_different_repository_stops_before_cluster_or_credentials_access(self):
        result, called = self.run_bootstrap("example/cloudlab", "main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Committed Flux source does not match", result.stderr)
        self.assertFalse(called)

    def test_matching_source_reaches_credential_preflight(self):
        result, called = self.run_bootstrap("fullstackjam/cloudlab", "main")
        self.assertIn("Encrypt platform/cloudflared/credentials.sops.yaml first", result.stdout)
        self.assertFalse(called)
