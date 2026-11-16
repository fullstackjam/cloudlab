#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in tofu kustomize helm flux kubeconform promtool python3; do
  command -v "$tool" >/dev/null || { echo "Required CLI missing: $tool" >&2; exit 1; }
done
for file in scripts/*.sh infra/*.sh infra/backup/*.sh; do
  bash -n "$file"
done
python3 - <<'PY'
from pathlib import Path
import re
import yaml

files = list(Path('.').glob('*.yaml'))
for directory in ('apps', 'platform', 'infra', '.github'):
    files.extend(Path(directory).rglob('*.yaml'))
    files.extend(Path(directory).rglob('*.yml'))
for file in files:
    documents = list(yaml.safe_load_all(file.read_text()))
    if file.name == 'kustomization.yaml':
        for resource in documents[0].get('resources', []):
            if not resource.startswith('https://'):
                assert (file.parent / resource).exists(), (file, resource)
rules = yaml.safe_load(Path('.sops.yaml').read_text())['creation_rules']
for directory in ('apps', 'platform'):
    for file in Path(directory).rglob('*.sops.yaml'):
        assert any(re.search(rule['path_regex'], str(file)) for rule in rules), file
for file in [*Path('.').glob('*.md'), *Path('docs').glob('*.md')]:
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', file.read_text()):
        if target.startswith(('https://', 'http://', '#')):
            continue
        path = target.split('#', 1)[0]
        assert (file.parent / path).exists(), (file, target)
print('YAML parsing, local references, Markdown links, SOPS filename rule: OK')
PY
tofu -chdir=infra fmt -check
tofu -chdir=infra init -backend=false -input=false
tofu -chdir=infra validate
python3 scripts/validate-manifests.py
python3 - <<'PY'
from pathlib import Path
import subprocess
import tempfile
import yaml

with tempfile.TemporaryDirectory(prefix='cloudlab-alerts-') as directory:
    work = Path(directory)
    release = yaml.safe_load(Path('platform/kube-prometheus-stack/thanos-monitoring.yaml').read_text())
    rules = release['spec']['values']['additionalPrometheusRulesMap']['cloudlab-thanos']
    (work / 'thanos-rules.yaml').write_text(yaml.safe_dump(rules))
    (work / 'tests.yaml').write_text(Path('scripts/tests/thanos-rules.test.yaml').read_text())
    subprocess.run(['promtool', 'test', 'rules', str(work / 'tests.yaml')], check=True)
PY
python3 -B -m unittest discover -s scripts/tests -p 'test_*.py'
