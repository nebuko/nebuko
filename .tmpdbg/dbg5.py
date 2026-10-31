"""Reproduce the scaffold test failing probe in-process."""
from pathlib import Path
import subprocess
import tempfile, os, shutil

import sys
sys.path.insert(0, 'src')
from nebuko.engine.lib.git.branch_scaffold import BranchScaffold
from nebuko.engine.lib.git.idempotency import GitIdempotency

tmp = Path(tempfile.mkdtemp(dir='.tmpdbg'))

def git(repo, *a):
    return subprocess.run(['git', '-C', str(repo), *a], capture_output=True, text=True)

remote = tmp / 'origin.git'
subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(remote)], check=True)
seed = tmp / 'seed'
subprocess.run(['git', 'init', '-q', '-b', 'main', str(seed)], check=True)
for a in [['config','user.name','t'],['config','user.email','t@e'],['remote','add','origin',str(remote)]]:
    git(seed, *a)
(seed/'base.txt').write_text('base\n')
git(seed,'add','.'); git(seed,'commit','-q','-m','base'); git(seed,'push','-q','origin','main')
work = tmp / 'work'
subprocess.run(['git','init','-q','-b','main',str(work)], check=True)
for a in [['config','user.name','t'],['config','user.email','t@e'],['remote','add','origin',str(remote)]]:
    git(work, *a)
git(work,'fetch','-q','origin','main')
git(work,'checkout','-q','-b','feat','origin/main')
(work/'base.txt').write_text('b\n')
git(work,'add','.'); git(work,'commit','-q','-m','br')
git(seed,'checkout','-q','main')
(seed/'base.txt').write_text('m\n')
git(seed,'add','.'); git(seed,'commit','-q','-m','m'); git(seed,'push','-q','origin','main')
git(work,'fetch','-q','origin','main')

guard = GitIdempotency(str(work), lambda args: BranchScaffold(work, {})._git(*args))
print('origin/main resolves:', git(work,'rev-parse','--verify','origin/main').returncode)
r = subprocess.run(['git','-C',str(work),'merge-tree','--write-tree','--name-only','--no-messages','origin/main','feat'],capture_output=True,text=True)
print('direct rc', r.returncode, repr(r.stdout))
try:
    print('via _git:', guard._run_git(['merge-tree','--write-tree','--name-only','--no-messages','origin/main','feat']))
except RuntimeError as e:
    print('via _git raised:', e)
print('has_base_conflict:', guard.has_base_conflict('feat','main'))
shutil.rmtree(tmp)
