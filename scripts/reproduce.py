"""Verify archives, recompute reported summaries, or replay recorded runs offline."""
import argparse, collections, csv, json, math, random, statistics, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from common import ROOT, batches, rows, read, write, verify

def group(rs, key):
    return {m:{'valid':sum(bool(r[key]) for r in rs if r['method']==m),
               'runs':sum(r['method']==m for r in rs),
               'mean_requests':round(statistics.mean(r['model_requests'] for r in rs if r['method']==m),2),
               'requests':sum(r['model_requests'] for r in rs if r['method']==m)} for m in sorted({r['method'] for r in rs})}
def paired(rs,a='B',b='A'):
    per={}
    for r in rs:per.setdefault(r['task_id'],{}).setdefault(r['method'],[]).append(float(r['all_stages_valid']))
    diffs={t:statistics.mean(v[a])-statistics.mean(v[b]) for t,v in per.items() if a in v and b in v}
    ds=list(diffs.values());pos=sum(d>0 for d in ds);neg=sum(d<0 for d in ds);n=pos+neg
    rng=random.Random(202610215099)
    boot=sorted(statistics.mean(rng.choices(ds,k=len(ds))) for _ in range(10000))
    return {'tasks':len(ds),'difference_pp':round(100*statistics.mean(ds),1),
            'bootstrap95_pp':[round(100*boot[249],1),round(100*boot[9749],1)],
            'sign_test_p':round(min(1.,2*sum(math.comb(n,k) for k in range(min(pos,neg)+1))/2**n) if n else 1.,4),
            'favor_a':pos,'favor_b':neg,'ties':len(ds)-n,'per_task':diffs}
def summarize(out):
    rs={b['id']:rows(b) for b in batches()}
    result={'note':'Derived from individual archived result.json files; offline replay separately checks their scoring.',
            'unit':'Task; average repeated sessions within each task. Stages are not independent tasks.',
            'batches':{k:group(v,'valid_design' if k=='v3_formal' else 'all_stages_valid') for k,v in rs.items()}}
    t2=[r for r in rs['v4_formal'] if r['category']=='T2']+rs['v5_A_spec_retained']+rs['v5_C_spec_retained']
    glm=rs['v5_B_glm_formal']+rs['v5_C_glm_formal']
    result['deepseek_T2_combined']=group(t2,'all_stages_valid')
    result['B_minus_F_T2_combined']=paired(t2)
    result['B_minus_F_T2_new_only']=paired(rs['v5_A_spec_retained'])
    result['glm_formal']=group(glm,'all_stages_valid')
    means={m:statistics.mean(r['model_requests'] for r in glm if r['method']==m) for m in 'ABC'}
    result['glm_fixed_request_reduction_percent']={m:round(100*(1-means['A']/means[m]),1) for m in 'BC'}
    result['interpretation']='The combined T2 analysis includes discovery tasks and is exploratory; the six new tasks alone give p=0.25. S groups in v5 were collected a day later. No universal agent superiority or human-time saving is established.'
    ref=read(ROOT/'data/original_reports/v5_statistics.json')
    for ours,theirs in [('B_minus_F_T2_combined','A_combined_T2_v4_plus_new'),('B_minus_F_T2_new_only','A_new_tasks_only')]:
        assert result[ours]['difference_pp']==ref[theirs]['mean_difference_pp']
        assert result[ours]['bootstrap95_pp']==ref[theirs]['bootstrap95_pp']
        assert result[ours]['sign_test_p']==ref[theirs]['exact_sign_test_two_sided_p']
    expected={'v3_formal':{'M0':21,'M1':23,'M2':24},'v4_formal':{'A':18,'B':23,'C':23},
              'v5_A_spec_retained':{'A':6,'B':12},'v5_B_glm_dev':{'A':6,'B':6},
              'v5_B_glm_formal':{'A':24,'B':24},'v5_C_spec_retained':{'C':11},'v5_C_glm_formal':{'C':24}}
    assert all({m:v['valid'] for m,v in result['batches'][b].items()}==v for b,v in expected.items())
    result['published_counts_and_primary_statistics_match']=True
    write(out/'summary.json',result)
    with (out/'batch_summary.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['batch','method','valid','runs','mean_requests','requests']);w.writeheader()
        for b,gs in result['batches'].items():
            for m,g in gs.items():w.writerow({'batch':b,'method':m,**g})
    print(json.dumps({'status':'passed','summary':str(out/'summary.json'),'glm_fixed_request_reduction_percent':result['glm_fixed_request_reduction_percent']}))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['verify','summarize','replay','all'])
    p.add_argument('--batch',default='all',choices=['all']+[b['id'] for b in batches()])
    p.add_argument('--output',type=Path,default=ROOT/'.repro'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    a=p.parse_args(); selected=[b for b in batches() if a.batch=='all' or b['id']==a.batch]
    a.output.mkdir(parents=True,exist_ok=True)
    if a.mode in ['verify','all']:
        checks=[]
        for b in selected:
            r=verify(b);checks.append(r);print(json.dumps(r),flush=True)
        write(a.output/'integrity.json',checks)
    if a.mode in ['summarize','all']:summarize(a.output)
    if a.mode in ['replay','all']:
        for b in selected:
            subprocess.run([sys.executable,'-B',str(ROOT/'scripts/replay_batch.py'),'--batch',b['id'],'--output',str(a.output/b['id'])],check=True)
    print(json.dumps({'status':'complete','output':str(a.output),'api_calls':0}),flush=True)
if __name__=='__main__':main()
