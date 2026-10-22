"""Reproducible ID-matched minimum-image measurement; run with staged inputs."""
import json, hashlib, itertools
from pathlib import Path
import numpy as np
from ovito.io import import_file
from ovito.modifiers import CommonNeighborAnalysisModifier

OUT=Path('/workspace'); GB=-0.700778484; A0=3.52
SOURCES=[dict(frame=77,path='/inputs/000-001_gb_shear_step_0077_minimized.cfg',sha256='1ae35958e3e21ad9f7519f9dc2a78e7519b40276212ee440fc24931874b724d1',artifact_id='fd34ba28-3de8-564e-a353-cf4c8e57cfbb'),dict(frame=80,path='/inputs/001-002_gb_shear_step_0080_minimized.cfg',sha256='60862ef89b28be07bf4cae161f25e283c5540e8c885f935df76c111069a8d7b8',artifact_id='278a3369-5c91-5e52-b400-374a947d39d5')]
def load(s):
    assert hashlib.sha256(Path(s['path']).read_bytes()).hexdigest()==s['sha256']
    pipe=import_file(s['path']); pipe.modifiers.append(CommonNeighborAnalysisModifier(mode=CommonNeighborAnalysisModifier.Mode.AdaptiveCutoff)); d=pipe.compute()
    ids=np.asarray(d.particles['Particle Identifier']); order=np.argsort(ids)
    assert len(ids)==11052 and len(np.unique(ids))==11052
    return dict(ids=ids[order],r=np.asarray(d.particles.positions)[order],cna=np.asarray(d.particles['Structure Type'])[order],cell=np.asarray(d.cell),pbc=list(d.cell.pbc),types=np.asarray(d.particles['Particle Type'])[order],type_names={t.id:t.name for t in d.particles['Particle Type'].types})
def scalar(x):
    return dict(median=float(np.median(x)),mean=float(np.mean(x)),min=float(np.min(x)),max=float(np.max(x)),p10=float(np.percentile(x,10)),p90=float(np.percentile(x,90)))
def stats(d):
    med=np.median(d,axis=0); mean=d.mean(axis=0)
    return dict(count=len(d),mean=mean.tolist(),median=med.tolist(),std=np.std(d,axis=0,ddof=1).tolist(),MAD=np.median(np.abs(d-med),axis=0).tolist(),p10=np.percentile(d,10,axis=0).tolist(),p90=np.percentile(d,90,axis=0).tolist(),descriptive_SE_mean=(np.std(d,axis=0,ddof=1)/np.sqrt(len(d))).tolist(),magnitude=scalar(np.linalg.norm(d,axis=1)),residual_after_grain_mean=dict(component_std=np.std(d-mean,axis=0,ddof=1).tolist(),magnitude=scalar(np.linalg.norm(d-mean,axis=1))))
def extent(data):
    y=data['r'][:,1]
    return {g:[float(y[side].min()),float(y[side].max())] for g,side in [('top',y>GB),('bottom',y<GB)]}

SOURCES.append(dict(frame=84,path='/inputs/002-003_gb_shear_step_0084_minimized.cfg',sha256='a47c52a1789052964dbbcb02fa7d5abff029521165b5beb3d117594dcc950b6e',artifact_id='86bd3cd1-b8b8-585c-a52a-90ce81cbd5e9'))

frames={s['frame']:load(s) for s in SOURCES}
r=frames[77]; H=r['cell'][:,:3]; L=np.diag(H)
assert np.array_equal(H,np.diag(L))
for f in frames.values():
    assert np.array_equal(f['ids'],r['ids']) and np.array_equal(f['cell'],r['cell']) and all(f['pbc'])
    assert np.array_equal(f['types'],r['types'])
def mic(v): return (v/L-np.rint(v/L))*L
fcc=int(CommonNeighborAnalysisModifier.Type.FCC)
def mask(fs,g,e):
    m=np.ones(11052,dtype=bool)
    for f in fs:
        y=f['r'][:,1]; ex=extent(f)[g]
        geom=(y>=GB+e)&(y<=ex[1]-e) if g=='top' else (y<=GB-e)&(y>=ex[0]+e)
        m &= geom & (f['cna']==fcc)
    return m
