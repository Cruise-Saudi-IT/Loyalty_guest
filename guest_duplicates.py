import datetime
import threading
from collections import defaultdict

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template

load_dotenv()

from zoho_enrichment import apply_zoho_enrichment
from zoho_analytics_client import ZohoAnalyticsError, fetch_nps_index
from zoho_desk_client import ZohoDeskError, fetch_desk_index

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache = {
    'duplicates': [],
    'total_booking_records': 0,
    'total_unique_guests': 0,
    'total_packages': 0,
    'last_updated': None,
    'status': 'pending',   # pending | ok | error
    'error': None,
}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_oracle_connection():
    import oracledb
    return oracledb.connect(
        user="aryadmin",
        password="Cruisesaudi_2021",
        dsn="localhost:15210/AROYAREP",
    )


def serialize(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


# ---------------------------------------------------------------------------
# Core fetch (runs at 12:30 PM daily and once on startup)
# ---------------------------------------------------------------------------

def refresh_cache():
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Refreshing cache from database...")
    try:
        duplicates = _fetch_duplicates()
        total_booking_records, total_packages, total_unique_guests = _fetch_stats()

        with _cache_lock:
            _cache['duplicates'] = duplicates
            _cache['total_booking_records'] = total_booking_records
            _cache['total_unique_guests'] = total_unique_guests
            _cache['total_packages'] = total_packages
            _cache['last_updated'] = datetime.datetime.now().isoformat()
            _cache['status'] = 'ok'
            _cache['error'] = None

        print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Cache refreshed — "
              f"{len(duplicates)} repeat-guest groups, {total_unique_guests} unique guests, "
              f"{total_booking_records} booking records.")
    except Exception as e:
        with _cache_lock:
            _cache['status'] = 'error'
            _cache['error'] = str(e)
        print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Cache refresh failed: {e}")


