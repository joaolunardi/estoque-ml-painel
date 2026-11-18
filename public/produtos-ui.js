// Aba "Produtos e margem": tabela editável com margem calculada na hora.
import { PLATAFORMAS, calcMargem, precoParaMargem, lerPlanilha } from "./produtos-lib.js";

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const brl = v => v == null ? "—" : "R$ " + Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (v, d = 1) => v == null ? "—" : (v * 100).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }) + "%";
const LIMITE = 150;
const num = v => { const n = parseFloat(v); return Number.isFinite(n) ? n : 0; };

export function iniciarProdutos({ F, db, demo }) {
  let produtos = new Map(), cfg = { imposto: 0.05, margemAlvo: 0.10 };
  let anuncios = [], tarifas = {}, mlPorSku = new Map();
  const filtro = { plat: "todas", busca: "", margem: "todas", ordem: "margem", limite: LIMITE };
  let pendente = null;   // arquivo lido, aguardando confirmação de importação

  // Produtos do ML importados antes: o "custo de venda" era o envio. Passa para o campo de frete (uma vez).
  const migrados = new Set();
  function migrar(lista) {
    const lote = [];
    for (const p of lista) {
      if (p.plat !== "ml" || p.envioMigrado) continue;
      p.frete = Math.round(((p.frete || 0) + (p.fixo || 0)) * 100) / 100; p.fixo = 0; p.envioMigrado = true;
      if (!migrados.has(p.id)) { migrados.add(p.id); lote.push(p); }
    }
    if (lote.length && !demo) (async () => {
      try {
        for (let i = 0; i < lote.length; i += 400) {
          const b = F.writeBatch(db);
          lote.slice(i, i + 400).forEach(p => b.update(F.doc(db, "produtos", p.id), { frete: p.frete, fixo: 0, envioMigrado: true }));
          await b.commit();
        }
      } catch (e) { console.warn("migração de envio falhou", e); }
    })();
    return lista;
  }

  // ── dados de fora ──────────────────────────────────────────────────────
  const api = {
    setProdutos(lista) { produtos = new Map(migrar(lista).map(p => [p.id, p])); render(); },
    setConfig(c) { cfg = { ...cfg, ...(c || {}) }; syncCfg(); render(); },
    setAnuncios(doc) {
      anuncios = doc?.anuncios || [];
      mlPorSku = new Map();
      for (const a of anuncios) { const k = String(a.sku || "").trim().toUpperCase(); if (k && !mlPorSku.has(k)) mlPorSku.set(k, a); }
      render();
    },
    setTarifas(doc) { tarifas = doc?.itens || {}; render(); },
    render,
  };

  // ── cálculo de um produto (usa tarifa real do ML quando existe) ────────
  function efetivo(p) {
    const e = { ...p, fonte: "planilha" };
    if (p.plat === "ml") e.fixo = 0;   // no ML não há tarifa fixa: só comissão e envio
    if (p.plat === "ml" && !p.manualTarifa) {
      const a = mlPorSku.get(String(p.sku).trim().toUpperCase());
      const t = a && tarifas[a.id];
      if (t) {
        if (t.comissao != null) e.comissao = t.comissao;
        if (t.freteCusto != null && !p.manualFrete) e.frete = t.freteCusto;   // custo de envio real do vendedor
        e.fonte = "ml";
      }
    }
    return e;
  }
  const classe = m => m == null ? "sem_dados" : m < 0 ? "critico" : m < cfg.margemAlvo ? "atencao" : "ok";

  // ── tela ───────────────────────────────────────────────────────────────
  function lista() {
    const q = filtro.busca.trim().toLowerCase();
    let L = [...produtos.values()].map(p => { const e = efetivo(p); return { p, e, c: calcMargem(e, cfg.imposto) }; });
    if (filtro.plat !== "todas") L = L.filter(x => x.p.plat === filtro.plat);
    if (q) L = L.filter(x => `${x.p.sku} ${x.p.nome} ${x.p.marca}`.toLowerCase().includes(q));
    if (filtro.margem !== "todas") L = L.filter(x => classe(x.c.margem) === filtro.margem);
    const ord = {
      margem: (a, b) => (a.c.margem ?? 9) - (b.c.margem ?? 9),
      margemDesc: (a, b) => (b.c.margem ?? -9) - (a.c.margem ?? -9),
      lucro: (a, b) => b.c.lucro - a.c.lucro,
      nome: (a, b) => (a.p.nome || a.p.sku).localeCompare(b.p.nome || b.p.sku, "pt-BR"),
    }[filtro.ordem];
    return L.sort(ord);
  }

  function inp(campo, valor, { pctMode = false, w = 84 } = {}) {
    const v = pctMode ? +(valor * 100).toFixed(2) : valor;
    return `<input class="ed" type="number" inputmode="decimal" step="${pctMode ? "0.1" : "0.01"}" min="0" data-c="${campo}"${pctMode ? ' data-pct="1"' : ""} value="${v ?? 0}" style="width:${w}px">`;
  }

  function linha({ p, e, c }) {
    const a = p.plat === "ml" ? mlPorSku.get(String(p.sku).trim().toUpperCase()) : null;
    const dif = a && a.preco != null && Math.abs(a.preco - p.preco) > 0.005;
    const alvo = precoParaMargem(e, cfg.margemAlvo, cfg.imposto);
    const cl = classe(c.margem);
    return `<tr data-id="${esc(p.id)}">
      <td class="prod"><b>${esc(p.nome || p.sku)}</b><small>${esc(p.sku)}${p.marca ? " · " + esc(p.marca) : ""} · ${PLATAFORMAS[p.plat]}${e.fonte === "ml" ? ' · <span class="tag-ml" title="Comissão e custo de envio vindos do próprio Mercado Livre">comissão e envio reais ML</span>' : ""}</small></td>
      <td class="n">${inp("preco", p.preco, { w: 92 })}${a ? `<small class="${dif ? "difml" : "mut"}">no ML: ${brl(a.preco)}${a.link ? ` <a href="${esc(a.link)}" target="_blank" rel="noopener">↗</a>` : ""}</small>` : ""}</td>
      <td class="n">${inp("custo", p.custo, { w: 88 })}</td>
      <td class="n">${inp("comissao", e.comissao, { pctMode: true, w: 66 })}</td>
      <td class="n">${p.plat === "ml" ? '<span class="zero" title="O Mercado Livre não cobra tarifa fixa: só comissão e envio">—</span>' : inp("fixo", e.fixo, { w: 72 })}</td>
      <td class="n">${inp("frete", e.frete, { w: 72 })}</td>
      <td class="n">${p.plat === "tiktok" || p.plat === "amazon" ? inp("afiliado", p.afiliado, { pctMode: true, w: 62 }) : '<span class="zero">—</span>'}</td>
      <td class="n r-imp">${brl(c.impostoR)}</td>
      <td class="n r-lucro"><b>${brl(c.lucro)}</b></td>
      <td class="n"><span class="pill r-margem ${cl}">${pct(c.margem)}</span></td>
      <td class="n r-alvo">${alvo == null ? "—" : brl(alvo)}</td></tr>`;
  }

  function render() {
    const raiz = $("#produtos"); if (!raiz) return;
    // não redesenha enquanto o usuário digita numa célula
    if (raiz.contains(document.activeElement) && document.activeElement.classList.contains("ed")) return;
    const todos = [...produtos.values()];
    if (!todos.length) {
      raiz.innerHTML = `<div class="ins-sum" style="flex-direction:column;align-items:flex-start;gap:10px">
        <b>Nenhum produto cadastrado ainda.</b>
        <span class="mut">Importe sua planilha geral (abas de produtos do ML, Shopee, TikTok e Amazon). Depois disso você edita tudo aqui no app.</span>
        <button class="pri" id="pdImportar">Importar planilha</button></div>`;
      return;
    }
    const L = lista();
    const todasCalc = todos.map(p => calcMargem(efetivo(p), cfg.imposto).margem);
    const cont = k => todasCalc.filter(m => classe(m) === k).length;
    const plats = ["todas", ...Object.keys(PLATAFORMAS)];
    const nPlat = pl => pl === "todas" ? todos.length : todos.filter(p => p.plat === pl).length;
    raiz.innerHTML = `
      <div class="pd-top">
        <div class="seg">${plats.map(pl => `<button class="${filtro.plat === pl ? "on" : ""}" data-pl="${pl}">${pl === "todas" ? "Todas" : PLATAFORMAS[pl]} <span class="mut">${nPlat(pl)}</span></button>`).join("")}</div>
        <div class="pd-cfg">
          <label>Imposto <input id="cfgImposto" type="number" step="0.1" min="0" value="${(cfg.imposto * 100).toFixed(1)}"> %</label>
          <label>Margem alvo <input id="cfgAlvo" type="number" step="0.5" min="0" value="${(cfg.margemAlvo * 100).toFixed(1)}"> %</label>
        </div>
      </div>
      <div class="pd-top">
        <div class="stats">
          <span class="stat ${filtro.margem === "todas" ? "on" : ""}" data-m="todas">Todos <b>${todos.length}</b></span>
          <span class="stat ${filtro.margem === "critico" ? "on" : ""}" data-m="critico"><i class="dot critico"></i>Prejuízo <b>${cont("critico")}</b></span>
          <span class="stat ${filtro.margem === "atencao" ? "on" : ""}" data-m="atencao"><i class="dot atencao"></i>Abaixo da meta <b>${cont("atencao")}</b></span>
          <span class="stat ${filtro.margem === "ok" ? "on" : ""}" data-m="ok"><i class="dot ok"></i>Na meta <b>${cont("ok")}</b></span>
        </div>
        <div class="pd-tools">
          <input id="pdBusca" type="text" placeholder="Buscar SKU, produto ou marca…" value="${esc(filtro.busca)}">
          <select id="pdOrdem"><option value="margem" ${filtro.ordem === "margem" ? "selected" : ""}>Menor margem primeiro</option><option value="margemDesc" ${filtro.ordem === "margemDesc" ? "selected" : ""}>Maior margem primeiro</option><option value="lucro" ${filtro.ordem === "lucro" ? "selected" : ""}>Maior lucro (R$)</option><option value="nome" ${filtro.ordem === "nome" ? "selected" : ""}>Nome</option></select>
          <button id="pdCsv">Exportar CSV</button><button id="pdImportar">Importar planilha</button>
        </div>
      </div>
      <div class="tw"><table class="pd"><thead><tr><th>Produto</th><th class="n">Preço de venda</th><th class="n">Custo</th><th class="n">Comissão</th><th class="n" title="Shopee, TikTok e Amazon. No ML não há tarifa fixa">Tarifa fixa</th><th class="n" title="No ML: custo de envio que o vendedor paga (aparece como Envios no extrato)">Frete / envio</th><th class="n">Afiliado</th><th class="n">Imposto</th><th class="n">Lucro</th><th class="n">Margem</th><th class="n" title="Preço necessário para atingir a margem alvo">Preço p/ margem alvo</th></tr></thead>
      <tbody>${L.slice(0, filtro.limite).map(linha).join("") || `<tr><td colspan="11" class="empty">Nenhum produto com esses filtros.</td></tr>`}</tbody></table></div>
      ${L.length > filtro.limite ? `<p class="ins-nota"><button id="pdMais">Mostrar mais ${Math.min(LIMITE, L.length - filtro.limite)}</button> Mostrando ${filtro.limite} de ${L.length}.</p>` : `<p class="ins-nota">${L.length} produtos.</p>`}
      <p class="ins-nota">Edite o preço (ou custo, comissão, frete) e a margem se recalcula na hora. Alterar aqui <b>não muda o preço no Mercado Livre</b>: serve para simular e decidir. Margem = lucro ÷ preço de venda, depois de custo, comissão, tarifa fixa, frete e imposto.</p>`;
  }

  function syncCfg() { const a = $("#cfgImposto"), b = $("#cfgAlvo"); if (a) a.value = (cfg.imposto * 100).toFixed(1); if (b) b.value = (cfg.margemAlvo * 100).toFixed(1); }

  // recalcula só a linha editada
  function recalcLinha(tr) {
    const p = produtos.get(tr.dataset.id); if (!p) return;
    const v = {};
    tr.querySelectorAll("input.ed").forEach(i => { const n = parseFloat(i.value); v[i.dataset.c] = (Number.isFinite(n) ? n : 0) / (i.dataset.pct ? 100 : 1); });
    const e = efetivo({ ...p, preco: v.preco });
    if (p.plat === "ml") v.fixo = 0;
    const x = { ...e, ...v };
    const c = calcMargem(x, cfg.imposto), alvo = precoParaMargem(x, cfg.margemAlvo, cfg.imposto);
    tr.querySelector(".r-imp").textContent = brl(c.impostoR);
    tr.querySelector(".r-lucro").innerHTML = `<b>${brl(c.lucro)}</b>`;
    const m = tr.querySelector(".r-margem"); m.textContent = pct(c.margem); m.className = "pill r-margem " + classe(c.margem);
    tr.querySelector(".r-alvo").textContent = alvo == null ? "—" : brl(alvo);
  }

  async function salvarCampo(tr, input) {
    const id = tr.dataset.id, p = produtos.get(id); if (!p) return;
    const campo = input.dataset.c;
    const n = parseFloat(input.value); const val = (Number.isFinite(n) ? n : 0) / (input.dataset.pct ? 100 : 1);
    const upd = { [campo]: Math.round(val * 10000) / 10000 };
    if (p.plat === "ml" && campo === "comissao") upd.manualTarifa = true;
    if (p.plat === "ml" && campo === "frete") upd.manualFrete = true;
    Object.assign(p, upd);
    if (demo) return;
    try { await F.updateDoc(F.doc(db, "produtos", id), { ...upd, atualizadoEm: F.serverTimestamp() }); input.classList.remove("erro"); input.classList.add("salvo"); setTimeout(() => input.classList.remove("salvo"), 900); }
    catch (err) { input.classList.add("erro"); input.title = "Não salvou: " + (err.code || err.message); }
  }

  async function salvarCfg() {
    if (demo) return;
    try { await F.setDoc(F.doc(db, "config", "geral"), cfg, { merge: true }); } catch (e) { alert("Não consegui salvar a configuração: " + (e.code || e.message)); }
  }

  // ── importação da planilha ─────────────────────────────────────────────
  async function carregarXLSX() {
    if (window.XLSX) return window.XLSX;
    await new Promise((ok, err) => { const s = document.createElement("script"); s.src = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"; s.onload = ok; s.onerror = () => err(new Error("Não consegui carregar o leitor de planilhas.")); document.head.appendChild(s); });
    return window.XLSX;
  }
  function abrirImport() {
    const d = $("#dlgImport"); $("#impMsg").textContent = ""; $("#impResumo").innerHTML = ""; $("#impOk").disabled = true; pendente = null; $("#impFile").value = ""; d.showModal();
  }
  async function lerArquivo(file) {
    $("#impMsg").textContent = "Lendo a planilha…";
    try {
      const XLSX = await carregarXLSX();
      const wb = XLSX.read(await file.arrayBuffer());
      const r = lerPlanilha(XLSX, wb);
      if (!r.produtos.length) { $("#impMsg").textContent = "Não encontrei produtos nas abas esperadas (PRODUTOS ML, SHOPEE, TIK TOK SHOP, AMAZON)."; return; }
      pendente = r;
      const novos = r.produtos.filter(p => !produtos.has(p.id)).length;
      $("#impResumo").innerHTML = `<p>Encontrei <b>${r.produtos.length}</b> produtos: ${Object.entries(r.resumo).map(([k, n]) => `${PLATAFORMAS[k]} <b>${n}</b>`).join(" · ")}.</p>
        <p>${novos} ainda não existem no app${r.produtos.length - novos ? `; ${r.produtos.length - novos} já existem` : ""}.</p>
        <label style="display:flex;gap:8px;align-items:flex-start"><input type="radio" name="impModo" value="novos" checked> <span><b>Adicionar só os novos</b><br><span class="mut">Mantém tudo que você já editou no app.</span></span></label>
        <label style="display:flex;gap:8px;align-items:flex-start;margin-top:8px"><input type="radio" name="impModo" value="tudo"> <span><b>Substituir tudo pela planilha</b><br><span class="mut">Sobrescreve preços e custos que você editou aqui.</span></span></label>`;
      $("#impMsg").textContent = ""; $("#impOk").disabled = false;
    } catch (e) { $("#impMsg").textContent = "Erro ao ler: " + e.message; }
  }
  async function confirmarImport() {
    if (!pendente) return;
    const modo = document.querySelector('input[name="impModo"]:checked')?.value || "novos";
    const alvo = pendente.produtos.filter(p => modo === "tudo" || !produtos.has(p.id));
    $("#impOk").disabled = true; $("#impMsg").textContent = `Salvando ${alvo.length} produtos…`;
    try {
      if (demo) { alvo.forEach(p => produtos.set(p.id, p)); }
      else {
        for (let i = 0; i < alvo.length; i += 400) {
          const b = F.writeBatch(db);
          alvo.slice(i, i + 400).forEach(p => b.set(F.doc(db, "produtos", p.id), { ...p, manualTarifa: false, manualFrete: false, atualizadoEm: F.serverTimestamp() }));
          await b.commit();
        }
      }
      $("#dlgImport").close(); render();
    } catch (e) { $("#impMsg").textContent = "Não consegui salvar: " + (e.code || e.message); $("#impOk").disabled = false; }
  }

  function csv() {
    const L = lista();
    const linhas = [["Plataforma", "SKU", "Produto", "Marca", "Preço", "Custo", "Comissão %", "Tarifa fixa", "Frete", "Afiliado %", "Imposto R$", "Lucro R$", "Margem %", "Preço p/ meta"]];
    for (const { p, e, c } of L) {
      const alvo = precoParaMargem(e, cfg.margemAlvo, cfg.imposto);
      linhas.push([PLATAFORMAS[p.plat], p.sku, p.nome, p.marca, p.preco, p.custo, (e.comissao * 100).toFixed(2), e.fixo, e.frete,
        (p.afiliado * 100).toFixed(2), c.impostoR.toFixed(2), c.lucro.toFixed(2), c.margem == null ? "" : (c.margem * 100).toFixed(2), alvo == null ? "" : alvo.toFixed(2)]);
    }
    const txt = "﻿" + linhas.map(r => r.map(v => `"${String(v ?? "").replace(/"/g, '""')}"`).join(";")).join("\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt], { type: "text/csv;charset=utf-8" }));
    a.download = "produtos-margem.csv"; a.click();
  }

  // ── eventos (delegação, registrados uma vez) ───────────────────────────
  document.addEventListener("input", e => {
    const i = e.target; if (!i.classList?.contains("ed")) return;
    recalcLinha(i.closest("tr"));
  });
  document.addEventListener("change", e => {
    const i = e.target;
    if (i.classList?.contains("ed")) { salvarCampo(i.closest("tr"), i); return; }
    if (i.id === "cfgImposto") { cfg.imposto = Math.max(0, parseFloat(i.value) || 0) / 100; salvarCfg(); render(); }
    else if (i.id === "cfgAlvo") { cfg.margemAlvo = Math.max(0, parseFloat(i.value) || 0) / 100; salvarCfg(); render(); }
    else if (i.id === "pdOrdem") { filtro.ordem = i.value; filtro.limite = LIMITE; render(); }
    else if (i.id === "impFile" && i.files[0]) lerArquivo(i.files[0]);
  });
  document.addEventListener("keydown", e => { if (e.key === "Enter" && e.target.classList?.contains("ed")) e.target.blur(); });
  document.addEventListener("focusout", e => { if (e.target.classList?.contains("ed")) setTimeout(render, 150); });
  let tBusca;
  document.addEventListener("input", e => {
    if (e.target.id !== "pdBusca") return;
    clearTimeout(tBusca); const pos = e.target.selectionStart;
    tBusca = setTimeout(() => { filtro.busca = e.target.value; filtro.limite = LIMITE; render(); const b = $("#pdBusca"); if (b) { b.focus(); b.setSelectionRange(pos, pos); } }, 200);
  });
  document.addEventListener("click", e => {
    const t = e.target;
    const pl = t.closest("[data-pl]"); if (pl) { filtro.plat = pl.dataset.pl; filtro.limite = LIMITE; render(); return; }
    const m = t.closest("[data-m]"); if (m) { filtro.margem = m.dataset.m; filtro.limite = LIMITE; render(); return; }
    if (t.closest("#pdMais")) { filtro.limite += LIMITE; render(); }
    else if (t.closest("#pdCsv")) csv();
    else if (t.closest("#pdImportar")) abrirImport();
    else if (t.closest("#impOk")) confirmarImport();
    else if (t.closest("#impCancel")) $("#dlgImport").close();
  });
  return api;
}
