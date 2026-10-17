"""
Diagnóstico (só leitura): mostra como o Mercado Livre expõe preço, promoção e visitas
dos seus anúncios, pra a gente construir o "guardião de promoção" em cima de dados reais.
Não altera nada no Mercado Livre e não imprime tokens.
"""
import json
import os
from collections import Counter
from datetime import datetime, timedelta

import requests

from pipeline import log, ml_get, renovar_token_ml

API = "https://api.mercadolibre.com"


def cru(url, headers):
    """GET que devolve (status, corpo) sem esconder erros."""
    try:
        r = requests.get(url, headers=headers, timeout=30)
        try:
            corpo = r.json()
        except Exception:
            corpo = r.text[:300]
        return r.status_code, corpo
    except Exception as e:
        return 0, str(e)


def mostra(titulo, url, headers, limite=1400):
    st, corpo = cru(url, headers)
    txt = json.dumps(corpo, ensure_ascii=False)
    log(f"── {titulo} → HTTP {st}")
    print("   " + (txt if len(txt) <= limite else txt[:limite] + " …(cortado)"), flush=True)
    return st, corpo


def main():
    import firebase_admin
    from firebase_admin import credentials, firestore

    firebase_admin.initialize_app(credentials.Certificate(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])))
    db = firestore.client()
    user_id = os.environ["ML_USER_ID"]
    headers = {"Authorization": f"Bearer {renovar_token_ml(db, firestore)}"}

    # 1) quantos anúncios no total
    for status in ("active", "paused"):
        r = ml_get(f"{API}/users/{user_id}/items/search?status={status}&limit=1", headers)
        log(f"Anúncios {status}: {r.get('paging', {}).get('total')}")

    # 2) amostra de até 100 ativos: como preço/promoção aparecem em /items
    r = ml_get(f"{API}/users/{user_id}/items/search?status=active&limit=100&offset=0", headers)
    ids = r.get("results", [])
    itens = []
    for i in range(0, len(ids), 20):
        for it in ml_get(f"{API}/items?ids={','.join(ids[i:i + 20])}", headers):
            if it.get("code") == 200:
                itens.append(it["body"])
    log(f"Amostra analisada: {len(itens)} anúncios ativos")
    c = Counter()
    for p in itens:
        price, orig = p.get("price"), p.get("original_price")
        c["tem original_price"] += orig is not None
        c["original_price > price (desconto visível)"] += bool(orig and price and orig > price)
        c["sem original_price"] += orig is None
        c["tem deal_ids"] += bool(p.get("deal_ids"))
        c["FULL"] += p.get("shipping", {}).get("logistic_type") == "fulfillment"
        for tag in p.get("tags", []):
            c[f"tag:{tag}"] += 1
    for k, v in sorted(c.items()):
        print(f"   {k}: {v}", flush=True)

    # 3) detalhe de 6 anúncios: 3 com original_price e 3 sem
    com = [p for p in itens if p.get("original_price")][:3]
    sem = [p for p in itens if not p.get("original_price")][:3]
    for p in com + sem:
        iid = p["id"]
        log(f"════ {iid} · {p['title'][:60]}")
        print(f"   price={p.get('price')} original_price={p.get('original_price')} base_price={p.get('base_price')} "
              f"deal_ids={p.get('deal_ids')} listing={p.get('listing_type_id')} "
              f"FULL={p.get('shipping', {}).get('logistic_type') == 'fulfillment'}", flush=True)
        mostra("sale_price", f"{API}/items/{iid}/sale_price", headers, 700)
        mostra("promoções do anúncio", f"{API}/seller-promotions/items/{iid}?app_version=v2", headers)
        mostra("prices", f"{API}/items/{iid}/prices", headers, 900)
        mostra("visitas 30d", f"{API}/items/{iid}/visits/time_window?last=30&unit=day", headers, 500)

    # 4) visitas em lote (se funcionar, dá pra puxar tudo de uma vez)
    amostra = ",".join(p["id"] for p in itens[:5])
    hoje = datetime.now()
    de = (hoje - timedelta(days=30)).strftime("%Y-%m-%d")
    mostra("visitas em lote (total)", f"{API}/visits/items?ids={amostra}", headers, 500)
    mostra("visitas em lote (período)", f"{API}/items/visits?ids={amostra}&date_from={de}&date_to={hoje:%Y-%m-%d}", headers, 700)

    # 5) campanhas do vendedor
    mostra("campanhas do vendedor", f"{API}/seller-promotions/users/{user_id}?app_version=v2", headers, 1500)
    log("Diagnóstico concluído.")


if __name__ == "__main__":
    main()
