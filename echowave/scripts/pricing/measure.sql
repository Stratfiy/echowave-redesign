-- Pricing measurement pack
-- ========================
--
-- Everything in the pricing study is provisional because three internal
-- documents assume 850, 900 and 2,300 TTS characters per minute — a 2.7x
-- spread — and nobody has ever measured it. At $0.015 per 1k characters a
-- 2.7x error in that one number is the difference between a healthy margin and
-- a negative one, so it decides every price underneath it.
--
-- Run against production (or a replica) and paste the output back. Nothing
-- here writes. Nothing selects a transcript, a phone number, a recording URL
-- or a customer name — only counts, rates and durations.
--
--   psql "$DATABASE_URL" -f scripts/pricing/measure.sql > pricing-measurements.txt
--
-- A section returning no rows is itself a finding. Say "section 3 was empty"
-- rather than assuming the query is broken.

\pset pager off
\timing off

-- ---------------------------------------------------------------------------
-- 1. The number everything hangs on.
-- ---------------------------------------------------------------------------
-- Median rather than mean: one call where the agent read a policy document
-- aloud drags an average up by a third and quietly re-prices the product. p90
-- is here because worst-case margin is set by the tail, not the middle.
--
-- `units` on a TTS cost item is the raw character count — the rate is quoted
-- per 1,000, and money.py divides at costing time.

\echo '=== 1. TTS characters per billable minute, by provider and model ==='
SELECT
    i.provider,
    COALESCE(NULLIF(i.model, ''), '(provider default)')            AS model,
    COUNT(*)                                                       AS calls,
    ROUND(AVG(r.billable_seconds))                                 AS avg_call_seconds,
    ROUND(percentile_cont(0.5) WITHIN GROUP (
        ORDER BY i.units / (r.billable_seconds / 60.0))::numeric)   AS median_chars_per_min,
    ROUND(AVG(i.units / (r.billable_seconds / 60.0))::numeric)      AS mean_chars_per_min,
    ROUND(percentile_cont(0.9) WITHIN GROUP (
        ORDER BY i.units / (r.billable_seconds / 60.0))::numeric)   AS p90_chars_per_min
FROM call_cost_items i
JOIN workflow_runs r ON r.id = i.workflow_run_id
WHERE i.component = 'tts'
  AND i.units > 0
  AND r.billable_seconds >= 30      -- short calls make a per-minute rate noisy
  AND r.created_at >= now() - interval '90 days'
GROUP BY 1, 2
HAVING COUNT(*) >= 5
ORDER BY calls DESC;

\echo ''
\echo '=== 1b. Same, by language — Indic scripts are not one character per phoneme ==='
-- Devanagari and Telugu encode a syllable in fewer characters than the Latin
-- transliteration of the same sentence, so a per-1k-character rate is not
-- language-neutral. If these rows differ materially, the rate card should too.
SELECT
    COALESCE(r.language, '(unset)')                                AS language,
    COUNT(*)                                                       AS calls,
    ROUND(percentile_cont(0.5) WITHIN GROUP (
        ORDER BY i.units / (r.billable_seconds / 60.0))::numeric)   AS median_chars_per_min
FROM call_cost_items i
JOIN workflow_runs r ON r.id = i.workflow_run_id
WHERE i.component = 'tts'
  AND i.units > 0
  AND r.billable_seconds >= 30
  AND r.created_at >= now() - interval '90 days'
GROUP BY 1
HAVING COUNT(*) >= 5
ORDER BY calls DESC;

-- ---------------------------------------------------------------------------
-- 2. Rate rows that are missing, which bill zero and report healthy.
-- ---------------------------------------------------------------------------
-- The query that would have caught the ElevenLabs multilingual leak a year
-- earlier. A component with no rate row does not raise — it costs zero, marks
-- the run uncosted, and inflates the margin by exactly what it failed to
-- charge. Resolution is "exact model, else provider-wide": a combination is
-- missing only when neither exists.

\echo ''
\echo '=== 2. Provider/model/component seen on calls with NO live rate row ==='
SELECT
    i.provider,
    COALESCE(NULLIF(i.model, ''), '(provider default)') AS model,
    i.component,
    COUNT(*)          AS cost_items,
    SUM(i.units)      AS total_units,
    SUM(i.cost_paise) AS charged_paise
FROM call_cost_items i
WHERE i.created_at >= now() - interval '90 days'
  AND NOT EXISTS (
      SELECT 1 FROM provider_rates p
      WHERE p.provider  = i.provider
        AND p.component = i.component
        AND p.model IN (LOWER(COALESCE(i.model, '')), '')
        AND p.effective_to IS NULL
  )
GROUP BY 1, 2, 3
ORDER BY cost_items DESC;

\echo ''
\echo '=== 2b. The live rate card, as it stands ==='
SELECT
    provider,
    COALESCE(NULLIF(model, ''), '(provider default)') AS model,
    component,
    unit,
    rate_mpaise,
    effective_from::date AS since,
    LEFT(COALESCE(note, ''), 60) AS note
FROM provider_rates
WHERE effective_to IS NULL
ORDER BY provider, component, model;

-- ---------------------------------------------------------------------------
-- 3. What is falling through uncosted.
-- ---------------------------------------------------------------------------
-- An uncosted run is a call nobody was charged for. The engine records the
-- fact rather than guessing, so this is a straight read of revenue forgone.

