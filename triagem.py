#!/usr/bin/env python3
"""
Triagem de repositorios candidatos segundo os criterios de selecao (Tabela de
criterios da metodologia), na mesma janela temporal do coletor (app.py).

    C1. Publico e original: nao e fork nem esta arquivado.
    C2. Atividade continua: commits nos 12 meses anteriores a data de
        referencia e pelo menos dois anos de historico.
    C3. Volume de colaboracao: >= 100 PRs integrados na janela.
    C4. Diversidade: >= 20 participantes humanos distintos na janela
        (autores de issues ou de PRs, sem contas de bot).
    C5. Uso dos tres mecanismos: issues e PRs na janela e pelo menos 25% dos
        PRs integrados (e no minimo 30) com revisao formal de alguem que nao
        e o autor nem bot.
    C6. Contexto nacional: verificacao manual; a planilha traz descricao,
        topicos e idioma para apoiar a decisao.

Gera triagem/criterios_<inicio>_<fim>.csv (sem dados pessoais, so contagens).

BUSCA SISTEMATICA (--buscar)
    Em vez de uma lista manual, os candidatos vem de buscas na API do GitHub
    (BUSCAS abaixo), em duas familias de termos:
        F1. referencia ao Brasil no nome, descricao, README ou topicos;
        F2. termos de contribuicao em portugues no README.
    Todas as buscas exigem: nao ser fork nem arquivado, ter sido criado ate o
    inicio da janela, push nos 12 meses anteriores a data de referencia e pelo
    menos MIN_ESTRELAS estrelas. Os resultados passam por um pre-filtro barato
    (>= 100 PRs integrados em todo o historico, condicao necessaria para C3) e
    depois pela triagem completa. Gera tambem triagem/busca_<inicio>_<fim>.csv
    com todos os repositorios encontrados e as buscas que os encontraram.

COMO USAR
    Defina GITHUB_TOKEN (ver app.py) e rode:
        python triagem.py --buscar          (busca sistematica)
        python triagem.py                   (lista CANDIDATOS)
        python triagem.py dono/repo1 ...    (repositorios especificos)
"""

import csv
import sys
import time
from datetime import date
from pathlib import Path

import requests

from app import CONFIG, janela
from github_api import (API, GitHubClient, ator_graphql, iso, ler_token,
                        meses_antes)


CANDIDATOS = CONFIG["repos"] + [
    # reservas
    "turicas/brasil.io",
    "dunossauro/fastapi-do-zero",
    "levxyca/diciotech",
    "frontendbr/vagas",
    "Universidade-Livre/ciencia-da-computacao",
]

BUSCAS = [
    # (familia, termo da busca)
    ("F1", "topic:brasil"),
    ("F1", "topic:brazil"),
    ("F1", "topic:brazilian"),
    ("F1", "brasil in:name,description,readme"),
    ("F1", "brasileiro in:description,readme"),
    ("F1", "brasileiros in:description,readme"),
    ("F1", "brasileira in:description,readme"),
    ("F1", "brasileiras in:description,readme"),
    ("F2", "contribuir in:readme"),
    ("F2", "contribuição in:readme"),
    ("F2", "contribuições in:readme"),
    ("F2", "contribuindo in:readme"),
]

# Corte de popularidade das buscas: afasta repositorios pessoais e de teste.
MIN_ESTRELAS = 100

LIMITES = {
    "prs_integrados": 100,   # C3
    "participantes": 20,     # C4
    "pct_revisados": 25,     # C5: % dos PRs integrados com revisao
    "min_revisados": 30,     # C5: minimo absoluto de PRs integrados revisados
    "anos_historico": 2,     # C2
    "meses_atividade": 12,   # C2
}

CONSULTA_PRS = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: 100, after: $cursor,
                 orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        createdAt mergedAt
        author { login __typename }
        reviews(first: 10) { nodes { author { login __typename } } }
      }
    }
  }
}
"""

CONSULTA_ISSUES = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $cursor,
           orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { createdAt author { login __typename } }
    }
  }
}
"""


def _listar_na_janela(client, consulta, conexao, repo, ini_iso, fim_iso):
    dono, nome = repo.split("/")
    nos = client.paginar_graphql(
        consulta, {"owner": dono, "name": nome}, ("repository", conexao),
        parar=lambda pagina: bool(pagina) and pagina[-1]["createdAt"] < ini_iso,
    )
    return [n for n in nos if ini_iso <= n["createdAt"] < fim_iso]


