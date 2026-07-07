#!/usr/bin/env python3
"""
Cezinha de Madureira — captura diária Meta (campanhas + crescimento de seguidores).

Gera/atualiza cezinha-data.json:
  - seguidores.serie     : 1 ponto por dia (FB + IG) -> gráfico de crescimento.
  - consolidado.totais   : métricas UNIFICADAS de todas as campanhas da conta (date_preset=maximum).
  - consolidado.serie    : série diária unificada (time_increment=1) da conta inteira.
  - consolidado.campanhas: quantas ativas e quais objetivos rodando (sem detalhar por nome).
  - publico              : demografia do IG (gênero/idade/cidade), alcance/views e top posts.

Token: variável de ambiente META_TOKEN_CEZINHA (user token long-lived, sem expiração).
Usa só stdlib (urllib) — sem dependências externas. Idempotente: roda 2x no mesmo dia
faz upsert pela data, não duplica. Falha de uma chamada não derruba o resto (mantém o que já existe).
"""

import os, json, re, sys, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

API_VERSION = 'v23.0'
TOKEN       = os.environ.get('META_TOKEN_CEZINHA', '')

FB_PAGE_ID = '1401978510018003'
IG_ID      = '17841400472685855'
AD_ACCOUNTS = ['act_3515790661909032', 'act_1395564544098811']  # atual + a 8811 (voltou)
CONS_SINCE  = '2026-06-01'  # operacao atual; evita misturar historico antigo da 8811 (desde 2023)

BRT = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRT).strftime('%Y-%m-%d')

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, 'cezinha-data.json')

OBJ_LABEL = {
    'OUTCOME_ENGAGEMENT':    'Engajamento',
    'OUTCOME_AWARENESS':     'Reconhecimento',
    'OUTCOME_TRAFFIC':       'Tráfego',
    'OUTCOME_LEADS':         'Leads',
    'OUTCOME_SALES':         'Vendas',
    'OUTCOME_APP_PROMOTION': 'Promoção de app',
    'OUTCOME_VIDEO_VIEWS':   'Views de vídeo',
}

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
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode('utf-8', 'replace')
        except Exception:
            pass
        raise RuntimeError(f'HTTP {e.code}: {body[:400]}') from None


def api_get_all(path, params, list_key='data', max_pages=25):
    """Como api_get, mas segue a paginacao (paging.next) ate acabar ou bater o limite de seguranca."""
    out = []
    d = api_get(path, params)
    out.extend(d.get(list_key) or [])
    next_url = (d.get('paging') or {}).get('next')
    pages = 1
    while next_url and pages < max_pages:
        try:
            with urllib.request.urlopen(next_url, timeout=30) as resp:
                d = json.loads(resp.read())
        except Exception as e:
            print(f'[warn] paginacao {path}: {e}', file=sys.stderr)
            break
        out.extend(d.get(list_key) or [])
        next_url = (d.get('paging') or {}).get('next')
        pages += 1
    return out


def _action(arr, action_type):
    for a in (arr or []):
        if a.get('action_type') == action_type:
            return float(a.get('value', 0) or 0)
    return 0.0


def _follows(arr):
    """Soma 'Seguidores no Instagram': a acao de follow do Meta (o nome do
    action_type varia, entao pega qualquer um que contenha 'follow')."""
    total = 0.0
    for a in (arr or []):
        if 'follow' in (a.get('action_type') or ''):
            total += float(a.get('value', 0) or 0)
    return total


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
        'seguidores':    int(_follows(actions)),
        'video_plays':   int(_action(row.get('video_play_actions'), 'video_view')),
        'video_views':   int(_action(actions, 'video_view')),
        'thruplays':     int(_action(row.get('video_thruplay_watched_actions'), 'video_view')),
        'custo_engajamento': round(_action(cpa, 'post_engagement'), 4),
        'custo_link_click':  round(_action(cpa, 'link_click'), 4),
        'ad_recallers':      int(float(row.get('estimated_ad_recallers', 0) or 0)),
        'ad_recall_rate':    round(float(row.get('estimated_ad_recall_rate', 0) or 0), 6),
    }


