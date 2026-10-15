#!/usr/bin/env python3
import csv, math, re, sys
from pathlib import Path

mode = sys.argv[1]
out = Path(sys.argv[2])
manifest = out / 'runs.tsv'

def num(x):
    try:
        v = float(x)
        if math.isfinite(v): return v
    except (TypeError, ValueError):
        pass
    return None

def last_match(text, pattern, group=1, flags=re.I | re.M):
    matches = list(re.finditer(pattern, text, flags))
    if not matches: return None
    return num(matches[-1].group(group))

def last_ints(text, pattern):
    matches = list(re.finditer(pattern, text, re.I | re.M))
    if not matches: return None
    try: return int(matches[-1].group(1))
    except (ValueError, TypeError): return None

def parse_log(path):
    p = Path(path)
    if not p.exists(): return {}
    text = p.read_text(errors='replace')
    d = {}
    d['ipc'] = last_match(text, r'CPU\s+0 cumulative IPC:\s*([0-9.eE+-]+)')
    l1 = re.compile(r'^L1D LOAD\s+ACCESS:\s*\d+\s+HIT:\s*\d+\s+MISS:\s*(\d+).*?MPKI:\s*([0-9.eE+-]+)', re.I | re.M)
    l2 = re.compile(r'^L2C LOAD\s+ACCESS:\s*\d+\s+HIT:\s*\d+\s+MISS:\s*(\d+).*?MPKI:\s*([0-9.eE+-]+)', re.I | re.M)
    m1 = list(l1.finditer(text)); m2 = list(l2.finditer(text))
    d['l1d_load_misses'] = int(m1[-1].group(1)) if m1 else None
    d['l1d_load_mpki'] = num(m1[-1].group(2)) if m1 else None
    d['l2c_load_misses'] = int(m2[-1].group(1)) if m2 else None
    d['l2c_load_mpki'] = num(m2[-1].group(2)) if m2 else None
    d['l2_instruction_load_mpki'] = last_match(text, r'^L2C INSTRUCTION LOAD MPKI:\s*([0-9.eE+-]+)')
    d['l2_data_load_mpki'] = last_match(text, r'^L2C DATA LOAD MPKI:\s*([0-9.eE+-]+)')
    pf = list(re.finditer(r'^L1D PREFETCH\s+REQUESTED:\s*(\d+)\s+ISSUED:\s*(\d+)\s+USEFUL:\s*(\d+)\s+USELESS:\s*(\d+)', text, re.I | re.M))
    if pf:
        d['prefetch_requested'], d['prefetch_issued'], d['prefetch_useful'], d['prefetch_useless'] = map(int, pf[-1].groups())
    else:
        d.update({k: None for k in ('prefetch_requested','prefetch_issued','prefetch_useful','prefetch_useless')})
    low = list(re.finditer(r'^L1D USEFUL LOAD PREFETCHES:\s*(\d+)\s+PREFETCH ISSUED TO LOWER LEVEL:\s*(\d+)', text, re.I | re.M))
    if low:
        d['useful_load_prefetches'] = int(low[-1].group(1))
        d['prefetch_issued_to_lower_level'] = int(low[-1].group(2))
        den = d['prefetch_issued_to_lower_level']
        d['prefetch_accuracy_pct'] = (100.0 * d['useful_load_prefetches'] / den) if den else None
    else:
        d.update({'useful_load_prefetches': None, 'prefetch_issued_to_lower_level': None, 'prefetch_accuracy_pct': None})
    d['final_instruction_pressure'] = last_match(text, r'^Final instruction pressure:\s*([0-9.eE+-]+)')
    pol = re.findall(r'^Follower policy at end:\s*(.+?)\s*$', text, re.I | re.M)
    d['follower_policy_at_end'] = pol[-1] if pol else ''
    for col, pat in [
        ('rrip_leader_instruction_accesses', r'^RRIP leader instruction accesses:\s*(\d+)'),
        ('rrip_leader_data_accesses', r'^RRIP leader data accesses:\s*(\d+)'),
        ('rrip_leader_instruction_misses', r'^RRIP leader instruction misses:\s*(\d+)'),
        ('rrip_leader_data_misses', r'^RRIP leader data misses:\s*(\d+)'),
        ('l2_prefetch_fills', r'^L2 prefetch fills:\s*(\d+)'),
        ('l2_prefetch_demand_hits', r'^L2 prefetch-line demand hits .*?:\s*(\d+)'),
        ('l2_unused_prefetch_evictions', r'^L2 unused-prefetch evictions:\s*(\d+)'),
    ]:
        d[col] = last_ints(text, pat)
    d['log_completed'] = 'ChampSim completed all CPUs' in text
    return d

