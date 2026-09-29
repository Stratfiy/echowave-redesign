# finance/costing-2026-10

Decibyl costing audit, prepared 29 September 2026. Read-only: nothing outside this folder was changed.

| File | What it is |
|---|---|
| `COSTING_REPORT.md` | The report for the founder: summary, unit costs, cost drivers, plan and voice margins, billing-code mismatches, recommendations, open questions, assumptions. |
| `cost_model.xlsx` | The model. Sheets: Vendors, Unit prices, Measured usage, Unit costs, Fixed costs, Plan margins, Voice margins, Sensitivity. Inputs live only on Unit prices and Measured usage; every other cell is a formula. |
| `build_cost_model.py` | Rebuilds the workbook from the labelled inputs (`python finance/costing-2026-10/build_cost_model.py`, needs openpyxl). |
| `step1_inventory.md` | Every paid dependency found in the code, with file:line and env-var names. |
| `step2_billing_map.md` | How each activity is charged and costed today, with file:line; the source for the billing-code mismatch section. |
| `step4_vendor_prices.md` | Official vendor prices as read on 29 September 2026, with URLs; items that could not be verified are listed. |
| `raw/` | Our own workspace's usage exports (60-day spend, 30-day calls, 200 runs, review, daily). Download tokens and phone numbers redacted. |

No secrets are stored here; keys are referred to by environment-variable name only.
