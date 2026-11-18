# Grouping per publisher (TAX-26): 3 runs

Model claude-opus-5-5 at effort low, as a batch; test set referee/testset/groups.yaml.

| run | merges | splits | otherwise | doubts | cost | per publisher |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 0 | 1 | 6 of 204 | 13 | $0.1261 | $0.0084 |
| 2 | 0 | 0 | 5 of 204 | 15 | $0.1237 | $0.0082 |
| 3 | 0 | 1 | 5 of 204 | 15 | $0.1186 | $0.0079 |

## Per kind of error (hand -> answer), per run

| kind | run 1 | run 2 | run 3 |
| --- | --- | --- | --- |
| edition -> alias | 1 | 1 | 1 |
| edition -> doubt | 1 | 3 | 1 |
| edition -> product | 1 | 0 | 1 |
| product -> doubt | 2 | 1 | 1 |
| split | 1 | 0 | 1 |

## Cost per publisher

| case | publisher | names | run 1 | run 2 | run 3 |
| --- | --- | --- | --- | --- | --- |
| P01 | ivanti | 23 | $0.0193 | $0.0161 | $0.0129 |
| P02 | fortinet | 21 | $0.0120 | $0.0119 | $0.0115 |
| P03 | sonicwall | 7 | $0.0043 | $0.0045 | $0.0043 |
| P04 | gitlab | 4 | $0.0028 | $0.0029 | $0.0029 |
| P05 | citrix | 9 | $0.0058 | $0.0054 | $0.0058 |
| P06 | progress | 7 | $0.0047 | $0.0051 | $0.0048 |
| P07 | beyondtrust | 4 | $0.0030 | $0.0030 | $0.0030 |
| P08 | gladinet | 3 | $0.0021 | $0.0021 | $0.0021 |
| P09 | draytek | 6 | $0.0030 | $0.0031 | $0.0032 |
| P10 | d-link | 15 | $0.0069 | $0.0070 | $0.0068 |
| P11 | the-apache-software-foundation | 17 | $0.0104 | $0.0106 | $0.0102 |
| P12 | adobe | 19 | $0.0123 | $0.0123 | $0.0120 |
| P13 | atlassian | 14 | $0.0092 | $0.0093 | $0.0094 |
| P14 | check-point | 9 | $0.0063 | $0.0061 | $0.0063 |
| P15 | cisco | 46 | $0.0241 | $0.0242 | $0.0233 |

**Norm (no run merges two different products): met.**

## Reading (18 November 2026)

The measurement of TAX-26, [run 44303726884](https://github.com/CobolKrasser/taxnmy/actions/runs/44303726884),
against the hand the tester checked (#40). The runs are
`20261118-122615-group-claude-opus-5-5-low-run1` to `-run3`.

- **The norm is met.** None of the three runs merges two products of the hand, nor a name into a
  product TAX already has.
- **Cost:** $0.3684 for the three runs, $0.12 per run, $0.008 per publisher. The estimate was $0.61:
  the answers used fewer output tokens than it assumes. The largest publisher (P15, 46 names) costs
  $0.024 per run.
- **Where the answers differ from the hand.** Almost all of it is in one case, P12, and goes in the
  safe direction:
  - *Adobe Commerce*, an edition of the existing `adobe/magento` in the hand, is a product of its
    own in runs 1 and 3 (the split) and *doubt* in run 2. A split is a candidate too many, which the
    referee then judges; it joins nothing.
  - the garbled ColdFusion name with version text is an *alias* instead of an *edition* of
    `adobe/adobe-coldfusion` in every run. Both point to the same product.
  - *Acrobat DC*, *Adobe Commerce B2B*, *Adobe Commerce Events* and *Magento Open Source* become
    *doubt* in one run each.
  - outside P12: *FortiSandbox PaaS* (P02) is *doubt* in runs 2 and 3, *Secure Email Gateway*
    (P15) in run 2.
- **The check after the answer works.** In every run the answer for P15 made a name an *alias* of
  a name it had not classed *product*; in run 3 twice, and once (P10) an *alias* without *of*,
  with the reason "placeholder". Code turned each into *doubt*, as `prompt-group.md` prescribes.
  Without the check these would have been aliases pointing nowhere.
- **Doubts:** 13 to 15 of the 204 names per run, about 7%. They are parked for a person (TR10).
- **Not seen:** no *product* became an *alias* of another product, and no *alias* pointed to the
  wrong product.