with manifest.open(newline='') as f:
    runs = list(csv.DictReader(f, delimiter='\t'))
rows = []
for run in runs:
    parsed = parse_log(run.get('log_file', ''))
    row = dict(run)
    row.update(parsed)
    try: rc = int(run.get('exit_code', '1'))
    except ValueError: rc = 1
    row['status'] = 'OK' if rc == 0 and row.get('log_completed') and row.get('ipc') is not None else 'FAILED_OR_INCOMPLETE'
    row['speedup_vs_lru_no_prefetch'] = None
    row['speedup_vs_matching_no_prefetch'] = None
    row['ipc_ratio_vs_dynamic_no_prefetch'] = None
    row['ipc_ratio_vs_spp_rrip'] = None
    row['ipc_ratio_vs_spp_lru'] = None
    row['l1d_coverage_pct'] = None
    rows.append(row)

# Calculate per-trace comparisons only against successful rows.
def find_row(config, trace):
    return next((r for r in rows if r.get('config') == config and r.get('trace') == trace and r.get('status') == 'OK'), None)
for r in rows:
    if r.get('status') != 'OK': continue
    trace = r.get('trace')
    lru = find_row('LRU' if mode == '2b' else 'BASE_LRU', trace)
    if lru and lru.get('ipc'):
        r['speedup_vs_lru_no_prefetch'] = r['ipc'] / lru['ipc']
    if mode == '3':
        match_cfg = {'SPP_LRU':'BASE_LRU', 'SPP_RRIP':'BASE_RRIP', 'SPP_DYN_IRRIP':'BASE_DYN_IRRIP'}.get(r.get('config'))
        base = find_row(match_cfg, trace) if match_cfg else None
        if base:
            if base.get('ipc'):
                r['speedup_vs_matching_no_prefetch'] = r['ipc'] / base['ipc']
            if base.get('l1d_load_misses') not in (None, 0) and r.get('l1d_load_misses') is not None:
                r['l1d_coverage_pct'] = 100.0 * (base['l1d_load_misses'] - r['l1d_load_misses']) / base['l1d_load_misses']
        if r.get('config') == 'SPP_DYN_IRRIP':
            dyn = find_row('BASE_DYN_IRRIP', trace)
            spp_rrip = find_row('SPP_RRIP', trace)
            spp_lru = find_row('SPP_LRU', trace)
            if dyn and dyn.get('ipc'):
                r['ipc_ratio_vs_dynamic_no_prefetch'] = r['ipc'] / dyn['ipc']
            if spp_rrip and spp_rrip.get('ipc'):
                r['ipc_ratio_vs_spp_rrip'] = r['ipc'] / spp_rrip['ipc']
            if spp_lru and spp_lru.get('ipc'):
                r['ipc_ratio_vs_spp_lru'] = r['ipc'] / spp_lru['ipc']

