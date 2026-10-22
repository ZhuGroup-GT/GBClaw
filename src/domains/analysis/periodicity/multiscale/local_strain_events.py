""" signed-strain scale-space diagnostics.

A Fourier wavelength is not an interval between localized features. This keeps
both descriptions, and reports all repeatable local-interval modes rather than
forcing a single short/long fundamental. No filename/target-period prior is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault('MPLBACKEND', 'Agg')
import numpy as np
from .io import ptm_cache_path
from .analyze_elastic_periodicity import _smooth, spectral_profile


def finite_json(value):
    if isinstance(value, dict):
        return {str(k):finite_json(v) for k,v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [finite_json(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def extrema(x, values, inner, prominence_fraction=.4):
    """Positive local extrema with topographic prominence; negative via -values.

    The prominence cutoff is tied to this signed channel, not a global core
    amplitude. Endpoint extrema and insufficiently covered bins are not events.
    Sub-bin parabolic interpolation reduces quantization of the local intervals.
    """
    x, values = np.asarray(x), np.asarray(values)
    keep = np.asarray(inner, bool) & np.isfinite(values)
    if keep.sum() < 20:
        return np.array([]), np.array([])
    valid_indices = np.flatnonzero(keep)
    if not np.all(keep[valid_indices[0]:valid_indices[-1]+1]):
        raise ValueError('Local strain events require a contiguous valid domain; do not bridge a missing-data gap')
    std = float(np.std(values[keep]))
    if std <= 1e-12:
        return np.array([]), np.array([])
    ids = np.flatnonzero((values[1:-1] > values[:-2]) & (values[1:-1] >= values[2:]))+1
    ids = ids[keep[ids]]
    chosen, prominence = [], []
    for idx in ids:
        l = np.flatnonzero(values[:idx] > values[idx])
        r = np.flatnonzero(values[idx+1:] > values[idx])
        left = max(int(l[-1]) if len(l) else 0, int(valid_indices[0]))
        right = min(int(idx+1+r[0]) if len(r) else len(x)-1, int(valid_indices[-1]))
        prom = values[idx]-max(float(np.min(values[left:idx+1])), float(np.min(values[idx:right+1])))
        if prom < prominence_fraction*std:
            continue
        curvature = values[idx-1]-2*values[idx]+values[idx+1]
        offset = .5*(values[idx-1]-values[idx+1])/curvature if curvature != 0 else 0
        offset = float(np.clip(offset, -.5, .5))
        chosen.append(x[idx]+offset*(x[idx+1]-x[idx]))
        prominence.append(prom)
    return np.asarray(chosen), np.asarray(prominence)


def interval_modes(intervals, nn, minimum_count=3):
    """Resolve modes by 1-D gaps, retaining their full observed spread.

    Gap tolerance 2 NN is a resolution policy, not an expected repeat length.
    Modes with fewer than minimum_count observations stay in the raw intervals.
    """
    values = np.sort(np.asarray(intervals, float))
    if not len(values):
        return []
    groups = np.split(values, np.flatnonzero(np.diff(values) > 2*nn)+1)
    return [{'center_angstrom':float(np.median(g)), 'range_angstrom':[float(g.min()),float(g.max())],
             'count':int(len(g)), 'relative_spread':float(np.ptp(g)/np.median(g))}
            for g in groups if len(g) >= minimum_count]


def collect_candidates(channels, nn):
    """Cluster channel modes without allowing a chain of broad modes to fuse.

    This is descriptive support across processing choices, not independent
    replication or a confidence score. Original channel observations are kept.
    """
    rows = [(mode['center_angstrom'], row, mode) for row in channels
            for mode in row['spacing_modes'] if mode['relative_spread'] <= .15]
    rows.sort(key=lambda row:row[0])
    groups = []
    for item in rows:
        if groups and item[0]-np.median([r[0] for r in groups[-1]]) <= 2*nn:
            groups[-1].append(item)
        else:
            groups.append([item])
    result = []
    for group in groups:
        ids = sorted({r[1]['id'] for r in group})
        depths = sorted({r[1]['depth_nn'] for r in group})
        scales = sorted({r[1]['sigma_nn'] for r in group})
        grains = sorted({r[1]['grain'] for r in group})
        # Preserve single-channel modes in channels, while summary candidates
        # need recurrence under another depth or smoothing choice.
        if len(depths) < 2 and len(scales) < 2:
            continue
        centers = [r[0] for r in group]
        result.append({'center_angstrom':float(np.median(centers)),
                       'channel_center_range_angstrom':[min(centers),max(centers)],
                       'observed_interval_range_angstrom':[min(r[2]['range_angstrom'][0] for r in group),max(r[2]['range_angstrom'][1] for r in group)],
                       'channels':ids, 'depths_nn':depths, 'smoothing_nn':scales,
                       'prominence_stable_channels':sorted({r[1]['id'] for r in group if r[2].get('prominence_stable')}),
                       'grains':grains,'observed_interval_count':sum(r[2]['count'] for r in group),
                       'interpretation':'Repeated local spacing of same-sign strain extrema; not necessarily a translation symmetry, Fourier wavelength, or identified dislocation spacing.'})
    return result


def choose_display(channels, candidates, maximum=6):
    """Show representative tracks at distinct resolved scales, not only largest amplitude."""
    eligible = [r for r in channels if len(r['extrema_x_angstrom']) >= 5 and r['spacing_modes']]
    if not eligible:
        return []
    def score(row):
        intervals = max(1,len(row['successive_spacing_angstrom']))
        tight = [m for m in row['spacing_modes'] if m['relative_spread'] <= .15]
        support = sum(m['count'] for m in tight)/intervals
        stable_support = sum(m['count'] for m in tight if m.get('prominence_stable'))/intervals
        # Prefer retaining multiple observed interval modes, if comparably stable.
        return (stable_support*min(sum(bool(m.get('prominence_stable')) for m in tight),2), support, -row['sigma_nn'])
    ordered = sorted(eligible, key=score, reverse=True)
    chosen=[]
    for grain in ('lower','upper'):
        subset = [r for r in ordered if r['grain']==grain]
        for row in subset:
            typical=float(np.median(row['successive_spacing_angstrom']))
            if all(old['grain'] != grain or abs(np.log(typical/np.median(old['successive_spacing_angstrom'])))>.28 for old in chosen):
                chosen.append(row)
            if sum(r['grain']==grain for r in chosen)>=maximum//2:
                break
    return [r['id'] for r in chosen]


def analyze_case(case_dir, coordinate_dir=None):
    case_dir=Path(case_dir)
    coordinate_dir = Path(coordinate_dir) if coordinate_dir is not None else case_dir
    with np.load(ptm_cache_path(coordinate_dir)/'ptm_atoms.npz', allow_pickle=False) as packed:
        arrays = dict(packed)
    with np.load(case_dir/'elastic/elastic_atoms.npz', allow_pickle=False) as packed:
        strain = dict(packed)
    boundary=json.loads((coordinate_dir/'boundary.json').read_text())
    elastic=json.loads((case_dir/'elastic/elastic_periodicity.json').read_text())
    p=arrays['positions']; nn=boundary['nearest_neighbor_angstrom']
    lo,hi=boundary['interior_x_interval_angstrom']; gb=boundary['gb_y_angstrom']
    bounds=np.asarray(boundary['occupied_bounds_angstrom'])
    edges=np.linspace(lo,hi,max(32,int(np.ceil((hi-lo)/(.5*nn))))+1)
    x=.5*(edges[1:]+edges[:-1]); dx=float(x[1]-x[0])
    channels=[]; raw_profiles={}; skipped=[]; depth_centers=[2,4,8,12,16,24,32,48,64]
    for grain,sign in [('lower',-1),('upper',1)]:
        depth=sign*(p[:,1]-gb)
        extent=gb-bounds[0,1] if sign<0 else bounds[1,1]-gb
        for center in depth_centers:
            if (center+1)*nn>min(.6*extent,extent-4*nn):
                continue
            mask=strain['valid']&(strain['grain']==sign)&(depth>=(center-1)*nn)&(depth<(center+1)*nn)
            mask&=(p[:,0]>=lo)&(p[:,0]<=hi)
            if mask.sum()<64:
                continue
            count=np.histogram(p[mask,0],edges)[0].astype(float)
            total=np.histogram(p[mask,0],edges,weights=strain['volumetric_strain'][mask])[0]
            for sigma in [1,2,4,8]:
                denominator=_smooth(count,sigma*nn/dx)
                numerator=_smooth(total,sigma*nn/dx)
                threshold=.1*np.median(denominator[denominator>0])
                covered=denominator>=max(threshold,1e-12)
                domain=(x>lo+3*sigma*nn)&(x<hi-3*sigma*nn)
                inner=covered&domain
                profile_id=f'{grain}_depth{center}_sigma{sigma}'
                if not np.all(covered[domain]):
                    skipped.append({'profile_id':profile_id,'status':'unresolved_missing_coverage',
                                    'missing_bins':int(np.sum(domain&~covered)),
                                    'reason':'A missing interval must not create a spurious long event spacing'})
                    continue
                if inner.sum()<32:
                    continue
                values=numerator/np.maximum(denominator,1e-30)
                values-=np.polyval(np.polyfit(x[inner]-x[inner].mean(),values[inner],1),x-x[inner].mean())
                std=float(np.std(values[inner]))
                raw_profiles[profile_id]=dict(values=values,inner=inner,grain=grain,depth_nn=center,sigma_nn=sigma,std=std)
                spectrum=spectral_profile(x[inner],values[inner],max(2.5*nn,2*sigma*nn),(x[inner][-1]-x[inner][0])/3)
                # Raw spectrum is retained once per profile, shared by both polarities.
                for polarity,sgn in [('positive',1),('negative',-1)]:
                    positions,prom=extrema(x,sgn*values,inner)
                    intervals=np.diff(positions)
                    modes=interval_modes(intervals,nn)
                    sensitivity=[]
                    for fraction in [.3,.5]:
                        other_positions,_=extrema(x,sgn*values,inner,prominence_fraction=fraction)
                        sensitivity.append({'prominence_fraction':fraction,'event_count':len(other_positions),
                                            'spacing_modes':interval_modes(np.diff(other_positions),nn)})
                    for mode in modes:
                        mode['prominence_stable']=all(any(abs(m['center_angstrom']-mode['center_angstrom'])<=2*nn
                                                                         for m in trial['spacing_modes'])
                                                        for trial in sensitivity)
                    channels.append({'id':f'{profile_id}_{polarity}','profile_id':profile_id,'grain':grain,
                                     'depth_nn':center,'sigma_nn':sigma,'polarity':polarity,
                                     'atom_count':int(mask.sum()),'amplitude_std':std,
                                     'coverage_fraction':float(covered.mean()),
                                     'extrema_x_angstrom':positions.tolist(),'prominence':prom.tolist(),
                                     'successive_spacing_angstrom':intervals.tolist(),'spacing_modes':modes,
                                     'prominence_sensitivity':sensitivity,
                                     'fourier_peaks':spectrum.get('peaks',[])})
    candidates=collect_candidates(channels,nn)
    shown=choose_display(channels,candidates)
    out=case_dir/'local_strain';out.mkdir(exist_ok=True)
    result={'source':elastic['source'],'source_sha256':elastic['source_sha256'],
            'method':{'observable':'Signed OVITO volumetric strain in valid crystalline atoms',
                      'depth_centers_nn':depth_centers,'depth_band_width_nn':2,
                      'gaussian_sigma_nn':[1,2,4,8],'prominence_fraction_of_channel_std':.4,
                      'prominence_sensitivity_fractions':[.3,.5],
                      'edge_margin_sigma':3,'interval_gap_resolution_nn':2,
                      'minimum_mode_observations':3,'candidate_mode_max_relative_spread':.15,
                      'detrend':'Linear, independently for each grain/depth/scale',
                      'selection':'Both positive and negative extrema; keep weak channels independently of core amplitude',
                      'caveats':['Smoothing can merge features and shift extrema; compare nearby scales and depths.',
                                 'Spacing support across channels is correlated evidence, not an independent sample count.',
                                 'The sum of differing event spacings can create a longer superperiod; their mean can dominate Fourier power.',
                                 'Extrema spacing alone cannot identify a secondary dislocation or its Burgers vector.',
                                 'No expected wavelength, orientation ratio or filename-dependent prior is used.']},
            'nearest_neighbor_angstrom':nn,'channels':channels,'skipped_channels':skipped,'spacing_candidates':candidates,
            'summary_display_channels':shown,'x_angstrom':x.tolist(),
            'profiles':{k:{'signed_values':v['values'].tolist(),'valid_for_events':v['inner'].tolist(),
                           'std':v['std'],'grain':v['grain'],'depth_nn':v['depth_nn'],'sigma_nn':v['sigma_nn']}
                        for k,v in raw_profiles.items()}}
    (out/'local_strain_events.json').write_text(json.dumps(finite_json(result),indent=2,allow_nan=False)+'\n')
    plot_result(out,result)
    return result


def plot_result(out,result):
    import matplotlib.pyplot as plt
    x=np.asarray(result['x_angstrom']); lookup={r['id']:r for r in result['channels']}
    shown=[lookup[i] for i in result['summary_display_channels']]
    fig=plt.figure(figsize=(16,12),layout='constrained');gs=fig.add_gridspec(3,2,height_ratios=[1.5,2.1,1.3])
    for col,grain in enumerate(['lower','upper']):
        ax=fig.add_subplot(gs[0,col]);rows=[v for k,v in result['profiles'].items() if v['grain']==grain and v['sigma_nn']==4]
        if rows:
            signal=np.array([np.array(row['signed_values'])/max(row['std'],1e-12) for row in rows])
            im=ax.imshow(signal,origin='lower',aspect='auto',extent=[x[0],x[-1],-.5,len(rows)-.5],cmap='coolwarm',vmin=-2,vmax=2)
            ax.set_yticks(range(len(rows)),[row['depth_nn'] for row in rows]);fig.colorbar(im,ax=ax,label='Signed strain / channel std',shrink=.7)
        ax.set(xlabel='x / Å',ylabel='Depth from GB / NN',title=f'{grain}: weak and strong depth channels kept visible (σ = 4 NN)')
    curve=fig.add_subplot(gs[1,:]);spacing=fig.add_subplot(gs[2,0]);text=fig.add_subplot(gs[2,1]);text.axis('off')
    colors=plt.cm.tab10(np.linspace(0,.9,max(1,len(shown))))
    for i,(row,color) in enumerate(zip(shown,colors)):
        profile=result['profiles'][row['profile_id']];values=np.array(profile['signed_values'])/max(profile['std'],1e-12)
        positions=np.array(row['extrema_x_angstrom']);polarity=1 if row['polarity']=='positive' else -1
        values*=polarity
        curve.plot(x,values+4*i,color=color,lw=.8,label=f"{row['grain']} d={row['depth_nn']} NN, σ={row['sigma_nn']} NN, {row['polarity']}")
        curve.scatter(positions,np.interp(positions,x,values)+4*i,color=color,s=12)
        delta=np.array(row['successive_spacing_angstrom']);mid=.5*(positions[:-1]+positions[1:])
        spacing.scatter(mid,delta,color=color,s=15,alpha=.8)
    curve.set(xlabel='x / Å',ylabel='Signed strain / own std + vertical offset',title='Representative scale-space tracks · markers: same-sign extrema · no supplied target period')
    curve.legend(loc='upper left',fontsize=8,ncol=2);curve.grid(alpha=.2)
    spacing.set(xlabel='Midpoint of successive extrema / Å',ylabel='Local interval / Å',title='Local spacing may take multiple values');spacing.grid(alpha=.2)
    candidates=result['spacing_candidates']
    text.text(0,1,'Recurring local-interval modes across depths / scales\n'+', '.join(f"{r["center_angstrom"]:.1f} Å" for r in candidates)+
              '\n\nCompare these intervals with FFT wavelengths and full-atom translations.\nMultiple local spacings need not each form a Fourier peak.\nSmoothing, sign and depth can change which spatial feature is followed.\nCandidates are not identified dislocations.',va='top',fontsize=10,wrap=True)
    fig.suptitle(Path(result['source']).name+' · signed-strain local spatial evidence',fontsize=15)
    fig.savefig(out/'local_strain_events.png',dpi=160);plt.close(fig)

