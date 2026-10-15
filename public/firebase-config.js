// ─── CONFIGURAÇÃO DO APP (preencha uma vez) ──────────────────────────────────────
// Nada aqui é segredo: a chave do Firebase Web é pública por design — quem protege
// seus dados são as regras do Firestore (firestore.rules) + o login com Google.

// Console do Firebase → ⚙ Configurações do projeto → Seus apps → app Web → "Config"
export const firebaseConfig = {
  apiKey: "AIzaSyDxivePfWsgtjmxN9G9mdjmptFgygAarTQ",
  authDomain: "estoque-ml-7254e.firebaseapp.com",
  projectId: "estoque-ml-7254e",
  appId: "1:205094040299:web:9bddcac396af21f55b84ae",
};

// Único e-mail Google que pode abrir o painel (a regra do Firestore também confere).
export const DONO_EMAIL = "joaopedrolunardi.fgv@gmail.com";

// Repositório do GitHub onde roda o job — usado só pelo botão "Atualizar agora".
export const GITHUB = {
  owner: "joaolunardi",
  repo: "estoque-ml-painel",
  workflow: "atualizar.yml",
  branch: "main",
};
