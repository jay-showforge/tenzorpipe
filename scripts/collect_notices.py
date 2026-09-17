#!/usr/bin/env python3
"""Collect license/notice texts from the exact resolved Cargo dependency graph."""
import subprocess,json,pathlib,shutil
ROOT=pathlib.Path(__file__).resolve().parents[1]
meta=json.loads(subprocess.check_output(['cargo','metadata','--locked','--format-version','1'],cwd=ROOT))
out=ROOT/'THIRD_PARTY_NOTICES';out.mkdir(exist_ok=True);inventory=[]
for p in sorted(meta['packages'],key=lambda p:p['name']):
    if p['name']=='tenzor-pipe':continue
    base=pathlib.Path(p['manifest_path']).parent;dest=out/f"{p['name']}-{p['version']}";files=[]
    for f in base.rglob('*'):
        if f.is_file() and (f.name.upper().startswith(('LICENSE','LICENCE','COPYING','NOTICE','COPYRIGHT'))):
            rel=f.relative_to(base);target=dest/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(f,target);files.append(str(rel))
    inventory.append({'name':p['name'],'version':p['version'],'license':p.get('license'),'source':p.get('source') or 'local patch','repository':p.get('repository'),'notice_files':files})
(ROOT/'evidence'/'dependency-inventory.json').write_text(json.dumps(inventory,indent=2))
# Omit local cache paths, package features and target details; lockfile is authoritative.
(out/'README.md').write_text('# Third-party notices\n\nCollected from the locked Cargo package sources. See evidence/dependency-inventory.json.\n'+''.join(f"- {p['name']} {p['version']}: {p['license']}\n" for p in inventory))
print('Collected notices for',len(inventory),'dependencies')
