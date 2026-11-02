"""Build the small app-identity wrapper used by macOS coaching LaunchAgents."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--developer-dir',type=Path);a=p.parse_args()
    bundle=a.output.expanduser().resolve()
    if bundle.suffix!='.app':raise SystemExit('--output must be an .app folder')
    env=os.environ.copy()
    if a.developer_dir:env['DEVELOPER_DIR']=str(a.developer_dir.resolve())
    clang=subprocess.check_output(['xcrun','--find','clang'],env=env,text=True).strip()
    sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],env=env,text=True).strip()
    binary=bundle/'Contents/MacOS/TennisCoach';binary.parent.mkdir(parents=True,exist_ok=True)
    info=dict(CFBundleIdentifier='local.tennis-coach.app',CFBundleName='Tennis Coach',CFBundleDisplayName='Tennis Coach',CFBundleExecutable='TennisCoach',CFBundlePackageType='APPL',CFBundleVersion='1',CFBundleShortVersionString='0.1.0',LSUIElement=True,NSLocalNetworkUsageDescription='Connect to your NAS tennis website to receive coaching tasks and return results.')
    (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    subprocess.run([clang,'-fobjc-arc','-fblocks','-isysroot',sdk,'-mmacosx-version-min=13.0',str(Path(__file__).with_name('mac_launcher.m')),'-o',str(binary),'-framework','AppKit','-framework','Network'],env=env,check=True)
    subprocess.run(['codesign','--force','--sign','-','--identifier','local.tennis-coach.app',str(bundle)],check=True)
    print(binary)


if __name__=='__main__':main()
