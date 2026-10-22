# Relative grain shift: relaxed 77 → 80

Source: `structure-minimize-20261021-220732-c86c47`; reference `fd34ba28-3de8-564e-a353-cf4c8e57cfbb`, current `278a3369-5c91-5e52-b400-374a947d39d5`. Exact input hashes and provenance are in JSON. Existing render `render-20261021-224107-c440c3` is not regenerated.

11052 unique IDs matched, identical orthogonal cells and all-axis PBC. Current minus reference MIC; no alignment, strain or drift subtraction. All lengths below are Å; vectors are signed simulation (x,y,z).

GB Y=-0.700778484. Occupied reference grain extents: {'top': [0.12142077789864913, 103.40291238784008], 'bottom': [-103.45615371252026, -1.522996338672229]}; current: {'top': [0.12143011927337, 103.40292214791552], 'bottom': [-103.45614487142457, -1.5229870879952898]}. Masks use reference Y, >=10/20/30 Å from GB and occupied outer surface (not vacuum box edge), same IDs, adaptive CNA FCC in both frames.

## Far-field statistics

| exclusion | grain | N | mean xyz | median xyz | std xyz | MAD xyz | p10 xyz | p90 xyz |
|---|---|---:|---|---|---|---|---|---|
| 10 | top | 4422 | (+0.173159558, +0.000009617, +0.030477204) | (+0.173159534, +0.000009689, +0.030477293) | (+0.001229514, +0.001726662, +0.000198640) | (+0.000027792, +0.000026472, +0.000000160) | (+0.172437737, -0.000242574, +0.030470059) | (+0.173831007, +0.000284131, +0.030487037) |
| 10 | bottom | 4428 | (-0.022514220, +0.000008990, -0.030665873) | (-0.022514555, +0.000007213, -0.030665881) | (+0.000198518, +0.000540410, +0.000741638) | (+0.000010401, +0.000032441, +0.000002379) | (-0.022606663, -0.000255289, -0.030840441) | (-0.022441010, +0.000212662, -0.030505778) |
| 20 | top | 3366 | (+0.173159556, +0.000009624, +0.030477206) | (+0.173159444, +0.000009595, +0.030477235) | (+0.000626776, +0.000396118, +0.000014364) | (+0.000027761, +0.000026379, +0.000000136) | (+0.172862412, -0.000176733, +0.030475496) | (+0.173438509, +0.000195813, +0.030478736) |
| 20 | bottom | 3348 | (-0.022514225, +0.000008981, -0.030665781) | (-0.022514575, +0.000006613, -0.030665829) | (+0.000064767, +0.000194833, +0.000153161) | (+0.000010410, +0.000034485, +0.000002379) | (-0.022567093, -0.000166945, -0.030733483) | (-0.022467350, +0.000156743, -0.030599357) |
| 30 | top | 2310 | (+0.173159554, +0.000009628, +0.030477175) | (+0.173159284, +0.000009653, +0.030477184) | (+0.000182151, +0.000100055, +0.000001101) | (+0.000027804, +0.000026703, +0.000000091) | (+0.173074516, -0.000128268, +0.030476776) | (+0.173235267, +0.000142816, +0.030477432) |
| 30 | bottom | 2268 | (-0.022514237, +0.000008974, -0.030665771) | (-0.022514253, +0.000006752, -0.030665780) | (+0.000030419, +0.000096473, +0.000033649) | (+0.000010663, +0.000034706, +0.000002403) | (-0.022546703, -0.000099912, -0.030689327) | (-0.022482497, +0.000117094, -0.030642255) |

## Top minus bottom

| exclusion | estimator | xyz | XZ norm |
|---|---|---|---:|
| 10 | mean | (+0.195673778, +0.000000628, +0.061143077) | 0.205004154 |
| 10 | median | (+0.195674088, +0.000002475, +0.061143174) | 0.205004479 |
| 20 | mean | (+0.195673781, +0.000000643, +0.061142988) | 0.205004130 |
| 20 | median | (+0.195674018, +0.000002982, +0.061143064) | 0.205004379 |
| 30 | mean | (+0.195673791, +0.000000654, +0.061142946) | 0.205004127 |
| 30 | median | (+0.195673538, +0.000002901, +0.061142964) | 0.205003891 |

