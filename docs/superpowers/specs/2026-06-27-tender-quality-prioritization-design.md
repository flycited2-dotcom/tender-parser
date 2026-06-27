# Tender Quality Prioritization Design

## Goal

Improve tender report quality without reducing coverage. The parser should keep collecting a wide set of potentially useful tenders, but the exported report must make the first manual review much faster.

## Current Problem

The parser now collects enough records from Rostender, B2B-Center, TenderPro, and Torgi82. The last live run found 1,633 raw records and exported 171 new tenders for CRM review.

The weak point is review order:

- strong matches and weak matches are too close together in the report;
- B2B-Center often has useful text but no region or price on the list page;
- broad phrases like "расходные материалы" can pull medical, lab, and unrelated supply tenders into the review set;
- obvious non-target topics should be excluded earlier, while uncertain business-relevant records should still remain visible.

## Proposed Behavior

Add a review priority layer on top of the existing `filter_status` and `match_confidence` fields.

The priority layer must classify records into:

- `hot`: strong candidate. Category, target region, acceptable price, and active deadline are known.
- `review`: plausible candidate. It has a useful product/category match but misses one important field such as region, price, or deadline.
- `wide`: low-confidence candidate. It may be useful, especially from broad public sources, but needs manual inspection before any CRM action.
- `excluded`: not useful for this supplier profile.

The parser must not silently delete uncertain tenders that could still be useful. It should only exclude records when a stop topic, expired deadline, non-target known region, low known price, or no target category is clear.

## Stop And Weak Topics

The exclusion layer should remain strict for:

- fuel, diesel, gasoline, GSM;
- capital construction, road works, building repair, design estimates;
- medicines, pharmaceuticals, medical preparations;
- lab and clinical-diagnostic supplies when they match only broad office or consumable wording;
- filter cartridges and chemical cartridges that are not printer cartridges;
- dialysis and medical consumables.

The weak-priority layer should demote broad matches instead of excluding them when there is still a possible fit:

- "расходные материалы" without printer, cartridge, toner, office equipment, MFP, or scanner context;
- B2B-Center records without region and price;
- records where only one weak office/equipment word matched, such as generic "ручка" or "ящик".

## Report Shape

Excel export should become easier to scan:

- `Горячие`: `filter_status=matched` and priority `hot`.
- `На проверку`: priority `review`.
- `Широкий хвост`: priority `wide`.
- `Отсеянные`: priority `excluded`.
- `Новые`: all new actionable records, ordered by priority: hot, review, wide.

JSON exports should include the priority field so the future CRM integration can use it directly.

## Data Model

Extend `TenderRecord` with a nullable `review_priority` field. Keep existing fields:

- `filter_status`;
- `match_confidence`;
- `category`;
- `include_reason`;
- `exclude_reason`;
- `matched_terms`.

This avoids replacing current logic and keeps backward compatibility with existing CRM-facing exports.

## Sorting

Actionable exports should be ordered by:

1. priority: hot, review, wide;
2. deadline ascending, with missing deadline after known deadlines;
3. price descending, with missing price after known prices;
4. discovered date descending.

This makes urgent and high-value records appear first without hiding weaker records.

## Testing

Add tests before implementation for:

- exact target tender becomes `hot`;
- missing deadline or price becomes `review`;
- missing region and price from B2B-Center becomes `wide`;
- medical or lab tender with broad office/consumable wording is excluded;
- generic расходные материалы without target context is excluded or demoted, not promoted to hot;
- Excel creates `Горячие`, `На проверку`, `Широкий хвост`, `Отсеянные`, and optional `Новые`;
- JSON includes `review_priority`.

## Out Of Scope

This change does not add new tender sources, login automation, or API token handling. EIS and EAT token work remains the next integration step after the report quality layer is stable.