_AGG_BASE = ['impressions', 'reach', 'clicks', 'link_clicks', 'seguidores', 'engajamentos', 'reacoes',
             'comentarios', 'salvamentos', 'compartilhamentos', 'video_plays', 'video_views',
             'thruplays', 'ad_recallers']


def _agg_rows(rows):
    """Soma as métricas-base de varias contas/dias e recalcula as derivadas."""
    if not rows:
        return None
    o = {}
    for k in _AGG_BASE:
        o[k] = sum(int(r.get(k, 0) or 0) for r in rows)
    spend = round(sum(float(r.get('spend', 0) or 0) for r in rows), 2)
    o['spend'] = spend
    o['cpm'] = round(spend / o['impressions'] * 1000, 4) if o['impressions'] else 0
    o['cpc'] = round(spend / o['clicks'], 4) if o['clicks'] else 0
    o['ctr'] = round(o['clicks'] / o['impressions'] * 100, 4) if o['impressions'] else 0
    o['frequency'] = round(o['impressions'] / o['reach'], 4) if o['reach'] else 0
    o['custo_engajamento'] = round(spend / o['engajamentos'], 4) if o['engajamentos'] else 0
    o['custo_link_click'] = round(spend / o['link_clicks'], 4) if o['link_clicks'] else 0
    o['ad_recall_rate'] = round(o['ad_recallers'] / o['reach'], 6) if o['reach'] else 0
    return o


def _range():
    return json.dumps({'since': CONS_SINCE, 'until': HOJE})


def fetch_conta_totais():
    """Totais UNIFICADOS somando TODAS as contas de anuncio (desde CONS_SINCE)."""
    parts = []
    for acct in AD_ACCOUNTS:
        try:
            d = api_get(f'{acct}/insights', {'fields': INSIGHT_FIELDS, 'time_range': _range()})
            rows = d.get('data') or []
            if rows:
                parts.append(parse_row(rows[0]))
        except Exception as e:
            print(f'[warn] totais {acct}: {e}', file=sys.stderr)
    return _agg_rows(parts)


def fetch_conta_serie():
    """Serie diaria UNIFICADA: soma todas as contas por dia (desde CONS_SINCE)."""
    by_date = {}
    for acct in AD_ACCOUNTS:
        try:
            d = api_get(f'{acct}/insights', {
                'fields': INSIGHT_FIELDS, 'time_range': _range(),
                'time_increment': '1', 'limit': '500',
            })
            for row in (d.get('data') or []):
                by_date.setdefault(row.get('date_start'), []).append(parse_row(row))
        except Exception as e:
            print(f'[warn] serie {acct}: {e}', file=sys.stderr)
    serie = []
    for dt in sorted(k for k in by_date if k):
        rec = _agg_rows(by_date[dt])
        rec['data'] = dt
        serie.append(rec)
    return serie


def fetch_campanhas_meta():
    """Campanhas ativas e objetivos somando TODAS as contas."""
    ativas = 0
    total = 0
    objetivos = []
    for acct in AD_ACCOUNTS:
        try:
            d = api_get(f'{acct}/campaigns',
                        {'fields': 'id,name,objective,effective_status', 'limit': '200'})
            camps = d.get('data') or []
            total += len(camps)
            for c in camps:
                if c.get('effective_status') == 'ACTIVE':
                    ativas += 1
                    lbl = OBJ_LABEL.get(c.get('objective'), c.get('objective'))
                    if lbl and lbl not in objetivos:
                        objetivos.append(lbl)
        except Exception as e:
            print(f'[warn] campanhas {acct}: {e}', file=sys.stderr)
    return {'ativas': ativas, 'total': total, 'objetivos': objetivos}


TAG_RX  = re.compile(r'^\s*\[([^\]]+)\]')
DATE_RX = re.compile(r'^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}$')  # ex: 16/5/2017 -> nao e dobrada