def _humano(ator):
    login, tipo = ator_graphql(ator)
    return login if login and tipo != "Bot" else ""


def avaliar(client, repo):
    inicio, fim = janela(repo)
    ini_iso, fim_iso = iso(inicio), iso(date.fromordinal(fim.toordinal() + 1))
    print(f"  {repo} ({inicio} a {fim})...")

    meta = client.get(f"{API}/repos/{repo}").json()
    repo = meta["full_name"]  # segue renomeacoes

    # C2: houve commit nos ultimos 12 meses? ha pelo menos 2 anos de historico?
    desde_atividade = iso(meses_antes(fim, LIMITES["meses_atividade"]))
    commits = client.get(f"{API}/repos/{repo}/commits",
                         {"since": desde_atividade, "until": fim_iso, "per_page": 1}).json()
    limite_historico = iso(meses_antes(fim, 12 * LIMITES["anos_historico"]))
    tem_historico = meta["created_at"] <= limite_historico

    prs = _listar_na_janela(client, CONSULTA_PRS, "pullRequests", repo, ini_iso, fim_iso)
    issues = (_listar_na_janela(client, CONSULTA_ISSUES, "issues", repo, ini_iso, fim_iso)
              if meta.get("has_issues") else [])

    integrados = [p for p in prs if p["mergedAt"]]
    integrados_humanos = [p for p in integrados if _humano(p["author"])]
    revisados = []
    for p in integrados:
        autor = _humano(p["author"])
        revisores = {_humano(r["author"]) for r in p["reviews"]["nodes"]} - {"", autor}
        if revisores:
            revisados.append(p)

    participantes = ({_humano(p["author"]) for p in prs}
                     | {_humano(i["author"]) for i in issues}) - {""}

    c1 = not meta["fork"] and not meta["archived"] and not meta["private"]
    c2 = bool(commits) and tem_historico
    c3 = len(integrados) >= LIMITES["prs_integrados"]
    c4 = len(participantes) >= LIMITES["participantes"]
    pct_revisados = 100 * len(revisados) / len(integrados) if integrados else 0.0
    c5 = (bool(issues) and bool(prs)
          and pct_revisados >= LIMITES["pct_revisados"]
          and len(revisados) >= LIMITES["min_revisados"])

    return {
        "repo": repo,
        "inicio_janela": inicio.isoformat(),
        "fim_janela": fim.isoformat(),
        "C1_publico_original": c1,
        "fork": meta["fork"],
        "arquivado": meta["archived"],
        "C2_atividade": c2,
        "criado_em": meta["created_at"][:10],
        "ultimo_push": meta["pushed_at"][:10],
        "commit_ultimos_12_meses": bool(commits),
        "C3_volume": c3,
        "prs_na_janela": len(prs),
        "prs_integrados": len(integrados),
        "prs_integrados_autor_humano": len(integrados_humanos),
        "C4_diversidade": c4,
        "participantes_humanos": len(participantes),
        "C5_mecanismos": c5,
        "issues_na_janela": len(issues),
        "prs_integrados_com_revisao": len(revisados),
        "pct_integrados_com_revisao": round(pct_revisados, 1),
        "C6_contexto_nacional": "verificar",
        "descricao": meta.get("description") or "",
        "topicos": "|".join(meta.get("topics") or []),
        "linguagem": meta.get("language") or "",
        "licenca": (meta.get("license") or {}).get("spdx_id") or "",
        "estrelas": meta.get("stargazers_count", 0),
        "atende_C1_a_C5": all((c1, c2, c3, c4, c5)),
    }


