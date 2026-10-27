# ZIP de demonstração em código — correção dos formulários

[Descarregar IBERBAG-DEMO-Codigo-Windows-b579ea8.zip](https://github.com/MickaTDN/iberbag-wms/raw/refs/heads/codex/windows-local-demo/downloads/IBERBAG-DEMO-Codigo-Windows-b579ea8.zip)

É necessário iniciar sessão no GitHub com acesso ao repositório privado. Se o link
abrir a página do ficheiro, selecionar **Download raw file** / **Descarregar**.

Este ZIP é disponibilizado nesta branch para revisão porque **GitHub Actions não
iniciou os jobs por faturação/limite da conta** e o ambiente recusou o envio do
ficheiro para `uploads.github.com` da Release de teste. O rascunho vazio foi removido.
Não é um artefacto Windows aprovado pela CI nem uma distribuição operacional.

O conteúdo foi gerado por `scripts/build_demo.py` a partir do commit **b579ea8**,
por lista explícita. Contém apenas o código/scripts/recursos da DEMO e manifesto.
Não contém executável próprio, PyInstaller, VBA, Excel, dados reais, bases existentes,
configurações empresariais, credenciais ou dependências PostgreSQL.

SHA-256 do ZIP:

```text
640db142c48f711009eb495e9a8b911b9a922960a251a5ca51d2c2a1516e54f9
```

Testes locais aprovados: 74 DEMO, 250 integrados com PostgreSQL 17, 105 aprovados e
145 omitidos na execução SQLite. Chromium real reproduz a falha antiga e confirma
13 formulários locais corrigidos, incluindo o pacote, recusando origem externa e
CSRF ausente. Chrome/Edge no runner Windows e Windows 11 da empresa estão **pendentes
nesta correção**; os jobs não chegaram a executar qualquer passo.

Encerrar a DEMO antiga, fechar todos os seus separadores e extrair para uma pasta
nova. Com Python 3.12 oficial, executar **INSTALAR_DEMO.bat**, depois
**INICIAR_DEMO.bat**. Não é necessário modificar antivírus, firewall ou navegador.
Seguir o LEIA-ME do pacote e o [guia da correção](../docs/CORRECAO_ORIGIN_DEMO.md).