\echo ''
\echo '=== 3. Runs with billable time but no cost items ==='
SELECT
    date_trunc('week', r.created_at)::date AS week,
    COUNT(*)                               AS runs,
    SUM(r.billable_seconds)                AS billable_seconds
FROM workflow_runs r
WHERE r.billable_seconds > 0
  AND r.created_at >= now() - interval '90 days'
  AND NOT EXISTS (SELECT 1 FROM call_cost_items i WHERE i.workflow_run_id = r.id)
GROUP BY 1
ORDER BY 1;

-- ---------------------------------------------------------------------------
-- 4. Whether we are billing on a real exchange rate.
-- ---------------------------------------------------------------------------
-- The platform fee is quoted in USD and settled in INR. With no rate on file
-- the code falls back to ₹96.00 (DEFAULT_USD_INR_PAISE in money.py). At a real
-- ₹104 that fallback under-bills every single call by 7.7%.

\echo ''
\echo '=== 4. USD/INR history — empty here means every call billed at the ₹96 fallback ==='
SELECT
    paise_per_usd,
    ROUND(paise_per_usd / 100.0, 2) AS rupees_per_usd,
    effective_from::date            AS since,
    effective_to::date              AS until,
    COALESCE(source, '(manual)')    AS source
FROM usd_inr_rate_history
ORDER BY effective_from DESC
LIMIT 20;

\echo ''
\echo '=== 4b. What rate calls were actually billed at ==='
SELECT
    COALESCE(usd_inr_paise_applied, 0)                  AS usd_inr_paise,
    ROUND(COALESCE(usd_inr_paise_applied, 0) / 100.0, 2) AS rupees_per_usd,
    COUNT(*)                                            AS runs
FROM workflow_runs
WHERE billable_seconds > 0
  AND created_at >= now() - interval '90 days'
GROUP BY 1
ORDER BY runs DESC;

-- ---------------------------------------------------------------------------
-- 5. Realised margin, per component.
-- ---------------------------------------------------------------------------
-- provider_cost_paise is what the vendor charges us; cost_paise is what we
-- charged for it. A component where these are equal is one we are reselling at
-- zero markup — deliberately or not.

\echo ''
\echo '=== 5. Charged vs provider cost, by component ==='
SELECT
    i.component,
    COUNT(*)                          AS cost_items,
    SUM(i.cost_paise)                 AS charged_paise,
    SUM(COALESCE(i.provider_cost_paise, 0)) AS provider_cost_paise,
    CASE WHEN SUM(i.cost_paise) > 0
         THEN ROUND(100.0 * (SUM(i.cost_paise) - SUM(COALESCE(i.provider_cost_paise, 0)))
                    / SUM(i.cost_paise), 1)
    END                               AS margin_pct
FROM call_cost_items i
WHERE i.created_at >= now() - interval '90 days'
GROUP BY 1
ORDER BY charged_paise DESC;

\echo ''
\echo '=== 5b. Any component we are reselling below cost ==='
-- A negative row here is a call that loses money every time it connects.
SELECT
    i.provider,
    COALESCE(NULLIF(i.model, ''), '(provider default)') AS model,
    i.component,
    COUNT(*)                                AS cost_items,
    SUM(i.cost_paise)                       AS charged_paise,
    SUM(COALESCE(i.provider_cost_paise, 0)) AS provider_cost_paise
FROM call_cost_items i
WHERE i.created_at >= now() - interval '90 days'
GROUP BY 1, 2, 3
HAVING SUM(COALESCE(i.provider_cost_paise, 0)) > SUM(i.cost_paise)
ORDER BY (SUM(COALESCE(i.provider_cost_paise, 0)) - SUM(i.cost_paise)) DESC;

-- ---------------------------------------------------------------------------
-- 6. Shape of the traffic — what the bundles have to be sized against.
-- ---------------------------------------------------------------------------
-- The ₹2,999 bundle grants ₹2,500 of balance. Whether that is a month's usage
-- or a fortnight's decides whether the bundle reads as generous or mean, and
-- there is no way to guess it without this.

\echo ''
\echo '=== 6. Monthly minutes and spend per organisation ==='
SELECT
    date_trunc('month', r.created_at)::date AS month,
    r.organization_id,
    COUNT(*)                                AS calls,
    ROUND(SUM(r.billable_seconds) / 60.0)   AS billable_minutes,
    ROUND(AVG(r.billable_seconds))          AS avg_call_seconds
FROM workflow_runs r
WHERE r.billable_seconds > 0
  AND r.created_at >= now() - interval '180 days'
GROUP BY 1, 2
ORDER BY 1 DESC, billable_minutes DESC;

\echo ''
\echo '=== 6b. Peak concurrency actually reached, by day ==='
-- The concurrency tier has to be priced against what people really use, not
-- what the limiter allows. Counts calls overlapping each hour as a proxy.
SELECT
    day,
    MAX(concurrent) AS peak_concurrent_calls
FROM (
    SELECT
        date_trunc('day', h)::date AS day,
        h,
        COUNT(*)                   AS concurrent
    FROM generate_series(
             now() - interval '30 days',
             now(),
             interval '1 hour'
         ) AS h
    JOIN workflow_runs r
      ON r.answered_at <= h
     AND COALESCE(r.ended_at, r.answered_at) >= h
    GROUP BY 1, 2
) hourly
GROUP BY day
ORDER BY day;
