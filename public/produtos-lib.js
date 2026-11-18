// Funções puras da aba "Produtos e margem": leitura da planilha e cálculo de margem.
// Sem dependência de navegador, para poder ser testado no Node.

export const PLATAFORMAS = { ml: "Mercado Livre", shopee: "Shopee", tiktok: "TikTok Shop", amazon: "Amazon" };

const num = v => {
  if (v == null || v === "") return 0;
  const n = typeof v === "number" ? v : parseFloat(String(v).replace(/[R$\s]/g, "").replace(/\./g, "").replace(",", "."));
  return Number.isFinite(n) ? n : 0;
};
const txt = v => (v == null ? "" : String(v).trim());
const r2 = v => Math.round(v * 100) / 100;

/** Margem de um produto. fixo = tarifa fixa/embalagem/custo de venda; afiliado e comissão são frações (0.11 = 11%). */
export function calcMargem(p, imposto = 0.05) {
  const preco = num(p.preco);
  const comissao = preco * num(p.comissao);
  const afil = preco * num(p.afiliado);
  const imp = preco * imposto;
  const lucro = preco - num(p.custo) - comissao - afil - imp - num(p.fixo) - num(p.frete);
  return { lucro, margem: preco > 0 ? lucro / preco : null, comissaoR: comissao, afiliadoR: afil, impostoR: imp };
}

/** Preço necessário para atingir uma margem-alvo (fração). null se for impossível. */
export function precoParaMargem(p, margemAlvo, imposto = 0.05) {
  const d = 1 - num(p.comissao) - num(p.afiliado) - imposto - margemAlvo;
  if (d <= 0) return null;
  return (num(p.custo) + num(p.fixo) + num(p.frete)) / d;
}

function slug(s) {
  return String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "sem-sku";
}

// colunas (1 = A) de cada aba, conforme a planilha
const LAYOUT = {
  ml:     { aba: "PRODUTOS ML", cab: 1, marca: 2, nome: 3, sku: 4, preco: 5, custo: 6, comissao: 8, fixo: 9, frete: 10 },
  shopee: { aba: "PRODUTOS SHOPEE", cab: 1, nome: 3, sku: 4, preco: 5, custo: 6, comissao: 8, fixo: 9, frete: 11 },
  tiktok: { aba: "PRODUTOS TIK TOK SHOP", cab: 1, marca: 1, nome: 2, sku: 3, preco: 4, custo: 5, comissao: 7, afiliado: 8, fixo: 9, frete: 11 },
  amazon: { aba: "PRODUTOS AMAZON", cab: 3, sku: 3, preco: 4, custo: 5, comissao: 7, afiliado: 8, fixo: 9, frete: 11 },
};

/** Lê as abas de produtos de um workbook do SheetJS. Devolve {produtos, resumo}. */
export function lerPlanilha(XLSX, wb) {
  const produtos = [], resumo = {};
  const achar = nome => wb.SheetNames.find(n => n.trim().toUpperCase() === nome);
  for (const [plat, L] of Object.entries(LAYOUT)) {
    const nomeAba = achar(L.aba);
    resumo[plat] = 0;
    if (!nomeAba) continue;
    const ws = wb.Sheets[nomeAba];
    const ref = XLSX.utils.decode_range(ws["!ref"] || "A1:A1");
    const cel = (r, c) => { const x = ws[XLSX.utils.encode_cell({ r: r - 1, c: c - 1 })]; return x ? x.v : null; };
    const vistos = {};
    for (let r = L.cab + 1; r <= ref.e.r + 1; r++) {
      const sku = txt(cel(r, L.sku)), preco = num(cel(r, L.preco));
      if (!sku || !preco) continue;           // linha vazia ou de anotação
      vistos[sku] = (vistos[sku] || 0) + 1;
      const id = `${plat}__${slug(sku)}` + (vistos[sku] > 1 ? `__${vistos[sku]}` : "");
      produtos.push({
        id, plat, sku,
        nome: L.nome ? txt(cel(r, L.nome)) : "",
        marca: L.marca ? txt(cel(r, L.marca)) : "",
        preco: r2(preco), custo: r2(num(cel(r, L.custo))),
        comissao: num(cel(r, L.comissao)), fixo: r2(num(cel(r, L.fixo))),
        frete: r2(num(cel(r, L.frete))), afiliado: L.afiliado ? num(cel(r, L.afiliado)) : 0,
      });
      resumo[plat]++;
    }
  }
  return { produtos, resumo };
}
