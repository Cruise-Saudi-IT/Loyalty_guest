"""
Zoho Desk client. Builds a per-email index of ticket signals used by the loyalty
score and the hard-stop exclusion:

  no_complaint_flag : True when the guest has zero tickets with cf_complaint_category set
  has_past_complaint: True when at least one ticket has cf_complaint_category set
                      (used for the -30 'Unresolved complaint, active service case, or low NPS' rule)
  has_open_ticket   : True when at least one ticket has statusType = 'Open' (any kind, includes refunds)

The hard-stop exclusion fires on has_open_ticket. The custom-field detail per ticket
(needed for cf_complaint_category) is fetched per-ticket because /tickets does not
return cf in the list response.
"""
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from zoho_client import _get_access_token


DESK_API = os.getenv('ZOHO_DESK_API_DOMAIN', 'https://desk.zoho.sa') + '/api/v1'

# Page size for list endpoint; max allowed by Zoho is 100.
LIST_PAGE_SIZE = 100
# Maximum offset Zoho Desk allows on /tickets list pagination (older docs: 5000).
LIST_MAX_OFFSET = 9000
# Concurrency for per-ticket cf fetches.
WORKER_THREADS = 10


class ZohoDeskError(RuntimeError):
    pass


def _headers() -> dict:
    return {'Authorization': f'Zoho-oauthtoken {_get_access_token()}'}


def _list_page(offset: int) -> list:
    r = requests.get(
        f'{DESK_API}/tickets',
        headers=_headers(),
        params={'limit': LIST_PAGE_SIZE, 'from': offset, 'sortBy': '-createdTime'},
        timeout=30,
    )
    if r.status_code == 204:
        return []
    if not r.ok:
        raise ZohoDeskError(f'list tickets failed at offset={offset}: {r.status_code} {r.text[:200]}')
    return r.json().get('data') or []


def _fetch_one_cf(ticket_id: str) -> dict:
    r = requests.get(f'{DESK_API}/tickets/{ticket_id}', headers=_headers(), timeout=20)
    if not r.ok:
        return {}
    return r.json().get('cf') or {}


def fetch_desk_index() -> dict:
    """
    Returns {email_lower: {no_complaint_flag, has_past_complaint, has_open_ticket, total_tickets}}
    Falls back to {} if Desk credentials are missing or the API errors.
    """
    print(f'[Desk] enumerating tickets...', flush=True)

    # Phase 1: walk the list endpoint, collecting (id, email, statusType, statusValue).
    raw = []
    offset = 0
    while offset <= LIST_MAX_OFFSET:
        try:
            page = _list_page(offset)
        except ZohoDeskError as e:
            print(f'[Desk] {e}', flush=True)
            break
        if not page:
            break
        for t in page:
            raw.append({
                'id': t.get('id'),
                'email': (t.get('email') or '').strip().lower(),
                'statusType': t.get('statusType') or '',
                'status': t.get('status') or '',
            })
        offset += LIST_PAGE_SIZE
        if offset % 1000 == 0:
            print(f'[Desk]   listed {len(raw)} tickets so far...', flush=True)
    print(f'[Desk] list done: {len(raw)} tickets', flush=True)

    # Phase 2: fetch cf fields per ticket in parallel.
    print(f'[Desk] fetching cf fields with {WORKER_THREADS} threads...', flush=True)
    t0 = time.time()
    cf_by_id = {}
    with ThreadPoolExecutor(max_workers=WORKER_THREADS) as ex:
        futures = {ex.submit(_fetch_one_cf, t['id']): t['id'] for t in raw}
        done = 0
        for fut in as_completed(futures):
            tid = futures[fut]
            cf_by_id[tid] = fut.result() or {}
            done += 1
            if done % 500 == 0:
                print(f'[Desk]   cf fetched {done}/{len(raw)}  ({time.time()-t0:.1f}s)', flush=True)
    print(f'[Desk] cf fetch done in {time.time()-t0:.1f}s', flush=True)

    # Phase 3: aggregate per email.
    per_email = defaultdict(lambda: {
        'no_complaint_flag': True,
        'has_past_complaint': False,
        'has_open_ticket': False,
        'total_tickets': 0,
    })
    for t in raw:
        email = t['email']
        if not email:
            continue
        cf = cf_by_id.get(t['id']) or {}
        entry = per_email[email]
        entry['total_tickets'] += 1
        if t['statusType'].lower() == 'open':
            entry['has_open_ticket'] = True
        if cf.get('cf_complaint_category'):
            entry['has_past_complaint'] = True
            entry['no_complaint_flag'] = False

    print(f'[Desk] index ready: {len(per_email)} distinct emails', flush=True)
    return dict(per_email)
