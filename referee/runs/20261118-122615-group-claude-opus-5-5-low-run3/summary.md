# Grouping 20261118-122615-group-claude-opus-5-5-low-run3

- names classed otherwise than the hand: **5** of 204
- merges (two products of the hand in one product of the answer): **0**
- splits (one product of the hand in two of the answer): 1
- doubts (parked): 15
- cost: $0.1186, $0.0079 per publisher; 9031 output tokens

## Per kind of error (hand -> answer)

- edition -> alias: 1
- edition -> doubt: 1
- edition -> product: 1
- product -> doubt: 1
- split: 1

## Per publisher

| case | publisher | names | otherwise | merges | splits | doubts | cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P01 | ivanti | 23 | 0 | 0 | 0 | 2 | $0.0129 |
| P02 | fortinet | 21 | 1 | 0 | 0 | 2 | $0.0115 |
| P03 | sonicwall | 7 | 0 | 0 | 0 | 0 | $0.0043 |
| P04 | gitlab | 4 | 0 | 0 | 0 | 0 | $0.0029 |
| P05 | citrix | 9 | 0 | 0 | 0 | 0 | $0.0058 |
| P06 | progress | 7 | 0 | 0 | 0 | 0 | $0.0048 |
| P07 | beyondtrust | 4 | 0 | 0 | 0 | 0 | $0.0030 |
| P08 | gladinet | 3 | 0 | 0 | 0 | 0 | $0.0021 |
| P09 | draytek | 6 | 0 | 0 | 0 | 0 | $0.0032 |
| P10 | d-link | 15 | 1 | 0 | 0 | 1 | $0.0068 |
| P11 | the-apache-software-foundation | 17 | 0 | 0 | 0 | 2 | $0.0102 |
| P12 | adobe | 19 | 3 | 0 | 1 | 0 | $0.0120 |
| P13 | atlassian | 14 | 0 | 0 | 0 | 2 | $0.0094 |
| P14 | check-point | 9 | 0 | 0 | 0 | 1 | $0.0063 |
| P15 | cisco | 46 | 0 | 0 | 0 | 5 | $0.0233 |

## Merges and names classed otherwise

- P02 n17 'FortiSandbox PaaS': hand edition of 'FortiSandbox', answer doubt. FortiSandbox PaaS shows its own version scheme alongside FortiSandbox versions, so edition versus own product is unclear.
- P10 n13 'DSL-2750B Devices': hand product, answer doubt. placeholder
- P10 code: n13: alias of None, which is neither an existing key nor a name classed product; doubt
- P12 n7 'Adobe ColdFusion ColdFusion 2016 Update 3 and earlier, ColdFusion 11 update 11 and earlier, ColdFusion 10 Update 22 and earlier': hand edition of adobe/adobe-coldfusion, answer alias of adobe/adobe-coldfusion. ColdFusion written with version text appended.
- P12 n8 'Adobe Commerce': hand edition of adobe/magento, answer product. Adobe Commerce is a product with its own updates.
- P12 n19 'Magento Open Source': hand alias of adobe/magento, answer alias of adobe/magento. Magento Open Source is the existing Magento product.
- P15 code: n4: alias of 'n10', which is neither an existing key nor a name classed product; doubt
- P15 code: n44: alias of 'n27', which is neither an existing key nor a name classed product; doubt
