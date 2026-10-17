"""
Guardião de promoção: acompanha o preço/promoção de TODOS os anúncios ativos.
Só leitura no Mercado Livre — nunca altera preço nem promoção.

Regra: anúncio "com promoção" = preço riscado (original_price) maior que o preço atual.
Alerta vermelho só para quem JÁ foi visto com promoção e perdeu; "acabando" para quem
tem promoção com data de fim próxima.
"""
from datetime import datetime, timezone

from pipeline import log, ml_get, _num

API = "https://api.mercadolibre.com"


def buscar_anuncios_ativos(headers, user_id):
    """Todos os anúncios ativos, com preço e preço riscado."""
    ids, offset = [], 0
    while True:
        r = ml_get(f"{API}/users/{user_id}/items/search?status=active&limit=100&offset={offset}", headers)
        lote = r.get("results", [])
        if not lote:
            break
        ids += lote
        offset += 100
        total = r.get("paging", {}).get("total", 0)
        if offset >= total:
            break
        if offset >= 1000:
            log("⚠️ Mais de 1000 anúncios ativos: a busca simples do ML corta em 1000.")
            break
    anuncios = {}
    for i in range(0, len(ids), 20):
        for it in ml_get(f"{API}/items?ids={','.join(ids[i:i + 20])}", headers):
            p = it.get("body") or {}
            if it.get("code") != 200 or not p.get("id"):
                continue
            sku = next((a.get("value_name") for a in p.get("attributes", []) if a.get("id") == "SELLER_SKU"), None) or "Sem SKU"
            fotos = p.get("pictures", [])
            anuncios[p["id"]] = {
                "id": p["id"], "titulo": p.get("title", ""), "sku": str(sku).strip(),
                "preco": p.get("price"), "precoDe": p.get("original_price"),
                "full": p.get("shipping", {}).get("logistic_type") == "fulfillment",
                "link": p.get("permalink", ""),
                "imagem": fotos[0].get("url", "") if fotos else (p.get("thumbnail") or ""),
            }
    log(f"{len(anuncios)} anúncios ativos lidos")
    return anuncios


def fim_da_promocao(headers, item_id):
    """Data de fim (ISO UTC) da promoção vigente, se o ML informar; senão None."""
    try:
        r = ml_get(f"{API}/items/{item_id}/prices", headers)
    except Exception:
        return None
    fins = []
    for pr in (r.get("prices") or []) if isinstance(r, dict) else []:
        if pr.get("type") == "promotion":
            fim = (pr.get("conditions") or {}).get("end_time")
            if fim:
                fins.append(fim)
    return min(fins) if fins else None


def _parse(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


def tem_promocao(a):
    de, preco = a.get("precoDe"), a.get("preco")
    return bool(de and preco and de > preco)


def avaliar(anuncios, fins, estado, agora, aviso_dias):
    """Classifica cada anúncio e devolve (linhas, novo_estado, resumo).
    estado: {id: {"tinhaPromo": bool, "ultimaPromo": iso, "perdeuEm": iso|None}}"""
    novo = {k: dict(v) for k, v in (estado or {}).items()}
    hoje = agora.isoformat()
    linhas = []
    for a in anuncios.values():
        st = novo.setdefault(a["id"], {"tinhaPromo": False, "ultimaPromo": None, "perdeuEm": None})
        promo = tem_promocao(a)
        fim, dias_fim = None, None
        if promo:
            st.update(tinhaPromo=True, ultimaPromo=hoje, perdeuEm=None)
            fim = fins.get(a["id"])
            dt = _parse(fim) if fim else None
            if dt:
                dias_fim = round((dt - agora).total_seconds() / 86400, 1)
            status = "acabando" if (dias_fim is not None and dias_fim <= aviso_dias) else "ok"
        elif st["tinhaPromo"]:
            if not st.get("perdeuEm"):
                st["perdeuEm"] = hoje
            status = "perdeu"
        else:
            status = "sem"
        desconto = round((1 - a["preco"] / a["precoDe"]) * 100) if promo else 0
        linhas.append({**a, "promo": promo, "descontoPct": desconto, "fimPromo": fim, "diasParaFim": dias_fim,
                       "status": status, "perdeuEm": st.get("perdeuEm") if status == "perdeu" else None,
                       "ultimaPromo": st.get("ultimaPromo")})
    ordem = {"perdeu": 0, "acabando": 1, "ok": 2, "sem": 3}
    linhas.sort(key=lambda l: (ordem[l["status"]],
                               l["diasParaFim"] if l["diasParaFim"] is not None else 9999, l["titulo"]))
    resumo = {s: sum(1 for l in linhas if l["status"] == s) for s in ordem}
    resumo["total"] = len(linhas)
    return linhas, novo, resumo


def executar_anuncios(db, firestore, headers, user_id, cfg):
    """Lê anúncios, atualiza memória de promoção e publica dashboard/anuncios."""
    agora = datetime.now(timezone.utc)
    anuncios = buscar_anuncios_ativos(headers, user_id)
    if not anuncios:
        raise RuntimeError("Nenhum anúncio ativo devolvido — mantive os dados anteriores.")
    fins = {i: fim_da_promocao(headers, i) for i, a in anuncios.items() if tem_promocao(a)}
    ref = db.collection("state").document("anuncios")
    snap = ref.get()
    estado = (snap.to_dict() or {}).get("itens", {}) if snap.exists else {}
    linhas, novo, resumo = avaliar(anuncios, fins, estado, agora, cfg.get("PROMO_AVISO_DIAS", 5))
    ref.set({"itens": novo, "atualizadoEm": firestore.SERVER_TIMESTAMP})
    db.collection("dashboard").document("anuncios").set(
        {"anuncios": linhas, "resumo": resumo, "atualizadoEm": firestore.SERVER_TIMESTAMP})
    log(f"Anúncios: {resumo['perdeu']} perderam promoção · {resumo['acabando']} acabando · "
        f"{resumo['ok']} ok · {resumo['sem']} sem promoção (nunca vista)")
    return resumo
