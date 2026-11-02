#!/usr/bin/env python3
"""
Coletor de dados do GitHub para pesquisa sobre colaboracao em software livre.

Para cada repositorio configurado, coleta issues, pull requests (com descricao,
issues vinculadas e revisoes), comentarios de issues/PRs, comentarios de
revisao, eventos de issues/PRs (labels, atribuicoes, pedidos de revisao,
fechamento etc.) e commits, dentro de uma janela temporal comum a todos os
casos. Cada tipo e salvo num CSV; as respostas brutas da API ficam em
<output_dir>/bruto/ para permitir retomar a coleta e reprocessar sem novas
requisicoes. Um coleta_meta.json registra data, janela e parametros usados.

COMO USAR
    1. Gere um token pessoal em https://github.com/settings/tokens
       (para dados publicos basta o escopo "public_repo" / "Public repositories").
       O token e obrigatorio: os PRs sao coletados pela API GraphQL.
    2. Exporte o token no terminal antes de rodar:
           Windows PowerShell: $env:GITHUB_TOKEN="seu_token"
           Windows cmd:        set GITHUB_TOKEN=seu_token
           Linux/Mac:          export GITHUB_TOKEN=seu_token
    3. Ajuste a lista "repos" e as opcoes em CONFIG abaixo.
    4. Rode:  python app.py

REQUISITOS
    Python 3.8+ e a biblioteca requests:  pip install -r requirements.txt

JANELA TEMPORAL
    Todos os itens sao filtrados pela data de CRIACAO dentro da janela
    [data_referencia - janela_meses, data_referencia]. Comentarios e eventos
    entram pela propria data de criacao, mesmo que o item pai seja mais antigo.

DADOS PESSOAIS
    Os CSVs e o JSON bruto contem logins e textos de pessoas. A pasta de saida
    esta no .gitignore; a pseudonimizacao (P1, P2, ...) e feita no
    pre-processamento, antes de qualquer divulgacao.
"""

import csv
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import requests

from github_api import (API, GitHubClient, agora_utc, ator_graphql, iso,
                        ler_token, login_rest, meses_antes, tipo_rest)


# ---------------------------------------------------------------------------
# CONFIGURACAO -- ajuste esta parte
# ---------------------------------------------------------------------------
CONFIG = {
    # Repositorios (casos) no formato "dono/repositorio".
    "repos": [
        "agenda-tech-brasil/agenda-tech-brasil",
        "okfn-brasil/querido-diario",
        "filipedeschamps/tabnews.com.br",
        "BrasilAPI/BrasilAPI",
        "brazilian-utils/python",
    ],

    # Pasta onde os CSVs serao salvos.
    "output_dir": "dados_github",

    # Janela comum a todos os casos: os N meses anteriores a data de referencia.
    # 48 meses e o maximo que cabe no caso mais novo (agenda, criado em
    # out/2022) e cobre os dois regimes de contribuicao do agenda.
    "janela_meses": 48,

    # Data de referencia (fim da janela), formato AAAA-MM-DD. None = hoje.
    # Fixada para que triagem, coleta e texto do artigo usem a mesma janela.
    "data_referencia": "2026-10-29",

    # Excecoes de inicio da janela por repositorio (AAAA-MM-DD), se for
    # necessario. Ex.: {"agenda-tech-brasil/agenda-tech-brasil": "2022-10-01"}
    "inicio_por_repo": {},

    # Reaproveitar respostas brutas ja salvas em <output_dir>/bruto/.
    # Permite retomar uma coleta interrompida. Apague a pasta do repositorio
    # (ou use False) para baixar tudo de novo.
    "reusar_cache": True,

    # Eventos (labels, atribuicoes, pedidos de revisao, fechamentos...).
    "coletar_eventos": True,

    # Commits (criterio C2 e concentracao de autoria).
    "coletar_commits": True,
}

# Revisoes trazidas junto com cada PR no GraphQL; PRs com mais revisoes que
# isso tem as restantes buscadas pela API REST.
REVISOES_POR_PR = 50