pair_ids=[(77,80),(80,84),(77,84)]; pairs={}
for a,b in pair_ids:
    key=f'{a}->{b}'; d=mic(frames[b]['r']-frames[a]['r']); res={}
    for mode in ['pair_both_frames','common_three_frames']:
        res[mode]={}
        for e in [10,20,30]:
            grains={}
            for g in ['top','bottom']:
                m=mask([frames[a],frames[b]] if mode=='pair_both_frames' else list(frames.values()),g,e)
                s=stats(d[m]); s['residual_RMS_A']=float(np.sqrt(np.mean(np.sum((d[m]-np.mean(d[m],axis=0))**2,axis=1))))
                grains[g]=s
            rel={}
            for est in ['mean','median']:
                v=np.array(grains['top'][est])-grains['bottom'][est]
                rel[est]={'xyz_A':v.tolist(),'transverse_yz_norm_A':float(np.linalg.norm(v[1:])), 'full_a_1m21_coefficient_c':float(v[0]/(A0*np.sqrt(6))), 'half_a_1m21_coefficient_2c':float(2*v[0]/(A0*np.sqrt(6)))}
            res[mode][str(e)]={'grains':grains,'top_minus_bottom':rel}
    pairs[key]={'exclusion_results':res,'sensitivity':{}}
    for mode in res:
        pairs[key]['sensitivity'][mode]={}
        for est in ['mean','median']:
            vs=np.array([res[mode][str(e)]['top_minus_bottom'][est]['xyz_A'] for e in [10,20,30]])
            pairs[key]['sensitivity'][mode][est]={'min_xyz_A':vs.min(axis=0).tolist(),'max_xyz_A':vs.max(axis=0).tolist(),'max_difference_from_20_A':float(np.max(np.linalg.norm(vs-vs[1],axis=1)))}
old=json.loads(Path('/inputs/004-grain_shift.json').read_text())
for e in [10,20,30]:
    for g in ['top','bottom']:
        y=r['r'][:,1]; ex=extent(r)[g]
        geom=(y>=GB+e)&(y<=ex[1]-e) if g=='top' else (y<=GB-e)&(y>=ex[0]+e)
        m=geom&(r['cna']==fcc)&(frames[80]['cna']==fcc)
        s=stats(mic(frames[80]['r']-r['r'])[m]); prior=old['exclusion_results'][str(e)]['grains'][g]
        assert s['count']==prior['count'] and np.allclose(s['mean'],prior['mean'],atol=1e-12,rtol=0)
additivity={}
d1=mic(frames[80]['r']-r['r']); d2=mic(frames[84]['r']-frames[80]['r']); dn=mic(frames[84]['r']-r['r'])
assert np.max(np.abs(d1+d2-dn))<1e-10
for e in [10,20,30]:
    additivity[str(e)]={}
    for est in ['mean','median']:
        vs=[np.array(pairs[f'{a}->{b}']['exclusion_results']['common_three_frames'][str(e)]['top_minus_bottom'][est]['xyz_A']) for a,b in pair_ids]
        additivity[str(e)][est+'_net_minus_sum_xyz_A']=(vs[2]-vs[0]-vs[1]).tolist()
from ovito.data import DataCollection, Particles, SimulationCell, NearestNeighborFinder
def collection(pos):
    d=DataCollection(); p=Particles(); p.create_property('Position',data=pos); d.objects.append(p)
    c=SimulationCell(pbc=(True,True,True)); c[:]=r['cell']; d.objects.append(c); return d

