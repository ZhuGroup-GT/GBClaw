# gb-periodicity-20261008-171025-3c6369: 全部坐标候选

长度单位 nm；残差单位 Å。频谱、场的空间递归与完整原子匹配分别列出。
原分类只保留作追溯；不把 relative_short 当成唯一短周期。所有局部事件间距及原始序列请查看旁边的 local_strain / coordinate_rows 报告。

方向：upper [0 0 -1] : lower [1 1 -2]
原始比值是完整 a[hkl] 向量的实测上 : 下倍数；方向依据：validated_source_direction
图中采用经双侧长度验证的名义配对；无合格配对时保留 ≈ 实测值。无已验证的源取向时，以立方对称等价晶向简写显示；原有有符号晶向不变。
高亮说明：P012: local-repeat display score 0.961 = 0.4×row 0.999 + 0.3×strain 0.930 + 0.3×geometry 0.940. This is visual emphasis, not a unique or confirmed period.

结构 FFT 列给出实际测得的峰位置，括号为背景／网格检查分类；不把候选平移改写为 FFT 波长。完整背景对照及每个局部峰见 `../elastic/structure_fft_peaks.csv`。
应变 FFT 层数只统计通过审查的真实峰，不含远场参考。所有未通过审查的峰也保存在 `../elastic/strain_fft_peaks.csv`；CSV 的 strain_actual_peak_channels 与 strain_reviewed_peak_channels 分别列出实际峰和通过审查的峰。

S1: 原子行 motif 3.880414 nm；关联坐标平移 3.874434 nm；11[0 0 -1] : 4.5[1 1 -2]；within_tolerance。

