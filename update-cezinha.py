#!/usr/bin/env python3
"""
Cezinha de Madureira — captura diária Meta (campanhas + crescimento de seguidores).

Gera/atualiza cezinha-data.json:
  - seguidores.serie : 1 ponto por dia (FB + IG) -> gráfico de crescimento de hoje pra frente.
  - campanhas[].totais: acumulado desde o lançamento (date_preset=maximum, reach deduplicado).
  - campanhas[].serie : série diária (time_increment=1) por campanha.

Token: variável de ambiente META_TOKEN_CEZINHA (user token long-lived, sem expiração).
Usa só stdlib (urllib) — sem dependências externas. Idempotente: roda 2x no mesmo dia
faz upsert pela data, não duplica. Falha de uma chamada não derruba o resto (mantém o que já existe).
"""

import os, json, sys, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

API_VERSION = 'v23.0'
TOKEN       = os.environ.get('META_TOKEN_CEZINHA', '')

FB_PAGE_ID = '1401978510018003'
IG_ID      = '17841400472685855'
AD_ACCOUNT = 'act_1395564544098811'

BRT = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRT).strftime('%Y-%m-%d')

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, 'cezinha-data.json')

CAMPAIGNS = [
    {'id': '52562360720227', 'nome': 'Engajamento',             'objetivo': 'OUTCOME_ENGAGEMENT', 'objetivo_label': 'Engajamento',              'desde': '2026-06-17'},
    {'id': '52562338465227', 'nome': 'Reconhecimento',          'objetivo': 'OUTCOME_AWARENESS',  'objetivo_label': 'Reconhecimento de marca', 'desde': '2026-06-17'},
    {'id': '52562600820027', 'nome': 'Reconhecimento Bragança', 'objetivo': 'OUTCOME_AWARENESS',  'objetivo_label': 'Reconhecimento de marca', 'desde': '2026-06-18'},
    {'id': '52562318873627', 'nome': 'Tráfego para o Instagram e Facebook', 'objetivo': 'OUTCOME_TRAFFIC', 'objetivo_label': 'Tráfego (Instagram e Facebook)', 'desde': '2026-06-17'},
]

INSIGHT_FIELDS = ','.join([
    'impressions', 'reach', 'frequency', 'spend', 'cpm', 'cpc', 'ctr',
    'clicks', 'inline_link_clicks', 'actions', 'cost_per_action_type',
    'video_play_actions', 'video_thruplay_watched_actions',
    'estimated_ad_recallers', 'estimated_ad_recall_rate',
])


def api_get(path, params):
    params = dict(params)
    params['access_token'] = TOKEN
    url = f'https://graph.facebook.com/{API_VERSION}/{path}?{urllib.parse.urlencode(params)}'
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def _action(arr, action_type):
    for a in (arr or []):
        if a.get('action_type') == action_type:
            return float(a.get('value', 0) or 0)
    return 0.0


def parse_row(row):
    """Extrai as métricas de uma linha de insights (totais ou diário)."""
    actions = row.get('actions') or []
    cpa     = row.get('cost_per_action_type') or []
    link_clicks = _action(actions, 'link_click') or float(row.get('inline_link_clicks', 0) or 0)
    return {
        'impressions':   int(float(row.get('impressions', 0) or 0)),
        'reach':         int(float(row.get('reach', 0) or 0)),
        'frequency':     round(float(row.get('frequency', 0) or 0), 4),
        'spend':         round(float(row.get('spend', 0) or 0), 2),
        'cpm':           round(float(row.get('cpm', 0) or 0), 4),
        'cpc':           round(float(row.get('cpc', 0) or 0), 4),
        'ctr':           round(float(row.get('ctr', 0) or 0), 4),
        'clicks':        int(float(row.get('clicks', 0) or 0)),
        'engajamentos':  int(_action(actions, 'post_engagement')),
        'reacoes':       int(_action(actions, 'post_reaction')),
        'comentarios':   int(_action(actions, 'comment')),
        'salvamentos':   int(_action(actions, 'onsite_conversion.post_save')),
        'compartilhamentos': int(_action(actions, 'post')),
        'link_clicks':   int(link_clicks),
        'video_plays':   int(_action(row.get('video_play_actions'), 'video_view')),
        'video_views':   int(_action(actions, 'video_view')),
        'thruplays':     int(_action(row.get('video_thruplay_watched_actions'), 'video_view')),
        'custo_engajamento': round(_action(cpa, 'post_engagement'), 4),
        'custo_link_click':  round(_action(cpa, 'link_click'), 4),
        'ad_recallers':      int(float(row.get('estimated_ad_recallers', 0) or 0)),
        'ad_recall_rate':    round(float(row.get('estimated_ad_recall_rate', 0) or 0), 6),
    }