def geometry(a,b,reflect,translation=None):
    source=frames[a]['r'].copy(); target=frames[b]['r']; side_s=source[:,1]>GB; side_t=target[:,1]>GB
    if reflect: source[:,2]*=-1
    groups=[]
    for side in [False,True]:
        si=np.flatnonzero(side_s==side); ti=np.flatnonzero(side_t==side)
        assert len(si)==len(ti)
        assert all(frames[a]['type_names'][int(t)]=='Ni' for t in frames[a]['types'][si])
        assert all(frames[b]['type_names'][int(t)]=='Ni' for t in frames[b]['types'][ti])
        data=collection(target[ti]); finder=NearestNeighborFinder(2,data)
        groups.append((si,ti,data,finder))
    def evaluate(tx,details=False):
        vals=[]; out=[]
        for si,ti,data,finder in groups:
            q=source[si].copy()
            if translation is None: q[:,0]+=tx
            else: q+=translation['top' if side_s[si[0]] else 'bottom']
            ni,nv=finder.find_all_at(q); ds=np.linalg.norm(nv[:,0,:],axis=1)
            vals.append(nv[:,0,:])
            if details:
                ids=ni[:,0]; counts=np.bincount(ids,minlength=len(ti)); unique=counts[ids]==1
                revdata=collection(q); rev=NearestNeighborFinder(1,revdata); ri,rv=rev.find_all_at(target[ti]); mutual=ri[ids,0]==np.arange(len(si)); good=unique&mutual
                slab=(np.abs(frames[a]['r'][si,1]-GB)<=10)&(np.abs(target[ti[ids],1]-GB)<=10)
                def metrics(sel):
                    z=ds[sel]
                    return {'count':int(sel.sum()),'RMS_A':float(np.sqrt(np.mean(z*z))) if len(z) else None,'median_A':float(np.median(z)) if len(z) else None,'p90_A':float(np.percentile(z,90)) if len(z) else None,'max_A':float(z.max()) if len(z) else None,'counts_within_A':{str(t):int((z<=t).sum()) for t in [0.001,0.01,0.05,0.1,0.25,0.5,1.0]}}
                out.append({'grain':'top' if side_s[si[0]] else 'bottom','N':len(si),'unique_target_count':int(np.count_nonzero(counts)),'unmatched_targets':int(np.count_nonzero(counts==0)),'conflicted_sources':int((~unique).sum()),'unique_mutual_count':int(good.sum()),'full_bijection_verified':bool(good.all()),'nearest_all':metrics(np.ones(len(si),bool)),'verified_one_to_one_subset':metrics(good),'GB_10A_verified_subset':metrics(good&slab),'mean_nearest_delta_xyz_A':nv[:,0,:].mean(axis=0).tolist(),'minimum_second_minus_first_distance_A':float(np.min(np.linalg.norm(nv[:,1,:],axis=1)-ds))})
        v=np.concatenate(vals)
        return (float(np.mean(np.sum(v*v,axis=1))),float(v[:,0].mean()),out)
    if translation is None:
        grid=np.linspace(-L[0]/2,L[0]/2,160,endpoint=False); scores=[evaluate(x)[0] for x in grid]
        fits=[]
        for ix in np.argsort(scores)[:12]:
            tx=float(grid[ix])
            for it in range(60):
                score,dx,_=evaluate(tx); tx=float(mic(np.array([tx+dx,0,0]))[0])
                if abs(dx)<1e-10: break
            fits.append((evaluate(tx)[0],tx))
        score,tx=min(fits)
    else: tx=0
    score,dx,out=evaluate(tx,True)
    return {'scope':'all 11052 atoms; Ni species; grain side preserved; all-axis periodic geometry','reflection_z0_A':0 if reflect else None,'fitted_global_x_translation_A':tx if translation is None else None,'fixed_pergrain_translation_xyz_A':{g:np.asarray(v).tolist() for g,v in translation.items()} if translation is not None else None,'full_nearest_RMS_A':float(np.sqrt(score)),'grains':out,'search':'160 uniformly spaced X seeds, best 12 refined by mean nearest X residual, max 60 iterations; not certified global optimization; no Y/Z fitting' if reflect else 'fixed ID-measured common 20 A grain mean translations; no geometry fitting','full_bijection_verified':all(g['full_bijection_verified'] for g in out)}
