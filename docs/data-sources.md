# Data sources

## Seed dataset

| | |
| --- | --- |
| Name | "Insurance claims" (`insurance_claims.csv`), 1,000 rows, 39 columns |
| Author | Abdelrahim Aqqad |
| Host | Mendeley Data, DOI [10.17632/992mh7dk9y](https://data.mendeley.com/datasets/992mh7dk9y/2) |
| License | CC BY 4.0 |
| Retrieved from | `raw.githubusercontent.com/mwitiderrick/insurancedata` (a mirror of the same file) |

Only aggregate distributions are used, from the 822 collision rows: incident-type share, collision-type share, the vehicle-claim amount quantiles, the vehicle make/model list and the fraud rate (24.7%). They are written to `eval/data/seed_stats.json` by `uv run python -m dataset.seed_stats`. No individual record from the seed dataset is copied into this repository.

How the distributions are used, and what is not taken from the seed:

- **Used:** the share of single- versus multi-vehicle collisions, front/rear/side collision zones, vehicle makes and models, and the shape of the repair-amount distribution (scaled by 0.15 to realistic collision-repair sizes).
- **Not used:** the seed's severity labels (their amounts are uncorrelated with severity), its fraud labels (our fraud signals are planted by construction) and all personal fields.
- **Our fraud mix is deliberately lower** (about 11%) than the seed's 24.7%, so the genuine claims dominate as they do in practice.

Everything else (claimants, addresses, policy forms, narratives, estimates, police reports) is synthetic and generated from the labels.

## Attribution

Aqqad, A. (2021). *Insurance claims*. Mendeley Data. https://doi.org/10.17632/992mh7dk9y.2, licensed CC BY 4.0.