fields = ['config','threshold','warmup_instructions','simulation_instructions','prefetcher','replacement','binary','trace','exit_code','elapsed_seconds','status','ipc','speedup_vs_lru_no_prefetch','speedup_vs_matching_no_prefetch','ipc_ratio_vs_dynamic_no_prefetch','ipc_ratio_vs_spp_rrip','ipc_ratio_vs_spp_lru','l1d_load_misses','l1d_load_mpki','l1d_coverage_pct','l2c_load_misses','l2c_load_mpki','l2_instruction_load_mpki','l2_data_load_mpki','prefetch_requested','prefetch_issued','prefetch_useful','prefetch_useless','useful_load_prefetches','prefetch_issued_to_lower_level','prefetch_accuracy_pct','final_instruction_pressure','follower_policy_at_end','rrip_leader_instruction_accesses','rrip_leader_data_accesses','rrip_leader_instruction_misses','rrip_leader_data_misses','l2_prefetch_fills','l2_prefetch_demand_hits','l2_unused_prefetch_evictions','log_file']
with (out/'results.csv').open('w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader()
    for r in rows:
        w.writerow({k: ('' if r.get(k) is None else r.get(k)) for k in fields})

def mean(vals):
    vals = [float(v) for v in vals if v is not None and isinstance(v, (int,float)) and math.isfinite(v)]
    return sum(vals)/len(vals) if vals else None

def gm(vals):
    vals = [float(v) for v in vals if v is not None and isinstance(v, (int,float)) and math.isfinite(v) and v > 0]
    return math.exp(sum(math.log(v) for v in vals)/len(vals)) if vals else None

def fmt(v): return 'NA' if v is None else f'{v:.6f}'

warmup_cfg = runs[0].get('warmup_instructions', '25000000') if runs else '25000000'
sim_cfg = runs[0].get('simulation_instructions', '25000000') if runs else '25000000'
summary_lines = [f'CS683 PA2 Task {mode.upper()} — Simulation Summary', '='*66,
                 f'warmup_instructions={warmup_cfg}; simulation_instructions={sim_cfg}.',
                 'All reported metrics are parsed from the final Region of Interest output. Missing values remain blank/NA; they are not coerced to zero.',
                 'Speedup vs LRU uses the no-prefetch LRU result for the same trace. Task 3 matching-policy coverage uses L1D load misses with matching no-prefetch replacement baseline.', '']
summary_rows = []
if mode == '2b':
    summary_lines += ['Per-threshold summary (geometric mean of per-trace IPC speedups vs LRU)', 'Threshold\tSuccessful traces\tMean IPC\tGM speedup vs LRU\tMean L2C Load MPKI\tMean L2 Instruction Load MPKI\tMean L2 Data Load MPKI']
    thresholds = sorted({r.get('threshold','') for r in rows if r.get('config','').startswith('DYN_T')}, key=lambda x: float(x) if x else 999)
    for th in thresholds:
        rs = [r for r in rows if r.get('config','').startswith('DYN_T') and r.get('threshold') == th and r.get('status') == 'OK']
        item = {'threshold': th, 'successful_traces': len(rs), 'mean_ipc': mean([r.get('ipc') for r in rs]), 'geomean_speedup_vs_lru': gm([r.get('speedup_vs_lru_no_prefetch') for r in rs]), 'mean_l2c_load_mpki': mean([r.get('l2c_load_mpki') for r in rs]), 'mean_l2_instruction_load_mpki': mean([r.get('l2_instruction_load_mpki') for r in rs]), 'mean_l2_data_load_mpki': mean([r.get('l2_data_load_mpki') for r in rs])}
        summary_rows.append(item)
        summary_lines.append('\t'.join([str(item['threshold']), str(item['successful_traces']), fmt(item['mean_ipc']), fmt(item['geomean_speedup_vs_lru']), fmt(item['mean_l2c_load_mpki']), fmt(item['mean_l2_instruction_load_mpki']), fmt(item['mean_l2_data_load_mpki'])]))
    with (out/'threshold_summary.csv').open('w', newline='') as f:
        w=csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()) if summary_rows else ['threshold','successful_traces']); w.writeheader(); w.writerows(summary_rows)
    best = max(summary_rows, key=lambda x: x['geomean_speedup_vs_lru'] if x['geomean_speedup_vs_lru'] is not None else -1, default=None)
    summary_lines += ['', 'Baseline per-trace comparison', 'Config\tTrace\tIPC\tSpeedup vs LRU\tL2C Load MPKI\tL2 Instruction Load MPKI\tL2 Data Load MPKI']
    for r in rows:
        if r.get('config') in ('LRU','RRIP','IRRIP4') or r.get('config','').startswith('DYN_T'):
            summary_lines.append('\t'.join([r.get('config',''),r.get('trace',''),fmt(r.get('ipc')),fmt(r.get('speedup_vs_lru_no_prefetch')),fmt(r.get('l2c_load_mpki')),fmt(r.get('l2_instruction_load_mpki')),fmt(r.get('l2_data_load_mpki'))]))
    if best:
        summary_lines += ['', f"Best threshold by measured GM speedup (among successful traces): {best['threshold']} (GM={fmt(best['geomean_speedup_vs_lru'])}); review every trace and MPKI trade-off before selecting it."]
