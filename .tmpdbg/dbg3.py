import subprocess, tempfile, os
tmp = tempfile.mkdtemp()
print('tmp:', tmp)
r = subprocess.run(['git','init','-q','-b','main',tmp],capture_output=True,text=True)
print('init rc', r.returncode, r.stderr)
print('exists .git:', os.path.exists(os.path.join(tmp, '.git')))
print('lsdir:', os.listdir(tmp))
r2 = subprocess.run(['git','-C',tmp,'status'],capture_output=True,text=True)
print('status rc', r2.returncode, r2.stderr)