def janela(repo=None):
    """Devolve (inicio, fim) da janela de coleta como objetos date."""
    ref = CONFIG["data_referencia"]
    fim = date.fromisoformat(ref) if ref else agora_utc().date()
    inicio = meses_antes(fim, CONFIG["janela_meses"])
    if repo and repo in CONFIG["inicio_por_repo"]:
        inicio = date.fromisoformat(CONFIG["inicio_por_repo"][repo])
    return inicio, fim


# ---------------------------------------------------------------------------
# AUXILIARES
# ---------------------------------------------------------------------------
def _nomes(lista, chave="name"):
    return "|".join(str(x.get(chave, "")) for x in (lista or []))


def _na_janela(data_iso, inicio_iso, fim_iso):
    """Compara datas ISO 8601 em UTC (texto) com o intervalo [inicio, fim)."""
    return bool(data_iso) and inicio_iso <= data_iso < fim_iso


def _referencias(texto):
    """Numeros citados como #123 num texto (vinculos informais a issues/PRs)."""
    return "|".join(sorted(set(re.findall(r"(?<![\w/&])#(\d+)\b", texto or "")), key=int))


def _com_cache(pasta, nome, funcao):
    """Executa `funcao` ou reaproveita o JSON bruto salvo anteriormente."""
    arquivo = pasta / f"{nome}.json"
    if CONFIG["reusar_cache"] and arquivo.exists():
        print(f"  {nome}: reaproveitando {arquivo.name}")
        return json.loads(arquivo.read_text(encoding="utf-8"))
    dados = funcao()
    arquivo.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    return dados


def _ordem_desc_saiu_da_janela(campo, inicio_iso):
    """Criterio de parada para listas ordenadas da mais nova para a mais antiga.

    So para se a pagina estiver de fato em ordem decrescente; se a API devolver
    em outra ordem, percorre tudo (mais lento, mas sem perder dados).
    """
    def parar(pagina):
        if not pagina:
            return False
        primeiro, ultimo = pagina[0].get(campo) or "", pagina[-1].get(campo) or ""
        return primeiro >= ultimo and ultimo < inicio_iso
    return parar


