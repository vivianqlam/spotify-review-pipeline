# Rejected first memo draft (memo_v1) - why it was not used

The first memo run (same aggregates and claim tables, prompt `memo_v1`) recommended **billing/support**. Human review rejected it because:

1. **Misleading issue names.** The grouper had named issues from each issue's *six most severe* quotes, so the generic
   catch-all `OTH-general` (93,866 complaints, mean severity 1.96) was named "Privacy and data-security concerns",
   `BIL-general` "Unexpected charges and account changes" and `PLA-general` "Playlists and data lost unexpectedly".
   The memo repeated those names, overstating financial/data harm.
2. **Unrepresentative evidence pack.** Memo examples were also the highest-severity members of each issue.
3. **A garbled evidence bullet** that cited the same review ID three times.

**Fix (code, not prompt-only):** both evidence packs now use a seeded pseudo-random sample of each issue's original
records (`sha256(review_id)` order) - see `pipeline/analysis.py` and `pipeline/memo.py`; `memo_v2` adds instructions to
treat model-written issue names as imprecise and cite each review ID once. Membership, counts, ranking and every claim
value were unchanged (`rerank_check.json`: identical). With representative evidence the memo recommends **playback**
and presents billing/support (cancellation intent) and usability (volume) as the alternatives - showing that the area
choice depends on how severity, volume and cancellation intent are weighted, which the memo states as a limitation.
