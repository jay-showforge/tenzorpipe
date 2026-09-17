#!/usr/bin/env python3
"""Package the verified release as two mergeable archives, each below30 MB."""
import hashlib,json,pathlib,shutil,subprocess,sys,tomllib,zipfile
R=pathlib.Path(__file__).resolve().parents[1];D=R.parent/'deliverables';D.mkdir(exist_ok=True)
v=tomllib.loads((R/'Cargo.toml').read_text())['package']['version'];prefix=f'tenzorpipe-v{v}/'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
subprocess.run([sys.executable,R/'scripts/write_reports.py'],check=True)
binary=R/'target/release/tenzor';summary=json.loads((R/'evidence/verification-summary.json').read_text());assert sha(binary)==summary['binary_sha256']
(R/'bin').mkdir(exist_ok=True)
for name in ['tenzor-linux-x86_64',f'tenzor-v{v}-linux-x86_64']:shutil.copy2(binary,R/'bin'/name)
example=pathlib.Path(json.loads((R/'evidence/matrix-output.json').read_text())['directory'])/'short-224.tenzor'
subprocess.run([sys.executable,R/'scripts/hash_arrow.py',example],check=True,stdout=subprocess.DEVNULL);shutil.copy2(example,R/'examples/short-224.tenzor')
reports=['TEST_REPORT.md','BENCHMARK_REPORT.md','BUILD_STATUS.md','DEPENDENCY_POLICY.md','CONCURRENCY_REPORT.md','V0.2.0_REPORT.md']
pick=json.loads((R/'evidence/worker-recommendation-v020.json').read_text())['recommended']
manifest=dict(version=v,binary='bin/tenzor-linux-x86_64',binary_sha256=sha(binary),compiler='Rust1.98.1',engine_source='unchanged from supplied v0.2.0',unit_tests=30,aac_tests=94,aac_ignored=4,aac_feature_configurations=['default','no-default-features'],media_cases=16,invalid_input_cases=12,epoch_comparisons=41,concurrency_checks=38,boundary_checks=30,generated_audio_successful_comparisons=168,repo_successful_comparisons=336,repo_expected_error_agreements=60,corruption_comparisons=92,write_limit_comparisons=12,measured_outputs=80,warmups=11,repeated_unprofiled_measurements=55,separate_diagnostics=14,retained_delayed_rereads=25,memory_runs=4,publication_rereads=20,license_check='PASS',default_video_workers=1,recommended_video_workers=pick,default_audio_decode_thread=True,project_license='BUSL-1.1 identifier; owner-specific terms unresolved',disk_audit='3 dynamic-libc writable-open audits; direct-syscall coverage not claimed',reproduce='bash scripts/reproduce.sh',reports=reports)
(R/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
(R/'UNPACK.md').write_text(f'''# TenzorPipe v{v}\n\nThe complete tenzorpipe-v{v}.zip includes everything. Alternatively, unzip\n tenzorpipe-v{v}-source.zip and tenzorpipe-v{v}-binaries.zip into the same\nparent directory. Both contribute files to tenzorpipe-v{v}/. The source archive\ncontains the full source, reports, short fixtures and evidence. The binary archive\ncontains the pinned current Linux binary, three comparison binaries and notices.\nAfter merging, run `sha256sum -c SHA256SUMS` from the repository directory.\n\nCurrent binary: bin/tenzor-linux-x86_64 (also named bin/tenzor-v{v}-linux-x86_64).\nComparators in reference/bin/ are for regression testing, not deployment.\nBuild and test instructions: BUILD_STATUS.md and scripts/reproduce.sh.\nLong media, extra audio media and gen-* video fixtures are regenerated, not bundled.\n''')
def include(p):
 rel=p.relative_to(R)
 if p.is_symlink() or not p.is_file():return False
 if any(x in ['target','target-experiment','out','.git','.venv','__pycache__'] for x in rel.parts):return False
 if p.suffix in ['.pyc','.zip'] or p.name=='SHA256SUMS':return False
 if rel.parts[0]=='fixtures' and (p.name.startswith(('long-','gen-')) or 'audio-v020' in rel.parts):return False
 return True
files=sorted(p for p in R.rglob('*') if include(p));sums={str(p.relative_to(R)):sha(p) for p in files}
(R/'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name,digest in sums.items()));files.append(R/'SHA256SUMS')
def is_binary(p):
 rel=p.relative_to(R);return rel.parts[0]=='bin' or rel.parts[:2]==('reference','bin')
archives=[]
for kind in ['source','binaries']:
 selected=[p for p in files if (not is_binary(p) if kind=='source' else is_binary(p) or p.relative_to(R).parts[0]=='THIRD_PARTY_NOTICES' or p.name in ['UNPACK.md','DEPENDENCY_POLICY.md'] or (p.relative_to(R).parts[0]=='vendor' and ('LICENSE' in p.name or p.name.startswith('NOTICE'))))]
 archive=D/f'tenzorpipe-v{v}-{kind}.zip';partial=archive.with_suffix('.zip.partial')
 with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
  for p in selected:z.write(p,prefix+str(p.relative_to(R)))
 with zipfile.ZipFile(partial) as z:
  assert z.testzip() is None
  for p in selected:assert hashlib.sha256(z.read(prefix+str(p.relative_to(R)))).hexdigest()==sha(p)
 assert partial.stat().st_size<30000000, f'{kind} exceeds30 MB'
 partial.replace(archive);archives.append(dict(path=str(archive),bytes=archive.stat().st_size,sha256=sha(archive),files=len(selected)))
# Also offer a single complete archive when it fits the user's 30 MB limit.
complete=D/f'tenzorpipe-v{v}.zip'
with zipfile.ZipFile(complete,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for p in files:z.write(p,prefix+str(p.relative_to(R)))
with zipfile.ZipFile(complete) as z:
 assert z.testzip() is None
 for name,digest in sums.items():assert hashlib.sha256(z.read(prefix+name)).hexdigest()==digest
assert complete.stat().st_size<30000000
# Check combined membership covers every manifest entry.
with zipfile.ZipFile(archives[0]['path']) as a,zipfile.ZipFile(archives[1]['path']) as b:
 assert set(prefix+n for n in sums).issubset(set(a.namelist())|set(b.namelist()))
shutil.copy2(binary,D/f'tenzor-v{v}-linux-x86_64')
for name in reports:shutil.copy2(R/name,D/name)
print(json.dumps(dict(archives=archives,complete=dict(path=str(complete),bytes=complete.stat().st_size,sha256=sha(complete)),binary_sha256=sha(binary)),indent=2))
