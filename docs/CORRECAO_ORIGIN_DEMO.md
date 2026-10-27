# DEMO Windows — confirmação de formulários (27/10/2026)

## Causa reproduzida

Foi reportado HTTP 403, `{"detail":"Origem externa recusada."}`, ao enviar
formulários em Chrome e Edge no endereço local `http://127.0.0.1:54237/dispatches`.
Não foram recolhidos cabeçalhos nem dados do computador da empresa. O problema foi
reproduzido num navegador Chromium real, com uma base exclusivamente fictícia.

O middleware DEMO emitia `Referrer-Policy: no-referrer`. Ao submeter um formulário
POST, Chromium aplica essa política também à origem: envia `Origin: null` e omite
`Referer`, mesmo quando o formulário vem da própria aplicação local. O middleware
aceitava apenas a origem local exata ou um cabeçalho Origin ausente e recusava `null`.
Os testes HTTP anteriores não reproduziam a política de um navegador: omitiam Origin.

| Cabeçalho | Com a política anterior | Com a correção |
| --- | --- | --- |
| Host | `127.0.0.1:<porta>` | `127.0.0.1:<porta>` |
| Origin do formulário POST | `null` | `http://127.0.0.1:<porta>` |
| Referer | Ausente | Página local de onde saiu o formulário |
| Resultado com CSRF válido | 403 | Revisão 200 / confirmação 303 e consulta 200 |

## Correção e proteções

Alteração restrita a `iberbag_demo/app.py`: `Referrer-Policy: same-origin` conserva
os cabeçalhos locais dos formulários e não envia Referer a destinos externos.
Não foram alterados a aplicação operacional, os serviços PostgreSQL, os modelos ou
as migrações. Não são necessárias alterações no Trend Micro, no navegador ou na firewall.

- O socket continua limitado a **127.0.0.1**, sem opções de rede/proxy.
- O cliente e Host têm de ser locais, com a porta exata da instância.
- Origin externo, `null`, inválido ou repetido continua recusado, mesmo com token válido.
- Referer, quando presente, tem de pertencer à mesma origem local. Não substitui
  nem valida um Origin externo/null. Credenciais, cabeçalhos repetidos e URLs
  ambíguos são recusados.
- Clientes técnicos podem omitir Origin/Referer; as mutações da interface exigem
  sempre o CSRF da sessão. Não foi criada exceção de confirmação nem CORS permissivo.
- Cookies HttpOnly/SameSite, permissões e controlo de encerramento são preservados.

Pedidos recusados emitem apenas classificações na janela do servidor, por exemplo:

```text
Pedido DEMO recusado: motivo=origem; Host=local; Origin=null; Referer=ausente
```

Não são registados valores brutos de cabeçalhos, URLs, caminhos, parâmetros, corpo,
cookies, identificadores de sessão, token CSRF ou segredo de controlo. Estes
diagnósticos não escrevem ficheiros de log. O ensaio de navegador também regista
somente classificações e códigos HTTP; não grava HAR, traces ou perfis pessoais.

## Validação automatizada

`demo_tests/test_origins.py` cobre formulários locais, CSRF, origens externas/null,
Referer externo/ambíguo, cabeçalhos repetidos, Host/porta e omissão segura de segredos
nos diagnósticos. Confirma entradas, saídas, Mercadona, arrumação, transferências,
inventário/ajustes, idempotência e leitura/reimpressão dos PDFs.

`scripts/test_demo_browser.py` utiliza formulários HTML nativos, preenchidos e
enviados por um navegador real. Reproduz a falha com a política anterior e depois
verifica os fluxos corrigidos, os valores classificados dos três cabeçalhos e a
recusa de formulário externo/CSRF ausente. Um documento externo de ataque é criado
apenas em memória pelo teste, sem contactar um site externo.

O workflow **DEMO Windows** testa o pacote em código numa pasta com espaços e
acentos, usando Python oficial, **Google Chrome e Microsoft Edge instalados no
runner Windows Server**. Playwright é dependência exclusiva de testes do workflow;
não entra no ZIP nem nas dependências da demonstração. O executável PyInstaller
continua suspenso.

