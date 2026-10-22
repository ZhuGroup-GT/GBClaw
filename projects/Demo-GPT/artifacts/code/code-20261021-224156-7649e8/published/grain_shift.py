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
    return dict(ids=ids[order],r=np.asarray(d.particles.positions)[order],cna=np.asarray(d.particles['Structure Type'])[order],cell=np.asarray(d.cell),pbc=list(d.cell.pbc))
r,c=map(load,SOURCES)
assert np.array_equal(r['ids'],c['ids']) and np.array_equal(r['cell'],c['cell'])
assert all(r['pbc']) and all(c['pbc'])
H=r['cell'][:,:3]; delta=c['r']-r['r']; frac=np.linalg.solve(H,delta.T).T; delta=(frac-np.rint(frac))@H.T
# These cells are orthogonal: componentwise fractional wrapping is exact MIC.
assert np.allclose(H,np.diag(np.diag(H)))
def scalar(x):
    return dict(median=float(np.median(x)),mean=float(np.mean(x)),min=float(np.min(x)),max=float(np.max(x)),p10=float(np.percentile(x,10)),p90=float(np.percentile(x,90)))
def stats(d):
    med=np.median(d,axis=0); mean=d.mean(axis=0)
    return dict(count=len(d),mean=mean.tolist(),median=med.tolist(),std=np.std(d,axis=0,ddof=1).tolist(),MAD=np.median(np.abs(d-med),axis=0).tolist(),p10=np.percentile(d,10,axis=0).tolist(),p90=np.percentile(d,90,axis=0).tolist(),descriptive_SE_mean=(np.std(d,axis=0,ddof=1)/np.sqrt(len(d))).tolist(),magnitude=scalar(np.linalg.norm(d,axis=1)),residual_after_grain_mean=dict(component_std=np.std(d-mean,axis=0,ddof=1).tolist(),magnitude=scalar(np.linalg.norm(d-mean,axis=1))))
def extent(data):
    y=data['r'][:,1]
    return {g:[float(y[side].min()),float(y[side].max())] for g,side in [('top',y>GB),('bottom',y<GB)]}
ext=extent(r); yc=c['r'][:,1]; yr=r['r'][:,1]
slab=(yc>=-2.0625117292300166)&(yc<=0.660954760508097)
validation=dict(all=stats(delta),slab=stats(delta[slab]))
assert abs(validation['all']['magnitude']['median']-0.1341720411477833)<1e-8
assert abs(validation['all']['magnitude']['max']-0.3722049718210282)<1e-8
assert validation['slab']['count']==174
assert abs(validation['slab']['magnitude']['median']-0.09554388953036258)<1e-8
fcc=int(CommonNeighborAnalysisModifier.Type.FCC)
results={}
for exclusion in [10,20,30]:
    grains={}
    for g in ['top','bottom']:
        outer=ext[g][1 if g=='top' else 0]
        geom=((yr>=GB+exclusion)&(yr<=outer-exclusion)) if g=='top' else ((yr<=GB-exclusion)&(yr>=outer+exclusion))
        mask=geom&(r['cna']==fcc)&(c['cna']==fcc)
        assert mask.sum()>0
        grains[g]=stats(delta[mask]); grains[g].update(geometric_count=int(geom.sum()),y_mask_limits_A=[GB+exclusion,outer-exclusion] if g=='top' else [outer+exclusion,GB-exclusion],selected_reference_y_extent_A=[float(yr[mask].min()),float(yr[mask].max())])
    rel={}
    for estimator in ['mean','median']:
        v=np.array(grains['top'][estimator])-np.array(grains['bottom'][estimator]); rel[estimator]=dict(xyz_A=v.tolist(),xz_norm_A=float(np.linalg.norm(v[[0,2]])),norm_A=float(np.linalg.norm(v)))
    rel['descriptive_SE_mean_xyz_A']=np.hypot(grains['top']['descriptive_SE_mean'],grains['bottom']['descriptive_SE_mean']).tolist()
    results[str(exclusion)]=dict(grains=grains,top_minus_bottom=rel)