| ID | 平移 / nm | 显示晶格配对 | 上 : 下实测倍数 | near RMS | defect RMS | 两侧近场应变相关性 | 有递归的层数 | 有通过审查 strain FFT 峰的层数 | near structure FFT / nm | core structure FFT / nm |
|---|---:|---|---|---:|---:|---|---:|---:|---|---|
| P001 | 0.6448 | ≈ 1.83[0 0 -1] : 0.75[1 1 -2] | ≈ 1.83 : 0.75 | 0.9768 | 0.9995 | -0.044 / -0.332 | 2/6 | 4/6 | 0.643 (gb_enhanced) | 0.647 (gb_enhanced) |
| P002 | 0.6777 | ≈ 1.93[0 0 -1] : 0.79[1 1 -2] | ≈ 1.93 : 0.79 | 0.9193 | 0.9445 | -0.076 / -0.410 | 2/6 | 0/6 | no_resolved_peak | no_resolved_peak |
| P003 | 0.7749 | ≈ 2.2[0 0 -1] : 0.9[1 1 -2] | ≈ 2.2 : 0.9 | 0.8359 | 0.8628 | -0.143 / -0.615 | 1/6 | 2/6 | 0.774 (gb_enhanced) | 0.779 (gb_enhanced) |
| P004 | 1.0653 | ≈ 3.03[0 0 -1] : 1.24[1 1 -2] | ≈ 3.03 : 1.24 | 0.8863 | 0.9142 | -0.086 / -0.776 | 0/6 | 0/6 | no_resolved_peak | no_resolved_peak |
| P005 | 1.3561 | 4[0 0 -1] : 1.5[1 1 -2] | ≈ 3.86 : 1.57 | 0.6346 | 0.6727 | -0.241 / -0.318 | 0/6 | 5/6 | no_resolved_peak | no_resolved_peak |
| P006 | 1.7433 | 5[0 0 -1] : 2[1 1 -2] | ≈ 4.96 : 2.02 | 0.2597 | 0.2990 | 0.143 / 0.528 | 1/6 | 0/6 | no_resolved_peak | no_resolved_peak |
| P007 | 2.1310 | 6[0 0 -1] : 2.5[1 1 -2] | ≈ 6.06 : 2.47 | 0.2789 | 0.3078 | 0.045 / 0.519 | 1/6 | 0/6 | no_resolved_peak | no_resolved_peak |
| P008 | 3.4490 | 10[0 0 -1] : 4[1 1 -2] | ≈ 9.81 : 4 | 0.5697 | 0.5979 | 0.072 / 0.254 | 0/6 | 5/6 | no_resolved_peak | no_resolved_peak |
| P009 | 3.4868 | 10[0 0 -1] : 4[1 1 -2] | ≈ 9.91 : 4.05 | 0.4259 | 0.4651 | 0.120 / 0.358 | 0/6 | 5/6 | 3.820 (not_background_enhanced) | 3.820 (not_background_enhanced) |
| P010 | 3.6092 | ≈ 10.26[0 0 -1] : 4.19[1 1 -2] | ≈ 10.26 : 4.19 | 1.1497 | 1.1665 | 0.302 / 0.644 | 1/6 | 5/6 | 3.820 (not_background_enhanced) | 3.820 (not_background_enhanced) |
| P011 | 3.6589 | ≈ 10.4[0 0 -1] : 4.24[1 1 -2] | ≈ 10.4 : 4.24 | 1.3433 | 1.3492 | 0.407 / 0.736 | 1/6 | 5/6 | 3.820 (not_background_enhanced) | 3.820 (not_background_enhanced) |
| P012 ★ (S1) | 3.8744 | 11[0 0 -1] : 4.5[1 1 -2] | ≈ 11.02 : 4.49 | 0.0915 | 0.0970 | 0.675 / 0.918 | 2/6 | 5/6 | 3.820 (not_background_enhanced) | 3.820 (not_background_enhanced) |
| P013 | 4.0938 | ≈ 11.64[0 0 -1] : 4.75[1 1 -2] | ≈ 11.64 : 4.75 | 1.2956 | 1.3077 | 0.367 / 0.693 | 1/6 | 5/6 | 3.820 (not_background_enhanced) | 3.820 (not_background_enhanced) |
| P014 | 4.1431 | ≈ 11.78[0 0 -1] : 4.81[1 1 -2] | ≈ 11.78 : 4.81 | 1.1096 | 1.1309 | 0.283 / 0.598 | 1/6 | 5/6 | 3.820 (not_background_enhanced) | 3.820 (not_background_enhanced) |
| P015 | 4.2288 | ≈ 12.02[0 0 -1] : 4.91[1 1 -2] | ≈ 12.02 : 4.91 | 0.5803 | 0.6077 | 0.126 / 0.398 | 0/6 | 5/6 | 3.820 (not_background_enhanced) | 3.820 (not_background_enhanced) |
| P016 | 4.2621 | 12[0 0 -1] : 5[1 1 -2] | ≈ 12.12 : 4.94 | 0.4762 | 0.5069 | 0.092 / 0.316 | 0/6 | 5/6 | 3.820 (not_background_enhanced) | 3.820 (not_background_enhanced) |
| P017 | 4.5287 | ≈ 12.88[0 0 -1] : 5.25[1 1 -2] | ≈ 12.88 : 5.25 | 0.9458 | 0.9696 | -0.180 / -0.386 | 0/6 | 5/6 | no_resolved_peak | no_resolved_peak |
| P018 | 4.5526 | ≈ 12.94[0 0 -1] : 5.28[1 1 -2] | ≈ 12.94 : 5.28 | 0.9147 | 0.9396 | -0.205 / -0.440 | 0/6 | 5/6 | no_resolved_peak | no_resolved_peak |
| P019 | 5.2309 | 15[0 0 -1] : 6[1 1 -2] | ≈ 14.87 : 6.07 | 0.6239 | 0.6644 | -0.334 / -0.320 | 0/6 | 0/6 | no_resolved_peak | no_resolved_peak |
| P020 | 5.6186 | 16[0 0 -1] : 6.5[1 1 -2] | ≈ 15.97 : 6.52 | 0.2546 | 0.2956 | 0.076 / 0.523 | 1/6 | 0/6 | no_resolved_peak | no_resolved_peak |
| P021 | 6.0058 | 17[0 0 -1] : 7[1 1 -2] | ≈ 17.07 : 6.97 | 0.2904 | 0.3165 | — / — | 0/6 | 0/6 | outside_observable_band | outside_observable_band |
| P022 | 7.3179 | 21[0 0 -1] : 8.5[1 1 -2] | ≈ 20.81 : 8.49 | 0.6066 | 0.6344 | — / — | 0/6 | 0/6 | outside_observable_band | outside_observable_band |
| P023 | 7.3621 | 21[0 0 -1] : 8.5[1 1 -2] | ≈ 20.93 : 8.54 | 0.4169 | 0.4587 | — / — | 0/6 | 0/6 | outside_observable_band | outside_observable_band |
| P024 | 7.7496 | 22[0 0 -1] : 9[1 1 -2] | ≈ 22.03 : 8.99 | 0.1052 | 0.1101 | — / — | 0/6 | 0/6 | outside_observable_band | outside_observable_band |
| P025 | 7.9627 | ≈ 22.64[0 0 -1] : 9.24[1 1 -2] | ≈ 22.64 : 9.24 | 1.3208 | 1.3324 | — / — | 0/6 | 0/6 | outside_observable_band | outside_observable_band |
| P026 | 8.0119 | ≈ 22.78[0 0 -1] : 9.3[1 1 -2] | ≈ 22.78 : 9.3 | 1.1149 | 1.1381 | — / — | 0/6 | 0/6 | outside_observable_band | outside_observable_band |
| P027 | 8.1368 | 23[0 0 -1] : 9.5[1 1 -2] | ≈ 23.13 : 9.44 | 0.4925 | 0.5199 | — / — | 0/6 | 0/6 | outside_observable_band | outside_observable_band |
| P028 | 8.1876 | ≈ 23.28[0 0 -1] : 9.5[1 1 -2] | ≈ 23.28 : 9.5 | 0.7064 | 0.7225 | — / — | 0/6 | 0/6 | outside_observable_band | outside_observable_band |
| P029 | 8.3975 | ≈ 23.87[0 0 -1] : 9.74[1 1 -2] | ≈ 23.87 : 9.74 | 0.9577 | 0.9818 | — / — | 0/6 | 0/6 | outside_observable_band | outside_observable_band |
| P030 | 8.4274 | ≈ 23.96[0 0 -1] : 9.78[1 1 -2] | ≈ 23.96 : 9.78 | 0.9095 | 0.9349 | — / — | 0/6 | 0/6 | outside_observable_band | outside_observable_band |
