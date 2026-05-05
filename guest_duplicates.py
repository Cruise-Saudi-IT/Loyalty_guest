import os
import datetime
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


def get_oracle_connection():
    """Connect to the Oracle database."""
    import oracledb
    return oracledb.connect(
        user="aryadmin",
        password="Cruisesaudi_2021",
        dsn="localhost:1521/AROYAREP",
    )


def serialize(obj):
    """Convert datetime objects for JSON."""
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


def fetch_duplicates():
    """
    Use SQL to find guests appearing in multiple packages.
    Match: same LOWER(email) + same last 9 digits of phone + same birthday,
    appearing in more than one PACKAGE_TYPE.
    """
    conn = get_oracle_connection()
    cursor = conn.cursor()

    # Step 1: Find the duplicate keys directly in the DB
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

    # Step 2: Fetch full guest rows only for the duplicate keys
    # Build bind variables for the IN clause
    email_list = list(set(k['email'] for k in dup_keys))

    # Use a temp approach: fetch guests matching any of the duplicate emails,
    # then filter in Python (much smaller set now)
    bind_vars = {}
    placeholders = []
    for i, email in enumerate(email_list):
        key = f"e{i}"
        bind_vars[key] = email
        placeholders.append(f":{key}")

    # Batch in groups of 999 (Oracle IN clause limit)
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
                   g.EMAIL, g.BIRTHDAY, g.RES_INIT_DATE, g.LAST_UPDATED_AT, g.HOUSEHOLD_ID,
                   g.CLIENT_ID, g.SEX, g.CITIZENSHIP, g.AGE, g.INTL_CODE, g.PHONE_NUMBER,
                   g.LANGUAGE_CODE, g.PASSPORT_NUMBER, g.PASSPORT_EXP_DATE,
                   g.SAIL_DATE_FROM, g.SAIL_DATE_TO, g.PACKAGE_TYPE, g.RES_STATUS,
                   g.SOURCE_CODE, g.OPERATOR_NAME, g.GUEST_CABIN, g.COMMISSION_EXCL_VAT,
                   g.COMMISSION_VAT, g.CABIN_FARE_NET, g.PORT_CHARGES_NET,
                   g.SERVICE_FEE_NET, g.AMENITY_FARE_NET, g.SHOREX_FARE_NET,
                   g.VAT_AMOUNT_NET, g.TOTAL_NET_AMOUNT, g.CURRENCY_CODE,
                   g.INVOICE_PACKAGE_CODE,
                   r.RES_TOTAL_NET
            FROM guest g
            LEFT JOIN (
                SELECT RES_ID, SUM(TOTAL_NET_AMOUNT) AS RES_TOTAL_NET
                FROM guest
                GROUP BY RES_ID
            ) r ON g.RES_ID = r.RES_ID
            WHERE LOWER(TRIM(g.EMAIL)) IN ({','.join(batch_placeholders)})
              AND g.PHONE_NUMBER IS NOT NULL
              AND g.BIRTHDAY IS NOT NULL
              AND LENGTH(TRIM(g.PHONE_NUMBER)) >= 9
        """
        cursor.execute(sql, batch_binds)
        columns = [col[0] for col in cursor.description]
        for row in cursor.fetchall():
            all_guests.append(dict(zip(columns, row)))

    cursor.close()
    conn.close()

    # Step 3: Group the fetched guests by the matching key
    from collections import defaultdict
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

    # Build result
    duplicates = []
    for key, group in groups.items():
        packages = sorted(set(g.get('PACKAGE_TYPE') for g in group))
        if len(packages) <= 1:
            continue

        # Calculate total spent by this guest across all their bookings
        guest_total_spent = sum(float(g.get('TOTAL_NET_AMOUNT') or 0) for g in group)

        cleaned_guests = [{k: serialize(v) for k, v in g.items()} for g in group]
        duplicates.append({
            'email': key[0],
            'phone_last9': key[1],
            'birthday': key[2],
            'guest_name': group[0].get('FULL_NAME', ''),
            'package_count': len(packages),
            'booking_count': len(group),
            'packages': packages,
            'guest_total_spent': round(guest_total_spent, 2),
            'guests': cleaned_guests,
        })

    duplicates.sort(key=lambda x: x['package_count'], reverse=True)
    return duplicates


def fetch_stats():
    """Get summary stats directly from SQL."""
    conn = get_oracle_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM guest")
    total_guests = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT PACKAGE_TYPE) FROM guest")
    total_packages = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return total_guests, total_packages


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('guest_duplicates.html')


@app.route('/api/duplicates')
def api_duplicates():
    """Return duplicate guests as JSON."""
    try:
        duplicates = fetch_duplicates()
        return jsonify({
            'duplicate_groups': len(duplicates),
            'duplicates': duplicates,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats')
def api_stats():
    """Return summary statistics."""
    try:
        total_guests, total_packages = fetch_stats()
        duplicates = fetch_duplicates()

        guests_in_duplicates = sum(d['booking_count'] for d in duplicates)
        total_revenue_dup = 0
        for dup in duplicates:
            for g in dup['guests']:
                val = g.get('TOTAL_NET_AMOUNT')
                if val:
                    total_revenue_dup += float(val)

        return jsonify({
            'total_guests': total_guests,
            'total_packages': total_packages,
            'duplicate_groups': len(duplicates),
            'guests_in_duplicates': guests_in_duplicates,
            'revenue_from_duplicates': round(total_revenue_dup, 2),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5001)
