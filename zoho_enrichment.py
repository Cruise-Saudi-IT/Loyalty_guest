"""
Enriches Oracle guest rows with email / phone / DOB from Zoho CRM.

Match strategy:
  1) Primary: Oracle CLIENT_ID matches Zoho Contacts.Client_ID AND
              First+Last name matches (case-insensitive, trimmed).
  2) Fallback: if Oracle CLIENT_ID is not in Zoho (the two systems don't
              share a stable customer id), match by First+Last name + DOB.
              Skipped when more than one Zoho contact shares that name+DOB.

Phone is preferred from Zoho Contacts.Phone, with Mobile as fallback.
Whichever value we write back is normalized to its last 9 digits so the
display stays consistent with Oracle's existing phone format.
"""
from typing import Iterable

from zoho_client import ZohoAPIError, ZohoAuthError, coql_paginated


def _normalize_name(value) -> str:
    if value is None:
        return ''
    s = value if isinstance(value, str) else str(value)
    return s.strip().lower()


def _normalize_id(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _normalize_dob(value) -> str:
    if value is None:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%Y-%m-%d')
    return str(value).strip()[:10]


def _phone_last9(value) -> str:
    if value is None:
        return ''
    digits = ''.join(c for c in str(value) if c.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits


def _chunked(items, size):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _quote_list(values):
    return ','.join(f"'{str(v).replace(chr(39), '')}'" for v in values)


def fetch_zoho_index(client_ids: Iterable[str]) -> dict:
    """
    Returns {client_id: {first_name, last_name, email, phone_last9, dob}}.
    """
    cleaned = {_normalize_id(c) for c in client_ids}
    cleaned.discard('')
    if not cleaned:
        return {}

    batches = list(_chunked(cleaned, 50))
    total_batches = len(batches)
    print(f"[Zoho] enrichment scope: {len(cleaned)} unique client_ids, "
          f"{total_batches} batches", flush=True)

    by_client = {}

    for i, batch in enumerate(batches, 1):
        in_clause = _quote_list(batch)
        query = (
            "SELECT id, First_Name, Last_Name, Client_ID, Email, Phone, Mobile, Date_of_Birth "
            f"FROM Contacts WHERE Client_ID in ({in_clause})"
        )
        count = 0
        for r in coql_paginated(query):
            count += 1
            cid = _normalize_id(r.get('Client_ID'))
            if not cid:
                continue
            phone_source = r.get('Phone') or r.get('Mobile') or ''
            email = r.get('Email')
            by_client[cid] = {
                'first_name': _normalize_name(r.get('First_Name')),
                'last_name': _normalize_name(r.get('Last_Name')),
                'email': email.strip() if isinstance(email, str) else '',
                'phone_last9': _phone_last9(phone_source),
                'dob': r.get('Date_of_Birth'),
            }
        print(f"[Zoho] Contacts batch {i}/{total_batches}: {count} record(s) fetched, "
              f"{len(by_client)} clients indexed", flush=True)

    return by_client


def _build_name_dob_index(by_client: dict) -> dict:
    """
    Returns {(first_name, last_name, dob_str): entry_or_None}.
    None marks an ambiguous (>1 contact) key — skipped at match time.
    """
    buckets = {}
    for entry in by_client.values():
        dob = _normalize_dob(entry['dob'])
        if not entry['first_name'] or not entry['last_name'] or not dob:
            continue
        key = (entry['first_name'], entry['last_name'], dob)
        if key in buckets:
            buckets[key] = None  # mark ambiguous
        else:
            buckets[key] = entry
    return buckets


def apply_zoho_enrichment(guests: list) -> dict:
    """
    Mutates each row in `guests` in place: overwrites EMAIL, PHONE_NUMBER (last 9 digits),
    and BIRTHDAY when a match is found. Adds `_ZOHO_ENRICHED = True` to enriched rows.

    Returns stats: {enriched, attempted, unique_zoho_clients, by_primary, by_fallback}.
    """
    if not guests:
        return {'enriched': 0, 'attempted': 0}

    client_ids = {_normalize_id(g.get('CLIENT_ID')) for g in guests}
    client_ids.discard('')
    if not client_ids:
        return {'enriched': 0, 'attempted': len(guests), 'reason': 'no client ids'}

    try:
        index = fetch_zoho_index(client_ids)
    except (ZohoAuthError, ZohoAPIError) as e:
        return {'enriched': 0, 'attempted': len(guests), 'reason': f'zoho error: {e}'}

    name_dob_index = _build_name_dob_index(index)

    enriched = by_primary = by_fallback = 0
    for g in guests:
        cid = _normalize_id(g.get('CLIENT_ID'))
        fn = _normalize_name(g.get('FIRST_NAME'))
        ln = _normalize_name(g.get('LAST_NAME'))

        entry = None
        path = None

        primary = index.get(cid)
        if primary and fn == primary['first_name'] and ln == primary['last_name']:
            entry = primary
            path = 'primary'
        else:
            dob = _normalize_dob(g.get('BIRTHDAY'))
            if fn and ln and dob:
                fallback = name_dob_index.get((fn, ln, dob))
                if fallback is not None:
                    entry = fallback
                    path = 'fallback'

        if entry is None:
            continue

        if entry['email']:
            g['EMAIL'] = entry['email']
        if entry['phone_last9']:
            g['PHONE_NUMBER'] = entry['phone_last9']
        if entry['dob']:
            g['BIRTHDAY'] = entry['dob']
        g['_ZOHO_ENRICHED'] = True
        enriched += 1
        if path == 'primary':
            by_primary += 1
        else:
            by_fallback += 1

    return {
        'enriched': enriched,
        'attempted': len(guests),
        'unique_zoho_clients': len(index),
        'by_primary': by_primary,
        'by_fallback': by_fallback,
    }
