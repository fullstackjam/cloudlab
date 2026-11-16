#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in tofu kustomize helm flux kubeconform python3; do
 command -v "$tool" >/dev/null || { echo "Required CLI missing: $tool" >&2; exit 1; }
done
for f in scripts/*.sh scripts/git-local; do bash -n "$f"; done
python3 - <<'PY'
from pathlib import Path
import re
import yaml
for f in Path('.').rglob('*.yaml'):
 if any(x.startswith('.') for x in f.parts):continue
 list(yaml.safe_load_all(f.read_text()))
 if f.name=='kustomization.yaml':
  for p in yaml.safe_load(f.read_text()).get('resources',[]):
   if not p.startswith('https://'):assert (f.parent/p).exists(), (f,p)
rules = yaml.safe_load(Path('.sops.yaml').read_text())['creation_rules']
assert any(re.search(r['path_regex'], 'secrets/credentials.sops.yaml') for r in rules)
print('YAML parsing, local references, SOPS filename rule: OK')
PY
tofu -chdir=infra fmt -check
tofu -chdir=infra init -backend=false -input=false
tofu -chdir=infra validate
python3 scripts/validate-manifests.py
python3 -B -m unittest discover -s tests -p 'test_*.py'
