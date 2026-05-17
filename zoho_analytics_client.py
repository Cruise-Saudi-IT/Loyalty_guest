"""
Zoho Analytics Bulk Export client.

Reuses the OAuth access-token cache from zoho_client (same refresh token, same
scope grant — we just call a different API host).
"""
import os
import json
import time
from typing import List

import requests

from zoho_client import _get_access_token


ZOHO_ANALYTICS_API = os.getenv('ZOHO_ANALYTICS_API_DOMAIN', 'https://analyticsapi.zoho.sa')


class ZohoAnalyticsError(RuntimeError):
    pass


def _headers() -> dict:
    org_id = os.getenv('ZOHO_ANALYTICS_ORG_ID')
    if not org_id:
        raise ZohoAnalyticsError('ZOHO_ANALYTICS_ORG_ID is not set')
    return {
        'Authorization': f'Zoho-oauthtoken {_get_access_token()}',
        'ZANALYTICS-ORGID': org_id,
    }


def export_view_json(workspace_id: str, view_id: str,
                     poll_interval: float = 2.0,
                     max_polls: int = 60) -> List[dict]:
    """
    Triggers a Zoho Analytics bulk export for the given saved view and returns
    parsed rows. The response format is JSON; rows live under the top-level
    "data" key in the downloaded payload.
    """
    base = f'{ZOHO_ANALYTICS_API}/restapi/v2/bulk/workspaces/{workspace_id}'

    init = requests.get(
        f'{base}/views/{view_id}/data',
        headers=_headers(),
        params={'CONFIG': json.dumps({'responseFormat': 'json'})},
        timeout=30,
    )
    if not init.ok:
        raise ZohoAnalyticsError(f'export init failed: {init.status_code} {init.text}')
    job_id = (init.json().get('data') or {}).get('jobId')
    if not job_id:
        raise ZohoAnalyticsError(f'no jobId in response: {init.text}')

    download_url = None
    for _ in range(max_polls):
        poll = requests.get(
            f'{base}/exportjobs/{job_id}',
            headers=_headers(),
            params={'CONFIG': '{}'},
            timeout=20,
        )
        if not poll.ok:
            raise ZohoAnalyticsError(f'poll failed: {poll.status_code} {poll.text}')
        info = poll.json().get('data') or {}
        state = info.get('jobStatus')
        if state == 'JOB COMPLETED':
            download_url = info.get('downloadUrl')
            break
        if state == 'JOB FAILED':
            raise ZohoAnalyticsError(f'export job failed: {info}')
        time.sleep(poll_interval)
    if download_url is None:
        raise ZohoAnalyticsError(f'export job did not complete within {max_polls * poll_interval}s')

    download = requests.get(download_url, headers=_headers(), timeout=60)
    if not download.ok:
        raise ZohoAnalyticsError(f'download failed: {download.status_code} {download.text}')
    payload = download.json()
    return payload.get('data') or []


def fetch_nps_index() -> dict:
    """
    Returns {client_id_str: nps_label_str} where label is one of
    'Promoter' | 'Passive' | 'Detractor'. Empty dict if any required config is
    missing or the call fails (caller decides whether to skip enrichment).
    """
    ws  = os.getenv('ZOHO_ANALYTICS_WORKSPACE_ID')
    vid = os.getenv('ZOHO_ANALYTICS_NPS_VIEW_ID')
    if not ws or not vid:
        return {}

    rows = export_view_json(ws, vid)

    index = {}
    for r in rows:
        cid = str(r.get('Client ID') or '').strip()
        raw = (r.get('NPS Label') or '').strip()
        if not cid or not raw:
            continue
        lower = raw.lower()
        if lower.startswith('promot'):
            label = 'Promoter'
        elif lower.startswith('detract'):
            label = 'Detractor'
        elif lower.startswith('passiv'):
            label = 'Passive'
        else:
            continue
        index[cid] = label
    return index