def _tag_of(nome):
    """A 'dobrada' e sempre a 1a tag entre colchetes no nome do CONJUNTO DE ANUNCIOS,
    ex: '[SP][LM] [LEIS][SP][02.07.25]' -> 'SP'. Conjunto sem colchete no inicio, ou com uma
    data no lugar da tag (resto de nome antigo/teste), nao entra em nenhuma dobrada."""
    m = TAG_RX.match(nome or '')
    if not m:
        return None
    tag = m.group(1).strip()
    if not tag or DATE_RX.match(tag):
        return None
    return tag


DOBRADAS_ACCOUNTS = ['act_1395564544098811']  # so a conta com o padrao de nome "[TAG][LM] ..."
                                               # (a act_3515790661909032 e outra operacao, sem esse padrao)


def fetch_dobradas():
    """
    Agrupa metricas por DOBRADA (a 1a tag do conjunto de anuncios, ex: [SP][LM] [LEIS][SP][02.07.25]
    -> dobrada 'SP'), somando entre campanhas diferentes que usem a mesma tag. So considera
    conjuntos de anuncios ATIVOS agora (pausado nao entra, mesmo tendo gasto no periodo).
    Automatico: dobrada nova (tag nova) aparece sozinha na proxima rodada do robo.
    """
    # 1) mapeia id -> tag, so dos conjuntos ATIVOS agora (status nao vem no endpoint de insights)
    ativos, total_adsets = {}, {}
    active_tag_by_id = {}
    for acct in DOBRADAS_ACCOUNTS:
        try:
            adsets = api_get_all(f'{acct}/adsets', {'fields': 'id,name,effective_status', 'limit': '500'})
            for a in adsets:
                tag = _tag_of(a.get('name'))
                if not tag:
                    continue
                total_adsets[tag] = total_adsets.get(tag, 0) + 1
                if a.get('effective_status') == 'ACTIVE':
                    ativos[tag] = ativos.get(tag, 0) + 1
                    active_tag_by_id[a.get('id')] = tag
        except Exception as e:
            print(f'[warn] dobradas/adsets {acct}: {e}', file=sys.stderr)

    # 2) totais no periodo (desde CONS_SINCE), so dos conjuntos ativos
    totals_by_tag = {}
    for acct in DOBRADAS_ACCOUNTS:
        try:
            rows = api_get_all(f'{acct}/insights', {
                'level': 'adset', 'fields': INSIGHT_FIELDS + ',adset_id',
                'time_range': _range(), 'limit': '500',
            })
            for row in rows:
                tag = active_tag_by_id.get(row.get('adset_id'))
                if tag:
                    totals_by_tag.setdefault(tag, []).append(parse_row(row))
        except Exception as e:
            print(f'[warn] dobradas/totais {acct}: {e}', file=sys.stderr)

    # 3) serie diaria (pro filtro de periodo do dashboard), so dos conjuntos ativos
    daily_by_tag = {}
    for acct in DOBRADAS_ACCOUNTS:
        try:
            rows = api_get_all(f'{acct}/insights', {
                'level': 'adset', 'fields': INSIGHT_FIELDS + ',adset_id',
                'time_range': _range(), 'time_increment': '1', 'limit': '500',
            })
            for row in rows:
                tag = active_tag_by_id.get(row.get('adset_id'))
                dt = row.get('date_start')
                if tag and dt:
                    daily_by_tag.setdefault(tag, {}).setdefault(dt, []).append(parse_row(row))
        except Exception as e:
            print(f'[warn] dobradas/serie {acct}: {e}', file=sys.stderr)

    out = {}
    for tag in set(ativos):
        serie = []
        for dt in sorted(k for k in daily_by_tag.get(tag, {}) if k):
            rec = _agg_rows(daily_by_tag[tag][dt])
            rec['data'] = dt
            serie.append(rec)
        out[tag] = {
            'totais': _agg_rows(totals_by_tag.get(tag) or []) or {},
            'serie': serie,
            'conjuntos_ativos': ativos.get(tag, 0),
            'conjuntos_total': total_adsets.get(tag, 0),
        }
    return out


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


