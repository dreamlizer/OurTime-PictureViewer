#!/usr/bin/env python3
"""Read-only checks: no installation, copying, database access or app service calls."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
NEW_FILES = ['web/home-query-ui.js', 'web/home-query-ui.css', 'web/home-query-bridge.js']
READ_FILES = ['README.md','AGENTS.md','web/index.html','web/app.js','web/style.css',
              'web/appearance.css','web/viewer.js','web/waterfall.js','app.py',
              'browse_queries.py','library_db.py']

class IndexParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.scripts=[]; self.ids=[]
    def handle_starttag(self, tag, attributes):
        attrs=dict(attributes)
        if 'id' in attrs: self.ids.append(attrs['id'])
        if tag=='script' and attrs.get('src'): self.scripts.append(attrs['src'])

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def git(repo, args):
    env=dict(os.environ);env['GIT_OPTIONAL_LOCKS']='0'
    try:
        done=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,
                            encoding='utf-8',errors='replace',timeout=20,env=env)
        return {'exit_code':done.returncode,'stdout':done.stdout.strip(),'stderr':done.stderr.strip()}
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {'exit_code':None,'stdout':'','stderr':str(exc)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',default='.')
    args=parser.parse_args();repo=Path(args.repo).resolve()
    result={'mode':'READ_ONLY','package_revision':'1.0.0','errors':[],'warnings':[],
            'local_files':{},'new_files':{},'full_upstream_commit_verified':False}
    manifest_path=PACKAGE/'manifest.json'
    if not manifest_path.is_file():
        result['errors'].append('Package manifest is missing.')
    else:
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        for relative, expected in manifest['sha256'].items():
            p=PACKAGE/relative
            if not p.is_file() or digest(p)!=expected:
                result['errors'].append('Package checksum mismatch: '+relative)
    for relative in READ_FILES:
        p=repo/relative
        if p.is_file(): result['local_files'][relative]={'bytes':p.stat().st_size,'sha256':digest(p)}
        elif relative in ('web/index.html','web/app.js','README.md'):
            result['errors'].append('Required project file missing: '+relative)
        else: result['warnings'].append('Not present; verify local layout: '+relative)
    for relative in NEW_FILES:
        target=repo/relative;supplied=PACKAGE/'files'/relative
        state='ABSENT'
        if target.exists():
            state='IDENTICAL' if target.is_file() and digest(target)==digest(supplied) else 'CONFLICT'
            if state=='CONFLICT':result['errors'].append('Do not overwrite existing file: '+relative)
        result['new_files'][relative]=state
    if (repo/'web/index.html').is_file():
        parsed=IndexParser();parsed.feed((repo/'web/index.html').read_text(encoding='utf-8-sig'))
        result['script_order']=parsed.scripts
        names=[Path(re.split(r'[?#]',s)[0]).name for s in parsed.scripts]
        required=['app.js','viewer.js','waterfall.js']
        if all(n in names for n in required):
            if [names.index(n) for n in required]!=sorted(names.index(n) for n in required):
                result['warnings'].append('Local core script order differs; do not reorder blindly.')
        else:result['warnings'].append('Core scripts not all found; inspect actual loader.')
        duplicates=sorted({i for i in parsed.ids if parsed.ids.count(i)>1})
        result['duplicate_ids']=duplicates
        if duplicates:result['warnings'].append('Existing duplicate IDs require local inspection.')
        result['known_id_presence']={n:n in parsed.ids for n in ('person-filter','sort-order')}
    head=git(repo,['rev-parse','HEAD']);result['local_head']=head['stdout'] if head['exit_code']==0 else None
    status=git(repo,['status','--porcelain'])
    if status['exit_code']==0:
        result['dirty_entry_count']=len(status['stdout'].splitlines()) if status['stdout'] else 0
        if result['dirty_entry_count']:result['warnings'].append('Working tree contains existing changes. Preserve them.')
    else:result['warnings'].append('Git status unavailable. Preserve file-level baselines manually.')
    if all(v=='ABSENT' for v in result['new_files'].values()) and not result['errors']:
        result['new_files_patch_check']=git(repo,['apply','--check',str(PACKAGE/'01-add-files.patch')])
        if result['new_files_patch_check']['exit_code']!=0:
            result['warnings'].append('New-file patch check did not pass; inspect before merging.')
    else:
        result['new_files_patch_check']={'status':'NOT_RUN','reason':'Files exist or preflight errors; no overwrite attempted.'}
    result['remaining_required_work']='Local adapter bindings, existing-toolbar/title integration and real-app regression. Patch is additions only.'
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 2 if result['errors'] else 0

if __name__=='__main__':
    raise SystemExit(main())
