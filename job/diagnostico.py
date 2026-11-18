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

    for pid in ("190367426370", "190365039726"):
        log(f"════ PACOTE {pid}")
        mostra("pacote", f"{API}/packs/{pid}", headers, 900)

    log("════ ÚLTIMOS PEDIDOS")
    st, b = cru(f"{API}/orders/search?seller={user_id}&sort=date_desc&limit=12", headers)
    log(f"busca de pedidos → HTTP {st}")
    if not isinstance(b, dict):
        return
    for o in b.get("results", []):
        ship = (o.get("shipping") or {}).get("id")
        print(f"   PEDIDO {o.get('id')} pack={o.get('pack_id')} total={o.get('total_amount')} status={o.get('status')} ship={ship}", flush=True)
        for it in o.get("order_items", []):
            print(f"     item={it.get('item', {}).get('id')} sku={it.get('item', {}).get('seller_sku')} qtd={it.get('quantity')} "
                  f"unit={it.get('unit_price')} full={it.get('full_unit_price')} sale_fee={it.get('sale_fee')} tipo={it.get('listing_type_id')}", flush=True)
        if ship:
            st2, c = cru(f"{API}/shipments/{ship}/costs", headers)
            snd = (c.get("senders") or [{}])[0] if isinstance(c, dict) else {}
            print(f"     frete HTTP {st2} senders={json.dumps(c.get('senders') if isinstance(c, dict) else c)[:300]} gross={c.get('gross_amount') if isinstance(c, dict) else ''}", flush=True)
    log("Diagnóstico concluído.")


if __name__ == "__main__":
    main()