def fetch_totais(camp_id):
    data = api_get(f'{camp_id}/insights', {'fields': INSIGHT_FIELDS, 'date_preset': 'maximum'})
    rows = data.get('data') or []
    return parse_row(rows[0]) if rows else None


def fetch_serie(camp_id, desde):
    # série diária COMPLETA (todos os campos por dia), pra tela poder somar qualquer período
    data = api_get(f'{camp_id}/insights', {
        'fields': INSIGHT_FIELDS,
        'time_range': json.dumps({'since': desde, 'until': HOJE}),
        'time_increment': '1', 'limit': '500',
    })
    serie = []
    for row in (data.get('data') or []):
        rec = parse_row(row)
        rec['data'] = row.get('date_start')
        serie.append(rec)
    return serie


def fetch_followers():
    fb = ig = ig_posts = ig_follows = None
    try:
        d = api_get(FB_PAGE_ID, {'fields': 'fan_count,followers_count'})
        fb = int(d.get('followers_count') or d.get('fan_count') or 0)
    except Exception as e:
        print(f'[warn] FB followers: {e}', file=sys.stderr)
    try:
        d = api_get(IG_ID, {'fields': 'followers_count,follows_count,media_count'})
        ig = int(d.get('followers_count') or 0)
        ig_posts = int(d.get('media_count') or 0)
        ig_follows = int(d.get('follows_count') or 0)
    except Exception as e:
        print(f'[warn] IG followers: {e}', file=sys.stderr)
    return fb, ig, ig_posts, ig_follows


def upsert_by_date(serie, ponto, campo='data'):
    """Substitui o ponto da mesma data ou adiciona; mantém ordenado por data."""
    serie = [p for p in (serie or []) if p.get(campo) != ponto.get(campo)]
    serie.append(ponto)
    serie.sort(key=lambda p: p.get(campo) or '')
    return serie


def main():
    if not TOKEN:
        print('ERRO: META_TOKEN_CEZINHA não definido', file=sys.stderr)
        sys.exit(1)

    # carrega o que já existe (preserva estrutura/histórico)
    try:
        with open(DATA_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {'cliente': 'Cezinha de Madureira',
                'fonte': 'Meta Marketing API + Graph API',
                'contas': {'ad_account': AD_ACCOUNT, 'fb_page_id': FB_PAGE_ID,
                           'ig_id': IG_ID, 'ig_username': 'cezinhademadureira'},
                'seguidores': {'serie': []}, 'campanhas': []}

    # ── seguidores ──
    fb, ig, ig_posts, ig_follows = fetch_followers()
    seg = data.setdefault('seguidores', {'serie': []})
    if fb is not None:
        seg['fb_atual'] = fb
    if ig is not None:
        seg['ig_atual'] = ig
    if ig_posts is not None:
        seg['ig_posts'] = ig_posts
    if ig_follows is not None:
        seg['ig_follows'] = ig_follows
    if fb is not None or ig is not None:
        ponto = {'data': HOJE,
                 'fb': fb if fb is not None else seg.get('fb_atual'),
                 'ig': ig if ig is not None else seg.get('ig_atual')}
        seg['serie'] = upsert_by_date(seg.get('serie'), ponto)

    # ── campanhas ──
    by_id = {c.get('id'): c for c in data.get('campanhas', [])}
    novas = []
    for cfg in CAMPAIGNS:
        c = by_id.get(cfg['id'], {})
        c.update({k: cfg[k] for k in ('id', 'nome', 'objetivo', 'objetivo_label', 'desde')})
        try:
            tot = fetch_totais(cfg['id'])
            if tot:
                c['totais'] = tot
        except Exception as e:
            print(f"[warn] totais {cfg['nome']}: {e}", file=sys.stderr)
        try:
            serie = fetch_serie(cfg['id'], cfg['desde'])
            if serie:
                c['serie'] = serie
        except Exception as e:
            print(f"[warn] serie {cfg['nome']}: {e}", file=sys.stderr)
        novas.append(c)
    data['campanhas'] = novas

    data['atualizado_em'] = datetime.now(BRT).isoformat(timespec='seconds')

    # escrita atômica
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)
    print(f'OK {HOJE} | FB={seg.get("fb_atual")} IG={seg.get("ig_atual")} | campanhas={len(novas)}')


if __name__ == '__main__':
    main()