def buscar_candidatos(client):
    """Executa as BUSCAS e devolve {repo: dados}, unindo os resultados."""
    inicio, fim = janela()
    filtros = (f"fork:false archived:false created:<={inicio.isoformat()} "
               f"pushed:>={meses_antes(fim, LIMITES['meses_atividade']).isoformat()} "
               f"stars:>={MIN_ESTRELAS}")
    encontrados = {}
    for familia, termo in BUSCAS:
        consulta = f"{termo} {filtros}"
        total = None
        for pagina in range(1, 11):  # a busca devolve no maximo 1000 itens
            dados = client.get(f"{API}/search/repositories",
                               {"q": consulta, "per_page": 100, "page": pagina}).json()
            total = dados["total_count"]
            if dados.get("incomplete_results"):
                print(f"    AVISO: resultados incompletos em '{termo}'")
            for it in dados["items"]:
                r = encontrados.setdefault(it["full_name"], {
                    "repo": it["full_name"],
                    "familias": set(),
                    "buscas": [],
                    "estrelas": it["stargazers_count"],
                    "criado_em": it["created_at"][:10],
                    "ultimo_push": it["pushed_at"][:10],
                    "descricao": it.get("description") or "",
                })
                r["familias"].add(familia)
                r["buscas"].append(termo)
            if len(dados["items"]) < 100:
                break
            time.sleep(2)  # limite da busca: 30 requisicoes por minuto
        print(f"  {termo:<40} {total:>5} resultados")
        time.sleep(2)
    return encontrados


def prefiltrar(client, repos):
    """Total de PRs integrados em todo o historico, em lotes de 20 por consulta."""
    totais = {}
    for i in range(0, len(repos), 20):
        lote = repos[i:i + 20]
        partes = []
        for j, repo in enumerate(lote):
            dono, nome = repo.split("/")
            partes.append(f'r{j}: repository(owner: "{dono}", name: "{nome}") '
                          "{ pullRequests(states: MERGED) { totalCount } }")
        dados = client.graphql("query {" + "\n".join(partes) + "}", {})
        for j, repo in enumerate(lote):
            no = dados.get(f"r{j}")
            totais[repo] = no["pullRequests"]["totalCount"] if no else 0
    return totais


def salvar(arquivo, linhas):
    with open(arquivo, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        writer.writeheader()
        writer.writerows(linhas)


def main():
    token = ler_token()
    if not token:
        print("ERRO: a triagem usa a API GraphQL, que exige GITHUB_TOKEN.")
        sys.exit(1)
    client = GitHubClient(token)
    pasta = Path("triagem")
    pasta.mkdir(exist_ok=True)
    inicio, fim = janela()
    sufixo = f"{inicio.isoformat()}_{fim.isoformat()}"

    buscar = "--buscar" in sys.argv
    if buscar:
        print("Buscando candidatos...")
        encontrados = buscar_candidatos(client)
        print(f"{len(encontrados)} repositorios distintos encontrados. Pre-filtrando...")
        totais = prefiltrar(client, sorted(encontrados))
        for repo, r in encontrados.items():
            r["familias"] = "|".join(sorted(r["familias"]))
            r["buscas"] = "|".join(r["buscas"])
            r["prs_integrados_historico"] = totais.get(repo, 0)
            r["passou_prefiltro"] = totais.get(repo, 0) >= LIMITES["prs_integrados"]
        salvar(pasta / f"busca_{sufixo}.csv",
               sorted(encontrados.values(), key=lambda r: -r["estrelas"]))
        candidatos = sorted(r for r, d in encontrados.items() if d["passou_prefiltro"])
        fora = [r for r in CANDIDATOS if r not in encontrados]
        if fora:
            print(f"Candidatos da lista manual NAO encontrados pela busca: {fora}")
    else:
        candidatos = sys.argv[1:] or CANDIDATOS

    print(f"Avaliando {len(candidatos)} repositorios...")
    arquivo = pasta / f"criterios_{'busca_' if buscar else ''}{sufixo}.csv"
    linhas = []
    for repo in candidatos:
        try:
            linhas.append(avaliar(client, repo))
            salvar(arquivo, linhas)  # salva a cada repositorio para nao perder progresso
        except (requests.HTTPError, RuntimeError) as e:
            print(f"    ERRO em {repo}: {e}")

    print("\nrepo                                     C1 C2 C3 C4 C5  PRs-int  pessoas  %rev")
    for l in linhas:
        marcas = " ".join("ok" if l[c] else "--" for c in
                          ("C1_publico_original", "C2_atividade", "C3_volume",
                           "C4_diversidade", "C5_mecanismos"))
        print(f"{l['repo']:<40} {marcas}  {l['prs_integrados']:>7}  "
              f"{l['participantes_humanos']:>7}  {l['pct_integrados_com_revisao']:>4}")
    print(f"\nSalvo em: {arquivo.resolve()}")
    print("C6 (contexto nacional) deve ser conferido manualmente na planilha.")


if __name__ == "__main__":
    main()
