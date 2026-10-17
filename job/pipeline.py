"""
Job de atualização do Estoque ML (FULL + Compras).

Roda no GitHub Actions (ver .github/workflows/atualizar.yml):
  1. renova o token do Mercado Livre (o refresh token fica guardado no Firestore,
     porque o ML troca ele a cada renovação);
  2. busca os anúncios FULL e o histórico de vendas no Mercado Livre;
  3. busca o estoque de cada SKU no Tiny (depósito "Full" e galpão numa passada só);
  4. calcula os status dos 2 painéis;
  5. grava o resultado no Firestore (dashboard/latest) e o andamento em control/status.

Segredos vêm de variáveis de ambiente (GitHub Secrets):
  ML_APP_ID, ML_SECRET_KEY, ML_USER_ID, ML_REFRESH_TOKEN (só o inicial),
  TINY_TOKEN, FIREBASE_SERVICE_ACCOUNT (JSON da conta de serviço).
"""
import json
import os
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TZ_BR = timezone(timedelta(hours=-3))
TINY_MIN_INTERVALO = 1.0  # segundos mínimos entre 2 chamadas à API do Tiny
_tiny_ultima_chamada = [0.0]


def log(msg):
    print(f"[{datetime.now(TZ_BR).strftime('%H:%M:%S')}] {msg}", flush=True)


