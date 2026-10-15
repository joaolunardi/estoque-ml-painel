# Estoque ML — app no Firebase (FULL + Compras)

Painel web (abre no Safari/Chrome do Mac, dá pra fixar no Dock) com login Google só pra você.
Um job no GitHub Actions busca ML + Tiny de hora em hora e grava no Firestore; o app lê em tempo real.
Botão **Atualizar agora** dispara o job na hora. Prévia sem login: abra `public/index.html` via servidor local com `?demo=1`.

## Passo a passo (uma vez só, ~30 min)

### 1. Firebase (plano Spark, grátis)
1. https://console.firebase.google.com → Adicionar projeto (sem Analytics).
2. **Build → Firestore Database** → Criar banco → modo **produção** → região `southamerica-east1`.
3. **Build → Authentication** → Começar → provedor **Google** → ativar.
4. ⚙ Configurações do projeto → "Seus apps" → ícone `</>` (Web) → registrar → copie o `firebaseConfig`
   e cole em `public/firebase-config.js` (apiKey, authDomain, projectId, appId). Preencha também `DONO_EMAIL` e `GITHUB.owner`.
5. Em `firestore.rules` troque `SEU_EMAIL_AQUI` pelo seu Gmail. Em `.firebaserc` troque pelo ID do projeto.
6. ⚙ → **Contas de serviço** → "Gerar nova chave privada" → baixa um .json (guarde; é segredo).

### 2. GitHub
1. Crie o repositório `estoque-ml-painel` (**público** = minutos ilimitados; **privado** = ~2000 min/mês, então use cron `7 */3 * * *`).
   O repositório não contém nenhum segredo — eles ficam em Secrets.
2. Suba esta pasta (`git init && git add . && git commit -m inicial && git branch -M main && git remote add origin ... && git push -u origin main`).
3. Settings → Secrets and variables → Actions → New secret, crie:
   `ML_APP_ID`, `ML_SECRET_KEY`, `ML_USER_ID`, `ML_REFRESH_TOKEN` (o refresh token atual do ML), `TINY_TOKEN`,
   `FIREBASE_SERVICE_ACCOUNT` (cole o **conteúdo inteiro** do .json do passo 1.6).

### 3. Deploy
```
npm i -g firebase-tools
firebase login
firebase deploy --only hosting,firestore:rules
```
A URL final é `https://SEU-PROJETO.web.app`.

### 4. Primeira execução
GitHub → aba **Actions** → "Atualizar estoque" → **Run workflow**. Em ~5 min abra a URL, entre com o Google.

### 5. Botão "Atualizar agora"
GitHub → Settings → Developer settings → Fine-grained tokens → só o repo `estoque-ml-painel`, permissão **Actions: Read and write**.
Cole o token quando o app pedir (fica só no seu navegador). No Safari: Arquivo → Adicionar ao Dock.

## Cuidados
- **Token do ML é de uso único e rotaciona.** A nuvem passa a guardar o token mais recente no Firestore (`secrets/ml`, inacessível pelo app).
  Se você continuar rodando o Colab ou o app de menu com o MESMO app do ML, eles podem invalidar o token da nuvem (e vice-versa).
  Se o job falhar com erro de token, gere um novo refresh token e atualize o secret `ML_REFRESH_TOKEN` **e apague o documento `secrets/ml`** no Firestore.
- O cron do GitHub pode atrasar alguns minutos. Se os dados passarem de 3 h, o app mostra um aviso vermelho.
- Em repositório público, o GitHub desativa o agendamento após 60 dias sem commits no repo — é só reativar na aba Actions.
- Se o job falhar (ML fora, Tiny fora), o app mantém os dados da última execução boa e mostra o erro.
- Metas (prazos, 60 dias de cobertura) ficam em `job/config.json`.
