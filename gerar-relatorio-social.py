#!/usr/bin/env python3
"""
gerar-relatorio-social.py
Cezinha de Madureira — relatório social pontual (não faz parte do cron automático).

Roda por workflow_dispatch manual. Gera relatorio-social-data.json com:
  1. Evolução de seguidores no período (inicial x final, % de crescimento)
  2. Views totais por post e por período
  3. Frequência média de posts (posts/semana)
  4. % de visualização (views/reach ÷ seguidores)
  5. Interações por tipo (curtidas, comentários, compartilhamentos, salvamentos)
  6. Ranking de posts (melhor e pior desempenho), com thumbnail embutida em base64

As URLs de thumbnail da Meta sao assinadas por sessao/IP: so funcionam a partir de
quem fez a chamada original na Graph API. Por isso baixamos e convertemos pra
base64 AQUI DENTRO do runner (mesmo contexto que gerou a URL), pro relatorio final
nao depender de rede nenhuma pra mostrar as fotos (funciona offline, direto no PDF).

Reaproveita o padrão de update-cezinha.py (mesmo token, mesma API).
"""
import os, json, sys, base64, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

API_VERSION = 'v23.0'
TOKEN       = os.environ.get('META_TOKEN_CEZINHA', '')
IG_ID       = '17841400472685855'

PERIODO_DESDE = '2026-06-18'  # inicio da campanha atual

BRT = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRT).strftime('%Y-%m-%d')

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(HERE, 'relatorio-social-data.json')
CEZINHA_FILE = os.path.join(HERE, 'cezinha-data.json')


def api_get(path, params):
    params = dict(params)
    params['access_token'] = TOKEN
    url = f'https://graph.facebook.com/{API_VERSION}/{path}?{urllib.parse.urlencode(params)}'
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def api_get_all(path, params, list_key='data', max_pages=25):
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


def fetch_todos_posts_periodo(desde):
    """Pagina TODOS os posts do IG e filtra pelo timestamp >= desde (YYYY-MM-DD)."""
    media = api_get_all(f'{IG_ID}/media', {
        'fields': ('id,caption,permalink,media_type,media_product_type,'
                   'thumbnail_url,media_url,timestamp,like_count,comments_count'),
        'limit': '50',
    }, max_pages=15)
    out = []
    for m in media:
        ts = (m.get('timestamp') or '')[:10]
        if ts and ts >= desde:
            out.append(m)
    return out


def fetch_insights_post(media_id, mpt):
    """Tenta o metric set mais completo primeiro, cai pro mais simples se a API recusar
    (Reels aceitam 'views'; foto/carrossel não tem 'views', só reach)."""
    metric_sets = (
        ['views,reach,shares,saved,total_interactions'] if mpt == 'REELS' else []
    ) + [
        'reach,shares,saved,total_interactions',
        'reach,shares,total_interactions',
        'reach,total_interactions',
        'reach',
    ]
    for mset in metric_sets:
        try:
            return _media_vals(api_get(f'{media_id}/insights', {'metric': mset}))
        except Exception:
            continue
    return {}


def montar_posts(desde):
    media = fetch_todos_posts_periodo(desde)
    posts = []
    for m in media:
        likes = int(m.get('like_count') or 0)
        comments = int(m.get('comments_count') or 0)
        mt, mpt = m.get('media_type'), m.get('media_product_type')
        vals = fetch_insights_post(m['id'], mpt)
        views  = int(vals['views'])  if vals.get('views')  is not None else None
        reach  = int(vals['reach'])  if vals.get('reach')  is not None else None
        shares = int(vals['shares']) if vals.get('shares') is not None else None
        saved  = int(vals['saved'])  if vals.get('saved')  is not None else None
        ti = vals.get('total_interactions')
        interacoes = int(ti) if ti is not None else likes + comments + (saved or 0) + (shares or 0)
        if m.get('thumbnail_url'):
            thumb = m['thumbnail_url']
        elif mt in ('IMAGE', 'CAROUSEL_ALBUM'):
            thumb = m.get('media_url')
        else:
            thumb = None
        tipo = ('Reel' if mpt == 'REELS' else 'Carrossel' if mt == 'CAROUSEL_ALBUM'
                else 'Vídeo' if mt == 'VIDEO' else 'Foto' if mt == 'IMAGE' else (mpt or mt))
        posts.append({
            'id': m.get('id'),
            'permalink': m.get('permalink'),
            'thumb': thumb,
            'data': (m.get('timestamp') or '')[:10],
            'tipo': tipo,
            'likes': likes,
            'comentarios': comments,
            'compartilhamentos': shares,
            'salvamentos': saved,
            'alcance': reach,
            'views': views,
            'interacoes': interacoes,
            'legenda': (m.get('caption') or '').replace('\n', ' ')[:160],
        })
    return posts