else:
    summary_lines += ['Per-configuration summary', 'Config\tSuccessful traces\tMean IPC\tGM speedup vs LRU\tMean L1D Load MPKI\tMean prefetch accuracy %\tMean L1D coverage %\tMean L2C Load MPKI\tMean L2 Instruction Load MPKI\tMean L2 Data Load MPKI']
    configs = list(dict.fromkeys(r.get('config','') for r in rows))
    for cfg in configs:
        rs=[r for r in rows if r.get('config')==cfg and r.get('status')=='OK']
        item={'config':cfg,'successful_traces':len(rs),'mean_ipc':mean([r.get('ipc') for r in rs]),'geomean_speedup_vs_lru':gm([r.get('speedup_vs_lru_no_prefetch') for r in rs]),'mean_l1d_load_mpki':mean([r.get('l1d_load_mpki') for r in rs]),'mean_prefetch_accuracy_pct':mean([r.get('prefetch_accuracy_pct') for r in rs]),'mean_l1d_coverage_pct':mean([r.get('l1d_coverage_pct') for r in rs]),'mean_l2c_load_mpki':mean([r.get('l2c_load_mpki') for r in rs]),'mean_l2_instruction_load_mpki':mean([r.get('l2_instruction_load_mpki') for r in rs]),'mean_l2_data_load_mpki':mean([r.get('l2_data_load_mpki') for r in rs])}
        summary_rows.append(item)
        summary_lines.append('\t'.join([str(item['config']),str(item['successful_traces']),fmt(item['mean_ipc']),fmt(item['geomean_speedup_vs_lru']),fmt(item['mean_l1d_load_mpki']),fmt(item['mean_prefetch_accuracy_pct']),fmt(item['mean_l1d_coverage_pct']),fmt(item['mean_l2c_load_mpki']),fmt(item['mean_l2_instruction_load_mpki']),fmt(item['mean_l2_data_load_mpki'])]))
    with (out/'configuration_summary.csv').open('w', newline='') as f:
        w=csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()) if summary_rows else ['config','successful_traces']); w.writeheader(); w.writerows(summary_rows)
    for trace in sorted({r.get('trace','') for r in rows}):
        vals={r.get('config'):r for r in rows if r.get('trace')==trace}
        d=vals.get('SPP_DYN_IRRIP'); base=vals.get('BASE_DYN_IRRIP'); spp=vals.get('SPP_RRIP'); spp_lru=vals.get('SPP_LRU')
        if d:
            summary_lines += ['', f'Comparison check for {trace}:']
            summary_lines.append(f"  SPP + Dynamic I-RRIP IPC: {fmt(d.get('ipc'))}")
            summary_lines.append(f"  Dynamic I-RRIP alone IPC: {fmt(base.get('ipc') if base else None)}")
            summary_lines.append(f"  SPP + RRIP IPC: {fmt(spp.get('ipc') if spp else None)}")
            summary_lines.append(f"  SPP + LRU IPC: {fmt(spp_lru.get('ipc') if spp_lru else None)}")
            summary_lines.append('  Task 3 IPC objective met on this trace vs both Dynamic I-RRIP and standalone SPP+RRIP: ' + ('YES' if d.get('ipc') is not None and base and spp and base.get('ipc') is not None and spp.get('ipc') is not None and d['ipc'] > base['ipc'] and d['ipc'] > spp['ipc'] else 'NO / INCOMPLETE'))

fail=[r for r in rows if r.get('status')!='OK']
summary_lines += ['', f'Failed or incomplete runs: {len(fail)}']
for r in fail:
    summary_lines.append(f"  {r.get('config')} | {r.get('trace')} | exit={r.get('exit_code')} | log={r.get('log_file')}")
(out/'summary.txt').write_text('\n'.join(summary_lines)+'\n')

