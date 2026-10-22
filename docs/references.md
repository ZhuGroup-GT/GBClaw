# References and acknowledgments

This page separates the GBClaw framework manuscript from the related study of grain boundary physics, then lists the scientific software and potentials that informed the project. The framework entry follows the author-provided manuscript; the related study links to its public arXiv record.

[Documentation](README.md) · [Architecture and design](architecture.md)

## GBClaw manuscript

**GBClaw: An agentic AI framework for atomistic grain boundary research and mechanistic discovery**

Yazhuo Liu, Kunqing Ding, Xiaoguo Gong, Zifeng Wang, Yichen Yang, Yifeng Che, Wen Chen, Josh Kacher, Olivier Pierron, Shigenobu Ogata, and Ting Zhu.

Yazhuo Liu and Kunqing Ding contributed equally.

| Publication detail | Status |
| --- | --- |
| arXiv | Forthcoming; link will be added after submission. |
| DOI | Will be added when assigned. |
| Citation record | Will be added with the public manuscript metadata. |

If you use this architecture in your work, or find it helpful, please cite this manuscript. These fields are placeholders for the framework paper; the separate study below already has an arXiv record.

## Related scientific study

Kunqing Ding, Yazhuo Liu, Yin Zhang, Lihua Wang, Xiaodong Han, and Ting Zhu. **Misfit-dislocation hierarchy governs sliding of asymmetric non-CSL grain boundaries.** [arXiv:2609.14673](https://arxiv.org/abs/2609.14673) (2026).

Readers interested in the grain boundary physics can start here. The study explains how primary and secondary misfit dislocations relate to structural periodicity and sliding in asymmetric non-CSL boundaries.

## Scientific and software foundations

- R. Hadian, B. Grabowski, J. Neugebauer, GB code: A grain boundary generation code. *Journal of Open Source Software* **3**, 900 (2018).
- C. Zhang, B. I. Yakobson, MatClaw: An Autonomous Code-First LLM Agent for End-to-End Materials Exploration. arXiv:2604.02688 [Preprint] (2026). [DOI](https://doi.org/10.48550/arXiv.2604.02688).
- A. Chaudhari, J. Ock, A. Barati Farimani, Modular large language model agents for multi-task computational materials science. *Commun Mater* **7**, 131 (2026).

GBClaw draws on [MatClaw](https://arxiv.org/abs/2604.02688v3) for code-first agent design and [MatSci-LLM-Agents](https://github.com/cakshat/MatSci-LLM-Agents) for multi-agent design. Its runtime builds on [LangChain](https://python.langchain.com/) and [LangGraph](https://langchain-ai.github.io/langgraph/). Its interactive lattice workflow uses [DichromaticMap](https://github.com/Yazhuo-Liu/DichromaticMap).

## Interatomic potentials

- **Al-Cu ADP:** Apostol, F. & Mishin, Y. Interatomic potential for the Al-Cu system. *Phys. Rev. B* **83**, 054116 (2011).
- **Ni-Cr ADP:** Howells, C. A. & Mishin, Y. Angular-dependent interatomic potential for the binary Ni–Cr system. *Modelling Simul. Mater. Sci. Eng.* **26**, 085008 (2018).
- **Fe EAM:** Mendelev, M. I. *et al.* Development of new interatomic potentials appropriate for crystalline and liquid iron. *Philosophical Magazine* **83**, 3977–3994 (2003).

The runtime's configured `potentials.json` determines which potentials are available for a particular installation. Ask the agent to list available potentials; see the [catalog documentation](tools.md#potentials-catalog) for configuration and resolution.