def _fetch_duplicates():
    conn = get_oracle_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT LOWER(TRIM(EMAIL)) AS norm_email,
               SUBSTR(TRIM(PHONE_NUMBER), -9) AS phone9,
               TRUNC(BIRTHDAY) AS bday,
               COUNT(DISTINCT PACKAGE_TYPE) AS pkg_count,
               COUNT(*) AS booking_count
        FROM guest
        WHERE EMAIL IS NOT NULL
          AND PHONE_NUMBER IS NOT NULL
          AND BIRTHDAY IS NOT NULL
          AND LENGTH(TRIM(PHONE_NUMBER)) >= 9
          AND GUEST_SEQN = 1
        GROUP BY LOWER(TRIM(EMAIL)),
                 SUBSTR(TRIM(PHONE_NUMBER), -9),
                 TRUNC(BIRTHDAY)
        HAVING COUNT(DISTINCT PACKAGE_TYPE) > 1
        ORDER BY COUNT(DISTINCT PACKAGE_TYPE) DESC
    """)
    dup_keys = []
    for row in cursor.fetchall():
        dup_keys.append({
            'email': row[0],
            'phone9': row[1],
            'birthday': row[2],
            'pkg_count': row[3],
            'booking_count': row[4],
        })

    if not dup_keys:
        cursor.close()
        conn.close()
        return []

    email_list = list(set(k['email'] for k in dup_keys))

    # Currency conversion to SAR. SAR is pegged at 3.75 to USD; EUR/GBP are
    # illustrative — replace with finance-approved rates as needed.
    fx_g = "(CASE g.CURRENCY_CODE WHEN 'SAR' THEN 1 WHEN 'USD' THEN 3.75 WHEN 'EUR' THEN 4.10 WHEN 'GBP' THEN 4.75 ELSE 1 END)"
    fx_inner = "(CASE CURRENCY_CODE WHEN 'SAR' THEN 1 WHEN 'USD' THEN 3.75 WHEN 'EUR' THEN 4.10 WHEN 'GBP' THEN 4.75 ELSE 1 END)"

    all_guests = []
    for batch_start in range(0, len(email_list), 999):
        batch_emails = email_list[batch_start:batch_start + 999]
        batch_binds = {}
        batch_placeholders = []
        for i, email in enumerate(batch_emails):
            key = f"e{i}"
            batch_binds[key] = email
            batch_placeholders.append(f":{key}")

        sql = f"""
            SELECT g.FULL_NAME, g.FIRST_NAME, g.LAST_NAME, g.RES_ID, g.GROUP_ID, g.GUEST_ID,
                   g.GUEST_SEQN,
                   g.EMAIL, g.BIRTHDAY, g.RES_INIT_DATE, g.LAST_UPDATED_AT, g.HOUSEHOLD_ID,
                   g.CLIENT_ID, g.SEX, g.CITIZENSHIP, g.AGE, g.INTL_CODE, g.PHONE_NUMBER,
                   g.LANGUAGE_CODE, g.PASSPORT_NUMBER, g.PASSPORT_EXP_DATE,
                   g.SAIL_DATE_FROM, g.SAIL_DATE_TO, g.PACKAGE_TYPE, g.RES_STATUS,
                   g.SOURCE_CODE, g.OPERATOR_NAME, g.GUEST_CABIN,
                   g.COMMISSION_EXCL_VAT * {fx_g} AS COMMISSION_EXCL_VAT,
                   g.COMMISSION_VAT     * {fx_g} AS COMMISSION_VAT,
                   g.CABIN_FARE_NET     * {fx_g} AS CABIN_FARE_NET,
                   g.PORT_CHARGES_NET   * {fx_g} AS PORT_CHARGES_NET,
                   g.SERVICE_FEE_NET    * {fx_g} AS SERVICE_FEE_NET,
                   g.AMENITY_FARE_NET   * {fx_g} AS AMENITY_FARE_NET,
                   g.SHOREX_FARE_NET    * {fx_g} AS SHOREX_FARE_NET,
                   g.VAT_AMOUNT_NET     * {fx_g} AS VAT_AMOUNT_NET,
                   g.TOTAL_NET_AMOUNT   * {fx_g} AS TOTAL_NET_AMOUNT,
                   g.CURRENCY_CODE AS SOURCE_CURRENCY_CODE,
                   'SAR' AS CURRENCY_CODE,
                   g.INVOICE_PACKAGE_CODE,
                   r.RES_TOTAL_NET,
                   cc.CABIN_GUEST_COUNT,
                   cc.CABIN_TOTAL_REV,
                   a.agency_name AS AGENCY_NAME,
                   ai.STATE_CODE AS AGENCY_STATE_CODE,
                   ai.COUNTRY_CODE AS AGENCY_COUNTRY_CODE,
                   rh.RES_INIT_DATE AS RH_INIT_DATE,
                   ip.GEOG_AREA_CODE,
                   ip.COMMENTS
            FROM guest g
            LEFT JOIN (
                SELECT RES_ID, SUM(TOTAL_NET_AMOUNT * {fx_inner}) AS RES_TOTAL_NET
                FROM guest
                GROUP BY RES_ID
            ) r ON g.RES_ID = r.RES_ID
            LEFT JOIN (
                SELECT RES_ID, GUEST_CABIN,
                       COUNT(*) AS CABIN_GUEST_COUNT,
                       SUM(TOTAL_NET_AMOUNT * {fx_inner}) AS CABIN_TOTAL_REV
                FROM guest
                WHERE GUEST_CABIN IS NOT NULL
                GROUP BY RES_ID, GUEST_CABIN
            ) cc ON cc.RES_ID = g.RES_ID AND cc.GUEST_CABIN = g.GUEST_CABIN
            LEFT JOIN seaware.res_header rh ON g.RES_ID = rh.RES_ID
            LEFT JOIN (
                SELECT AGENCY_ID, MAX(agency_name) AS agency_name
                FROM seaware.agency
                GROUP BY AGENCY_ID
            ) a ON a.AGENCY_ID = rh.AGENCY_ID
            LEFT JOIN (
                SELECT AGENCY_ID,
                       MAX(STATE_CODE) AS STATE_CODE,
                       MAX(COUNTRY_CODE) AS COUNTRY_CODE
                FROM agency_info
                GROUP BY AGENCY_ID
            ) ai ON ai.AGENCY_ID = rh.AGENCY_ID
            LEFT JOIN (
                SELECT PACKAGE_CODE,
                       MAX(GEOG_AREA_CODE) AS GEOG_AREA_CODE,
                       MAX(COMMENTS) AS COMMENTS
                FROM interporting
                GROUP BY PACKAGE_CODE
            ) ip ON g.INVOICE_PACKAGE_CODE = ip.PACKAGE_CODE
            WHERE LOWER(TRIM(g.EMAIL)) IN ({','.join(batch_placeholders)})
              AND g.PHONE_NUMBER IS NOT NULL
              AND g.BIRTHDAY IS NOT NULL
              AND LENGTH(TRIM(g.PHONE_NUMBER)) >= 9
              AND g.GUEST_SEQN = 1
        """
        cursor.execute(sql, batch_binds)
        columns = [col[0] for col in cursor.description]
        for row in cursor.fetchall():
            all_guests.append(dict(zip(columns, row)))

    cursor.close()
    conn.close()

    dup_key_set = set()
    for k in dup_keys:
        bday_str = k['birthday'].strftime('%Y-%m-%d') if hasattr(k['birthday'], 'strftime') else str(k['birthday'])
        dup_key_set.add((k['email'], k['phone9'], bday_str))

    groups = defaultdict(list)
    for g in all_guests:
        email = (g.get('EMAIL') or '').strip().lower()
        phone = (str(g.get('PHONE_NUMBER') or '')).strip()
        phone9 = phone[-9:] if len(phone) >= 9 else phone
        bday = g.get('BIRTHDAY')
        bday_str = bday.strftime('%Y-%m-%d') if hasattr(bday, 'strftime') else str(bday)

        key = (email, phone9, bday_str)
        if key in dup_key_set:
            groups[key].append(g)

    surviving_rows = []
    for group in groups.values():
        if len({g.get('PACKAGE_TYPE') for g in group}) > 1:
            surviving_rows.extend(group)
    zoho_stats = apply_zoho_enrichment(surviving_rows)
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Zoho enrichment: {zoho_stats}")

    try:
        nps_index = fetch_nps_index()
        nps_hits = 0
        for g in surviving_rows:
            cid = str(g.get('CLIENT_ID') or '').strip()
            label = nps_index.get(cid)
            if label:
                g['_NPS_LABEL'] = label
                nps_hits += 1
        print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] NPS attached: {nps_hits} row(s), "
              f"{len(nps_index)} clients in NPS view")
    except Exception as e:
        # NPS is optional enrichment — any error (auth, SSL, timeout, etc.)
        # should NOT kill the cache refresh.
        print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] NPS fetch skipped: {type(e).__name__}: {e}")

    try:
        desk_index = fetch_desk_index()
        desk_hits = 0
        for g in surviving_rows:
            email = (g.get('EMAIL') or '').strip().lower()
            entry = desk_index.get(email)
            if entry:
                desk_hits += 1
                g['_HAS_OPEN_TICKET'] = entry['has_open_ticket']
                g['_HAS_PAST_COMPLAINT'] = entry['has_past_complaint']
                g['_NO_COMPLAINT_FLAG'] = entry['no_complaint_flag']
            else:
                # No Desk record at all for this email -> no complaint history.
                g['_HAS_OPEN_TICKET'] = False
                g['_HAS_PAST_COMPLAINT'] = False
                g['_NO_COMPLAINT_FLAG'] = True
        print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Desk attached: {desk_hits} "
              f"email match(es), {len(desk_index)} emails in Desk index")
    except Exception as e:
        # Desk is optional enrichment — Zoho's SSL was dropping connections
        # mid-fetch and a narrow ZohoDeskError catch let the exception escape,
        # killing the whole refresh. Any failure here should just skip enrichment.
        print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Desk fetch skipped: {type(e).__name__}: {e}")
        # Default flags so downstream logic still works.
        for g in surviving_rows:
            g.setdefault('_HAS_OPEN_TICKET', False)
            g.setdefault('_HAS_PAST_COMPLAINT', False)
            g.setdefault('_NO_COMPLAINT_FLAG', True)

    duplicates = []
    excluded_open_ticket_groups = 0
    for key, group in groups.items():
        packages = sorted(set(g.get('PACKAGE_TYPE') for g in group))
        if len(packages) <= 1:
            continue

        # Hard stop: any guest in the group with an open Desk ticket
        # excludes the entire group from the repeat-guest list.
        if any(g.get('_HAS_OPEN_TICKET') for g in group):
            excluded_open_ticket_groups += 1
            continue

        seen_res_totals = {}
        for gr in group:
            rid = gr.get('RES_ID')
            if rid is not None and rid not in seen_res_totals:
                seen_res_totals[rid] = float(gr.get('RES_TOTAL_NET') or 0)
        guest_total_spent = sum(seen_res_totals.values())

        seen_cabin_rev = {}
        for gr in group:
            ckey = (gr.get('RES_ID'), gr.get('GUEST_CABIN'))
            if all(x is not None for x in ckey) and ckey not in seen_cabin_rev:
                seen_cabin_rev[ckey] = float(gr.get('CABIN_TOTAL_REV') or 0)
        guest_total_cabin_rev = sum(seen_cabin_rev.values())

        guest_total_net = sum(float(g.get('TOTAL_NET_AMOUNT') or 0) for g in group)
        cleaned_guests = [{k: serialize(v) for k, v in g.items()} for g in group]

        # Prefer an enriched row's values for the group header so the card
        # reflects Zoho data (when available) rather than the pre-enrichment
        # group key. Falls back to the group key when no row was enriched.
        header_src = next((g for g in group if g.get('_ZOHO_ENRICHED')), group[0])
        header_phone = (str(header_src.get('PHONE_NUMBER') or '')).strip()
        header_phone9 = ''.join(c for c in header_phone if c.isdigit())[-9:]
        header_bday = header_src.get('BIRTHDAY')
        header_bday_str = header_bday.strftime('%Y-%m-%d') if hasattr(header_bday, 'strftime') else (str(header_bday)[:10] if header_bday else '')

        group_nps = next((g.get('_NPS_LABEL') for g in group if g.get('_NPS_LABEL')), None)
        group_has_past_complaint = any(g.get('_HAS_PAST_COMPLAINT') for g in group)
        # 'no complaint flag' holds true only if every guest in the group is clean.
        group_no_complaint_flag = all(g.get('_NO_COMPLAINT_FLAG', True) for g in group)

        duplicates.append({
            'email': (header_src.get('EMAIL') or '').strip() or key[0],
            'phone_last9': header_phone9 or key[1],
            'birthday': header_bday_str or key[2],
            'guest_name': group[0].get('FULL_NAME', ''),
            'package_count': len(packages),
            'booking_count': len(group),
            'packages': packages,
            'guest_total_spent': round(guest_total_spent, 2),
            'guest_total_cabin_rev': round(guest_total_cabin_rev, 2),
            'guest_total_net': round(guest_total_net, 2),
            'nps_label': group_nps,
            'has_past_complaint': group_has_past_complaint,
            'no_complaint_flag': group_no_complaint_flag,
            'guests': cleaned_guests,
        })

    if excluded_open_ticket_groups:
        print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Hard-stop: excluded "
              f"{excluded_open_ticket_groups} group(s) with open Desk tickets")
    duplicates.sort(key=lambda x: x['package_count'], reverse=True)
    return duplicates


def _fetch_stats():
    conn = get_oracle_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM guest")
    total_booking_records = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT PACKAGE_TYPE) FROM guest")
    total_packages = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT LOWER(TRIM(EMAIL)),
                            SUBSTR(TRIM(PHONE_NUMBER), -9),
                            TRUNC(BIRTHDAY)
            FROM guest
            WHERE EMAIL IS NOT NULL
              AND PHONE_NUMBER IS NOT NULL
              AND BIRTHDAY IS NOT NULL
              AND LENGTH(TRIM(PHONE_NUMBER)) >= 9
              AND GUEST_SEQN = 1
        )
    """)
    total_unique_guests = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return total_booking_records, total_packages, total_unique_guests


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('guest_duplicates.html')


