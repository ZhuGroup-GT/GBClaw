# Alternating-Z / fixed-plane mirror test

All lengths Å. Sources: independently relaxed 77,80,84, `structure-minimize-20261021-220732-c86c47`; artifact IDs/hashes in JSON. Previous method reproduced to 1e-12 Å. No simulation/render.
11052 identical IDs/orthogonal boxes; all-axis MIC, no alignment. FCC and surface exclusions required in both frames or all three for common masks; GB y=-0.700778484.

## 20 Å pair-mask grain displacements
| pair | grain | N | mean xyz | median xyz | residual RMS |
|---|---|---:|---|---|---:|
| 77->80 | top | 3366 | (+0.173159556, +0.000009624, +0.030477206) | (+0.173159444, +0.000009595, +0.030477235) | 0.000741485645 |
| 77->80 | bottom | 3348 | (-0.022514225, +0.000008981, -0.030665781) | (-0.022514575, +0.000006613, -0.030665829) | 0.000256112109 |
| 80->84 | top | 3366 | (+0.200464560, -0.000002117, -0.030477269) | (+0.200464453, -0.000002186, -0.030477355) | 0.000741482833 |
| 80->84 | bottom | 3348 | (+0.004789875, -0.000002156, +0.030665945) | (+0.004789489, -0.000004433, +0.030665955) | 0.0002561145 |
| 77->84 | top | 3366 | (+0.373624115, +0.000007506, -0.000000062) | (+0.373624203, +0.000007588, -0.000000149) | 0.000968855755 |
| 77->84 | bottom | 3348 | (-0.017724350, +0.000006825, +0.000000164) | (-0.017724213, +0.000007614, +0.000000120) | 0.000336281689 |

## Top minus bottom: common three-frame masks
| pair | exclusion | estimator | signed xyz | YZ norm | full c | half 2c |
|---|---:|---|---|---:|---:|---:|
| 77->80 | 10 | mean | (+0.195673778, +0.000000628, +0.061143077) | 0.0611430768 | 0.0226941719876 | 0.0453883439752 |
| 77->80 | 10 | median | (+0.195674088, +0.000002475, +0.061143174) | 0.0611431739 | 0.022694207974 | 0.045388415948 |
| 77->80 | 20 | mean | (+0.195673781, +0.000000643, +0.061142988) | 0.0611429879 | 0.0226941722847 | 0.0453883445694 |
| 77->80 | 20 | median | (+0.195674018, +0.000002982, +0.061143064) | 0.0611430638 | 0.022694199824 | 0.045388399648 |
| 77->80 | 30 | mean | (+0.195673791, +0.000000654, +0.061142946) | 0.0611429462 | 0.0226941734407 | 0.0453883468813 |
| 77->80 | 30 | median | (+0.195673538, +0.000002901, +0.061142964) | 0.061142964 | 0.0226941441163 | 0.0453882882327 |
| 80->84 | 10 | mean | (+0.195674690, +0.000000038, -0.061143312) | 0.0611433122 | 0.0226942777389 | 0.0453885554778 |
| 80->84 | 10 | median | (+0.195675042, +0.000001698, -0.061143414) | 0.0611434139 | 0.0226943186041 | 0.0453886372081 |
| 80->84 | 20 | mean | (+0.195674685, +0.000000038, -0.061143214) | 0.0611432143 | 0.0226942771511 | 0.0453885543021 |
| 80->84 | 20 | median | (+0.195674964, +0.000002247, -0.061143310) | 0.0611433101 | 0.0226943095542 | 0.0453886191085 |
| 80->84 | 30 | mean | (+0.195674667, +0.000000039, -0.061143169) | 0.0611431686 | 0.0226942750854 | 0.0453885501708 |
| 80->84 | 30 | median | (+0.195674453, +0.000002211, -0.061143246) | 0.0611432456 | 0.0226942502451 | 0.0453885004902 |
| 77->84 | 10 | mean | (+0.391348468, +0.000000665, -0.000000235) | 7.05692708e-07 | 0.0453884497265 | 0.090776899453 |
| 77->84 | 10 | median | (+0.391348562, -0.000000066, -0.000000263) | 2.70730003e-07 | 0.0453884606175 | 0.0907769212349 |
| 77->84 | 20 | mean | (+0.391348465, +0.000000682, -0.000000226) | 7.18128644e-07 | 0.0453884494358 | 0.0907768988716 |
| 77->84 | 20 | median | (+0.391348416, -0.000000027, -0.000000270) | 2.71142234e-07 | 0.0453884436929 | 0.0907768873857 |
| 77->84 | 30 | mean | (+0.391348458, +0.000000693, -0.000000222) | 7.28127267e-07 | 0.0453884485261 | 0.0907768970522 |
| 77->84 | 30 | median | (+0.391348909, +0.000000363, -0.000000306) | 4.7414485e-07 | 0.0453885008946 | 0.0907770017891 |

Common-mask direct minus sum (means additive; median nonlinear):
```json
{
  "10": {
    "mean_net_minus_sum_xyz_A": [
      -4.718447854656915e-16,
      0.0,
      -6.245004513516506e-17
    ],
    "median_net_minus_sum_xyz_A": [
      -5.687256523501105e-07,
      -4.238692568492297e-06,
      -2.2552911538298304e-08
    ]
  },
  "20": {
    "mean_net_minus_sum_xyz_A": [
      1.7486012637846216e-15,
      8.470329472543003e-22,
      1.249000902703301e-16
    ],
    "median_net_minus_sum_xyz_A": [
      -5.663525994015206e-07,
      -5.25537741680182e-06,
      -2.3516631308950764e-08
    ]
  },
  "30": {
    "mean_net_minus_sum_xyz_A": [
      9.159339953157541e-16,
      0.0,
      -4.163336342344337e-17
    ],
    "median_net_minus_sum_xyz_A": [
      9.185504810105272e-07,
      -4.749830466721506e-06,
      -2.391749420738165e-08
    ]
  }
}
```