Comandos de desenvolvimento (não necessários para o utilizador Windows):

```text
python -m pytest -q demo_tests
python -m pytest -q
python scripts/test_postgres.py
python scripts/test_demo_browser.py --channel chrome
python scripts/test_demo_browser.py --channel msedge
```

### Resultados desta revisão

- **74 testes DEMO aprovados**; incluem 31 novos casos de cabeçalhos/fluxos.
- Suite completa com PostgreSQL 17 descartável: **250 aprovados** (176 anteriores + 74 DEMO).
- Execução SQLite: **105 aprovados, 145 omitidos** por exigirem PostgreSQL/Docker.
- Chromium real: falha anterior reproduzida e **13 formulários nativos locais
  aprovados**, CSRF ausente e origem externa recusados; repetido com o código do ZIP.
- `pip check`, manifesto/conteúdo do ZIP e `git diff --check` sem erros.
- O [workflow desta correção](https://github.com/MickaTDN/iberbag-wms/actions/runs/40555970571)
  não iniciou os jobs: GitHub indicou falha de pagamentos/limite da conta.
  **Chrome/Edge Windows Server e Windows 11 permanecem pendentes nesta revisão.**

Sem artefacto desse workflow e com o upload de Release recusado pelo ambiente,
o [ZIP em código e respetivo SHA-256](../downloads/README.md) foram incluídos na
branch para descarregar e rever. O rascunho vazio de Release foi removido.
O ZIP foi produzido localmente; não é apresentado como pacote validado em Windows.

## Instalar o ZIP corrigido e testar no escritório

1. Encerrar a DEMO antiga pelo seu `PARAR_DEMO.bat` e fechar todos os seus separadores
   no Chrome e no Edge. Uma página antiga conserva a política antiga até ser recarregada.
2. Enquanto a CI estiver bloqueada, descarregar o [ZIP desta correção](../downloads/README.md).
   Depois de a conta permitir os jobs, poderá usar **IBERBAG-DEMO-Codigo-Windows**
   de uma execução verde atualizada do workflow [DEMO Windows](https://github.com/MickaTDN/iberbag-wms/actions/workflows/demo-windows.yml),
   na branch `codex/windows-local-demo` do [PR #8](https://github.com/MickaTDN/iberbag-wms/pull/8).
3. Escolher **Extrair Tudo** para uma pasta nova. Não substituir a instalação
   operacional, copiar bases existentes ou restaurar ficheiros em quarentena.
4. Com Python 3.12 oficial já instalado, executar `INSTALAR_DEMO.bat` nessa pasta
   nova e depois `INICIAR_DEMO.bat`. Usar o novo endereço 127.0.0.1 apresentado;
   a porta pode ser diferente de 54237.
5. Saídas: lote `LOTE-DEMO-A`, 12 paletes, cliente fictício; guardar, rever e
   confirmar. Ver desconto fictício imediato, pendente e PDF; reimprimir.
6. Mercadona: criar preparação com `LOTE-DEMO-C`, uma palete e destino fictício;
   rever, confirmar e abrir PDF.
7. Entradas: carga fictícia nova, data explícita, `ART-DEMO-SACO / LOTE-DEMO-B / 4`;
   rever, confirmar e abrir PDF. Arrumar a palete excedente em A104.
8. Transferir A104 → A105; criar inventário parcial A105 A106, contar ambas vazias,
   submeter e aprovar com motivo fictício. Confirmar que submissão não altera stock
   e que só a aprovação administrativa aplica o ajuste simulado.
9. Repetir no outro navegador, repondo dados fictícios se necessário.

Se aparecer `Origin: null` depois de fechar/reabrir todas as páginas do pacote
corrigido, conservar apenas a linha de classificações acima para diagnóstico.
Uma política/extensão empresarial pode exigir análise pelo responsável informático;
não aceitar origens desconhecidas nem desativar proteções para contornar o erro.

O teste num runner Windows Server não substitui a aceitação no computador Windows 11
com as políticas da empresa. Nenhuma instalação desse computador foi modificada
por este trabalho.