bases={'top':np.array([[0,1,-1],[0,1,1],[1,0,0]],float),'bottom':np.array([[1,1,-1],[-2,1,0],[1,1,1]],float)}
transforms={}
for g,B in bases.items():
    B=B/np.linalg.norm(B,axis=0); assert np.allclose(B.T@B,np.eye(3))
    transforms[g]={'normalized_basis_columns':B.tolist(),'estimators':{}}
    for estimator in ['mean','median']:
        v=np.array(results['20']['top_minus_bottom'][estimator]['xyz_A']); coeff=B@v/A0
        # FCC translations: (a/2)[h,k,l], integer h,k,l with even sum.
        center=np.rint(2*coeff).astype(int); candidates=np.array([center+o for o in itertools.product(range(-3,4),repeat=3) if (center+o).sum()%2==0])
        dist=np.linalg.norm(candidates/2-coeff,axis=1)*A0; idx=np.argmin(dist); nearest=candidates[idx]/2
        nz=np.any(candidates!=0,axis=1); nidx=np.flatnonzero(nz)[np.argmin(dist[nz])]
        transforms[g]['estimators'][estimator]=dict(a_uvw_coefficients=coeff.tolist(),cubic_shift_A=(coeff*A0).tolist(),nearest_FCC_translation_a_coefficients=nearest.tolist(),nearest_FCC_translation_sim_xyz_A=(B.T@(nearest*A0)).tolist(),distance_to_nearest_FCC_translation_A=float(dist[idx]),nearest_nonzero_FCC_translation_a_coefficients=(candidates[nidx]/2).tolist(),distance_to_nearest_nonzero_FCC_translation_A=float(dist[nidx]))
sensitivity={}
for est in ['mean','median']:
    vs=np.array([results[str(e)]['top_minus_bottom'][est]['xyz_A'] for e in [10,20,30]])
    sensitivity[est]=dict(component_min_A=vs.min(axis=0).tolist(),component_max_A=vs.max(axis=0).tolist(),max_vector_difference_from_20A_A=float(np.max(np.linalg.norm(vs-vs[1],axis=1))),xz_norm_range_A=[float(min(np.linalg.norm(v[[0,2]]) for v in vs)),float(max(np.linalg.norm(v[[0,2]]) for v in vs))])
obj=dict(units='Angstrom; signed xyz simulation coordinates',source_run_id='structure-minimize-20261021-220732-c86c47',sources=SOURCES,existing_render_run_id='render-20261021-224107-c440c3',definition='current80-reference77, ID matched, source-cell MIC all axes; no alignment, affine mapping or common drift subtraction',cell=r['cell'].tolist(),pbc=r['pbc'],matched_unique_ID_count=11052,gb_y_A=GB,occupied_y_extents_A={'reference':ext,'current':extent(c)},CNA=dict(algorithm='OVITO adaptive cutoff CNA on both frames, require FCC in both; masks from reference Y and same IDs',version='3.15.5',fcc_code=fcc,reference_FCC_count=int((r['cna']==fcc).sum()),current_FCC_count=int((c['cna']==fcc).sum())),mask_definition='reference-Y distance >= exclusion from GB and occupied outer grain surface, plus FCC in both frames',exclusion_results=results,sensitivity=sensitivity,orientation_transforms=transforms,a0_A=A0,validation=validation,limitations=['SE assumes independent atoms and is descriptive only, not a confidence interval: atomic displacements are spatially correlated. Mask sensitivity is the principal robustness check.','Geometric side assignment plus FCC is not independent crystallographic orientation indexing. Orientations are supplied task metadata.','MIC cannot infer trajectories or motion exceeding half the box.','Relative shift need not be a translation of either crystal or a DSC vector; no dislocation/Burgers-vector inference.','CNA filters local defects; residual statistics describe selected interior atoms only.'])
(OUT/'grain_shift.json').write_text(json.dumps(obj,indent=2)+'\n')
def vec(v): return '('+', '.join(f'{x:+.9f}' for x in v)+')'
lines=['# Relative grain shift: relaxed 77 → 80','',f"Source: `{obj['source_run_id']}`; reference `{SOURCES[0]['artifact_id']}`, current `{SOURCES[1]['artifact_id']}`. Exact input hashes and provenance are in JSON. Existing render `{obj['existing_render_run_id']}` is not regenerated.",'','11052 unique IDs matched, identical orthogonal cells and all-axis PBC. Current minus reference MIC; no alignment, strain or drift subtraction. All lengths below are Å; vectors are signed simulation (x,y,z).','',f'GB Y={GB:.9f}. Occupied reference grain extents: {ext}; current: {extent(c)}. Masks use reference Y, >=10/20/30 Å from GB and occupied outer surface (not vacuum box edge), same IDs, adaptive CNA FCC in both frames.','', '## Far-field statistics','', '| exclusion | grain | N | mean xyz | median xyz | std xyz | MAD xyz | p10 xyz | p90 xyz |','|---|---|---:|---|---|---|---|---|---|']
for e,res in results.items():
    for g,s in res['grains'].items(): lines.append(f"| {e} | {g} | {s['count']} | "+' | '.join(vec(s[k]) for k in ['mean','median','std','MAD','p10','p90'])+' |')