def fetch_image_b64(url, max_bytes=1_500_000):
    """Baixa a imagem e devolve como data URI base64. Precisa rodar no MESMO runner
    que gerou a URL assinada (Meta bloqueia acesso de outro IP/sessao)."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(max_bytes + 1)
            ctype = resp.headers.get('Content-Type', 'image/jpeg')
        if len(data) > max_bytes:
            print(f'[warn] thumb maior que {max_bytes} bytes, pulando: {url[:80]}', file=sys.stderr)
            return None
        b64 = base64.b64encode(data).decode('ascii')
        return f'data:{ctype};base64,{b64}'
    except Exception as e:
        print(f'[warn] download thumb falhou: {e}', file=sys.stderr)
        return None


def evolucao_seguidores(desde):
    """Le a serie ja coletada pelo cron (cezinha-data.json), filtra pro periodo."""
    try:
        with open(CEZINHA_FILE, encoding='utf-8') as f:
            d = json.load(f)
    except Exception as e:
        print(f'[warn] cezinha-data.json: {e}', file=sys.stderr)
        return None
    serie = [p for p in (d.get('seguidores') or {}).get('serie') or [] if p.get('data')]
    serie = [p for p in serie if p['data'] >= desde]
    serie.sort(key=lambda p: p['data'])
    if len(serie) < 2:
        return None
    ini, fim = serie[0], serie[-1]
    ig_ini = int(ini.get('ig') or 0)
    ig_fim = int(fim.get('ig') or 0)
    pct = round(((ig_fim - ig_ini) / ig_ini * 100), 2) if ig_ini > 0 else None
    return {
        'data_inicial': ini['data'], 'seguidores_inicial': ig_ini,
        'data_final': fim['data'], 'seguidores_final': ig_fim,
        'crescimento_abs': ig_fim - ig_ini,
        'crescimento_pct': pct,
        'serie': [{'data': p['data'], 'ig': int(p.get('ig') or 0)} for p in serie],
    }


def main():
    if not TOKEN:
        print('ERRO: META_TOKEN_CEZINHA não definido', file=sys.stderr)
        sys.exit(1)

    print(f'Gerando relatorio social desde {PERIODO_DESDE}...')

    seg = evolucao_seguidores(PERIODO_DESDE)
    print(f'  seguidores: {seg}' if seg else '  [warn] sem serie de seguidores suficiente')

    posts = montar_posts(PERIODO_DESDE)
    print(f'  {len(posts)} posts encontrados no periodo')

    # 3) frequencia media (posts/semana)
    dias_periodo = (datetime.strptime(HOJE, '%Y-%m-%d') - datetime.strptime(PERIODO_DESDE, '%Y-%m-%d')).days + 1
    semanas = max(dias_periodo / 7, 1)
    posts_por_semana = round(len(posts) / semanas, 2)

    # 2) views totais no periodo (soma views quando existe, senao usa alcance como proxy)
    views_total = sum((p['views'] if p['views'] is not None else (p['alcance'] or 0)) for p in posts)
    views_com_dado = sum(1 for p in posts if p['views'] is not None)
    alcance_com_dado = sum(1 for p in posts if p['alcance'] is not None)

    # 4) % de visualizacao em relacao ao total de seguidores (usa seguidores_final como base)
    seguidores_base = seg['seguidores_final'] if seg else None
    pct_visualizacao = None
    if seguidores_base and posts:
        media_views_post = views_total / len(posts)
        pct_visualizacao = round((media_views_post / seguidores_base * 100), 2)

    # 5) interacoes por tipo, somadas
    interacoes_tipo = {
        'curtidas':          sum(p['likes'] for p in posts),
        'comentarios':       sum(p['comentarios'] for p in posts),
        'compartilhamentos': sum((p['compartilhamentos'] or 0) for p in posts),
        'salvamentos':       sum((p['salvamentos'] or 0) for p in posts),
    }

    # 6) ranking (por interacoes)
    ranking = sorted(posts, key=lambda p: p['interacoes'], reverse=True)
    melhores = ranking[:5]
    piores = ranking[-5:][::-1] if len(ranking) >= 5 else []

    # baixa e embute em base64 as thumbs SO desses 10 posts (evita baixar as 38)
    print('  baixando thumbnails do ranking (base64)...')
    for p in melhores + piores:
        p['thumb_b64'] = fetch_image_b64(p.get('thumb'))

    output = {
        'gerado_em': datetime.now(BRT).isoformat(timespec='seconds'),
        'periodo': {'desde': PERIODO_DESDE, 'ate': HOJE, 'dias': dias_periodo},
        'evolucao_seguidores': seg,
        'visualizacao': {
            'views_totais_periodo': views_total,
            'posts_com_views': views_com_dado,
            'posts_com_alcance': alcance_com_dado,
            'media_views_por_post': round(views_total / len(posts), 1) if posts else 0,
            'pct_visualizacao_vs_seguidores': pct_visualizacao,
        },
        'frequencia': {
            'total_posts': len(posts),
            'semanas_periodo': round(semanas, 2),
            'posts_por_semana': posts_por_semana,
        },
        'interacoes_por_tipo': interacoes_tipo,
        'ranking_melhores': melhores,
        'ranking_piores': piores,
        'todos_posts': posts,
    }

    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f'\nOK relatorio-social-data.json gerado! {len(posts)} posts, {views_total} views totais.')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'ERRO: {e}', file=sys.stderr)
        sys.exit(1)
