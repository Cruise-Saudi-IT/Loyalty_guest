# Aroya Repeat Guests Insights — KPI Reference

This document explains every number on the dashboard: what it measures, exactly how it is calculated, and which data field it comes from. Share this with management so everyone reads the same numbers the same way.

## Core definitions

| Term | Meaning |
|---|---|
| **Booking record** | One row in the Oracle `guest` table. Each row = one guest on one specific sailing. A single person who took 3 cruises with us appears as 3 rows. |
| **Unique guest** | A distinct person, identified by the combination of `(email, last 9 digits of phone, birthday)`. Used to merge multiple booking records for the same person. |
| **Repeat guest** | A unique guest with **two or more distinct cruise packages** booked. A guest who took the same package twice does *not* count — there has to be a different package. |
| **Cruise Saudi staff** | A guest whose first + last name matches an entry in `static/Book1.txt` (152 employees). Staff are excluded from every metric on the *Repeat Guests* tab and the *Dashboard* tab; they appear only on the *Cruise Saudi Staff* tab. |
| **Repeat-guest revenue** | Sum of `TOTAL_NET_AMOUNT` across all booking records belonging to repeat guests. Source-currency values, no FX conversion. |

---

## Tab 1 — Repeat Guests

| KPI | What it shows | Calculation | Field used |
|---|---|---|---|
| **Total Guests** | Number of distinct people in our database. | Count of unique `(email, phone-last-9, birthday)` combinations from the `guest` table. | `EMAIL`, `PHONE_NUMBER`, `BIRTHDAY` |
| **Cruise Packages** | Number of different cruise packages we offer. | `SELECT COUNT(DISTINCT PACKAGE_TYPE) FROM guest` | `PACKAGE_TYPE` |
| **Repeat Guests** | Number of unique non-staff people who have booked more than one cruise package. | Count of `(email, phone, birthday)` groups with `COUNT(DISTINCT PACKAGE_TYPE) > 1`, after removing matches from `Book1.txt`. | `EMAIL`, `PHONE_NUMBER`, `BIRTHDAY`, `PACKAGE_TYPE`, `FIRST_NAME`, `LAST_NAME`, `FULL_NAME` |
| **Repeat Bookings** | Total booking records made by those repeat guests. | Sum of `COUNT(*)` per repeat-guest group. | `guest` table rows |
| **Repeat-Guest Revenue** | Total revenue from those bookings. | Sum of `TOTAL_NET_AMOUNT` over all repeat-guest bookings. | `TOTAL_NET_AMOUNT` |

---

## Tab 2 — Dashboard

> All numbers below reflect **non-staff repeat guests only**, and respect the package filter at the top of the tab.

### KPI cards

| KPI | What it shows | Calculation |
|---|---|---|
| **Avg Lifetime Value** | Average total spend per repeat guest across all their bookings to date. | `Repeat-Guest Revenue ÷ Repeat Guests` |
| **Repeat Rate** | Share of our guest base that has come back for a second different cruise package. | `Repeat Guests ÷ Total Guests × 100%` |
| **Avg Cruises per Guest** | Average number of bookings (cruises taken) per repeat guest. | `Repeat Bookings ÷ Repeat Guests` |
| **Top Nationality** | Citizenship most frequently seen across repeat-guest bookings. Blank/`Unknown` values are skipped. | Mode of `CITIZENSHIP` over all repeat-guest booking records. |
| **Engaged Travel Agents** | Number of distinct travel **agencies** (from `seaware.agency`) that have at least one repeat-guest reservation. | Count of distinct `agency_name` (joined via `res_header.agency_id`) across repeat-guest bookings. |

### Charts

