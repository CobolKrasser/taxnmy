# Notes on run A (fetched, Claude Opus 5.5, effort medium)

Run by the workflow *referee* on 13 November 2026,
[run 43441720776](https://github.com/CobolKrasser/taxnmy/actions/runs/43441720776). Before it,
*check-urls* ([run 43441608472](https://github.com/CobolKrasser/taxnmy/actions/runs/43441608472))
gave only the known deviations: H02 403, B20 and B21 404 (intended), B29 outlook page 404.

## Result

- **Bad cases let through: 0** of 30. B15 and B30 went to a person, which counts as caught.
- **Good cases rejected: 6** of 28: G05, G08, G10, G19, G20, G27.
- **Cases for a person: 19.** 14 of them are good cases that `expect` does not allow to be human.
- H01 (an AI model) was accepted; `expect` is *human*.
- **Cost: $2.8969** for 55 calls. That is **$0.0527 per case sent to the referee** (max $0.1195),
  or $0.0483 per case over all 60. On average a call had 10,500 input tokens and 1,030 output
  tokens.
- The quote check changed nothing: every quote of a *yes* was found in its page.

## Why good cases fail

| cause | cases | what the referee said |
| --- | --- | --- |
| The CPE source is an NVD search page. It renders with JavaScript, so plain HTTP gets only the title "NVD - Home". | G05, G08, G10, G19, G20 rejected on S8; G03, G09 human | the CPE is not confirmed by its source (S8 *no*) |
| The site blocks plain requests (403) | G06 (Sony), G09 (Dell), B15 (Sony) | doubt on all rules |
| The page has no text without JavaScript or frames | G03 (Netgear), G07, G27 (Samsung doc pages) | doubt; G27 S8 *no* |
| The versions source is general and does not name the product | G01, G04, G08 | S4 or S8 doubt |
| The publisher source is an endoflife.date page or a tag, not a page of the publisher | G12, G16, G17, G21, G22 | S5 doubt |
| The publisher's full name does not appear in the source | G14 ("Amazon Web Services") | S6 doubt |

Most failures come from the sources that the fetcher can read, not from the referee's judgment.
Five of the six good cases that were rejected fail on one thing: an NVD page that plain HTTP cannot
read. The referee treated the unreadable page as "does not confirm" (*no*) rather than as
unavailable (*doubt*).

`sources.json` (the fetched texts) is not committed: it holds the texts of third-party pages. It is
kept in the artifact of the run for 90 days.
