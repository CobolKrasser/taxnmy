# Grouping 20261118-122615-group-claude-opus-5-5-low-run1

- names classed otherwise than the hand: **6** of 204
- merges (two products of the hand in one product of the answer): **0**
- splits (one product of the hand in two of the answer): 1
- doubts (parked): 13
- cost: $0.1261, $0.0084 per publisher; 9438 output tokens

## Per kind of error (hand -> answer)

- edition -> alias: 1
- edition -> doubt: 1
- edition -> product: 1
- product -> doubt: 2
- split: 1

## Per publisher

| case | publisher | names | otherwise | merges | splits | doubts | cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P01 | ivanti | 23 | 0 | 0 | 0 | 1 | $0.0193 |
| P02 | fortinet | 21 | 0 | 0 | 0 | 1 | $0.0120 |
| P03 | sonicwall | 7 | 0 | 0 | 0 | 0 | $0.0043 |
| P04 | gitlab | 4 | 0 | 0 | 0 | 0 | $0.0028 |
| P05 | citrix | 9 | 0 | 0 | 0 | 0 | $0.0058 |
| P06 | progress | 7 | 0 | 0 | 0 | 0 | $0.0047 |
| P07 | beyondtrust | 4 | 0 | 0 | 0 | 0 | $0.0030 |
| P08 | gladinet | 3 | 0 | 0 | 0 | 0 | $0.0021 |
| P09 | draytek | 6 | 0 | 0 | 0 | 0 | $0.0030 |
| P10 | d-link | 15 | 0 | 0 | 0 | 0 | $0.0069 |
| P11 | the-apache-software-foundation | 17 | 0 | 0 | 0 | 1 | $0.0104 |
| P12 | adobe | 19 | 6 | 0 | 1 | 3 | $0.0123 |
| P13 | atlassian | 14 | 0 | 0 | 0 | 2 | $0.0092 |
| P14 | check-point | 9 | 0 | 0 | 0 | 1 | $0.0063 |
| P15 | cisco | 46 | 0 | 0 | 0 | 4 | $0.0241 |

## Merges and names classed otherwise

- P12 n4 'Acrobat DC': hand edition of 'Acrobat', answer doubt. Acrobat DC may be an edition of Acrobat, but its versions match Reader DC, so it is unclear.
- P12 n7 'Adobe ColdFusion ColdFusion 2016 Update 3 and earlier, ColdFusion 11 update 11 and earlier, ColdFusion 10 Update 22 and earlier': hand edition of adobe/adobe-coldfusion, answer alias of adobe/adobe-coldfusion. Garbled ColdFusion name with version text.
- P12 n8 'Adobe Commerce': hand edition of adobe/magento, answer product. Adobe Commerce is a product with its own releases.
- P12 n9 'Adobe Commerce B2B': hand product, answer doubt. Commerce B2B has its own version numbers and may be a separate extension product or part of Commerce.
- P12 n10 'Adobe Commerce Events': hand product, answer doubt. Commerce Events has its own versions; unclear if a separate product.
- P12 n19 'Magento Open Source': hand alias of adobe/magento, answer alias of adobe/magento. Magento Open Source is the existing Magento product written in full.
- P15 code: n4: alias of 'n10', which is neither an existing key nor a name classed product; doubt