# ── público: demografia do IG + alcance/views + posts que mais bombam ──
IG_DEMO_TIMEFRAME = 'last_30_days'


def _demographics(metric, breakdown, timeframe=IG_DEMO_TIMEFRAME):
    """[{'k':dim,'v':valor}] desc. [] se falhar ou sem dado (ex: conta <100 seguidores)."""
    try:
        d = api_get(f'{IG_ID}/insights', {
            'metric': metric, 'period': 'lifetime', 'metric_type': 'total_value',
            'timeframe': timeframe, 'breakdown': breakdown,
        })
    except Exception as e:
        print(f'[warn] {metric}/{breakdown}: {e}', file=sys.stderr)
        return []
    out = []
    for m in (d.get('data') or []):
        for bd in ((m.get('total_value') or {}).get('breakdowns') or []):
            for res in (bd.get('results') or []):
                dv = res.get('dimension_values') or []
                out.append({'k': dv[0] if dv else '?', 'v': int(res.get('value') or 0)})
    out.sort(key=lambda x: x['v'], reverse=True)
    return out


def _account_total(metric):
    """Total dos últimos 30 dias (reach/views). None se falhar."""
    until = datetime.now(BRT)
    since = until - timedelta(days=28)   # janela <=30d inclusiva (limite da API de conta)
    try:
        d = api_get(f'{IG_ID}/insights', {
            'metric': metric, 'period': 'day', 'metric_type': 'total_value',
            'since': since.strftime('%Y-%m-%d'), 'until': until.strftime('%Y-%m-%d'),
        })
    except Exception as e:
        print(f'[warn] account {metric}: {e}', file=sys.stderr)
        return None
    for m in (d.get('data') or []):
        tv = m.get('total_value') or {}
        if tv.get('value') is not None:
            return int(tv['value'])
    return None


def _media_vals(ins):
    out = {}
    for x in (ins.get('data') or []):
        v = None
        if x.get('values'):
            v = x['values'][0].get('value')
        elif x.get('total_value'):
            v = x['total_value'].get('value')
        out[x.get('name')] = v
    return out


def fetch_top_posts(limit=12, keep=6):
    """Últimos posts do IG ordenados por interações; tenta reach/shares/saved por post."""
    try:
        media = api_get(f'{IG_ID}/media', {
            'fields': ('id,caption,permalink,media_type,media_product_type,'
                       'thumbnail_url,media_url,timestamp,like_count,comments_count'),
            'limit': str(limit),
        })
    except Exception as e:
        print(f'[warn] media list: {e}', file=sys.stderr)
        return []
    posts = []
    for m in (media.get('data') or []):
        likes = int(m.get('like_count') or 0)
        comments = int(m.get('comments_count') or 0)
        vals = {}
        for mset in ('reach,shares,saved,total_interactions', 'reach,shares,total_interactions',
                     'reach,total_interactions', 'reach'):
            try:
                vals = _media_vals(api_get(f"{m['id']}/insights", {'metric': mset}))
                break
            except Exception:
                continue
        reach  = int(vals['reach'])  if vals.get('reach')  is not None else None
        shares = int(vals['shares']) if vals.get('shares') is not None else None
        saved  = int(vals['saved'])  if vals.get('saved')  is not None else None
        ti = vals.get('total_interactions')
        interacoes = int(ti) if ti is not None else likes + comments + (saved or 0) + (shares or 0)
        mt, mpt = m.get('media_type'), m.get('media_product_type')
        if m.get('thumbnail_url'):
            thumb = m['thumbnail_url']
        elif mt in ('IMAGE', 'CAROUSEL_ALBUM'):
            thumb = m.get('media_url')
        else:
            thumb = None
        tipo = ('Reel' if mpt == 'REELS' else 'Carrossel' if mt == 'CAROUSEL_ALBUM'
                else 'Vídeo' if mt == 'VIDEO' else 'Foto' if mt == 'IMAGE' else (mpt or mt))
        posts.append({
            'permalink': m.get('permalink'),
            'thumb': thumb,
            'data': (m.get('timestamp') or '')[:10],
            'tipo': tipo,
            'likes': likes, 'comentarios': comments,
            'reach': reach, 'shares': shares, 'saved': saved,
            'interacoes': interacoes,
            'legenda': (m.get('caption') or '').replace('\n', ' ')[:120],
        })
    posts.sort(key=lambda p: p.get('interacoes') or 0, reverse=True)
    return posts[:keep]