# ---------------------------------------------------------------------------
# COLETA BRUTA (respostas da API, sem transformacao)
# ---------------------------------------------------------------------------
CONSULTA_PRS = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: 50, after: $cursor,
                 orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body state isDraft
        createdAt closedAt mergedAt
        author { login __typename }
        authorAssociation
        mergedBy { login __typename }
        additions deletions changedFiles
        labels(first: 20) { nodes { name } }
        closingIssuesReferences(first: 10) {
          nodes { number repository { nameWithOwner } }
        }
        reviews(first: %d) {
          totalCount
          nodes {
            databaseId state body submittedAt authorAssociation
            author { login __typename }
          }
        }
      }
    }
  }
}
""" % REVISOES_POR_PR


def bruto_repo(client, repo):
    return client.get(f"{API}/repos/{repo}").json()


def bruto_issues(client, repo, inicio_iso):
    # "since" filtra por data de ATUALIZACAO; todo item criado na janela foi
    # atualizado nela, entao serve de pre-filtro. Inclui PRs (campo
    # "pull_request"), usados para saber se um comentario e de issue ou de PR.
    try:
        return client.get_paginado(f"{API}/repos/{repo}/issues",
                                   {"state": "all", "since": inicio_iso})
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (404, 410):
            print("  (issues desativadas neste repositorio)")
            return []
        raise


def bruto_pulls(client, repo, inicio_iso):
    dono, nome = repo.split("/")
    prs = client.paginar_graphql(
        CONSULTA_PRS, {"owner": dono, "name": nome},
        ("repository", "pullRequests"),
        parar=_ordem_desc_saiu_da_janela("createdAt", inicio_iso),
    )
    # Completa as revisoes de PRs que passaram do limite da consulta.
    for pr in prs:
        revs = pr["reviews"]
        if revs["totalCount"] > len(revs["nodes"]) and pr["createdAt"] >= inicio_iso:
            print(f"    PR #{pr['number']}: {revs['totalCount']} revisoes, buscando pela REST")
            revs["rest"] = client.get_paginado(
                f"{API}/repos/{repo}/pulls/{pr['number']}/reviews")
    return prs


def bruto_comentarios_issues(client, repo, inicio_iso):
    return client.get_paginado(f"{API}/repos/{repo}/issues/comments",
                               {"since": inicio_iso})


def bruto_comentarios_revisao(client, repo, inicio_iso):
    return client.get_paginado(f"{API}/repos/{repo}/pulls/comments",
                               {"since": inicio_iso})


def bruto_eventos(client, repo, inicio_iso):
    return client.get_paginado(f"{API}/repos/{repo}/issues/events",
                               parar=_ordem_desc_saiu_da_janela("created_at", inicio_iso))


def bruto_commits(client, repo, inicio_iso, fim_iso):
    return client.get_paginado(f"{API}/repos/{repo}/commits",
                               {"since": inicio_iso, "until": fim_iso})


# ---------------------------------------------------------------------------
# TRANSFORMACAO EM LINHAS DE CSV
# ---------------------------------------------------------------------------
CAMPOS = {
    "issues": ["repo", "numero", "titulo", "corpo", "autor", "tipo_autor",
               "assoc_autor", "estado", "motivo_estado", "criado_em",
               "fechado_em", "atualizado_em", "labels", "assignees", "milestone",
               "qtd_comentarios", "qtd_reacoes", "referencias"],
    "pull_requests": ["repo", "numero", "titulo", "corpo", "autor", "tipo_autor",
                      "assoc_autor", "estado", "rascunho", "criado_em",
                      "fechado_em", "integrado_em", "foi_integrado",
                      "integrado_por", "tipo_integrado_por", "labels",
                      "issues_vinculadas", "referencias", "adicoes", "remocoes",
                      "arquivos_alterados", "qtd_revisoes"],
    "revisoes": ["repo", "numero_pr", "id", "revisor", "tipo_revisor",
                 "assoc_revisor", "veredito", "enviado_em", "tamanho_texto",
                 "texto"],
    "comentarios_issues": ["repo", "id", "numero_item", "tipo_item", "autor",
                           "tipo_autor", "assoc_autor", "criado_em",
                           "atualizado_em", "tamanho_texto", "texto"],
    "comentarios_revisao": ["repo", "id", "numero_pr", "id_revisao",
                            "em_resposta_a", "autor", "tipo_autor",
                            "assoc_autor", "criado_em", "arquivo", "linha",
                            "tamanho_texto", "texto"],
    "eventos": ["repo", "id", "numero_item", "tipo_item", "evento", "ator",
                "tipo_ator", "criado_em", "label", "responsavel",
                "revisor_solicitado", "commit_id"],
    "commits": ["repo", "sha", "autor", "tipo_autor", "committer",
                "data_autoria", "data_commit", "qtd_pais", "titulo"],
}


def linhas_issues(repo, itens, ini, fim):
    linhas = []
    for it in itens:
        if "pull_request" in it or not _na_janela(it.get("created_at"), ini, fim):
            continue
        linhas.append({
            "repo": repo,
            "numero": it["number"],
            "titulo": it.get("title") or "",
            "corpo": it.get("body") or "",
            "autor": login_rest(it.get("user")),
            "tipo_autor": tipo_rest(it.get("user")),
            "assoc_autor": it.get("author_association", ""),
            "estado": it.get("state", ""),
            "motivo_estado": it.get("state_reason") or "",
            "criado_em": it.get("created_at", ""),
            "fechado_em": it.get("closed_at") or "",
            "atualizado_em": it.get("updated_at", ""),
            "labels": _nomes(it.get("labels")),
            "assignees": "|".join(login_rest(a) for a in it.get("assignees") or []),
            "milestone": (it.get("milestone") or {}).get("title", ""),
            "qtd_comentarios": it.get("comments", 0),
            "qtd_reacoes": (it.get("reactions") or {}).get("total_count", 0),
            "referencias": _referencias(it.get("body")),
        })
    return linhas


def linhas_pulls(repo, prs, ini, fim):
    linhas, revisoes = [], []
    for pr in prs:
        if not _na_janela(pr.get("createdAt"), ini, fim):
            continue
        autor, tipo_autor = ator_graphql(pr.get("author"))
        integrador, tipo_integrador = ator_graphql(pr.get("mergedBy"))
        vinculadas = [str(i["number"]) for i in pr["closingIssuesReferences"]["nodes"]
                      if i["repository"]["nameWithOwner"].lower() == repo.lower()]
        linhas.append({
            "repo": repo,
            "numero": pr["number"],
            "titulo": pr.get("title") or "",
            "corpo": pr.get("body") or "",
            "autor": autor,
            "tipo_autor": tipo_autor,
            "assoc_autor": pr.get("authorAssociation", ""),
            "estado": (pr.get("state") or "").lower(),  # open, closed ou merged
            "rascunho": pr.get("isDraft", False),
            "criado_em": pr.get("createdAt", ""),
            "fechado_em": pr.get("closedAt") or "",
            "integrado_em": pr.get("mergedAt") or "",
            "foi_integrado": bool(pr.get("mergedAt")),
            "integrado_por": integrador,
            "tipo_integrado_por": tipo_integrador,
            "labels": _nomes(pr["labels"]["nodes"]),
            "issues_vinculadas": "|".join(vinculadas),
            "referencias": _referencias(pr.get("body")),
            "adicoes": pr.get("additions", ""),
            "remocoes": pr.get("deletions", ""),
            "arquivos_alterados": pr.get("changedFiles", ""),
            "qtd_revisoes": pr["reviews"]["totalCount"],
        })
        revisoes.extend(_linhas_revisoes(repo, pr))
    return linhas, revisoes


def _linhas_revisoes(repo, pr):
    linhas = []
    if "rest" in pr["reviews"]:
        for r in pr["reviews"]["rest"]:
            linhas.append({
                "repo": repo, "numero_pr": pr["number"], "id": r.get("id", ""),
                "revisor": login_rest(r.get("user")),
                "tipo_revisor": tipo_rest(r.get("user")),
                "assoc_revisor": r.get("author_association", ""),
                "veredito": r.get("state", ""),
                "enviado_em": r.get("submitted_at") or "",
                "tamanho_texto": len(r.get("body") or ""),
                "texto": r.get("body") or "",
            })
        return linhas
    for r in pr["reviews"]["nodes"]:
        revisor, tipo = ator_graphql(r.get("author"))
        linhas.append({
            "repo": repo, "numero_pr": pr["number"], "id": r.get("databaseId", ""),
            "revisor": revisor,
            "tipo_revisor": tipo,
            "assoc_revisor": r.get("authorAssociation", ""),
            # veredito: APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED
            "veredito": r.get("state", ""),
            "enviado_em": r.get("submittedAt") or "",
            "tamanho_texto": len(r.get("body") or ""),
            "texto": r.get("body") or "",
        })
    return linhas


def _tipos_item(issues_brutas):
    """Mapa numero -> 'issue' ou 'pr', a partir da listagem /issues."""
    return {it["number"]: ("pr" if "pull_request" in it else "issue")
            for it in issues_brutas}


def linhas_comentarios_issues(repo, itens, tipos, ini, fim):
    linhas = []
    for c in itens:
        if not _na_janela(c.get("created_at"), ini, fim):
            continue
        numero = int(c.get("issue_url", "").rstrip("/").split("/")[-1])
        linhas.append({
            "repo": repo,
            "id": c.get("id", ""),
            "numero_item": numero,
            "tipo_item": tipos.get(numero, ""),
            "autor": login_rest(c.get("user")),
            "tipo_autor": tipo_rest(c.get("user")),
            "assoc_autor": c.get("author_association", ""),
            "criado_em": c.get("created_at", ""),
            "atualizado_em": c.get("updated_at", ""),
            "tamanho_texto": len(c.get("body") or ""),
            "texto": c.get("body") or "",
        })
    return linhas


def linhas_comentarios_revisao(repo, itens, ini, fim):
    linhas = []
    for c in itens:
        if not _na_janela(c.get("created_at"), ini, fim):
            continue
        linhas.append({
            "repo": repo,
            "id": c.get("id", ""),
            "numero_pr": c.get("pull_request_url", "").rstrip("/").split("/")[-1],
            "id_revisao": c.get("pull_request_review_id") or "",
            "em_resposta_a": c.get("in_reply_to_id") or "",  # monta as threads
            "autor": login_rest(c.get("user")),
            "tipo_autor": tipo_rest(c.get("user")),
            "assoc_autor": c.get("author_association", ""),
            "criado_em": c.get("created_at", ""),
            "arquivo": c.get("path", ""),
            # "line" fica vazio quando o trecho comentado mudou depois
            "linha": c.get("line") or c.get("original_line") or "",
            "tamanho_texto": len(c.get("body") or ""),
            "texto": c.get("body") or "",
        })
    return linhas


def linhas_eventos(repo, itens, ini, fim):
    linhas = []
    for e in itens:
        if not _na_janela(e.get("created_at"), ini, fim):
            continue
        issue = e.get("issue") or {}
        linhas.append({
            "repo": repo,
            "id": e.get("id", ""),
            "numero_item": issue.get("number", ""),
            "tipo_item": "pr" if "pull_request" in issue else "issue",
            # ex.: labeled, assigned, review_requested, closed, merged,
            # referenced, renamed, reopened
            "evento": e.get("event", ""),
            "ator": login_rest(e.get("actor")),
            "tipo_ator": tipo_rest(e.get("actor")),
            "criado_em": e.get("created_at", ""),
            "label": (e.get("label") or {}).get("name", ""),
            "responsavel": login_rest(e.get("assignee")),
            "revisor_solicitado": login_rest(e.get("requested_reviewer")),
            "commit_id": e.get("commit_id") or "",
        })
    return linhas


def linhas_commits(repo, itens):
    linhas = []
    for c in itens:
        info = c.get("commit") or {}
        linhas.append({
            "repo": repo,
            "sha": c.get("sha", ""),
            # vazio quando o e-mail do commit nao esta ligado a uma conta
            "autor": login_rest(c.get("author")),
            "tipo_autor": tipo_rest(c.get("author")),
            "committer": login_rest(c.get("committer")),
            "data_autoria": (info.get("author") or {}).get("date", ""),
            "data_commit": (info.get("committer") or {}).get("date", ""),
            "qtd_pais": len(c.get("parents") or []),
            "titulo": (info.get("message") or "").split("\n", 1)[0],
        })
    return linhas


# ---------------------------------------------------------------------------
# SALVAR
# ---------------------------------------------------------------------------
def salvar_csv(caminho, linhas, campos):
    # Textos sao mantidos com quebras de linha (o csv os coloca entre aspas),
    # preservando a estrutura dos formularios e do markdown.
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(linhas)
    print(f"  salvo: {caminho.name} ({len(linhas)} linhas)")


def _versao_script():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


# ---------------------------------------------------------------------------
# PRINCIPAL
# ---------------------------------------------------------------------------
def coletar_repo(client, repo, saida):
    inicio, fim = janela(repo)
    # fim da janela e exclusivo: inclui o proprio dia de referencia
    ini_iso, fim_iso = iso(inicio), iso(date.fromordinal(fim.toordinal() + 1))
    print(f"\n=== {repo} ({inicio} a {fim}) ===")
    slug = repo.replace("/", "__")
    # o cache e separado por janela, para nao misturar coletas diferentes
    bruto = saida / "bruto" / slug / f"{inicio}_{fim}"
    bruto.mkdir(parents=True, exist_ok=True)

    meta_repo = _com_cache(bruto, "repo", lambda: bruto_repo(client, repo))
    if meta_repo.get("full_name", repo).lower() != repo.lower():
        print(f"  AVISO: {repo} foi renomeado para {meta_repo['full_name']}")

    issues_b = _com_cache(bruto, "issues", lambda: bruto_issues(client, repo, ini_iso))
    prs_b = _com_cache(bruto, "pull_requests", lambda: bruto_pulls(client, repo, ini_iso))
    com_b = _com_cache(bruto, "comentarios_issues",
                       lambda: bruto_comentarios_issues(client, repo, ini_iso))
    rev_b = _com_cache(bruto, "comentarios_revisao",
                       lambda: bruto_comentarios_revisao(client, repo, ini_iso))
    eventos_b = (_com_cache(bruto, "eventos", lambda: bruto_eventos(client, repo, ini_iso))
                 if CONFIG["coletar_eventos"] else [])
    commits_b = (_com_cache(bruto, "commits",
                            lambda: bruto_commits(client, repo, ini_iso, fim_iso))
                 if CONFIG["coletar_commits"] else [])

    prs, revisoes = linhas_pulls(repo, prs_b, ini_iso, fim_iso)
    tabelas = {
        "issues": linhas_issues(repo, issues_b, ini_iso, fim_iso),
        "pull_requests": prs,
        "revisoes": revisoes,
        "comentarios_issues": linhas_comentarios_issues(
            repo, com_b, _tipos_item(issues_b), ini_iso, fim_iso),
        "comentarios_revisao": linhas_comentarios_revisao(repo, rev_b, ini_iso, fim_iso),
        "eventos": linhas_eventos(repo, eventos_b, ini_iso, fim_iso),
        "commits": linhas_commits(repo, commits_b),
    }
    for nome, linhas in tabelas.items():
        salvar_csv(saida / f"{slug}__{nome}.csv", linhas, CAMPOS[nome])

    return {
        "inicio_janela": inicio.isoformat(),
        "fim_janela": fim.isoformat(),
        "nome_atual": meta_repo.get("full_name", repo),
        "criado_em": meta_repo.get("created_at", ""),
        "ultimo_push": meta_repo.get("pushed_at", ""),
        "fork": meta_repo.get("fork"),
        "arquivado": meta_repo.get("archived"),
        "licenca": (meta_repo.get("license") or {}).get("spdx_id"),
        "estrelas": meta_repo.get("stargazers_count"),
        "contagens": {nome: len(linhas) for nome, linhas in tabelas.items()},
    }


def main():
    token = ler_token()
    if not token:
        print("ERRO: o coletor usa a API GraphQL, que exige GITHUB_TOKEN.")
        sys.exit(1)

    if not CONFIG["data_referencia"]:
        print("AVISO: data_referencia nao fixada; usando hoje. Para retomar a coleta")
        print("em outro dia com o mesmo cache, fixe a data em CONFIG.\n")

    client = GitHubClient(token)
    saida = Path(CONFIG["output_dir"])
    saida.mkdir(parents=True, exist_ok=True)

    meta = {
        "iniciada_em": agora_utc().isoformat(timespec="seconds"),
        "versao_script": _versao_script(),
        "config": CONFIG,
        "repos": {},
    }
    for repo in CONFIG["repos"]:
        meta["repos"][repo] = coletar_repo(client, repo, saida)
    meta["concluida_em"] = agora_utc().isoformat(timespec="seconds")

    (saida / "coleta_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== RESUMO ===")
    for repo, r in meta["repos"].items():
        print(f"{repo}: " + ", ".join(f"{k}={v}" for k, v in r["contagens"].items()))
    print(f"\nArquivos salvos em: {saida.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"\nErro HTTP: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuario. Rode de novo para retomar (cache em bruto/).")
        sys.exit(1)
