import concurrent.futures, hashlib, json, os, pathlib, subprocess, time, urllib.request, urllib.error, urllib.parse, zipfile

SOURCE='XingYunStar/NapCatQQ'
DEST=os.environ['GITHUB_REPOSITORY']
TOKEN=os.environ['GH_TOKEN']
ROOT=pathlib.Path.cwd()/'backup-output'
ROOT.mkdir(exist_ok=True)
API='https://api.github.com/repos/'

def api(path, data=None, method=None, auth=True):
    headers={'User-Agent':'NapCatQQ-cloud-backup','Accept':'application/vnd.github+json'}
    if auth: headers['Authorization']='Bearer '+TOKEN
    payload=json.dumps(data).encode() if data is not None else None
    if payload is not None: headers['Content-Type']='application/json'
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request('https://api.github.com'+path,data=payload,headers=headers,method=method),timeout=120) as r:
                body=r.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if e.code<500 or payload is not None or attempt==4: raise
        except (OSError,urllib.error.URLError):
            if payload is not None or method not in (None,'GET') or attempt==4: raise
        time.sleep(3)

def git(*args,cwd=None,input=None):
    result=subprocess.run(['git',*args],cwd=cwd,input=input,text=True,capture_output=True)
    if result.returncode: raise RuntimeError(result.stderr)
    return result.stdout.strip()

