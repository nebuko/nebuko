import subprocess, tempfile, os
from nebuko.engine.lib.git.branch_scaffold import BranchScaffold

tmp = tempfile.mkdtemp()
def git(*a): return subprocess.run(['git','-C',tmp,*a],capture_output=True,text=True)
def commit(msg):
    git('add','-A')
    return git('-c','user.name=t','-c','user.email=t@t','commit','-qm',msg)
subprocess.run(['git','init','-q','-b','main',tmp],capture_output=True,text=True)
open(tmp+'/base.txt','w').write('base\n'); commit('base')
git('checkout','-q','-b','feature/x')
open(tmp+'/base.txt','w').write('branch\n'); commit('b')
git('checkout','-q','main')
open(tmp+'/base.txt','w').write('main\n'); commit('m')
git('push','-q','file:///tmp/probe-origin.git','main') if False else None

s = BranchScaffold(tmp, {}, issue_number=1, issue_title='t')
print('has_base_conflict (no origin):', s._idempotency.has_base_conflict('feature/x','main'))
print('gitdir:', os.path.exists(tmp+'/.git'))
print('branch:', git('rev-parse','--abbrev-ref','HEAD').stdout)
