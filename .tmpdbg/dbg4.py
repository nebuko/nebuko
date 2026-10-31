import subprocess, tempfile, os
t = tempfile.mkdtemp(dir='.tmpdbg')
def g(repo,*a):
    return subprocess.run(['git','-C',repo,*a],capture_output=True,text=True)
remote=os.path.join(t,'o.git'); subprocess.run(['git','init','-q','--bare','-b','main',remote])
seed=os.path.join(t,'s'); subprocess.run(['git','init','-q','-b','main',seed])
for args in [['config','user.name','t'],['config','user.email','t@e'],['remote','add','origin',remote]]: g(seed,*args)
open(seed+'/base.txt','w').write('base\n'); g(seed,'add','.'); g(seed,'commit','-q','-m','base'); g(seed,'push','-q','origin','main')
work=os.path.join(t,'w'); subprocess.run(['git','init','-q','-b','main',work])
for args in [['config','user.name','t'],['config','user.email','t@e'],['remote','add','origin',remote]]: g(work,*args)
g(work,'fetch','-q','origin','main'); g(work,'checkout','-q','-b','feat','origin/main')
open(work+'/base.txt','w').write('b\n'); g(work,'add','.'); g(work,'commit','-q','-m','br')
g(seed,'checkout','-q','main'); open(seed+'/base.txt','w').write('m\n'); g(seed,'add','.'); g(seed,'commit','-q','-m','m'); g(seed,'push','-q','origin','main')
g(work,'fetch','-q','origin','main')
for br in ['origin/main','main']:
    r=g(work,'rev-parse','--verify',br)
    print(br, r.returncode)
r=g(work,'merge-tree','--write-tree','--name-only','--no-messages','origin/main','feat')
print('rc',r.returncode); print('out',repr(r.stdout)); print('err',repr(r.stderr))