def save(name,value):
    (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def ensure_release(tag,name,body,prerelease=False):
    try: return api('/repos/'+DEST+'/releases/tags/'+urllib.parse.quote(tag,safe=''))
    except urllib.error.HTTPError as e:
        if e.code!=404: raise
    return api('/repos/'+DEST+'/releases',{'tag_name':tag,'target_commitish':'main','name':name,'body':body,'prerelease':prerelease,'draft':True})

def upload(release,path,digest):
    for attempt in range(5):
        assets=api(f"/repos/{DEST}/releases/{release['id']}/assets?per_page=100")
        existing=next((a for a in assets if a['name']==path.name),None)
        if existing:
            if existing['size']==path.stat().st_size and existing.get('digest')=='sha256:'+digest: return existing
            if existing.get('state')=='starter' and existing['size']==0:
                api(f"/repos/{DEST}/releases/assets/{existing['id']}",method='DELETE')
                continue
            raise RuntimeError('Existing asset differs: '+path.name)
        url=release['upload_url'].split('{')[0]+'?name='+urllib.parse.quote(path.name)
        try:
            with path.open('rb') as stream:
                headers={'User-Agent':'NapCatQQ-cloud-backup','Authorization':'Bearer '+TOKEN,'Content-Type':'application/octet-stream','Content-Length':str(path.stat().st_size)}
                with urllib.request.urlopen(urllib.request.Request(url,data=stream,headers=headers,method='POST'),timeout=1800) as r: item=json.load(r)
            if item['size']!=path.stat().st_size or item.get('digest')!='sha256:'+digest: raise RuntimeError('Remote checksum mismatch: '+path.name)
            return item
        except (OSError,urllib.error.URLError):
            if attempt==4: raise
            time.sleep(5)

print('Cloning source repository directly on GitHub runner',flush=True)
mirror=ROOT/'NapCatQQ.git'
git('clone','--mirror','https://github.com/'+SOURCE+'.git',str(mirror))
git('fsck','--full',cwd=mirror)
refs=dict(line.split() for line in git('for-each-ref','--format=%(refname) %(objectname)','refs/heads','refs/tags',cwd=mirror).splitlines())
source_commit_count=int(git('rev-list','--all','--count',cwd=mirror))
source=refs['refs/heads/main']
save('source-refs.json',refs)
git('bundle','create',str(ROOT/'NapCatQQ.bundle'),'--all',cwd=mirror)
git('bundle','verify',str(ROOT/'NapCatQQ.bundle'),cwd=mirror)

# Configure a temporary deploy key restricted to this destination repository.
key=pathlib.Path(os.environ['RUNNER_TEMP'])/'napcat-backup-key'
key.write_text(os.environ.pop('BACKUP_SSH_KEY'),encoding='utf-8')
key.chmod(0o600)
hostkeys=api('/meta',auth=False)['ssh_keys']
known=key.with_name('napcat-known-hosts')
known.write_text(''.join('github.com '+k+'\n' for k in hostkeys),encoding='utf-8')
os.environ['GIT_SSH_COMMAND']=f'ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={known}'
desturl='git@github.com:'+DEST+'.git'
git('fetch',desturl,'refs/heads/main:refs/remotes/destination/main','refs/heads/backup-control-20261116:refs/remotes/destination/control',cwd=mirror)
original=git('rev-parse','refs/remotes/destination/main',cwd=mirror)
control=git('rev-parse','refs/remotes/destination/control',cwd=mirror)
git('update-ref','refs/heads/backup-original-main-20261116','f9b5e023074c801649976a5a16cf6e8818feea5e',cwd=mirror)
git('update-ref','refs/heads/backup-control-20261116',control,cwd=mirror)
git('update-ref','refs/heads/napcat-source-main',source,cwd=mirror)
tree=git('rev-parse',source+'^{tree}',cwd=mirror)
git('config','user.name','Elysia-ailixiya',cwd=mirror)
git('config','user.email','116481053+Elysia-ailixiya@users.noreply.github.com',cwd=mirror)
merged=git('commit-tree',tree,'-p',original,'-p',source,input='Back up NapCatQQ directly from source on GitHub runner\n',cwd=mirror)
git('update-ref','refs/heads/main',merged,cwd=mirror)
print('Copying all source branches and tags to destination',flush=True)
git('push','--atomic',desturl,'--all',cwd=mirror)
# Disable imported build/release automation; this repository is an archive.
for attempt in range(4):
    workflows=api('/repos/'+DEST+'/actions/workflows?per_page=100')['workflows']
    for workflow in workflows:
        if workflow['path'].endswith('/napcat-cloud-backup.yml'): continue
        api(f"/repos/{DEST}/actions/workflows/{workflow['id']}/disable",method='PUT')
        runs=api(f"/repos/{DEST}/actions/workflows/{workflow['id']}/runs?per_page=100")['workflow_runs']
        for run in runs:
            if run['status']!='completed':
                try: api(f"/repos/{DEST}/actions/runs/{run['id']}/cancel",method='POST')
                except urllib.error.HTTPError as e:
                    if e.code!=409: raise
    if len(workflows)>=6: break
    time.sleep(3)
git('push',desturl,'--tags',cwd=mirror)
actual={ref:sha for sha,ref in (line.split() for line in git('ls-remote','--refs',desturl,cwd=mirror).splitlines())}
for ref,sha in refs.items():
    destination='refs/heads/napcat-source-main' if ref=='refs/heads/main' else ref
    if actual.get(destination)!=sha: raise RuntimeError('Remote ref mismatch: '+ref)
if actual['refs/heads/main']!=merged: raise RuntimeError('Destination main mismatch')
save('remote-git-verification.json',{'source_main':source,'destination_main':merged,'source_tree':tree,'source_refs_verified':len(refs),'source_commits':source_commit_count,'mode':'github_runner_direct_copy'})
print(f'Verified all {len(refs)} source branches and tags',flush=True)

releases=api('/repos/'+SOURCE+'/releases?per_page=100')
save('source-repository.json',api('/repos/'+SOURCE))
save('source-releases.json',releases)
manifest=[]

def copy_asset(pair):
    source_release,release,asset=pair
    folder=ROOT/'releases'/source_release['tag_name']
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/asset['name']
    print('Copying '+source_release['tag_name']+'/'+asset['name'],flush=True)
    subprocess.run(['curl','-fL','--retry','5','--retry-all-errors','--connect-timeout','30','--max-time','1800','-sS','-o',str(path),asset['browser_download_url']],check=True)
    digest=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
    if path.stat().st_size!=asset['size'] or (asset.get('digest') and asset['digest']!='sha256:'+digest): raise RuntimeError('Source checksum mismatch')
    copied=upload(release,path,digest)
    print('Verified remote '+source_release['tag_name']+'/'+asset['name'],flush=True)
    return {'tag':source_release['tag_name'],'name':path.name,'size':asset['size'],'sha256':digest,'source_url':asset['browser_download_url'],'destination_url':copied['browser_download_url']}

for source_release in reversed(releases):
    release=ensure_release(source_release['tag_name'],source_release['name'] or source_release['tag_name'],source_release['body'] or '',source_release['prerelease'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        manifest.extend(pool.map(copy_asset,[(source_release,release,a) for a in source_release['assets']]))
    api(f"/repos/{DEST}/releases/{release['id']}",{'draft':False},method='PATCH')
    save('remote-assets-verification.json',manifest)

for sidecar in (ROOT/'releases').rglob('*.tar.sha256'):
    tar=sidecar.with_suffix('')
    if sidecar.read_text().split()[0].lower()!=hashlib.file_digest(tar.open('rb'),'sha256').hexdigest(): raise RuntimeError('Docker checksum mismatch')
(ROOT/'SHA256SUMS.txt').write_text(''.join(f"{a['sha256']}  releases/{a['tag']}/{a['name']}\n" for a in manifest),encoding='utf-8')
save('STATUS.json',{'local_backup':'separate_complete_copy_on_user_computer','remote_backup':'source_git_and_all_release_assets_verified','transfer_mode':'source_repository_to_github_runner_to_destination_repository','source_repository':SOURCE,'destination_repository':DEST,'release_count':len(releases),'asset_count':len(manifest),'asset_bytes':sum(a['size'] for a in manifest),'run_url':f"https://github.com/{DEST}/actions/runs/{os.environ['GITHUB_RUN_ID']}"})
archive=ensure_release('backup-2026-11-16','完整备份归档 2026-11-16','GitHub 云端直接从源仓库复制：全部源码历史、分支、标签及原始 Release 附件。此版本提供自包含 Git bundle 和 SHA-256 校验记录。')
with zipfile.ZipFile(ROOT/'backup-metadata.zip','w',zipfile.ZIP_DEFLATED) as z:
    for path in ROOT.glob('*.json'): z.write(path,path.name)
    z.write(ROOT/'SHA256SUMS.txt','SHA256SUMS.txt')
for name in ['NapCatQQ.bundle','backup-metadata.zip','SHA256SUMS.txt']:
    path=ROOT/name
    upload(archive,path,hashlib.file_digest(path.open('rb'),'sha256').hexdigest())
api(f"/repos/{DEST}/releases/{archive['id']}",{'draft':False,'make_latest':'false'},method='PATCH')
key.unlink()
print(f"COMPLETE: {len(refs)} source refs, {len(releases)} releases, {len(manifest)} verified assets, recovery bundle",flush=True)
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as summary:
    summary.write(f'# NapCatQQ 完整备份完成\n\n源仓库：{SOURCE}\n\n已验证 {len(refs)} 个源引用、{len(releases)} 个 Release、{len(manifest)} 个附件（{sum(a["size"] for a in manifest):,} bytes），并上传完整 Git bundle 和 SHA-256 校验清单。\n')
