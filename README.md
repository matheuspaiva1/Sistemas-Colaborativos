# Sistemas Colaborativos: indicadores 3C em projetos de software livre

Mineração de dados do GitHub para estudar como os mecanismos de comunicação e
coordenação apoiam a colaboração entre contribuidores em projetos de software
livre brasileiros. A análise usa o Modelo 3C de Colaboração (comunicação,
coordenação, cooperação e percepção) e associa cada mecanismo do GitHub a uma
dimensão:

| Dimensão     | Mecanismos do GitHub                                                        |
|--------------|-----------------------------------------------------------------------------|
| Comunicação  | comentários em issues e PRs                                                 |
| Coordenação  | atribuições, rótulos, metas, pedidos de revisão, vereditos, fechamento e integração |
| Cooperação   | PRs, commits, comentários em linha e sugestões de código                    |
| Percepção    | menções e referências                                                       |

## Casos estudados

Janela comum de 48 meses, de **2022-10-29 a 2026-10-29**:

- [agenda-tech-brasil/agenda-tech-brasil](https://github.com/agenda-tech-brasil/agenda-tech-brasil)
- [okfn-brasil/querido-diario](https://github.com/okfn-brasil/querido-diario)
- [filipedeschamps/tabnews.com.br](https://github.com/filipedeschamps/tabnews.com.br)
- [BrasilAPI/BrasilAPI](https://github.com/BrasilAPI/BrasilAPI)
- [brazilian-utils/python](https://github.com/brazilian-utils/python)

Os casos saíram de uma busca sistemática no GitHub (401 repositórios) filtrada
por critérios de seleção (C1–C6). O registro de cada etapa está em `triagem/`.

## Etapas

| Script                | O que faz                                                                 | Precisa de rede | Saída                 |
|-----------------------|---------------------------------------------------------------------------|:---------------:|-----------------------|
| `github_api.py`       | Cliente REST/GraphQL usado pelos outros scripts (paginação, limites, retentativa) | —        | —                     |
| `triagem.py`          | Busca sistemática e aplicação dos critérios C1–C5                         | sim             | `triagem/`            |
| `app.py`              | Coleta issues, PRs, revisões, comentários, eventos e commits dos casos    | sim             | `dados_github/`       |
| `preprocessamento.py` | Marca bots, semestres e autoria efetiva; pseudonimiza os participantes   | não             | `dados_processados/`  |
| `indicadores.py`      | Calcula os indicadores 3C por caso e semestre                             | não             | `indicadores/`        |
| `graficos.py`         | Gera as figuras em PDF a partir dos indicadores                           | não             | PDFs (pasta em `SAIDA`) |

## Como rodar

Requer Python 3.8+.

```bash
pip install -r requirements.txt
```

A triagem e a coleta usam a API GraphQL do GitHub, que exige um token pessoal
(escopo de leitura de repositórios públicos). Gere um em
<https://github.com/settings/tokens> e defina a variável de ambiente:

```bash
# Linux/Mac
export GITHUB_TOKEN=seu_token
```

```powershell
# Windows PowerShell
$env:GITHUB_TOKEN="seu_token"
```

Depois rode as etapas em ordem:

```bash
python triagem.py --buscar     # opcional: refaz a seleção dos casos
python app.py                  # coleta (demorada; pode ser retomada)
python preprocessamento.py
python indicadores.py
python graficos.py
```

A coleta guarda as respostas brutas da API em `dados_github/bruto/`. Se for
interrompida, basta rodar `app.py` de novo para continuar de onde parou. A
janela, os repositórios e as demais opções ficam em `CONFIG`, no início de
`app.py`.

## Dados e privacidade

- `triagem/` e `indicadores/` têm só contagens e proporções e estão no repositório.
- `dados_github/` e `dados_processados/` **não estão no repositório**: contêm
  logins e textos de pessoas. Para reproduzir a análise é preciso rodar a coleta.
- No pré-processamento, cada participante recebe um código (P0001, P0002, …). O
  mapa login → código fica em `dados_github/privado/` e não deve ser compartilhado.
- Nunca grave o token em arquivo; use só a variável de ambiente.
