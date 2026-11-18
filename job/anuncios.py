"""
Guardião de promoção: acompanha o preço/promoção de TODOS os anúncios ativos.
Só leitura no Mercado Livre — nunca altera preço nem promoção.

Regra: anúncio "com promoção" = preço riscado (original_price) maior que o preço atual.
Alerta vermelho só para quem JÁ foi visto com promoção e perdeu; "acabando" para quem
tem promoção com data de fim próxima.
"""
from datetime import datetime, timezone

from pipeline import log, ml_get, buscar_itens

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
        for it in buscar_itens(headers, ids[i:i + 20]):
            p = it.get("body") or {}
            if it.get("code") != 200 or not p.get("id"):
                continue
            sku = next((a.get("value_name") for a in p.get("attributes", []) if a.get("id") == "SELLER_SKU"), None) or "Sem SKU"
            fotos = p.get("pictures", [])
            anuncios[p["id"]] = {
                "id": p["id"], "titulo": p.get("title", ""), "sku": str(sku).strip(),
                "preco": p.get("price"), "precoDe": p.get("original_price"),
                "full": p.get("shipping", {}).get("logistic_type") == "fulfillment",
                "categoria": p.get("category_id"), "tipo": p.get("listing_type_id"),
                "freteGratis": bool(p.get("shipping", {}).get("free_shipping")),
                "link": p.get("permalink", ""),
                "imagem": fotos[0].get("url", "") if fotos else (p.get("thumbnail") or ""),
            }
    log(f"{len(anuncios)} anúncios ativos lidos")
    return anuncios


def preco_vigente(headers, item_id):
    """(preço atual, preço 'de') pelo endpoint de preço de venda — o campo original_price
    da listagem não reflete a promoção em vários anúncios (catálogo / user product)."""
    try:
        r = ml_get(f"{API}/items/{item_id}/sale_price", headers)
    except Exception:
        return None, None
    if not isinstance(r, dict) or r.get("amount") is None:
        return None, None
    return r.get("amount"), r.get("regular_amount")


def aplicar_preco_vigente(headers, anuncios):
    """Corrige preco/precoDe de cada anúncio com o preço de venda vigente."""
    for a in anuncios.values():
        amount, regular = preco_vigente(headers, a["id"])
        if amount is None:
            continue
        a["preco"] = amount
        if regular and regular > amount:
            a["precoDe"] = regular
        elif not (a.get("precoDe") and a["precoDe"] > amount):
            a["precoDe"] = None


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


def tarifa_do_anuncio(headers, user_id, a):
    """Tarifas reais do ML para um anúncio: comissão (fração) e custo de frete do vendedor
    (quanto ele paga SE o frete for grátis). Devolve None se o ML não responder."""
    comissao = frete = None
    try:
        r = ml_get(f"{API}/sites/MLB/listing_prices?price={a['preco']}&listing_type_id={a.get('tipo')}"
                   f"&category_id={a.get('categoria')}", headers)
        det = (r[0] if isinstance(r, list) and r else r).get("sale_fee_details") if r else None
        if det and det.get("percentage_fee") is not None:
            comissao = round(det["percentage_fee"] / 100, 4)
    except Exception:
        pass
    try:
        r = ml_get(f"{API}/users/{user_id}/shipping_options/free?item_id={a['id']}&zip_code=01310100", headers)
        c = ((r or {}).get("coverage") or {}).get("all_country") or {}
        if c.get("list_cost") is not None:
            frete = round(float(c["list_cost"]), 2)
    except Exception:
        pass
    if comissao is None and frete is None:
        return None
    return {"comissao": comissao, "freteCusto": frete, "freteGratis": a.get("freteGratis", False)}


def atualizar_tarifas(db, firestore, headers, user_id, anuncios, horas=6):
    """Grava dashboard/tarifasml (no máximo a cada `horas` horas — são ~2 chamadas por anúncio)."""
    ref = db.collection("dashboard").document("tarifasml")
    snap = ref.get()
    if snap.exists:
        ts = (snap.to_dict() or {}).get("atualizadoEm")
        if ts and (datetime.now(timezone.utc) - ts).total_seconds() < horas * 3600:
            log("Tarifas do ML ainda recentes — pulando")
            return
    itens, falhas = {}, 0
    for a in anuncios.values():
        if not a.get("preco") or not a.get("categoria"):
            continue
        t = tarifa_do_anuncio(headers, user_id, a)
        if t:
            itens[a["id"]] = t
        else:
            falhas += 1
    if not itens:
        raise RuntimeError("Nenhuma tarifa devolvida pelo Mercado Livre")
    ref.set({"itens": itens, "atualizadoEm": firestore.SERVER_TIMESTAMP})
    log(f"Tarifas do ML: {len(itens)} anúncios ({falhas} sem resposta)")


def executar_anuncios(db, firestore, headers, user_id, cfg):
    """Lê anúncios, atualiza memória de promoção e publica dashboard/anuncios."""
    agora = datetime.now(timezone.utc)
    anuncios = buscar_anuncios_ativos(headers, user_id)
    if not anuncios:
        raise RuntimeError("Nenhum anúncio ativo devolvido — mantive os dados anteriores.")
    aplicar_preco_vigente(headers, anuncios)
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
    try:
        atualizar_tarifas(db, firestore, headers, user_id, anuncios)
    except Exception as e:
        log(f"⚠️ Tarifas do ML falharam: {e}")
    return resumo
