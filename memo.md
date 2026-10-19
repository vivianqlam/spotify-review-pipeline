> **Provenance:** written by the memo role (`claude-sonnet-5-5`, prompt `memo_v2`, request `msg_011CgC8Jqr3Bi89VSiZNbVhq`) from [`runs/full/memo_inputs.json`](runs/full/memo_inputs.json) only. Automated check: [`runs/full/memo_check.json`](runs/full/memo_check.json) - 36 claim IDs, 9 issue IDs, 4 review IDs and 49 numbers, all matched to saved calculations. `C###` claims are in [`grading/claims.csv`](grading/claims.csv); `A##` area claims in [`runs/full/area_claims.csv`](runs/full/area_claims.csv). Quoted snippets were checked against the source text by hand. An earlier draft (`memo_v1`) was rejected in human review; see [`docs/memo_v1_rejected.md`](docs/memo_v1_rejected.md).

# Decision memo: next-quarter product priority

## Recommendation

**Prioritize playback (playback+downloads) next quarter.** It has the highest mean severity among the areas that carry volume, and by far the most severe complaints. It has 40,662 complaints [A13] with mean severity 3.119128 [A15] and 10,936 severity-4+ complaints [A17]. Usability has more complaints but far fewer severe ones.

Fix first:
1. **PLA-general** (Frequent bugs during playback): 12,266 complaints [C017], severity sum 37,202 [C018], mean severity 3.032937 [C019]. The name looks too broad; the examples cover songs looping, the app switching off, and failing to stay on in the background.
2. **PLA-stops_skips_pauses** (Music stops unexpectedly): 6,623 complaints [C033], severity sum 20,756 [C034], mean severity 3.133927 [C035].
3. **ACC-login_failure** (Cannot log in via phone or Facebook): 6,445 complaints [C029], mean severity 3.884717 [C031]. This is an access issue, but it is the most severe named issue. It is a small, targeted fix to run alongside the playback work.

## Evidence

- The largest issue by volume is OTH-general (General dissatisfaction with the app): 93,866 complaints [C001], priority score 184,272 [C004]. It is a catch-all with mean severity 1.963139 [C003], so it is not actionable.
- The next-highest is USA-ads_interruptions (Too many unskippable ads): 29,771 complaints [C005], priority score 62,012 [C008].
- PLA-general ranks fifth by priority score at 37,202 [C020], but its severity is well above the higher-volume issues.
- Representative reviews:
  - c2c38440-5dce-479b-aac4-e336b0d8cb79 (severity 4): "2023 suddenly off, why??" after clearing cache and restarting.
  - ec296b31-61cb-4588-ba80-e8ab992b2281 (severity 3): the app is "suppose to stay on when you go on another app."
  - 54dca140-19f7-4c8c-a176-19fcdeef283d (severity 2): "same 4 songs playing on repeat."

## Alternatives considered

- **Usability**: 75,616 complaints [A07], 25.4% of all complaints [A10], severity sum 173,911 [A08]. Mean severity is only 2.299923 [A09], and severity-4+ complaints number 554 [A11]. Much of the volume is ads (USA-ads_interruptions, 62,012 [C008]) and shuffle limits (USA-shuffle_queue_controls, 13,603 complaints [C025]). The ad and shuffle complaints are mostly annoyance tied to the free tier. This ranks lower on severity, but it would move up if leadership weights volume over severity. It also has 7,498 cancellation-intent reviews [A12].
- **Billing/support**: 55,661 complaints [A19], severity sum 144,756 [A20], mean 2.600672 [A21], and 2,515 severity-4+ [A23]. It has the most cancellation-intent reviews, 13,412 [A24]. Its biggest issues are paywall frustration (BIL-general, 24,254 complaints [C009]; BIL-premium_paywall_free_limits, 17,349 [C013]). These are partly pricing and product-model questions, not defects. If cancellation intent is the main goal, this area would rank first. Note that one BIL-premium_paywall_free_limits example (e7747485-42c5-4bb9-b1e2-1b4044eedb5a) is really about shuffle and previous-track controls, so that label may overlap with USA issues.
- **Access**: 11,697 complaints [A01], 3.9% of all complaints [A04], but mean severity 3.806361 [A03], 9,670 severity-4+ [A05] and 745 cancellation-intent reviews [A06]. It is small but severe, and the login fix is cheap to combine with the playback work. It ranks lower mainly because its volume is smallest.

## Limitations

- Reviews are self-selected, cover May 2022 to Nov 2023 (first and last months partial), and are not a full customer population. Cancellation figures are expressed intent, not observed churn.
- Labels come from a model. An independent check on 1,000 reviews showed topic agreement of 84.9%, intent 90.5% and severity 85.5%. A 50-review human-labeled set showed topic agreement of 80.0%, intent 90.0%, exact severity 84.0% and severity MAE 0.20. Topic labels are the weakest, so issue boundaries are imprecise.
- Issue names were generated from a few examples. Several are broad or mixed (OTH-general, BIL-general, PLA-general, USA-general). Some examples in USA-general and BIL-general are plain ad or pricing complaints, and some fit other categories.
- The baseline priority score is complaint volume weighted by severity. It ignores revenue, user segment, fix cost, how many users are affected beyond reviewers, and causes. The evidence pack has only a few examples per issue, so it cannot show root causes.
- The area ranking depends on how much severity is weighted against volume or cancellation intent. Leadership should confirm that weighting before committing.