# BaltigoFlix

Landing page oficial da BaltigoFlix, reconstruída como um projeto estático, responsivo e independente.

## O que mudou

A versão anterior era um HTML exportado de WordPress/Elementor e carregava arquivos, metadados e configurações de outro domínio. Esta reconstrução removeu essas dependências e criou uma implementação própria.

- HTML semântico e acessível;
- identidade visual vetorial própria;
- CSS responsivo sem framework;
- JavaScript pequeno, sem bibliotecas externas;
- sem WordPress, Elementor, jQuery, pixels ou fontes de terceiros;
- SEO, sitemap, robots, manifesto e página 404;
- Termos de Uso e Política de Privacidade;
- preservação dos quatro checkouts afiliados;
- propagação segura de parâmetros UTM para os checkouts;
- suporte a `prefers-reduced-motion`;
- funcionamento direto no GitHub Pages.

## Estrutura

```text
.
├── index.html
├── 404.html
├── termos-de-uso.html
├── politica-de-privacidade.html
├── manifest.webmanifest
├── robots.txt
├── sitemap.xml
├── .nojekyll
└── assets
    ├── css
    │   └── styles.css
    ├── images
    │   ├── brand.svg
    │   ├── favicon.svg
    │   └── social-cover.svg
    └── js
        └── main.js
```

## Editar planos e checkouts

Os cards ficam na seção `#planos` do `index.html`. Os links preservados são:

| Plano | Preço exibido | Checkout |
| --- | ---: | --- |
| Mensal | R$ 19,90 | `https://pay.cakto.com.br/9snqsP3?affiliate=gWviKfhb` |
| Trimestral | R$ 49,90 | `https://pay.cakto.com.br/35znaim?affiliate=gWviKfhb` |
| Semestral | R$ 79,90 | `https://pay.cakto.com.br/3eehsw8?affiliate=gWviKfhb` |
| Anual | R$ 119,90 | `https://pay.cakto.com.br/u8e2fgg?affiliate=gWviKfhb` |

Antes de divulgar, confirme no painel da Cakto se preços, parcelamentos, quantidade de telas e URLs permanecem vigentes.

## Domínio próprio

A versão atual usa como referência:

```text
https://qgbaltigo.github.io/MarcosBaltigoFlix/
```

Ao conectar um domínio próprio:

1. crie um arquivo `CNAME` com o domínio;
2. altere os endereços absolutos do `index.html`, `robots.txt`, `sitemap.xml`, `termos-de-uso.html` e `politica-de-privacidade.html`;
3. gere uma imagem PNG de 1200 × 630 para compartilhamento social, caso a rede não aceite o SVG atual;
4. confira HTTPS e redirecionamento entre `www` e domínio raiz.

## Contato

O site usa `suporte@baltigoflix.com`. Confirme se a caixa está ativa antes da publicação comercial. Caso o endereço correto seja outro, substitua-o no `index.html`, nos Termos e na Política de Privacidade.

## Desenvolvimento local

Não há etapa de compilação. Abra `index.html` em um servidor HTTP local. Exemplos:

```bash
python -m http.server 8000
```

ou

```bash
npx serve .
```

Depois, acesse `http://localhost:8000`.

## Publicação

O projeto foi preparado para GitHub Pages a partir da raiz da branch `main`.

## Segurança e conformidade

A página não distribui mídia nem armazena dados de pagamento. A oferta comercial e qualquer serviço associado devem possuir as autorizações, licenças e informações legais aplicáveis. Não use marcas ou conteúdos de terceiros sem autorização.

A branch `backup-fireplay-export-2026-09-03` preserva a versão antiga somente para recuperação e comparação. Ela não deve ser usada em produção.

## Direitos

Código, identidade e textos desta reconstrução: © 2026 BaltigoFlix. Todos os direitos reservados. Nenhuma licença pública é concedida por este repositório.
