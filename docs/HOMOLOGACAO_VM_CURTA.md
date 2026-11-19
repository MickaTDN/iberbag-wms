# PR #41 — roteiro curto Windows 11 / VirtualBox

**Estado: ensaio físico pendente. Não produção; Draft.** Usar só dados fictícios.
Os testes SCM do runner Windows não são este ensaio nem instalação completa PG/TLS.

## A. Fornecer PostgreSQL sem mirrors nem binários GVA

1. Técnico descarrega o ZIP Windows x64 de PostgreSQL 17 do canal oficial EDB indicado
   em `SCM_E_CREDENCIAIS_WINDOWS.md`, por um navegador autorizado se o download automático
   devolver 403. Obter SHA-256 por canal independente do ficheiro local e registar a
   versão exata/URL. Sem prova de origem/hash, parar; não calcular hash local e apresentá-lo
   como prova independente, não copiar a instalação GVA, não renomear conteúdo contraditório.
2. No pacote, duplo clique em INSTALAR_DDN_ENSAIO.bat → opção **7** e preencher
   ZIP/URL/versão/hash aprovados; destino vazio faz só prévia. Alternativa técnica: executar
   com Python 3.12 o importador **windows/import_postgres.py** com
   `--archive <ZIP oficial> --origin <URL oficial HTTPS> --version <17.patch real>
   --sha256 <hash independente>`. Sem `--output`, só analisa e apresenta totais.
3. Repetir com `--output <pasta NOVA de staging>`. Escrever a confirmação completa
   apresentada. Fonte preservada; todos os ficheiros são verificados, ZIP/path/PE x64
   validados e extração reconciliada. Não executa binários nem instala serviços.
   A confirmação é uma **atestação técnica de proveniência**, não assinatura do fornecedor.
4. Resultado: `components/postgres` e `DDN_POSTGRES_APPROVED.json`, com hashes integrais,
   origem, versão, digest original e aprovação UTC. Copiar para a implantação DDN
   protegida e aplicar ACL administrativas antes do provisionador. Tentativa incompleta
   é preservada; não sobrescrever nem apagar dados para repetir. Binários importados
   nunca são acrescentados a Git/PR/artefactos públicos.

## B. Cluster — sequência assistida mínima, com pontos de paragem

Usar o procedimento existente `DDN_PROVISIONAMENTO_ASSISTIDO.md`, secção 2, sem inventar
senhas/comandos de fornecedor. Checklist obrigatória antes de GESTAO_DDN → 7:

- Nova pasta `ProgramData\DDNWMS-Ensaio\postgres`, vazia antes de initdb; novos roles
  setup/owner/runtime, sem reutilizar credenciais ou servidor GVA. Ferramentas sempre
  pelo caminho DDN aprovado; limpar `PG*` só na sessão, nunca nas variáveis globais.
- initdb pede senha bootstrap através de ficheiro temporário privado. Loopback 127.0.0.1,
  porta **55433**, SCRAM; base **ddn_wms_ensaio_test**. Se porta ocupada, parar/escolher
  alternativa aprovada, nunca reconfigurar GVA 55432/8765/8443.
- Preparar esquema pela opção 8; administrador fictício pela 9; grants runtime pela
  ferramenta existente. Rever `validate_runtime` e head **ddn_0022**. Não executar
  migrações sobre base existente sem backup/restauro reconciliado.
- Parar cluster DDN, conferir ausência de postmaster.pid e registar system_identifier
  real por pg_controldata em DDN_CLUSTER.json. Aplicar owner/ACL administrativas.
- Certificado/chave DDN próprios, DNS e confiança aprovados; DDN_HTTPS.json correto.
  Certificados GVA/mTLS/gateway permanecem intocados. A chave nunca vai para clientes.

**Limitação explícita:** criação de cluster/roles e certificados ainda exige técnico;
este PR não adiciona um initdb/SQL automático sobre recursos existentes. A lista acima
reduz a execução a pontos verificáveis, sem eliminar controlos de segurança.

## C. VM limpa, depois VM com GVA

| Passo | Execução | Esperado / prova |
|---|---|---|
| 1 | Snapshot pré-ensaio, anotar commit/ZIP/hash, SO, hora e contas fictícias | Snapshot identificável e nenhuma instalação empresarial modificada |
| 2 | Na VM GVA, inventário privado antes: serviços/contas/paths/digests, portas, configuração e referências de backup; confirmar GVA funcional | Não publicar linhas de comando/segredos; prova inicial |
| 3 | Extrair ZIP, instalar dependências; importar distribuição oficial e preparar B | Hashes reconciliados; nenhuma cópia GVA; conflitos bloqueiam |
| 4 | Implantação protegida em Program Files; GESTAO_DDN → 7 com UAC explícito | Três serviços DDN, SIDs distintos, DPAPI, Auto; sem LocalSystem; diário preservado em falha |
| 5 | Estado/health, login fictício, HTTPS confiável :8447 e app interna loopback :8100 | Certificado válido; PG :55433 local; portas GVA inalteradas; sem exceção TLS |
| 6 | Repetir instalação, parar/iniciar/reiniciar DDN e executar ambas as apps | UNCHANGED quando completo; nenhuma senha/serviço duplicado; GVA permanece funcional |
| 7 | Reiniciar Windows, sem terminal ou senha interativa | Serviços DDN automáticos, health e login; GVA igualmente operacional. Registar tempos/estados reais |
| 8 | Cópia cifrada, restauro numa base DDN separada vazia e reconciliação | Livros/saldos/IDs/PDFs iguais, GVA não alterada |
| 9 | Falha controlada só DDN; rever diário e recuperar com técnico/snapshot | Sem limpeza destrutiva ou adoção de serviços; qualquer parcial fica visível e exige prova |
| 10 | Comparar inventário GVA antes/depois, impressão A4 e dois clientes/Android | Sem diferenças GVA não autorizadas; rubricar APROVADO/FALHOU/NÃO TESTADO por cenário |

DPAPI LocalMachine sobrevive ao reboot da mesma instalação Windows, mas não é portátil
para outra máquina. Backup PostgreSQL não inclui essa chave de máquina. Noutra VM/Windows,
reprovisionar a credencial por entrada segura com manutenção aprovada; não exportar chaves
LSA nem guardar password em claro. Snapshot consistente e backup/restauro verificado são
complementares. Nunca recuperar automaticamente sobre a origem ou sobre GVA.

Perante alerta AV, política que bloqueie scripts, hash/certificado divergente ou recurso
GVA afetado: parar, guardar prova privada e pedir revisão técnica; sem bypass/exclusões.