mirrors={f'{a}->{b}':geometry(a,b,True) for a,b in [(77,80),(80,84)]}
t={g:np.array(pairs['77->84']['exclusion_results']['common_three_frames']['20']['grains'][g]['mean']) for g in ['top','bottom']}
control=geometry(77,84,False,t)
obj={'units':'Angstrom; signed simulation xyz','sources':SOURCES,'source_run_id':'structure-minimize-20261021-220732-c86c47','method_reused_from':'code-20261021-224156-7649e8','previous_method_reproduction_passed':True,'matched_unique_ID_count':11052,'identical_box_verified':True,'cell':r['cell'].tolist(),'pbc':r['pbc'],'gb_y_A':GB,'occupied_y_extents_A':{str(k):extent(f) for k,f in frames.items()},'CNA_FCC_counts':{str(k):int((f['cna']==fcc).sum()) for k,f in frames.items()},'mask_definition':'FCC and interior in BOTH frames, or all THREE for common masks; >=10/20/30 A from GB and each occupied outer grain Y surface','definition':'ID matched all-axis orthogonal MIC; no alignment or drift subtraction. Relative median = top component median minus bottom component median. Geometry test separately uses non-ID matching.','pairs':pairs,'coefficient_summary':pairs['77->84']['exclusion_results']['common_three_frames']['20']['top_minus_bottom'],'coefficient_denominator_A':float(A0*np.sqrt(6)),'coefficient_definition':'Lx=c FULL a[1,-2,1] along bottom X, or 2c times (a/2)[1,-2,1]; Y/Z residual retained','common_mask_additivity':additivity,'all_atom_MIC_additivity_max_A':float(np.max(np.abs(d1+d2-dn))),'mirror_tests':mirrors,'unreflected_77_to_84_control':control,'limitations':['No confidence intervals: spatially correlated atoms; sensitivity is descriptive.','MIC cannot infer path or half-box-exceeding motion. Median is nonlinear and need not add.','Fixed z0=0 reflection is origin dependent; no adjustable plane or fitted Z tested. Opposite mean Z alone is not mirror evidence.','Nearest geometry is a lower-bound relaxation of assignment at tested translation. Only unique mutual subset is verified one-to-one if full bijection fails; no forced permutation.','No arbitrary tolerance defines pure X or mirror: transverse residuals and geometry distributions/counts explicit.','CNA and grain sides do not independently index supplied orientations; no DSC/Burgers vector inference.']}
(OUT/'two_drop_shift.json').write_text(json.dumps(obj,indent=2)+'\n')
def vec(v): return '('+', '.join(f'{x:+.9f}' for x in v)+')'
lines=['# Alternating-Z / fixed-plane mirror test','','All lengths Å. Sources: independently relaxed 77,80,84, `structure-minimize-20261021-220732-c86c47`; artifact IDs/hashes in JSON. Previous method reproduced to 1e-12 Å. No simulation/render.','11052 identical IDs/orthogonal boxes; all-axis MIC, no alignment. FCC and surface exclusions required in both frames or all three for common masks; GB y='+str(GB)+'.','', '## 20 Å pair-mask grain displacements','| pair | grain | N | mean xyz | median xyz | residual RMS |','|---|---|---:|---|---|---:|']
for key,p in pairs.items():
    for g,s in p['exclusion_results']['pair_both_frames']['20']['grains'].items(): lines.append(f"| {key} | {g} | {s['count']} | {vec(s['mean'])} | {vec(s['median'])} | {s['residual_RMS_A']:.9g} |")
lines+=['','## Top minus bottom: common three-frame masks','| pair | exclusion | estimator | signed xyz | YZ norm | full c | half 2c |','|---|---:|---|---|---:|---:|---:|']
for key,p in pairs.items():
    for e,s in p['exclusion_results']['common_three_frames'].items():
        for est,v in s['top_minus_bottom'].items(): lines.append(f"| {key} | {e} | {est} | {vec(v['xyz_A'])} | {v['transverse_yz_norm_A']:.9g} | {v['full_a_1m21_coefficient_c']:.12g} | {v['half_a_1m21_coefficient_2c']:.12g} |")
lines+=['','Common-mask direct minus sum (means additive; median nonlinear):','```json',json.dumps(additivity,indent=2),'```','','## 10/20/30 Å sensitivity']
for key,p in pairs.items():
    for mode,s in p['sensitivity'].items(): lines.append(f"- {key}, {mode}: maximum vector difference from 20 Å, mean {s['mean']['max_difference_from_20_A']:.9g}, median {s['median']['max_difference_from_20_A']:.9g} Å. Signed envelopes and all grain statistics in JSON.")