def fetch_publico(prev):
    """Monta o bloco 'publico'. Preserva o anterior quando uma chamada falha/volta vazia."""
    gen     = _demographics('follower_demographics', 'gender')
    age     = _demographics('follower_demographics', 'age')
    city    = _demographics('follower_demographics', 'city')
    # engaged/reached só aceitam this_month/this_week (last_30_days foi descontinuado na v20+)
    eng_gen = _demographics('engaged_audience_demographics', 'gender', 'this_month')
    eng_cty = _demographics('engaged_audience_demographics', 'city', 'this_month')
    reach30 = _account_total('reach')
    views30 = _account_total('views')
    tops    = fetch_top_posts()
    prev = prev or {}
    pseg = prev.get('seguidores') or {}
    peng = prev.get('engajados') or {}
    return {
        'atualizado_em': datetime.now(BRT).isoformat(timespec='seconds'),
        'timeframe': IG_DEMO_TIMEFRAME,
        'seguidores': {
            'genero':  gen  or pseg.get('genero')  or [],
            'idade':   age  or pseg.get('idade')   or [],
            'cidades': (city[:12] if city else pseg.get('cidades')) or [],
        },
        'engajados': {
            'genero':  eng_gen or peng.get('genero')  or [],
            'cidades': (eng_cty[:12] if eng_cty else peng.get('cidades')) or [],
        },
        'alcance_30d': reach30 if reach30 is not None else prev.get('alcance_30d'),
        'views_30d':   views30 if views30 is not None else prev.get('views_30d'),
        'top_posts':   tops or prev.get('top_posts') or [],
    }


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
                'contas': {'ad_accounts': AD_ACCOUNTS, 'fb_page_id': FB_PAGE_ID,
                           'ig_id': IG_ID, 'ig_username': 'cezinhademadureira'},
                'seguidores': {'serie': []}, 'consolidado': {}}

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

    # ── consolidado (todas as campanhas da conta unificadas, sem separar por nome) ──
    cons = data.get('consolidado') or {}
    try:
        tot = fetch_conta_totais()
        if tot:
            cons['totais'] = tot
    except Exception as e:
        print(f'[warn] totais conta: {e}', file=sys.stderr)
    try:
        serie = fetch_conta_serie()
        if serie:
            cons['serie'] = serie
    except Exception as e:
        print(f'[warn] serie conta: {e}', file=sys.stderr)
    try:
        cons['campanhas'] = fetch_campanhas_meta()
    except Exception as e:
        print(f'[warn] campanhas meta: {e}', file=sys.stderr)
    data['consolidado'] = cons
    data.pop('campanhas', None)   # estrutura antiga (por nome de campanha) aposentada

    # ── dobradas (unificado por tag do conjunto de anuncios, ex: [SP], [BARUERI], [PIZZARIA]) ──
    try:
        data['dobradas'] = fetch_dobradas()
    except Exception as e:
        print(f'[warn] dobradas: {e}', file=sys.stderr)

    # ── público (demografia IG + alcance/views + posts) ──
    try:
        data['publico'] = fetch_publico(data.get('publico'))
    except Exception as e:
        print(f'[warn] publico: {e}', file=sys.stderr)

    data['atualizado_em'] = datetime.now(BRT).isoformat(timespec='seconds')

    # escrita atômica
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)
    print(f'OK {HOJE} | FB={seg.get("fb_atual")} IG={seg.get("ig_atual")} | '
          f'campanhas_ativas={(cons.get("campanhas") or {}).get("ativas")} | '
          f'dobradas={sorted((data.get("dobradas") or {}).keys())}')


if __name__ == '__main__':
    main()
