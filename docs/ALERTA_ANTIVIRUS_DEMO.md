# Alerta antivírus da demonstração — 27/10/2026

Foi reportado pelo utilizador um bloqueio pelo **Trend Micro Security Agent**, com
identificação **TROJ.WIN32.TRX.XXPE50FFF110**, antes de a aplicação abrir. O nome exato
do ficheiro, o seu SHA-256, a versão do agente e o parecer do fornecedor ainda não
foram confirmados. Este é um relato, não o resultado de uma análise antimalware independente.

Não se concluiu que é um falso positivo. Os testes funcionais anteriores não provam
que um binário está livre de malware. O utilizador deve manter o ficheiro bloqueado/
em quarentena, sem o restaurar, renomear para o executar ou excluir da proteção.
Se o computador for gerido pela empresa, a avaliação cabe ao responsável pelo Agent.

## Medidas adotadas

- Suspensa a construção/distribuição automática de novos executáveis PyInstaller.
- O construtor recusa `--standalone` explicitamente enquanto o alerta está por esclarecer.
- Retirada a recomendação dos artefactos executáveis anteriores. Não são apagados
  automaticamente, preservando a referência da construção para investigação.
- Os scripts atuais não procuram nem executam `IBERBAG_DEMO.exe`, mesmo se estiver
  numa pasta antiga. O pacote de código deve ser extraído para uma pasta nova.
- Preparada distribuição do código legível, sem `.exe`, `.dll`, `.pyd`, `_runtime`
  ou instalador de binários próprio, com lista explícita de ficheiros e hashes SHA-256.
- A alternativa usa Python oficial instalado pelo utilizador, mantendo o antivírus
  ativo; bibliotecas só na pasta local. Isto não certifica o binário anterior nem
  é uma instrução para contornar políticas de segurança.

A investigação continua dependente do nome/caminho/hash do ficheiro efetivamente
assinalado e de revisão pelo responsável/Trend Micro. Não se submetem automaticamente
binários, código privado, ficheiros da empresa ou relatórios a serviços externos.

## Alternativa disponível para revisão

Consultar [DEMO_WINDOWS.md](DEMO_WINDOWS.md) e o artefacto **IBERBAG-DEMO-Codigo-Windows**
no workflow DEMO Windows. Testes com Python oficial verificam funcionamento e isolamento;
não são uma análise de segurança antimalware. Os hashes identificam o conteúdo do
pacote, mas não provam a sua segurança. Não há deploy, merge ou alteração de stock real.
