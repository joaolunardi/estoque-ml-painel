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

    for oid in ("190367426370", "190365039726"):
        log(f"════ PEDIDO {oid}")
        st, o = mostra("pedido", f"{API}/orders/{oid}", headers, 2200)
        if not isinstance(o, dict):
            continue
        ship = (o.get("shipping") or {}).get("id")
        print(f"   shipping_id={ship}", flush=True)
        for it in o.get("order_items", []):
            print(f"   item={it.get('item', {}).get('id')} sku={it.get('item', {}).get('seller_sku')} qtd={it.get('quantity')} "
                  f"unit_price={it.get('unit_price')} full_unit_price={it.get('full_unit_price')} sale_fee={it.get('sale_fee')} "
                  f"listing_type={it.get('listing_type_id')}", flush=True)
        if ship:
            mostra("custos do envio (shipments/costs)", f"{API}/shipments/{ship}/costs", headers, 1800)
            mostra("envio (shipments)", f"{API}/shipments/{ship}", headers, 1200)
    log("Diagnóstico concluído.")


if __name__ == "__main__":
    main()