### Uncertainty / robustness

No calibrated statistical confidence interval: spatial correlation invalidates naive independent-atom SE. Descriptive independent-atom SE of 20 Å mean relative xyz: (+0.000010861, +0.000007613, +0.000002659).
mean: 10–30 Å component envelope (+0.195673778, +0.000000628, +0.061142946) to (+0.195673791, +0.000000654, +0.061143077); maximum vector difference from 20 Å 9.0273241e-08 Å.
median: 10–30 Å component envelope (+0.195673538, +0.000002475, +0.061142964) to (+0.195674088, +0.000002982, +0.061143174); maximum vector difference from 20 Å 5.23593961e-07 Å.

## Collective vs local displacement

| 20 Å grain | raw magnitude median | raw magnitude max | residual RMS | residual median | residual p90 | residual max |
|---|---:|---:|---:|---:|---:|---:|
| top | 0.175821191 | 0.178880487 | 0.000741485645 | 5.65430004e-05 | 0.00126674033 | 0.00339485056 |
| bottom | 0.038043498 | 0.038771649 | 0.000256112109 | 4.00661461e-05 | 0.000437950233 | 0.00129809037 |

Per-grain mean is subtracted only for these residual diagnostics, not the primary displacement. The top-minus-bottom difference cancels a common translation. Residuals quantify departures from uniform collective motion, without fitting rotations or strain.

## Cubic coefficients / FCC translation test

Top simulation basis: x[001], y[110], z[-1,1,0]; bottom x[1,-2,1], y[111], z[-1,0,1]. Normalized basis columns B convert simulation shift to cubic coordinates as B·shift; a[uvw] coefficients = B·shift/3.52, not integer rounding.

| grain basis | estimator | a[uvw] coefficients | nearest FCC a coefficients | distance Å | nearest nonzero FCC a coefficients | distance Å |
|---|---|---|---|---:|---|---:|
| top | mean | (-0.012282434, +0.012282692, +0.055589142) | (+0.000000000, +0.000000000, +0.000000000) | 0.205004130 | (+0.000000000, +0.500000000, +0.500000000) | 2.322986767 |
| top | median | (-0.012281979, +0.012283177, +0.055589210) | (+0.000000000, +0.000000000, +0.000000000) | 0.205004380 | (+0.000000000, +0.500000000, +0.500000000) | 2.322985315 |
| bottom | mean | (+0.010411715, -0.045388239, +0.034976841) | (+0.000000000, +0.000000000, +0.000000000) | 0.205004130 | (+0.000000000, -0.500000000, +0.500000000) | 2.289425956 |
| bottom | median | (+0.010412111, -0.045387911, +0.034977267) | (+0.000000000, +0.000000000, +0.000000000) | 0.205004380 | (+0.000000000, -0.500000000, +0.500000000) | 2.289425714 |

FCC rule: (a/2)[hkl] with integer h,k,l and even h+k+l, including zero. Both zero and nearest nonzero comparisons are reported; small measured shifts must not be mistaken for a nonzero lattice vector. This is not a DSC test or evidence of a dislocation.

## Independent magnitude reproduction

all: N=11052; magnitude median 0.134172041148, max 0.372204971821 Å.
slab: N=174; magnitude median 0.095543889530, max 0.372204971821 Å.

Slab uses current Y in [-2.0625117292300166, 0.660954760508097] Å to match the existing render. Full magnitude and slab median/max match stored render evidence to <1e-8 Å.

## Limitations

- SE assumes independent atoms and is descriptive only, not a confidence interval: atomic displacements are spatially correlated. Mask sensitivity is the principal robustness check.
- Geometric side assignment plus FCC is not independent crystallographic orientation indexing. Orientations are supplied task metadata.
- MIC cannot infer trajectories or motion exceeding half the box.
- Relative shift need not be a translation of either crystal or a DSC vector; no dislocation/Burgers-vector inference.
- CNA filters local defects; residual statistics describe selected interior atoms only.