@app.route('/api/duplicates')
def api_duplicates():
    with _cache_lock:
        if _cache['status'] == 'error':
            return jsonify({'error': _cache['error']}), 500
        if _cache['status'] == 'pending':
            return jsonify({'error': 'Cache is still loading, please try again shortly.'}), 503
        return jsonify({
            'duplicate_groups': len(_cache['duplicates']),
            'duplicates': _cache['duplicates'],
            'last_updated': _cache['last_updated'],
        })


@app.route('/api/stats')
def api_stats():
    with _cache_lock:
        if _cache['status'] == 'error':
            return jsonify({'error': _cache['error']}), 500
        if _cache['status'] == 'pending':
            return jsonify({'error': 'Cache is still loading, please try again shortly.'}), 503

        duplicates = _cache['duplicates']
        bookings_by_repeat_guests = sum(d['booking_count'] for d in duplicates)
        total_revenue_dup = sum(
            float(g.get('TOTAL_NET_AMOUNT') or 0)
            for d in duplicates
            for g in d['guests']
        )
        return jsonify({
            'total_booking_records': _cache['total_booking_records'],
            'total_unique_guests': _cache['total_unique_guests'],
            'total_packages': _cache['total_packages'],
            'repeat_guest_count': len(duplicates),
            'bookings_by_repeat_guests': bookings_by_repeat_guests,
            'revenue_from_repeat_guests': round(total_revenue_dup, 2),
            'last_updated': _cache['last_updated'],
        })


@app.route('/api/cache-status')
def api_cache_status():
    with _cache_lock:
        return jsonify({
            'status': _cache['status'],
            'last_updated': _cache['last_updated'],
            'error': _cache['error'],
        })


@app.route('/api/refresh-cache', methods=['POST'])
def api_refresh_cache():
    with _cache_lock:
        if _cache['status'] == 'pending':
            return jsonify({'started': False, 'message': 'A refresh is already in progress.'}), 409
        _cache['status'] = 'pending'
        _cache['error'] = None
    threading.Thread(target=refresh_cache, daemon=True).start()
    return jsonify({'started': True})


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    # Load cache immediately on startup
    threading.Thread(target=refresh_cache, daemon=True).start()

    # Schedule daily refresh at 12:30 PM
    scheduler = BackgroundScheduler()
    scheduler.add_job(refresh_cache, 'cron', hour=12, minute=30)
    scheduler.start()

    app.run(debug=False, host='0.0.0.0', port=5001)
