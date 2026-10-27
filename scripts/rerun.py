"""Start a NEW API experiment from a published protocol. No implicit paid calls."""
import argparse, json, os, shutil, sys
from datetime import datetime, timezone
from pathlib import Path
from common import ROOT, batch, unpack, verify, read, write, sha

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',default='v4_formal');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--api-key-env',default='CHASSIS_API_KEY');p.add_argument('--model');p.add_argument('--base-url')
    p.add_argument('--allow-paid',action='store_true');p.add_argument('--dry-run',action='store_true')
    a=p.parse_args();b=batch(a.batch);verify(b)
    frozen=read(ROOT/'data/batches'/b['id']/'freeze.json')
    config=dict(frozen['model_settings'])
    if a.model:config.update(model=a.model,allowed_response_models=[a.model])
    if a.base_url:config['base_url']=a.base_url
    if a.dry_run:
        print(json.dumps({'batch':b['id'],'planned_runs':b['runs'],'model':config['model'],
              'base_url':config['base_url'],'output_exists':a.output.exists(),'api_calls':0,'credentials_read':False}));return
    if not a.allow_paid:p.error('--allow-paid is required; use --dry-run for a free protocol check')
    if a.output.exists():p.error('output already exists; use a fresh directory to preserve previous runs')
    key=os.environ.get(a.api_key_env)
    if not key:p.error('required API-key environment variable is unset; its value is never printed')
    # Work from exact historical code. No old result is loaded as a new model answer.
    output=a.output.resolve();protocol=unpack(b,output/'source_protocol')
    sys.path.insert(0,str(protocol/'source_snapshot'))
    from prototype.chat_api import public_settings, ModelServiceError
    config['api_key']=key
    new=output/'new_run';new.mkdir()
    for n in ['tasks.json','prompts.json','schedule.json']:shutil.copy2(protocol/n,new/n)
    write(new/'run_protocol.json',{'created_at':datetime.now(timezone.utc).isoformat(),
          'source_batch':b['id'],'source_freeze_id':frozen['freeze_id'],'source_archive_sha256':b['archive_sha256'],
          'model_settings':public_settings(config),'request_budget':frozen.get('budget',frozen.get('request_settings')),
          'new_experiment':True,'historical_results_untouched':True,
          'scope':'Frozen task/prompt/schedule reuse with run_one from the exact source snapshot; no old model response enters context.'})
    tasks={t['id']:t for t in read(new/'tasks.json')};prompts=read(new/'prompts.json');jobs=read(new/'schedule.json')
    if b['engine']=='v3':
        from prototype.process_comparison.study import run_one
    else:
        from prototype.chassis_skill_v4.study import run_one
    results=[]
    for i,job in enumerate(jobs):
        result=run_one(tasks[job['task_id']],job,new,prompts,config);results.append(result)
        write(new/'completed_results.json',results)
        print(json.dumps({'completed':i+1,'planned':len(jobs),'id':job['id']}),flush=True)
        if result.get('infrastructure_error') or result.get('service_error_requests',0):
            raise SystemExit('Infrastructure failure retained. Diagnose before creating another experiment; do not overwrite.')
    print(json.dumps({'status':'completed','runs':len(results)}))
if __name__=='__main__':main()