# Plots are optional: CSV and text summaries are still written if matplotlib is unavailable.
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    graphdir=out/'graphs'; graphdir.mkdir(exist_ok=True)
    good=[r for r in rows if r.get('status')=='OK']
    traces=sorted({r.get('trace','') for r in good})
    def plot_lines(metric, ylabel, name, title):
        plt.figure(figsize=(8.5,5))
        if mode=='2b':
            for tr in traces:
                rr=sorted([r for r in good if r.get('trace')==tr and r.get('config','').startswith('DYN_T') and r.get(metric) is not None], key=lambda r:float(r.get('threshold') or 0))
                if rr: plt.plot([float(r['threshold']) for r in rr],[r[metric] for r in rr],marker='o',label=tr)
            plt.xlabel('Dynamic I-RRIP decision threshold')
        else:
            configs=list(dict.fromkeys(r.get('config','') for r in good))
            width=0.8/max(1,len(configs)); xs=list(range(len(traces)))
            for i,cfg in enumerate(configs):
                values=[]
                for tr in traces:
                    x=next((r.get(metric) for r in good if r.get('config')==cfg and r.get('trace')==tr),None)
                    values.append(float('nan') if x is None else x)
                plt.bar([x-0.4+width*(i+0.5) for x in xs],values,width=width,label=cfg)
            plt.xticks(xs,traces,rotation=20,ha='right'); plt.xlabel('Trace')
        plt.ylabel(ylabel); plt.title(title); plt.grid(True,axis='y',alpha=.25); plt.legend(fontsize=7); plt.tight_layout(); plt.savefig(graphdir/name,dpi=170); plt.close()
    if mode=='2b':
        for metric, ylabel, name, title in [
            ('ipc','IPC','threshold_ipc.png','Task 2B: IPC vs threshold'),
            ('speedup_vs_lru_no_prefetch','Speedup vs LRU','threshold_speedup.png','Task 2B: speedup vs threshold'),
            ('l2c_load_mpki','L2C Load MPKI','threshold_l2c_load_mpki.png','Task 2B: L2C Load MPKI vs threshold'),
            ('l2_instruction_load_mpki','L2 instruction Load MPKI','threshold_l2_instruction_mpki.png','Task 2B: instruction Load MPKI vs threshold'),
            ('l2_data_load_mpki','L2 data Load MPKI','threshold_l2_data_mpki.png','Task 2B: data Load MPKI vs threshold')]: plot_lines(metric,ylabel,name,title)
    else:
        for metric, ylabel, name, title in [
            ('ipc','IPC','ipc.png','Task 3: IPC comparisons'),
            ('speedup_vs_lru_no_prefetch','Speedup vs no-prefetch LRU','speedup.png','Task 3: speedup comparisons'),
            ('prefetch_accuracy_pct','Prefetch accuracy (%)','prefetch_accuracy.png','Task 3: L1D prefetch accuracy'),
            ('l1d_coverage_pct','L1D coverage (%)','l1d_coverage.png','Task 3: L1D coverage vs matching no-prefetch policy'),
            ('l1d_load_mpki','L1D Load MPKI','l1d_load_mpki.png','Task 3: L1D Load MPKI'),
            ('l2c_load_mpki','L2C Load MPKI','l2c_load_mpki.png','Task 3: L2C Load MPKI'),
            ('l2_instruction_load_mpki','L2 instruction Load MPKI','l2_instruction_load_mpki.png','Task 3: L2 instruction Load MPKI'),
            ('l2_data_load_mpki','L2 data Load MPKI','l2_data_load_mpki.png','Task 3: L2 data Load MPKI')]: plot_lines(metric,ylabel,name,title)
    print(f'Graphs written to {graphdir}')
except Exception as e:
    print(f'WARNING: Could not create graphs ({e}). CSV/text reports were still written. Install with: sudo apt install python3-matplotlib', file=sys.stderr)

print(f'Results CSV: {out / "results.csv"}')
print(f'Summary: {out / "summary.txt"}')
if mode=='2b': print(f'Threshold summary: {out / "threshold_summary.csv"}')
else: print(f'Configuration summary: {out / "configuration_summary.csv"}')

