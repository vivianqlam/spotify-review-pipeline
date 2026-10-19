# Decision memo: next-quarter product priority

## Recommendation

**Priority area: usability.** Fix these first:

1. **USA-shuffle_queue_controls (Shuffle forced and replay controls removed):** 3 complaints, severity sum 9, mean severity 3.000000 [C009, C010, C011].
2. **USA-ui_redesign (Basic player features removed in redesign):** 3 complaints, severity sum 8, mean severity 2.666667 [C017, C018, C019].
3. **USA-general (Ads, bugs, and general dissatisfaction):** 4 complaints, severity sum 8 [C013, C014]. This one is broad, so triage it into specific fixes.

Usability has the most complaints of any area at 15, with a severity sum of 36, 28.8% of all complaints, and 4 cancellation statements [A07, A08, A10, A12]. No usability complaint reached severity 4 or higher [A11], so this is a case for volume and breadth, not for acute harm. The first two issues describe the same pattern: players lost basic controls (skip, go back, repeat, choose a song) after an update.

## Evidence

- The top issue by priority score is OTH-general (Assorted complaints about the app), at 28 [C004]. It is a catch-all with mean severity 2.000000 [C003], so it can't be assigned to a team.
- The top specific issue is BIL-premium_paywall_free_limits (Free users lose skip and song selection), with 4 complaints and a priority score of 12 [C005, C008]. It overlaps strongly with the usability issues above.
- Total complaints analysed: 52 [A25].
- Representative reviews:
  - 88439ec2-7c49-4c57-bedb-c5e098692aff (USA-shuffle_queue_controls, cancellation intent): "remove all the new features… skip songs."
  - e16a4550-43e7-4ae1-b538-a65cc652c31b (USA-shuffle_queue_controls, cancellation intent): "Can't repeat and play specific part."
  - 9e5f9745-f8be-48d3-b9de-4d118507d94a (USA-ui_redesign, cancellation intent): "can't even go back few second."

## Alternatives considered

- **Billing/support:** 8 complaints, severity sum 22, mean severity 2.750000, 15.4% of complaints, 1 cancellation [A19, A20, A21, A22, A24]. Its top issue, BIL-premium_paywall_free_limits, is really a product-design question about what free users can do, and it scores 12 [C008]. Reviewers describe the same lost controls as the usability issues. If leadership treats the free-tier limits as a deliberate commercial decision, usability fixes would be limited to the paid experience, and this area would move up.
- **Playback (playback+downloads):** 5 complaints, severity sum 15, mean severity 3.000000, 9.6% of complaints, 1 item at severity 4+, 1 cancellation [A13–A18]. It is smaller but more severe per complaint. It would rise if more reports of failures like the crash or Google assistant playback issue appeared.
- **Access:** 3 complaints, severity sum 11, mean severity 3.666667, 5.8% of complaints, 2 at severity 4+, 0 cancellations [A01–A06]. It has the highest severity per complaint. ACC-general (App stuck on loading and ad-heavy login) has 2 complaints and priority score 7 [C021, C024], and ACC-password_reset (Password rejected during sign-in) has 1 complaint at mean severity 4.000000 [C037, C039]. Review 66a29b3c-c14d-440e-b722-b23e0c99ae82 ("stuck on loading/logo screen") is severity 4. A small, targeted reliability fix is reasonable alongside the main priority, but the volume is too low to lead. Further severe access reports would change this.

## Limitations

- The reviews are self-selected, historical (2022-05 to 2023-11, first and last months partial) and come from 100 source rows, all completed. They don't represent all users.
- Cancellation counts are expressed intent, not observed churn.
- An independent check of 20 reviews showed topic agreement of 90.0%, intent agreement of 95.0% and severity agreement of 95.0%. That is decent, but with a sample this small some labels are likely wrong, especially topic labels.
- Several issues are catch-all labels (OTH-general, USA-general, BIL-general, ACC-general). Their classifications are unresolved, and part of the usability total may belong with billing. Some evidence reviews fit poorly: for example, a review about a blend feature request sits under USA-ui_redesign.
- The priority score (severity sum) ignores user reach, revenue, effort and cost to fix, and it favours issues with many low-severity complaints over a few severe ones. Counts are small throughout, so the ranking differences are modest and should be checked against product telemetry before committing resources.