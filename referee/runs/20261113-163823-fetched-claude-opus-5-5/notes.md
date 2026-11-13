# Notes on run A2 (fetched, Claude Opus 5.5, effort medium, with the ladder)

Run by the workflow *referee* on 13 November 2026, from main 19eba95:
[run 43462875320](https://github.com/CobolKrasser/taxnmy/actions/runs/43462875320). It had four jobs: fetch, search,
refetch and judge. It used ladder steps 0–4, the fixes from the review of run A, and the AI models
(H01 and H02 are now *accept*). The search of step 4 is a run of its own:
`../20261113-163355-search-claude-opus-5-5/`.

## Result, compared with run A

| measure | run A | run A2 |
| --- | --- | --- |
| bad cases let through (target 0) | 0 | **0** |
| good cases rejected | 6 | **1** (G07) |
| cases for a person | 19 | **12** |
| outcome in `expect` | 39 of 60 | **50 of 60** |
| stopped by code (lint, or 404 on the product source) | 5 | 7 |
| cost per case sent to the referee | $0.0527 | $0.0651 (max $0.1236) |
| cost of the referee calls | $2.8969 | $3.4521 |
| cost of step 4 (7 searches) | – | $0.4170 |
| spent on all runs | $2.8969 | **$6.7660 of $20.00**, so $13.2340 is left |

A call costs more than in run A, because the ladder now gives the referee more text.

## The methods (`sources.json` of the refetch job)

| method | read | not read |
| --- | --- | --- |
| http | 73 | 4 refused, 3 disallowed, 3 404 |
| machine (step 0, the NVD API) | 8 | – |
| browser | 3 | – |
| api | – | 1 timeout (NVD) |
| search+http (step 4) | 9 | – |

## Step 4

There were 7 searches, one for each closed source, at about $0.06 each. That is more than the
$0.01 fee, because the results come in as input tokens of Opus 5.5.

| closed source | found and read | effect |
| --- | --- | --- |
| fortinet.com, 3 sources (`disallowed`) | `docs.fortinet.com`: the fast-path page of the 60F and 61F, and the hardware list. The other result URLs are on www.fortinet.com, which robots.txt closes | G05 is now *accept*. B21 is rejected on S8: the hardware list has no FortiGate 66F |
| dell.com, 2 sources (`refused`) | the R650 shop page, and the technical guide and spec sheet as PDFs on `i.dell.com` | G09 is still *human*: they confirm the product, but not the published versions |
| sony.nl, 2 sources (`refused`) | nothing: every URL found is on sony.nl, and sony.nl refuses | G06 and B15 are still *human* |

**Waste:** the same query ran more than once for one product. Fortinet ran 3 times, Dell twice and
Sony twice. Running each query once would have cost 3 searches instead of 7, about $0.24 less.

## What is still wrong

| case | outcome | why | remedy |
| --- | --- | --- | --- |
| G07 | reject (S8) | the Samsung page now redirects to the Galaxy Tab S10 | data: a current source (review of run A, §3) |
| G12, G17, G21, G22 | human (S5) | no source shows who publishes; the publisher sources are endoflife.date pages or tags | data: the round of filling in publisher sources (review of run A, §2) |
| G09 | human (S8) | the versions source refuses, and what was found does not show versions | data: Dell's catalog in `machine_sources` of `publishers/dell.yaml`, once its source is checked |
| G06, B15 | human | Sony refuses, and the search finds only sony.nl | data: another open source (the analyst's question to Fred) |
| G03 | human (S8) | the NVD API timed out on one CPE | harness: one retry on a timeout of the API step |
| H02 | human (S4, S6) | the referee doubts whether "OpenAI API Models" is one product and a name, rather than a catalogue and a label | a choice: allow *human* in `expect`, or another name |

`sources.json` (the fetched texts) is not committed. It holds the texts of third-party pages, and it
is kept in the artifacts of the run for 90 days.