## 10/20/30 Å sensitivity
- 77->80, pair_both_frames: maximum vector difference from 20 Å, mean 9.02732411e-08, median 5.23593961e-07 Å. Signed envelopes and all grain statistics in JSON.
- 77->80, common_three_frames: maximum vector difference from 20 Å, mean 9.02732411e-08, median 5.23593961e-07 Å. Signed envelopes and all grain statistics in JSON.
- 80->84, pair_both_frames: maximum vector difference from 20 Å, mean 9.80554735e-08, median 5.64067181e-07 Å. Signed envelopes and all grain statistics in JSON.
- 80->84, common_three_frames: maximum vector difference from 20 Å, mean 9.80554735e-08, median 5.64067181e-07 Å. Signed envelopes and all grain statistics in JSON.
- 77->84, pair_both_frames: maximum vector difference from 20 Å, mean 1.87372196e-08, median 6.29246486e-07 Å. Signed envelopes and all grain statistics in JSON.
- 77->84, common_three_frames: maximum vector difference from 20 Å, mean 1.87372196e-08, median 6.29246486e-07 Å. Signed envelopes and all grain statistics in JSON.

Pure-X suitability is judged from retained net Y/Z, not c alone. c=Lx/(3.52 sqrt(6)) for FULL a[1,-2,1]; 2c for (a/2)[1,-2,1]. No integer rounding or hidden zero tolerance.

## Fixed-plane mirror: z→−z, z0=0, only global periodic X fitted
160 X grid points, best 12 locally refined; not certified global optimum. No Y/Z fit or adjustable mirror plane. Ni-aware, grain-side-preserving full geometry search; only unique mutually nearest subset claimed one-to-one if full bijection fails.
| test | fitted X | full nearest RMS | full bijection | grain | unique mutual/N | verified RMS | verified p90 | verified max |
|---|---:|---:|---|---|---|---:|---:|---:|
| 77->80 | 10.739571291600235 | 0.618383574 | True | bottom | 5508/5508 | 0.618383379 | 0.618383476 | 0.618383715 |
| 77->80 | 10.739571291600235 | 0.618383574 | True | top | 5544/5544 | 0.618383769 | 0.618384043 | 0.618384064 |
| 80->84 | 10.766875856686909 | 0.618383574 | True | bottom | 5508/5508 | 0.618383497 | 0.618383543 | 0.618383593 |
| 80->84 | 10.766875856686909 | 0.618383574 | True | top | 5544/5544 | 0.618383651 | 0.618383727 | 0.618383745 |
| 77->84 unreflected control | None | 0.0203224489 | True | bottom | 5508/5508 | 0.01781273 | 0.00602825809 | 0.168672278 |
| 77->84 unreflected control | None | 0.0203224489 | True | top | 5544/5544 | 0.0225408302 | 0.00982027701 | 0.261003825 |

Unreflected control uses per-grain ID-measured common 20 Å mean translations, not optimized geometry. Counts within 0.001/0.01/0.05/0.1/0.25/0.5/1 Å, conflicts, unmatched targets, uniqueness gaps and GB ±10 Å verified subset in JSON. Fixed-plane mismatch cannot exclude another plane, but none fitted or invented.

## Limitations
- No confidence intervals: spatially correlated atoms; sensitivity is descriptive.
- MIC cannot infer path or half-box-exceeding motion. Median is nonlinear and need not add.
- Fixed z0=0 reflection is origin dependent; no adjustable plane or fitted Z tested. Opposite mean Z alone is not mirror evidence.
- Nearest geometry is a lower-bound relaxation of assignment at tested translation. Only unique mutual subset is verified one-to-one if full bijection fails; no forced permutation.
- No arbitrary tolerance defines pure X or mirror: transverse residuals and geometry distributions/counts explicit.
- CNA and grain sides do not independently index supplied orientations; no DSC/Burgers vector inference.

## Interpretation

Alternating relative Z is quantitatively present: +0.061142987917 Å for 77→80 and −0.061143214329 Å for 80→84. It cancels to −2.264119975e-7 Å over 77→84, with net Y +6.815030129e-7 Å and X +0.391348465495 Å. Thus the net interior relative displacement is overwhelmingly X, not exactly pure X; residual YZ norm 7.181286439e-7 Å. Common-mask mean sensitivity is 1.873721961e-8 Å; median sensitivity 6.292464863e-7 Å. Net per-grain residual RMS is 9.688557552e-4 Å top and 3.362816889e-4 Å bottom, much larger than transverse net mean residual.

Fixed-plane geometric reflection does NOT superpose the structures: both tests yield full bijections but ≈0.618383574 Å RMS, with all 11052 residuals exceeding 0.5 Å. The mismatch is predominantly positive Z (~+0.6183834 to +0.6183838 Å), while X/Y means are tiny after X-only fitting. This is an origin/plane-dependent mismatch, not proof that no mirror at any other plane exists; no Z translation or alternate plane was fitted. Opposite mean Z is therefore not sufficient to assert the requested fixed-z0 mirror. The unreflected 77→84 translated control has full-nearest RMS 0.020322449 Å; its bijection/conflict details are given above and in JSON.

The imported snapshots expose one Ni particle type (all atoms); species preservation is validated by element name, not an invented second species. Side preservation is independently enforced. Supplied orientations: top (110), x[001], y[110], z[-1,1,0]; bottom (111), x[1,-2,1], y[111], z[-1,0,1]. a=3.52 Å.