lines+=['','Pure-X suitability is judged from retained net Y/Z, not c alone. c=Lx/(3.52 sqrt(6)) for FULL a[1,-2,1]; 2c for (a/2)[1,-2,1]. No integer rounding or hidden zero tolerance.','', '## Fixed-plane mirror: z→−z, z0=0, only global periodic X fitted','160 X grid points, best 12 locally refined; not certified global optimum. No Y/Z fit or adjustable mirror plane. Ni-aware, grain-side-preserving full geometry search; only unique mutually nearest subset claimed one-to-one if full bijection fails.','| test | fitted X | full nearest RMS | full bijection | grain | unique mutual/N | verified RMS | verified p90 | verified max |','|---|---:|---:|---|---|---|---:|---:|---:|']
for label,s in list(mirrors.items())+[('77->84 unreflected control',control)]:
    for g in s['grains']:
        v=g['verified_one_to_one_subset']; lines.append(f"| {label} | {s['fitted_global_x_translation_A']} | {s['full_nearest_RMS_A']:.9g} | {s['full_bijection_verified']} | {g['grain']} | {v['count']}/{g['N']} | {v['RMS_A']:.9g} | {v['p90_A']:.9g} | {v['max_A']:.9g} |")
lines+=['','Unreflected control uses per-grain ID-measured common 20 Å mean translations, not optimized geometry. Counts within 0.001/0.01/0.05/0.1/0.25/0.5/1 Å, conflicts, unmatched targets, uniqueness gaps and GB ±10 Å verified subset in JSON. Fixed-plane mismatch cannot exclude another plane, but none fitted or invented.','', '## Limitations']+['- '+s for s in obj['limitations']]
(OUT/'two_drop_report.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'net':obj['coefficient_summary'],'mirrors':{k:{'RMS':v['full_nearest_RMS_A'],'x':v['fitted_global_x_translation_A'],'bijection':v['full_bijection_verified']} for k,v in mirrors.items()},'control_RMS':control['full_nearest_RMS_A']}))

# Independent consistency checks of generated tables and geometric matching scope.
assert len(pairs)==3 and all(sum(g['N'] for g in v['grains'])==11052 for v in mirrors.values())
for e,a in additivity.items(): assert max(abs(x) for x in a['mean_net_minus_sum_xyz_A'])<1e-12
for p in pairs.values():
    for mode in p['exclusion_results'].values():
        for entry in mode.values():
            for est in ['mean','median']:
                v=np.array(entry['grains']['top'][est])-entry['grains']['bottom'][est]
                assert np.allclose(v,entry['top_minus_bottom'][est]['xyz_A'],atol=1e-14,rtol=0)
for v in list(mirrors.values())+[control]:
    if v['full_bijection_verified']:
        assert sum(g['unique_mutual_count'] for g in v['grains'])==11052
        assert sum(g['conflicted_sources']+g['unmatched_targets'] for g in v['grains'])==0
net=obj['coefficient_summary']['mean']['xyz_A']
conclusion='\n## Interpretation\n\nAlternating relative Z is quantitatively present: +0.061142987917 Å for 77→80 and −0.061143214329 Å for 80→84. It cancels to −2.264119975e-7 Å over 77→84, with net Y +6.815030129e-7 Å and X +0.391348465495 Å. Thus the net interior relative displacement is overwhelmingly X, not exactly pure X; residual YZ norm 7.181286439e-7 Å. Common-mask mean sensitivity is 1.873721961e-8 Å; median sensitivity 6.292464863e-7 Å. Net per-grain residual RMS is 9.688557552e-4 Å top and 3.362816889e-4 Å bottom, much larger than transverse net mean residual.\n\nFixed-plane geometric reflection does NOT superpose the structures: both tests yield full bijections but ≈0.618383574 Å RMS, with all 11052 residuals exceeding 0.5 Å. The mismatch is predominantly positive Z (~+0.6183834 to +0.6183838 Å), while X/Y means are tiny after X-only fitting. This is an origin/plane-dependent mismatch, not proof that no mirror at any other plane exists; no Z translation or alternate plane was fitted. Opposite mean Z is therefore not sufficient to assert the requested fixed-z0 mirror. The unreflected 77→84 translated control has full-nearest RMS 0.020322449 Å; its bijection/conflict details are given above and in JSON.\n\nThe imported snapshots expose one Ni particle type (all atoms); species preservation is validated by element name, not an invented second species. Side preservation is independently enforced. Supplied orientations: top (110), x[001], y[110], z[-1,1,0]; bottom (111), x[1,-2,1], y[111], z[-1,0,1]. a=3.52 Å.\n'
with (OUT/'two_drop_report.md').open('a') as f: f.write(conclusion)
print('All deterministic consistency validations passed.')