def carregar_config():
    with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def _num(v):
    """Converte o que o Tiny devolver (número ou texto) em número; vazio vira 0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    return int(f) if f == int(f) else f


# ─── Mercado Livre ───────────────────────────────────────────────────────────
def ml_get(url, headers, tentativas=4):
    ultimo = None
    for t in range(1, tentativas + 1):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code in (429, 500, 502, 503, 504):
                ultimo = f"HTTP {r.status_code}"
                time.sleep(2 * t)
                continue
            return r.json()
        except Exception as e:  # rede / JSON
            ultimo = str(e)
            time.sleep(2 * t)
    raise RuntimeError(f"Mercado Livre não respondeu ({ultimo}): {url[:90]}")


def renovar_token_ml(db, firestore):
    """O ML invalida o refresh token a cada uso e devolve um novo, então o último
    fica guardado em secrets/ml. Se ele falhar, tenta o inicial dos Secrets."""
    ref = db.collection("secrets").document("ml")
    snap = ref.get()
    candidatos = []
    if snap.exists and (snap.to_dict() or {}).get("refresh_token"):
        candidatos.append(("Firestore", snap.to_dict()["refresh_token"]))
    if os.environ.get("ML_REFRESH_TOKEN"):
        candidatos.append(("GitHub Secret", os.environ["ML_REFRESH_TOKEN"]))
    if not candidatos:
        raise RuntimeError("Nenhum refresh token do Mercado Livre disponível (configure ML_REFRESH_TOKEN).")

    erros = []
    for origem, refresh in candidatos:
        resp = requests.post("https://api.mercadolibre.com/oauth/token", data={
            "grant_type": "refresh_token",
            "client_id": os.environ["ML_APP_ID"],
            "client_secret": os.environ["ML_SECRET_KEY"],
            "refresh_token": refresh,
        }, timeout=30).json()
        if "access_token" in resp:
            # grava o novo refresh token ANTES de qualquer trabalho pesado
            ref.set({"refresh_token": resp["refresh_token"], "atualizadoEm": firestore.SERVER_TIMESTAMP})
            log(f"Token do ML renovado (usando o de: {origem})")
            return resp["access_token"]
        erros.append(f"{origem}: {resp.get('message') or resp.get('error') or resp}")
    raise RuntimeError("Não consegui renovar o token do Mercado Livre — " + " | ".join(erros))


def buscar_produtos_full(headers, user_id):
    produtos = {}

    def por_status(status):
        resultado = {}
        offset = 0
        while True:
            resp = ml_get(f"https://api.mercadolibre.com/users/{user_id}/items/search"
                          f"?status={status}&limit=100&offset={offset}", headers)
            ids = resp.get("results", [])
            if not ids:
                break
            for i in range(0, len(ids), 20):
                itens = ml_get(f"https://api.mercadolibre.com/items?ids={','.join(ids[i:i + 20])}", headers)
                for item in itens:
                    p = item.get("body", {})
                    if p.get("shipping", {}).get("logistic_type", "") != "fulfillment":
                        continue
                    sku = next((a.get("value_name") for a in p.get("attributes", []) if a.get("id") == "SELLER_SKU"), None) or "Sem SKU"
                    fotos = p.get("pictures", [])
                    resultado[p["id"]] = {
                        "titulo": p["title"], "sku": str(sku).strip(),
                        "estoque_atual": p["available_quantity"],
                        "imagem": fotos[0].get("url", "") if fotos else "",
                        "status": status,
                    }
            offset += 100
            if offset >= resp["paging"]["total"]:
                break
        return resultado

    ativos = por_status("active")
    produtos.update(ativos)
    pausados = por_status("paused")

    # pausados só entram se venderam nos últimos 10 dias
    de = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%dT00:00:00.000-00:00")
    ate = datetime.now().strftime("%Y-%m-%dT23:59:59.000-00:00")
    recentes = defaultdict(int)
    offset = 0
    while True:
        resp = ml_get(f"https://api.mercadolibre.com/orders/search?seller={user_id}&order.status=paid"
                      f"&order.date_created.from={de}&order.date_created.to={ate}&limit=50&offset={offset}", headers)
        pedidos = resp.get("results", [])
        if not pedidos:
            break
        for pedido in pedidos:
            for it in pedido["order_items"]:
                recentes[it["item"]["id"]] += it["quantity"]
        offset += 50
        if offset >= resp["paging"]["total"]:
            break
    incluidos = {k: v for k, v in pausados.items() if k in recentes}
    produtos.update(incluidos)
    log(f"{len(ativos)} anúncios FULL ativos + {len(incluidos)} pausados com venda recente = {len(produtos)}")
    return produtos


def buscar_historico_vendas(headers, user_id, produtos):
    """Janelas de 7 dias (a API do ML limita cada busca a 10.000 pedidos)."""
    DIAS_TOTAIS, JANELA = 37, 7
    vendas_por_data = defaultdict(lambda: defaultdict(int))
    agora = datetime.now()
    fim, limite = agora, agora - timedelta(days=DIAS_TOTAIS)
    total_pedidos = 0
    while fim > limite:
        inicio = max(fim - timedelta(days=JANELA), limite)
        de = inicio.strftime("%Y-%m-%dT00:00:00.000-00:00")
        ate = fim.strftime("%Y-%m-%dT23:59:59.000-00:00")
        offset = 0
        while True:
            resp = ml_get(f"https://api.mercadolibre.com/orders/search?seller={user_id}&order.status=paid"
                          f"&order.date_created.from={de}&order.date_created.to={ate}&limit=50&offset={offset}", headers)
            if "results" not in resp:
                log(f"⚠️ Erro da API na janela {inicio.date()}–{fim.date()} (offset {offset}): {str(resp)[:150]}")
                break
            pedidos = resp["results"]
            if not pedidos:
                break
            for pedido in pedidos:
                total_pedidos += 1
                dia = datetime.fromisoformat(pedido["date_created"]).astimezone(TZ_BR).date().isoformat()
                for it in pedido["order_items"]:
                    vendas_por_data[it["item"]["id"]][dia] += it["quantity"]
            offset += 50
            if offset >= resp["paging"]["total"]:
                break
        fim = inicio

    hoje = datetime.now(TZ_BR).date()
    c7, c30 = hoje - timedelta(days=7), hoje - timedelta(days=30)
    v7, v30, ultima = defaultdict(int), defaultdict(int), {}
    for item_id, datas in vendas_por_data.items():
        for dia_str, qtd in datas.items():
            dia = datetime.strptime(dia_str, "%Y-%m-%d").date()
            if dia >= c30:
                v30[item_id] += qtd
            if dia >= c7:
                v7[item_id] += qtd
            if item_id not in ultima or dia > ultima[item_id]:
                ultima[item_id] = dia

    venda_dia_envio = {}
    for item_id, d in produtos.items():
        if item_id not in ultima:
            venda_dia_envio[item_id] = 0
        elif d["estoque_atual"] == 0:
            # zerado: usa a semana que antecedeu a última venda
            sete_antes = ultima[item_id] - timedelta(days=7)
            soma = sum(q for ds, q in vendas_por_data[item_id].items()
                       if sete_antes <= datetime.strptime(ds, "%Y-%m-%d").date() <= ultima[item_id])
            venda_dia_envio[item_id] = round(soma / 7, 2)
        else:
            venda_dia_envio[item_id] = round(v7.get(item_id, 0) / 7, 2)
    log(f"Histórico: {total_pedidos} pedidos · {sum(v30.values())} un. em 30d · {sum(v7.values())} un. em 7d")
    return v7, v30, venda_dia_envio


# ─── Tiny ────────────────────────────────────────────────────────────────────
def tiny_request(url, params, max_tentativas=4):
    """Devolve (retorno, erro). O Tiny não lança exceção: devolve status != OK."""
    erro_final = None
    for tentativa in range(1, max_tentativas + 1):
        espera = TINY_MIN_INTERVALO - (time.time() - _tiny_ultima_chamada[0])
        if espera > 0:
            time.sleep(espera)
        try:
            r = requests.get(url, params=params, timeout=20).json()
            _tiny_ultima_chamada[0] = time.time()
        except Exception as e:
            erro_final = f"falha de conexão/JSON: {e}"
            time.sleep(2 * tentativa)
            continue
        retorno = r.get("retorno")
        if retorno is None:
            return None, f"resposta sem campo 'retorno': {str(r)[:150]}"
        if retorno.get("status") in ("OK", "Ok"):
            return retorno, None
        erros = retorno.get("erros", [])
        msg = "; ".join(str(e.get("erro", e)) for e in erros) if erros else str(retorno)[:150]
        erro_final = msg
        if any(t in msg.lower() for t in ["limite", "excedido", "numero de solicita", "número de solicita"]):
            time.sleep(3 * tentativa)
            continue
        return None, msg
    return None, f"falhou após {max_tentativas} tentativas — último erro: {erro_final}"


def estoques_tiny(token, sku):
    """Devolve ({'full': n, 'galpao': n}, None) ou (None, erro).
    full   = saldo do depósito "Full" (negativo vira 0 — controle interno do Tiny)
    galpao = saldo do depósito geral menos reservas (estoque físico do galpão)
    Se mais de um cadastro tiver exatamente esse código, somamos todos."""
    retorno, erro = tiny_request("https://api.tiny.com.br/api2/produtos.pesquisa.php",
                                 {"token": token, "formato": "json", "pesquisa": sku})
    if erro:
        return None, erro
    achados = retorno.get("produtos", [])
    # a busca do Tiny é por "contém": evita somar SKUs parecidos (ABC e ABC-2)
    exatos = [m for m in achados if str(m["produto"].get("codigo", "")).strip().lower() == sku.lower()]
    full = saldo = reservado = 0
    for m in (exatos or achados):
        ret, erro = tiny_request("https://api.tiny.com.br/api2/produto.obter.estoque.php",
                                 {"token": token, "formato": "json", "id": m["produto"]["id"]})
        if erro:
            return None, erro
        prod = ret.get("produto", {})
        saldo += _num(prod.get("saldo"))
        reservado += _num(prod.get("saldoReservado"))
        for dep in prod.get("depositos", []):
            d = dep.get("deposito", {})
            if d.get("nome") == "Full":
                full += _num(d.get("saldo"))
    return {"full": max(0, full), "galpao": saldo - reservado}, None


# ─── Cálculo (sem rede — fácil de testar) ────────────────────────────────────
def calcular(produtos, v7, v30, venda_dia_envio, tiny, cfg):
    prazo_full = cfg["PREP_TIME_FULL"] + cfg["ESTOQUE_SEGURANCA"]
    prazo_compra = cfg["DIAS_LEAD_COMPRA"] + cfg["ESTOQUE_SEGURANCA"]
    meta = cfg["DIAS_COBERTURA_MINIMA_TOTAL"]
    cobertura_envio = cfg["DIAS_COBERTURA_ENVIO_FULL"]

    linhas = []
    for item_id, d in produtos.items():
        sku = d["sku"]
        t = tiny.get(sku)
        vendas30, vendas7 = v30.get(item_id, 0), v7.get(item_id, 0)
        vd30 = round(vendas30 / 30, 2)
        vd7 = venda_dia_envio.get(item_id, 0)

        if t:
            estoque_full, fonte, galpao = t["full"], "tiny", t["galpao"]
        else:  # sem dado do Tiny: FULL cai pro valor do ML; galpão fica desconhecido
            estoque_full, fonte, galpao = d["estoque_atual"], "ml", None

        # ── painel FULL ──
        dias_full = round(estoque_full / vd30, 1) if vd30 > 0 else None
        envio = max(0, round(vd7 * cobertura_envio - estoque_full))
        if dias_full is not None and dias_full <= prazo_full:
            status_full = "critico"
        elif dias_full is not None and dias_full <= prazo_full + 10:
            status_full = "atencao"
        else:
            status_full = "ok"

        # ── painel Compras ──
        if galpao is None:
            total = dias_total = compra = None
            status_compra = "sem_dados"
        else:
            total = estoque_full + galpao
            dias_total = round(total / vd30, 1) if vd30 > 0 else None
            compra = max(0, round(vd30 * meta - total)) if vd30 > 0 else 0
            if dias_total is None:
                status_compra = "ok"
            elif dias_total <= prazo_compra:
                status_compra = "critico"
            elif dias_total < meta:
                status_compra = "atencao"
            else:
                status_compra = "ok"

        linhas.append({
            "id": item_id, "sku": sku, "produto": d["titulo"], "imagem": d["imagem"],
            "estoqueFull": estoque_full, "fonteFull": fonte,
            "vendas7d": vendas7, "vendas30d": vendas30, "vendaDia7d": vd7, "vendaDia30d": vd30,
            "diasFull": dias_full, "envioFull": envio, "statusFull": status_full,
            "estoqueGalpao": galpao, "estoqueTotal": total, "diasTotal": dias_total,
            "compraSugerida": compra, "statusCompra": status_compra,
        })

    def contar(campo):
        r = {"critico": 0, "atencao": 0, "ok": 0, "sem_dados": 0}
        for l in linhas:
            r[l[campo]] += 1
        return r

    config = {
        "prepTimeFull": cfg["PREP_TIME_FULL"], "estoqueSeguranca": cfg["ESTOQUE_SEGURANCA"],
        "diasCoberturaEnvioFull": cobertura_envio, "diasLeadCompra": cfg["DIAS_LEAD_COMPRA"],
        "diasCoberturaMinimaTotal": meta, "prazoFull": prazo_full, "prazoCompra": prazo_compra,
    }
    return {"produtos": linhas, "resumo": {"full": contar("statusFull"), "compras": contar("statusCompra")}, "config": config,
            "insights": gerar_insights(linhas, cfg)}


# ─── Insights (dicas automáticas) ────────────────────────────────────────────
INS_MIN_VENDAS_7D_ALTA = 5      # volume mínimo nos últimos 7d pra considerar "acima do normal"
INS_RAZAO_ALTA = 1.5            # ritmo recente / ritmo anterior
INS_MIN_VENDAS_ANT_QUEDA = 8    # volume mínimo nos 23d anteriores pra considerar "queda"
INS_RAZAO_QUEDA = 0.5
INS_MAX_POR_TIPO = 8


def _un(n):
    return f"{int(round(n)):,}".replace(",", ".")


def _dias(c):
    return "mais de 1 ano" if c > 365 else f"~{_un(c)} dias"


def gerar_insights(linhas, cfg):
    """Compara o ritmo dos últimos 7 dias com o dos 23 dias anteriores (30d - 7d)."""
    lead = cfg["DIAS_LEAD_COMPRA"]
    meta = cfg["DIAS_COBERTURA_MINIMA_TOTAL"]
    altas, quedas = [], []
    tot7 = tot_ant = 0
    for l in linhas:
        v7, v30 = l["vendas7d"], l["vendas30d"]
        ant = max(v30 - v7, 0)                 # vendas nos 23 dias anteriores
        sem_ant = ant / 23 * 7                 # média semanal anterior
        tot7 += v7
        tot_ant += sem_ant
        vd_rec = v7 / 7
        vd_ant = ant / 23
        cobertura = None                        # dias que o estoque total dura no ritmo recente
        base_estoque = l["estoqueTotal"] if l["estoqueTotal"] is not None else l["estoqueFull"]
        if vd_rec > 0:
            cobertura = base_estoque / vd_rec
        item = {
            "id": l["id"], "sku": l["sku"], "produto": l["produto"], "imagem": l["imagem"],
            "vendas7d": v7, "mediaSemanalAnterior": round(sem_ant, 1),
            "estoque": base_estoque, "estoqueTotalConhecido": l["estoqueTotal"] is not None,
        }
        # ── vendendo acima do normal ──
        if v7 >= INS_MIN_VENDAS_7D_ALTA and (vd_ant == 0 or vd_rec / vd_ant >= INS_RAZAO_ALTA):
            if vd_ant == 0:
                var, titulo = None, f"Começou a vender: {_un(v7)} un. nos últimos 7 dias (quase nada antes)"
            else:
                var = round((vd_rec / vd_ant - 1) * 100)
                titulo = f"Vendendo {var}% acima do normal: {_un(v7)} un. em 7 dias vs. ~{_un(sem_ant)} por semana antes"
            partes = [f"No ritmo atual o estoque{'' if item['estoqueTotalConhecido'] else ' do FULL'} dura ~{_un(cobertura)} dias."]
            if cobertura is not None and cobertura <= lead + cfg["ESTOQUE_SEGURANCA"]:
                partes.append(f"Isso é menos que o prazo de reposição ({lead} dias + margem): vale comprar já.")
            elif cobertura is not None and cobertura < meta:
                partes.append(f"Fica abaixo da sua meta de {meta} dias: considere antecipar a compra.")
            else:
                partes.append("Ainda há folga, mas acompanhe se a alta se sustenta antes de aumentar o pedido.")
            if l["envioFull"] and l["envioFull"] > 0:
                partes.append(f"Envio sugerido ao FULL: {_un(l['envioFull'])} un.")
            item.update({"tipo": "alta", "variacaoPct": var, "titulo": titulo, "dica": " ".join(partes),
                         "impacto": round(vd_rec - vd_ant, 3), "coberturaDias": None if cobertura is None else round(cobertura, 1)})
            altas.append(item)
        # ── vendas em queda ──
        elif ant >= INS_MIN_VENDAS_ANT_QUEDA and vd_ant > 0 and vd_rec / vd_ant <= INS_RAZAO_QUEDA:
            var = round((vd_rec / vd_ant - 1) * 100)   # negativo
            titulo = f"Vendas caíram {abs(var)}%: {_un(v7)} un. em 7 dias vs. ~{_un(sem_ant)} por semana antes"
            partes = []
            if base_estoque and vd_rec > 0:
                partes.append(f"No ritmo atual o estoque{'' if item['estoqueTotalConhecido'] else ' do FULL'} dura {_dias(cobertura)}.")
            elif base_estoque:
                partes.append(f"Sem vendas na última semana e {_un(base_estoque)} un. em estoque.")
            if l["compraSugerida"] and l["compraSugerida"] > 0:
                partes.append(f"O sistema sugere comprar {_un(l['compraSugerida'])} un., mas com a queda vale segurar e reavaliar.")
            else:
                partes.append("Evite novas compras por enquanto e veja se é sazonal, preço ou concorrência.")
            item.update({"tipo": "queda", "variacaoPct": var, "titulo": titulo, "dica": " ".join(partes),
                         "impacto": round(vd_ant - vd_rec, 3), "coberturaDias": None if cobertura is None else round(cobertura, 1)})
            quedas.append(item)
    altas.sort(key=lambda x: -x["impacto"])
    quedas.sort(key=lambda x: -x["impacto"])
    var_total = None if tot_ant <= 0 else round((tot7 / tot_ant - 1) * 100)
    if var_total is None:
        resumo = f"Vendas nos últimos 7 dias: {_un(tot7)} un."
    elif abs(var_total) < 5:
        resumo = f"Vendas nos últimos 7 dias: {_un(tot7)} un., em linha com a média das semanas anteriores."
    else:
        resumo = (f"Vendas nos últimos 7 dias: {_un(tot7)} un., {abs(var_total)}% "
                  f"{'acima' if var_total > 0 else 'abaixo'} da média semanal anterior (~{_un(tot_ant)} un.).")
    return {
        "resumo": resumo, "variacaoTotalPct": var_total,
        "totalAlta": len(altas), "totalQueda": len(quedas),
        "alta": altas[:INS_MAX_POR_TIPO], "queda": quedas[:INS_MAX_POR_TIPO],
    }


# ─── Execução ────────────────────────────────────────────────────────────────
def executar(db, firestore):
    inicio = time.time()
    cfg = carregar_config()
    user_id, tiny_token = os.environ["ML_USER_ID"], os.environ["TINY_TOKEN"]

    db.collection("control").document("status").set(
        {"estado": "rodando", "iniciadoEm": firestore.SERVER_TIMESTAMP, "mensagem": "Buscando dados no Mercado Livre…"})

    access = renovar_token_ml(db, firestore)
    headers = {"Authorization": f"Bearer {access}"}

    produtos = buscar_produtos_full(headers, user_id)
    if not produtos:
        raise RuntimeError("O Mercado Livre não devolveu nenhum anúncio FULL — mantive os dados anteriores.")
    v7, v30, venda_dia_envio = buscar_historico_vendas(headers, user_id, produtos)

    db.collection("control").document("status").set(
        {"estado": "rodando", "iniciadoEm": firestore.SERVER_TIMESTAMP, "mensagem": "Buscando estoque no Tiny…"}, merge=True)
    skus = sorted({d["sku"] for d in produtos.values() if d["sku"] != "Sem SKU"})
    log(f"Buscando estoque no Tiny para {len(skus)} SKUs…")
    tiny, erros = {}, []
    for i, sku in enumerate(skus, 1):
        try:
            res, erro = estoques_tiny(tiny_token, sku)
        except Exception as e:
            res, erro = None, f"exceção: {e}"
        if erro:
            erros.append((sku, erro))
        else:
            tiny[sku] = res
        if i % 20 == 0:
            log(f"   …{i}/{len(skus)}")
    if skus and not tiny:
        raise RuntimeError(f"O Tiny falhou para TODOS os SKUs (ex.: {erros[0][1]}). Confira o TINY_TOKEN — mantive os dados anteriores.")

    avisos = []
    if erros:
        avisos.append(f"{len(erros)} de {len(skus)} SKUs sem dados do Tiny (ex.: {erros[0][0]} — {erros[0][1]}). "
                      "Aparecem como “sem dados” em Compras e usam o estoque do ML no FULL.")
        log("⚠️ " + avisos[-1])

    resultado = calcular(produtos, v7, v30, venda_dia_envio, tiny, cfg)
    payload = {
        **resultado,
        "avisos": avisos,
        "duracaoSeg": round(time.time() - inicio),
        "atualizadoEm": firestore.SERVER_TIMESTAMP,
    }
    tamanho = len(json.dumps(resultado, ensure_ascii=False).encode("utf-8"))
    if tamanho > 900_000:
        raise RuntimeError(f"Resultado grande demais pro Firestore ({tamanho} bytes) — preciso dividir em mais documentos.")
    db.collection("dashboard").document("latest").set(payload)
    db.collection("control").document("status").set(
        {"estado": "ok", "concluidoEm": firestore.SERVER_TIMESTAMP, "mensagem": f"{len(resultado['produtos'])} produtos atualizados"})
    try:
        from anuncios import executar_anuncios
        executar_anuncios(db, firestore, headers, user_id, cfg)
    except Exception as e:  # o guardião nunca derruba a atualização de estoque
        log(f"⚠️ Guardião de anúncios falhou: {e}")
    log(f"✅ Pronto em {round(time.time() - inicio)}s · FULL {resultado['resumo']['full']} · Compras {resultado['resumo']['compras']}")


def main():
    import firebase_admin
    from firebase_admin import credentials, firestore

    info = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    firebase_admin.initialize_app(credentials.Certificate(info))
    db = firestore.client()
    try:
        executar(db, firestore)
    except Exception as e:
        traceback.print_exc()
        try:
            db.collection("control").document("status").set(
                {"estado": "erro", "concluidoEm": firestore.SERVER_TIMESTAMP, "mensagem": str(e)[:500]})
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
