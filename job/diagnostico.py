"""
Diagnóstico (só leitura): como o Mercado Livre expõe tarifa de venda e custo de frete
por anúncio, para calcular a margem com valores reais. Não altera nada nem imprime tokens.
"""
import json
import os

import requests

from pipeline import log, ml_get, renovar_token_ml

API = "https://api.mercadolibre.com"


def cru(url, headers):
    try:
        r = requests.get(url, headers=headers, timeout=30)
        try:
            corpo = r.json()
        except Exception:
            corpo = r.text[:300]
        return r.status_code, corpo
    except Exception as e:
        return 0, str(e)


def mostra(titulo, url, headers, limite=1500):
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

    r = ml_get(f"{API}/users/{user_id}/items/search?status=active&limit=60&offset=0", headers)
    ids = r.get("results", [])
    itens = []
    for i in range(0, len(ids), 20):
        for it in ml_get(f"{API}/items?ids={','.join(ids[i:i + 20])}", headers):
            if isinstance(it, dict) and it.get("code") == 200:
                itens.append(it["body"])
    full = [i for i in itens if i.get("shipping", {}).get("logistic_type") == "fulfillment"][:2]
    outros = [i for i in itens if i.get("shipping", {}).get("logistic_type") != "fulfillment"][:2]
    for it in full + outros:
        iid = it["id"]
        sh = it.get("shipping", {})
        log(f"════ {iid} · {it['title'][:60]}")
        print(f"   price={it.get('price')} cat={it.get('category_id')} tipo={it.get('listing_type_id')} "
              f"logistica={sh.get('logistic_type')} modo={sh.get('mode')} free={sh.get('free_shipping')} dims={sh.get('dimensions')}", flush=True)
        mostra("tarifa de venda (listing_prices)",
               f"{API}/sites/MLB/listing_prices?price={it['price']}&listing_type_id={it.get('listing_type_id')}&category_id={it.get('category_id')}",
               headers)
        mostra("frete (shipping_options CEP SP)", f"{API}/items/{iid}/shipping_options?zip_code=01310100", headers)
        mostra("frete do vendedor (users free)", f"{API}/users/{user_id}/shipping_options/free?item_id={iid}&zip_code=01310100", headers)
        mostra("frete (sites shipping_options/free)",
               f"{API}/sites/MLB/shipping_options/free?dimensions=10x10x10,500&item_price={it['price']}&listing_type_id={it.get('listing_type_id')}&mode=me2&condition=new&logistic_type={sh.get('logistic_type')}&verbose=true",
               headers)
    log("Diagnóstico concluído.")


if __name__ == "__main__":
    main()
