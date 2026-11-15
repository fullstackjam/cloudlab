#!/usr/bin/env python3
"""Render every root and HelmRelease; validate using built-in and actual CRD schemas."""
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.request
import yaml

# CRD enums may contain a bare '='; Kubernetes YAML accepts this scalar.
yaml.SafeLoader.add_constructor('tag:yaml.org,2002:value',
                                lambda loader, node: loader.construct_scalar(node))

ROOT = Path(__file__).resolve().parents[1]

def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True)

with tempfile.TemporaryDirectory(prefix='cloudlab-validate-') as directory:
    work = Path(directory)
    manifests = {}
    objects = []
    for source in sorted(ROOT.rglob('kustomization.yaml')):
        if any(part.startswith('.') for part in source.relative_to(ROOT).parts):
            continue
        name = str(source.parent.relative_to(ROOT))
        rendered = run('kustomize', 'build', str(source.parent))
        manifests[name] = rendered
        objects.extend(yaml.safe_load_all(rendered))
        print(f'Kustomize: {name}', flush=True)
    # Export only: no kubeconfig, API server or cluster mutation.
    manifests['flux-controllers'] = run('flux', 'install', '--export')
    repos = {obj['metadata']['name']: obj['spec']['url'] for obj in objects
             if obj and obj.get('kind') == 'HelmRepository'}
    for obj in objects:
        if not obj or obj.get('kind') != 'HelmRelease':
            continue
        spec = obj['spec']
        chart = spec['chart']['spec']
        name = obj['metadata']['name']
        values = work / f'{name}-values.yaml'
        values.write_text(yaml.safe_dump(spec.get('values', {})))
        # Pull the pinned chart including its packaged dependencies.
        subprocess.run(['helm', 'pull', chart['chart'], '--repo', repos[chart['sourceRef']['name']],
                        '--version', chart['version'], '--destination', directory], check=True)
        archive = work / f"{chart['chart']}-{chart['version']}.tgz"
        manifests[f'helm-{name}'] = run(
            'helm', 'template', name, str(archive), '--namespace', obj['metadata']['namespace'],
            '--kube-version', '1.34.1', '--api-versions', 'gateway.networking.k8s.io/v1',
            '--include-crds', '-f', str(values))
        print(f"Helm: {name} ({chart['chart']} {chart['version']})", flush=True)
    # Use the schemas shipped with the exact Gateway, Flux and Helm CRDs.
    for rendered in manifests.values():
        for obj in yaml.safe_load_all(rendered):
            if not obj or obj.get('kind') != 'CustomResourceDefinition':
                continue
            spec = obj['spec']
            for version in spec['versions']:
                schema = version.get('schema', {}).get('openAPIV3Schema')
                if schema:
                    target = work / spec['group'] / f"{spec['names']['kind'].lower()}_{version['name']}.json"
                    target.parent.mkdir(exist_ok=True)
                    target.write_text(json.dumps(schema))
    # The community standalone bundle omits CRDs. Use Kubernetes' own pinned
    # API definition instead of skipping these resources.
    with urllib.request.urlopen(
            'https://raw.githubusercontent.com/kubernetes/kubernetes/v1.34.1/api/openapi-spec/swagger.json',
            timeout=60) as response:
        definitions = json.load(response)['definitions']
    def convert_openapi(node):
        # Match standalone schema conversion: Go API fields may serialize null.
        if isinstance(node, dict):
            for value in list(node.values()):
                convert_openapi(value)
            if isinstance(node.get('type'), str):
                node['type'] = [node['type'], 'null']
            if 'properties' in node:
                node.setdefault('additionalProperties', False)
        elif isinstance(node, list):
            for value in node:
                convert_openapi(value)
    convert_openapi(definitions)
    crd_schema = {
        '$ref': '#/definitions/io.k8s.apiextensions-apiserver.pkg.apis.apiextensions.v1.CustomResourceDefinition',
        'definitions': definitions,
    }
    target = work / 'apiextensions.k8s.io' / 'customresourcedefinition_v1.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(crd_schema))
    for index, (name, rendered) in enumerate(manifests.items()):
        path = work / f'rendered-{index}.yaml'
        path.write_text(rendered)
        print(f'Validate: {name}', flush=True)
        subprocess.run(['kubeconform', '-strict', '-summary', '-kubernetes-version', '1.34.0',
                        '-schema-location', 'default', '-schema-location',
                        str(work / '{{.Group}}' / '{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'),
                        str(path)], check=True)
print('All Kustomize roots, Helm charts and CRD schemas: OK')