| Chart | What it shows | Calculation |
|---|---|---|
| **Repeat Bookings by Package** | How many repeat-guest bookings each package has. | For each `PACKAGE_TYPE`, count of repeat-guest booking records. |
| **Revenue Share by Package** (doughnut) | Which packages drive the most repeat-guest revenue. Top 5 packages are shown individually; the remainder are grouped as "Others". | Sum of `TOTAL_NET_AMOUNT` per `PACKAGE_TYPE`. |
| **Revenue by Cabin Category** | Which cabin categories generate the most repeat-guest revenue. | Each booking's `GUEST_CABIN` is matched to a category via `static/ssssss.csv` (1,839 cabins). Revenue summed per category. Bookings whose cabin is missing from the file are excluded. |
| **Top 10 Guest Nationalities** | The 10 most common citizenships among repeat-guest bookings. | Count of bookings per `CITIZENSHIP`, blank/`Unknown` excluded, top 10. |
| **Repeat Bookings by Sail Month** | Seasonality of repeat-guest sailings. | Parse `SAIL_DATE_FROM`, group by year-month, count of bookings per month, sorted chronologically. |
| **Top 10 Travel Agencies (by Reservation)** | Which travel agencies drive the most repeat-guest reservations. Each reservation is counted once even if multiple guests are on it. | Join `guest.RES_ID` → `seaware.res_header.RES_ID` → `seaware.agency.AGENCY_ID` to get `agency_name`. Group by `RES_ID` (deduped), count reservations per agency, top 10. |
| **Average Revenue per Booking (by Package)** | Which packages have the highest average ticket price among repeat guests. | For each `PACKAGE_TYPE`: `Σ TOTAL_NET_AMOUNT ÷ booking count`. |
| **Most Common Package Pairs** | Which two packages are most often booked by the same guest. | For each repeat-guest group, generate every unordered pair of packages they booked. Count occurrences across all repeat guests, top 8. |
| **Guest Languages** | Languages spoken by repeat guests. | Count of bookings per `LANGUAGE_CODE`, top 8. |
| **Repeat Guest Age Distribution** | Age profile of repeat guests. Each guest counted once (using their first booking's `AGE`). | Bucketed: `<18`, `18-24`, `25-34`, `35-44`, `45-54`, `55-64`, `65+`. |
| **Revenue Heatmap — Cabin Category × Sail Month** | Two-dimensional view of which cabin categories generate revenue in which sail months. Surfaces seasonality per cabin tier (e.g. premium suites peaking in winter). Top 10 cabin categories shown. | For each repeat-guest booking, look up cabin category from `static/ssssss.csv`, parse `SAIL_DATE_FROM` to year-month, sum `TOTAL_NET_AMOUNT` into each (category, month) cell. Color intensity scales linearly from 0 to the max cell value. |

---

## Tab 3 — Cruise Saudi Staff

Same structure as Tab 1, restricted to people whose first + last name appears in `static/Book1.txt`.

| KPI | What it shows | Calculation |
|---|---|---|
| **Staff Members** | Number of unique staff who are repeat guests. | Count of repeat-guest groups whose name matches the staff list. |
| **Staff Bookings** | Total bookings made by those staff members. | Sum of `booking_count` per matched group. |
| **Staff Revenue** | Total revenue from staff bookings. | Sum of `TOTAL_NET_AMOUNT` over staff bookings. |

---

## How a "repeat guest" is identified (technical)

In the database query (in `guest_duplicates.py`), repeat guests are detected as follows:

```sql
SELECT LOWER(TRIM(EMAIL))                     AS norm_email,
       SUBSTR(TRIM(PHONE_NUMBER), -9)         AS phone9,
       TRUNC(BIRTHDAY)                        AS bday,
       COUNT(DISTINCT PACKAGE_TYPE)           AS pkg_count,
       COUNT(*)                               AS booking_count
FROM   guest
WHERE  EMAIL IS NOT NULL
  AND  PHONE_NUMBER IS NOT NULL
  AND  BIRTHDAY IS NOT NULL
  AND  LENGTH(TRIM(PHONE_NUMBER)) >= 9
GROUP  BY LOWER(TRIM(EMAIL)), SUBSTR(TRIM(PHONE_NUMBER), -9), TRUNC(BIRTHDAY)
HAVING COUNT(DISTINCT PACKAGE_TYPE) > 1
```

Three things to note:
1. We require **email + phone + birthday** all to match before treating two booking records as the same person. Records missing any of those are excluded from this analysis.
2. `pkg_count > 1` means we only flag a person as repeat if they have at least **two different package types**. Same-package re-bookings are ignored.
3. `booking_count` is the total `guest`-table rows for the person — that's what we treat as their "number of cruises".

---

## Data refresh

The dashboard reads from an in-memory cache populated by querying the Oracle database. The cache is refreshed:

1. Once on server startup (background thread, takes ~30s).
2. Daily at 12:30 PM via a scheduler job.
3. On demand by `POST /api/refresh-cache`.

The "Cache: …" timestamp in the page header is the time of the last successful refresh.
