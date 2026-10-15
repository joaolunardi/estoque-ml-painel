import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "job"))
import pipeline as p

CFG = p.carregar_config()

# ── calcular() ──
produtos = {
  "MLB1": {"titulo": "Caderno A", "sku": "A", "estoque_atual": 50, "imagem": ""},   # tiny ok, crítico
  "MLB2": {"titulo": "Caneta B",  "sku": "B", "estoque_atual": 5,  "imagem": ""},   # tiny falhou
  "MLB3": {"titulo": "Lápis C",   "sku": "C", "estoque_atual": 0,  "imagem": ""},   # sem venda
  "MLB4": {"titulo": "Sem sku",   "sku": "Sem SKU", "estoque_atual": 9, "imagem": ""},
}
v30 = {"MLB1": 60, "MLB2": 30}          # 2/dia e 1/dia
v7  = {"MLB1": 14, "MLB2": 7}
vde = {"MLB1": 2.0, "MLB2": 1.0, "MLB3": 0, "MLB4": 0}
tiny = {"A": {"full": 10, "galpao": 20}, "C": {"full": 0, "galpao": 100}}
r = p.calcular(produtos, v7, v30, vde, tiny, CFG)
L = {l["sku"]: l for l in r["produtos"]}
a = L["A"]
assert a["estoqueFull"] == 10 and a["fonteFull"] == "tiny"
assert a["diasFull"] == 5.0 and a["statusFull"] == "critico"      # 10/2=5 <= 12
assert a["envioFull"] == 30                                       # 2*20-10
assert a["estoqueTotal"] == 30 and a["diasTotal"] == 15.0
assert a["statusCompra"] == "critico"                             # 15 <= 20 (15+5)
assert a["compraSugerida"] == 90                                  # 2*60-30
b = L["B"]
assert b["fonteFull"] == "ml" and b["estoqueFull"] == 5
assert b["estoqueGalpao"] is None and b["statusCompra"] == "sem_dados" and b["compraSugerida"] is None
assert L["C"]["statusFull"] == "ok" and L["C"]["diasFull"] is None and L["C"]["statusCompra"] == "ok" and L["C"]["compraSugerida"] == 0
assert L["Sem SKU"]["statusCompra"] == "sem_dados"
assert r["resumo"]["compras"] == {"critico": 1, "atencao": 0, "ok": 1, "sem_dados": 2}
assert r["config"]["prazoCompra"] == 20 and r["config"]["prazoFull"] == 12
json.dumps(r)  # serializável
print("calcular OK")

# ── estoques_tiny(): filtro por código exato, soma de duplicados, FULL negativo ──
def fake_tiny(url, params, max_tentativas=4):
    if "pesquisa" in url:
        return {"produtos": [
            {"produto": {"id": "1", "codigo": "ABC"}},
            {"produto": {"id": "2", "codigo": "ABC"}},       # cadastro duplicado -> soma
            {"produto": {"id": "3", "codigo": "ABC-2"}},     # parecido -> ignora
        ]}, None
    pid = params["id"]
    tabela = {"1": (30, 5, 12), "2": (10, 0, "8"), "3": (999, 0, 999)}   # saldo, reservado, full
    s, rs, f = tabela[pid]
    return {"produto": {"saldo": s, "saldoReservado": rs, "depositos": [
        {"deposito": {"nome": "Geral", "saldo": s}}, {"deposito": {"nome": "Full", "saldo": f}}]}}, None
p.tiny_request = fake_tiny
res, erro = p.estoques_tiny("tok", "ABC")
assert erro is None and res == {"full": 20, "galpao": 35}, res
p.tiny_request = lambda url, params, max_tentativas=4: ({"produto": {"saldo": 5, "saldoReservado": 0,
    "depositos": [{"deposito": {"nome": "Full", "saldo": -1780}}]}}, None) if "estoque" in url else ({"produtos": [{"produto": {"id": "9", "codigo": "NEG"}}]}, None)
res, erro = p.estoques_tiny("tok", "NEG")
assert res == {"full": 0, "galpao": 5}, res
p.tiny_request = lambda *a, **k: (None, "token invalido")
assert p.estoques_tiny("tok", "X") == (None, "token invalido")
print("estoques_tiny OK")

# ── executar() com Firestore falso e rede simulada ──
class Doc:
    def __init__(s, store, path): s.store, s.path = store, path
    def get(s):
        d = s.store.get(s.path); o = type("S", (), {})(); o.exists = d is not None; o.to_dict = lambda: d; return o
    def set(s, data, merge=False):
        s.store[s.path] = {**s.store.get(s.path, {}), **data} if merge else dict(data)
class Col:
    def __init__(s, store, name): s.store, s.name = store, name
    def document(s, d): return Doc(s.store, f"{s.name}/{d}")
class DB:
    def __init__(s): s.store = {}
    def collection(s, n): return Col(s.store, n)
class FS: SERVER_TIMESTAMP = "<ts>"

os.environ.update(ML_USER_ID="1", TINY_TOKEN="t", ML_APP_ID="a", ML_SECRET_KEY="s", ML_REFRESH_TOKEN="inicial")
db = DB()
p.renovar_token_ml = lambda db_, fs: "acc"
p.buscar_produtos_full = lambda h, u: produtos
p.buscar_historico_vendas = lambda h, u, pr: (v7, v30, vde)
def tiny_parcial(token, sku):
    return (tiny[sku], None) if sku in tiny else (None, "Produto não encontrado")
p.estoques_tiny = tiny_parcial
p.executar(db, FS)
latest = db.store["dashboard/latest"]
assert len(latest["produtos"]) == 4 and latest["avisos"] and "1 de 3" in latest["avisos"][0] or "de 3" in latest["avisos"][0], latest["avisos"]
assert db.store["control/status"]["estado"] == "ok"
print("executar OK  | aviso:", latest["avisos"][0][:90])

# Tiny falha em tudo -> não sobrescreve e levanta erro
db2 = DB(); db2.store["dashboard/latest"] = {"velho": True}
p.estoques_tiny = lambda token, sku: (None, "token invalido")
try:
    p.executar(db2, FS); raise SystemExit("deveria falhar")
except RuntimeError as e:
    assert "TODOS" in str(e)
assert db2.store["dashboard/latest"] == {"velho": True}
print("falha total do Tiny preserva dados antigos OK")
