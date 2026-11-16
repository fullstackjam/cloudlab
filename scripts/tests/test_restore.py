import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


recovery = module("recovery", "infra/restore.py")
imports = module("imports", "infra/backup/import-apps.py")


class RecoveryTest(unittest.TestCase):
    def test_recovery_branch_cannot_start_public_ingress_or_cos_writers(self):
        files = ["infra/flux/flux-system/gotk-sync.yaml", "platform/kustomization.yaml",
                 "apps/kustomization.yaml", "platform/thanos/resources.yaml",
                 "platform/kube-prometheus-stack/resources.yaml"]
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for file in files:
                (repo / file).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / file, repo / file)
            recovery.isolate(repo, "codex/recovery-test")
            sync = list(yaml.safe_load_all((repo / files[0]).read_text()))[0]
            self.assertEqual(sync["spec"]["ref"]["branch"], "codex/recovery-test")
            self.assertNotIn("cloudflared", yaml.safe_load((repo / files[1]).read_text())["resources"])
            self.assertNotIn("apple-health-influxdb", yaml.safe_load((repo / files[2]).read_text())["resources"])
            compactor = next(item for item in yaml.safe_load_all((repo / files[3]).read_text())
                             if item.get("kind") == "Deployment" and item["metadata"]["name"] == "thanos-compactor")
            self.assertEqual(compactor["spec"]["replicas"], 0)
            thanos = yaml.safe_load((repo / files[4]).read_text())["spec"]["values"]["prometheus"]["prometheusSpec"]["thanos"]
            self.assertNotIn("objectStorageConfig", thanos)
            self.assertNotIn("additionalArgs", thanos)

    def test_handover_converges_to_main_and_is_safe_to_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            origin, repo = Path(directory) / "origin.git", Path(directory) / "repo"
            def git(*args, cwd=None):
                return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()
            git("init", "--bare", str(origin))
            git("init", "-b", "main", str(repo))
            git("config", "user.name", "Recovery test", cwd=repo)
            git("config", "user.email", "test@example.com", cwd=repo)
            git("remote", "add", "origin", str(origin), cwd=repo)
            sync = repo / "infra/flux/flux-system/gotk-sync.yaml"
            sync.parent.mkdir(parents=True)
            sync.write_text(yaml.safe_dump({"kind": "GitRepository", "spec": {"ref": {"branch": "main"}}}))
            (repo / "ingress").write_text("enabled")
            git("add", ".", cwd=repo)
            git("commit", "-m", "Main deployment", cwd=repo)
            git("push", "origin", "main", cwd=repo)
            main = git("rev-parse", "HEAD", cwd=repo)
            branch = "codex/recovery-test"
            git("checkout", "-b", branch, cwd=repo)
            sync.write_text(yaml.safe_dump({"kind": "GitRepository", "spec": {"ref": {"branch": branch}}}))
            (repo / "ingress").write_text("disabled")
            git("commit", "-am", "Isolated recovery", cwd=repo)
            git("push", "origin", branch, cwd=repo)
            git("checkout", "main", cwd=repo)
            runner = recovery.Recovery.__new__(recovery.Recovery)
            runner.repo = repo
            original_command = recovery.command
            def quiet_command(args, **kwargs):
                return original_command(args, **dict(kwargs, quiet=True))
            with patch.object(recovery, "command", side_effect=quiet_command):
                runner.handover_branch(branch)
                handed_over = git("--git-dir=" + str(origin), "rev-parse", branch)
                runner.handover_branch(branch)
            self.assertEqual(git("--git-dir=" + str(origin), "rev-parse", branch), handed_over)
            self.assertEqual(git("--git-dir=" + str(origin), "rev-parse", "main"), main)
            restored_source = yaml.safe_load(git("--git-dir=" + str(origin), "show", branch + ":infra/flux/flux-system/gotk-sync.yaml"))
            self.assertEqual(restored_source["spec"]["ref"], {"branch": "main"})
            self.assertEqual(git("--git-dir=" + str(origin), "show", branch + ":ingress"), "disabled")
            self.assertEqual(git("branch", "--show-current", cwd=repo), "main")

    def test_credentials_are_read_in_one_batch(self):
        environment = {"FIRST": "op://Dev/item-one/private-key", "SECOND": "op://Dev/item-one/password",
                       "THIRD": "op://Dev/item-two/credential"}
        items = [{"id": "item-one", "fields": [{"id": "private-key", "value": "fixture-key"},
                                                  {"id": "password", "value": "fixture-password"}]},
                 {"id": "item-two", "fields": [{"id": "credential", "value": "fixture-token"}]}]
        # The CLI can emit separate objects rather than a JSON array.
        with patch.object(recovery, "command", return_value="\n".join(map(json.dumps, items))) as execute:
            recovery.resolve_credentials(environment, environment.keys())
        execute.assert_called_once()
        self.assertEqual(len(json.loads(execute.call_args.kwargs["data"])), 2)
        self.assertEqual(environment["FIRST"], "fixture-key")
        self.assertEqual(environment["THIRD"], "fixture-token")

    def test_influx_comparison_ignores_csv_table_ids_but_detects_changed_tags(self):
        prefix = '#datatype,string,long,dateTime:RFC3339,double,string,string,string\n#group,false,false,false,false,true,true,true\n,result,table,_time,_value,_field,_measurement,source\n'
        first = prefix + ',,0,2026-01-01T00:00:00Z,1.0,value,steps,watch\n'
        reordered = prefix + ',,12,2026-01-01T00:00:00.000000000Z,1,value,steps,watch\n'
        different = reordered.replace(',watch\n', ',phone\n')
        self.assertEqual(imports.influx_records(first), imports.influx_records(reordered))
        self.assertNotEqual(imports.influx_records(first), imports.influx_records(different))


if __name__ == "__main__":
    unittest.main()
