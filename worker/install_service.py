"""Optional macOS worker service generator/controller. No IDE paths required."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['install','start','stop','restart','status']);p.add_argument('--role',choices=['ai','render'],required=True);p.add_argument('--config',type=Path);p.add_argument('--executable',default=shutil.which('tennis-coach-worker'));a=p.parse_args()
    label='local.tennis-coach.'+a.role;domain='gui/'+str(os.getuid());agent=Path.home()/'Library/LaunchAgents'/f'{label}.plist'
    if a.action=='install':
        if not a.executable or not a.config:raise SystemExit('--executable and --config required')
        cfg=a.config.expanduser().resolve();logs=Path.home()/'Library/Logs/tennis-coach';logs.mkdir(parents=True,exist_ok=True);agent.parent.mkdir(parents=True,exist_ok=True)
        data=dict(Label=label,ProgramArguments=[str(Path(a.executable).resolve()),'--config',str(cfg),'--role',a.role],RunAtLoad=True,KeepAlive=True,ThrottleInterval=15,StandardOutPath=str(logs/(a.role+'.log')),StandardErrorPath=str(logs/(a.role+'.error.log')))
        agent.write_bytes(plistlib.dumps(data));print('Installed definition. Use start to run it.');return
    if a.action=='stop':subprocess.run(['launchctl','bootout',domain+'/'+label],check=True)
    elif a.action=='start':subprocess.run(['launchctl','bootstrap',domain,str(agent)],check=True)
    elif a.action=='restart':subprocess.run(['launchctl','kickstart','-k',domain+'/'+label],check=True)
    else:subprocess.run(['launchctl','print',domain+'/'+label],check=True)

if __name__=='__main__':main()