lines+=['','## Top minus bottom','', '| exclusion | estimator | xyz | XZ norm |','|---|---|---|---:|']
for e,res in results.items():
    for est in ['mean','median']:
        s=res['top_minus_bottom'][est]; lines.append(f"| {e} | {est} | {vec(s['xyz_A'])} | {s['xz_norm_A']:.9f} |")
lines+=['', '### Uncertainty / robustness','', 'No calibrated statistical confidence interval: spatial correlation invalidates naive independent-atom SE. Descriptive independent-atom SE of 20 Å mean relative xyz: '+vec(results['20']['top_minus_bottom']['descriptive_SE_mean_xyz_A'])+'.']
for est,s in sensitivity.items(): lines.append(f"{est}: 10–30 Å component envelope {vec(s['component_min_A'])} to {vec(s['component_max_A'])}; maximum vector difference from 20 Å {s['max_vector_difference_from_20A_A']:.9g} Å.")
lines+=['','## Collective vs local displacement','', '| 20 Å grain | raw magnitude median | raw magnitude max | residual RMS | residual median | residual p90 | residual max |','|---|---:|---:|---:|---:|---:|---:|']
for g,s in results['20']['grains'].items():
    rr=s['residual_after_grain_mean']['magnitude']; rms=np.sqrt(sum(np.array(s['std'])**2)*(s['count']-1)/s['count'])
    lines.append(f"| {g} | {s['magnitude']['median']:.9f} | {s['magnitude']['max']:.9f} | {rms:.9g} | {rr['median']:.9g} | {rr['p90']:.9g} | {rr['max']:.9g} |")
lines+=['','Per-grain mean is subtracted only for these residual diagnostics, not the primary displacement. The top-minus-bottom difference cancels a common translation. Residuals quantify departures from uniform collective motion, without fitting rotations or strain.','', '## Cubic coefficients / FCC translation test','', 'Top simulation basis: x[001], y[110], z[-1,1,0]; bottom x[1,-2,1], y[111], z[-1,0,1]. Normalized basis columns B convert simulation shift to cubic coordinates as B·shift; a[uvw] coefficients = B·shift/3.52, not integer rounding.','', '| grain basis | estimator | a[uvw] coefficients | nearest FCC a coefficients | distance Å | nearest nonzero FCC a coefficients | distance Å |','|---|---|---|---|---:|---|---:|']
for g,t in transforms.items():
    for est,s in t['estimators'].items(): lines.append(f"| {g} | {est} | {vec(s['a_uvw_coefficients'])} | {vec(s['nearest_FCC_translation_a_coefficients'])} | {s['distance_to_nearest_FCC_translation_A']:.9f} | {vec(s['nearest_nonzero_FCC_translation_a_coefficients'])} | {s['distance_to_nearest_nonzero_FCC_translation_A']:.9f} |")
lines+=['','FCC rule: (a/2)[hkl] with integer h,k,l and even h+k+l, including zero. Both zero and nearest nonzero comparisons are reported; small measured shifts must not be mistaken for a nonzero lattice vector. This is not a DSC test or evidence of a dislocation.','', '## Independent magnitude reproduction','']
for label,s in validation.items(): lines.append(f"{label}: N={s['count']}; magnitude median {s['magnitude']['median']:.12f}, max {s['magnitude']['max']:.12f} Å.")
lines+=['','Slab uses current Y in [-2.0625117292300166, 0.660954760508097] Å to match the existing render. Full magnitude and slab median/max match stored render evidence to <1e-8 Å.','', '## Limitations','']+['- '+s for s in obj['limitations']]
(OUT/'grain_shift_report.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'primary':results['20'],'sensitivity':sensitivity,'transforms':transforms},indent=2))
