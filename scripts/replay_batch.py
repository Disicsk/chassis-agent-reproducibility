"""Replay original audit logic from the batch's exact source snapshot, without keys or network."""
import argparse, importlib.util, json, socket, sys
from pathlib import Path
from common import ROOT, batch, unpack, verify, read, write

def blocked(*args,**kwargs):raise RuntimeError('network_disabled_for_offline_replay')
def main():
    p=argparse.ArgumentParser();p.add_argument('--batch',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    b=batch(a.batch);folder=unpack(b,a.output/'records');snapshot=folder/'source_snapshot'
    sys.path.insert(0,str(snapshot))
    socket.socket.connect=blocked;socket.create_connection=blocked
    frozen=read(folder/'freeze.json')
    # Equivalent frozen-byte verification with portable slash handling. The original
    # verifier compares live files too; here every import comes from this frozen snapshot.
    def frozen_verify(_folder):
        assert Path(_folder).resolve()==folder.resolve()
        verify(b)
        return frozen
    marker='__NO_REAL_CREDENTIAL_IN_PUBLIC_OFFLINE_REPLAY_20261027__'
    if b['engine']=='v3':
        spec=importlib.util.spec_from_file_location('original_v3_audit',ROOT/'scripts/original_v3_audit.py')
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mod.ROOT=folder.parent;mod.PROJECT=snapshot;mod.verify=frozen_verify
        original_sha=mod.sha
        mod.sha=lambda path:original_sha(Path(str(path).replace('\\','/')))
        mod.load_config=lambda *args,**kwargs:{'api_key':marker}
        mod.audit(folder.name);result=read(folder/'audit.json')
    else:
        from prototype.chassis_skill_v4 import audit as mod
        mod.verify=frozen_verify;mod.settings=lambda:{'api_key':marker}
        result=mod.audit(folder.parent,folder.name)
    assert result['status']=='passed'
    count=result.get('runs',result.get('completed'))
    assert count==b['runs']
    write(a.output/'replay_report.json',{'batch':b['id'],'status':'passed','runs':count,
          'requests':b['requests'],'api_calls':0,'network_disabled':True,
          'audit_report':str(Path('records')/'audit.json'),
          'adaptations':['Normalize snapshot paths across operating systems','Run in a fresh export workspace','Replace credential-dependent historical audit scan with a non-secret marker; never load credentials'],
          'credential_scan_scope':'Original-key scan is not repeated by public users. Export-time scan is documented in data/export_audit.json.',
          'scope':'Recorded messages, actions, engineering outputs, state transitions, scripted reviews, scoring and usage; excludes provider internals and prose semantic correctness'})
    print(json.dumps({'batch':b['id'],'replay':'passed','runs':count,'api_calls':0}),flush=True)
if __name__=='__main__':main()
