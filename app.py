#!/usr/bin/env python3
"""
Coletor de dados do GitHub para pesquisa sobre colaboracao em software livre.

Coleta issues, comentarios, pull requests, revisoes de codigo e comentarios de
revisao dos repositorios configurados, salvando cada tipo em um arquivo CSV.
Cada arquivo pode ser importado depois num banco (ex.: SQL Server) para analise.

COMO USAR
    1. Gere um token pessoal em https://github.com/settings/tokens
       (para dados publicos basta o escopo "public_repo" / "Public repositories").
    2. Exporte o token no terminal antes de rodar:
           Linux/Mac:   export GITHUB_TOKEN=seu_token
           Windows cmd: set GITHUB_TOKEN=seu_token
    3. Ajuste a lista "repos" e as opcoes em CONFIG abaixo.
    4. Rode:  python coletor_github.py

REQUISITOS
    Python 3.8+ e a biblioteca requests:  pip install requests

OBS. SOBRE OS LIMITES DA API (motivo do token)
    Sem autenticar, a API do GitHub permite so 60 requisicoes por hora.
    Com um token pessoal, sobe para 5.000 por hora. A parte mais cara e a
    coleta de revisoes (1 requisicao por PR); se estourar o limite, reduza o
    periodo em CONFIG["since"] ou o numero de repositorios.
"""

import csv
import os
import sys
import time
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# CONFIGURACAO -- ajuste esta parte
# ---------------------------------------------------------------------------
CONFIG = {
    # Repositorios no formato "dono/repositorio".
    "repos": [
        "agenda-tech-brasil/agenda-tech-brasil",
    ],

    # Pasta onde os CSVs serao salvos.
    "output_dir": "dados_github",

    # Coleta apenas itens a partir desta data (formato AAAA-MM-DD).
    # Use None para nao filtrar. Delimitar o periodo e o principal jeito de
    # nao estourar o limite -- e vira parte da sua metodologia (recorte).
    "since": "2024-01-01",

    # Buscar detalhes de cada PR (linhas adicionadas/removidas, arquivos).
    # Custa 1 requisicao POR PR. Deixe False se houver muitos PRs.
    "detalhar_prs": False,

    # Coletar revisoes de codigo (1 requisicao POR PR). E o dado central do
    # tema, mas tambem o mais caro. Reduza "since" se necessario.
    "coletar_revisoes": True,
}

API = "https://api.github.com"


# ---------------------------------------------------------------------------
# CLIENTE DA API (autenticacao, paginacao e controle de limite)
# ---------------------------------------------------------------------------
class GitHubClient:
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pesquisa-sistemas-colaborativos",
        })
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def _esperar_reset(self, resp):
        """Se o limite primario zerou, dorme ate o horario de reset."""
        restantes = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")
        if restantes is not None and int(restantes) == 0 and reset:
            espera = int(reset) - int(time.time()) + 5
            if espera > 0:
                print(f"    [limite primario atingido] aguardando {espera}s...")
                time.sleep(espera)

    def _requisitar(self, url, params=None):
        """Faz um GET tratando limite primario e secundario, com retentativa."""
        while True:
            resp = self.session.get(url, params=params)
            if resp.status_code in (403, 429):
                retry = resp.headers.get("Retry-After")
                if retry:  # limite secundario (rajada de requisicoes)
                    print(f"    [limite secundario] aguardando {retry}s...")
                    time.sleep(int(retry) + 1)
                    continue
                if resp.headers.get("X-RateLimit-Remaining") == "0":
                    self._esperar_reset(resp)
                    continue
            resp.raise_for_status()
            self._esperar_reset(resp)
            return resp

    def get_paginado(self, url, params=None):
        """Percorre todas as paginas (cabecalho Link) e devolve a lista completa."""
        params = dict(params or {})
        params.setdefault("per_page", 100)
        resultados = []
        while url:
            resp = self._requisitar(url, params)
            dados = resp.json()
            resultados.extend(dados if isinstance(dados, list) else [dados])
            url = resp.links.get("next", {}).get("url")
            params = None  # a URL do "next" ja carrega os parametros
        return resultados


# ---------------------------------------------------------------------------
# AUXILIARES
# ---------------------------------------------------------------------------
def _login(usuario):
    return (usuario or {}).get("login", "")


def _nomes(lista, chave="name"):
    return "|".join(str(x.get(chave, "")) for x in (lista or []))


def _limpar(texto):
    """Remove quebras de linha para o texto caber numa celula de CSV."""
    return (texto or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _desde(since):
    return f"{since}T00:00:00Z" if since else None


# ---------------------------------------------------------------------------
# FUNCOES DE COLETA
# ---------------------------------------------------------------------------
def coletar_issues(client, repo, since):
    print("  coletando issues...")
    params = {"state": "all"}
    if since:
        params["since"] = _desde(since)  # obs.: filtra por data de ATUALIZACAO
    itens = client.get_paginado(f"{API}/repos/{repo}/issues", params)
    linhas = []
    for it in itens:
        if "pull_request" in it:
            continue  # PRs tambem aparecem aqui; sao tratados a parte
        linhas.append({
            "repo": repo,
            "numero": it["number"],
            "titulo": _limpar(it.get("title")),
            "autor": _login(it.get("user")),
            "assoc_autor": it.get("author_association", ""),
            "estado": it.get("state", ""),
            "criado_em": it.get("created_at", ""),
            "fechado_em": it.get("closed_at", ""),
            "atualizado_em": it.get("updated_at", ""),
            "labels": _nomes(it.get("labels")),
            "assignees": "|".join(_login(a) for a in it.get("assignees", [])),
            "qtd_comentarios": it.get("comments", 0),
        })
    return linhas


def coletar_pulls(client, repo, since, detalhar):
    print("  coletando pull requests...")
    itens = client.get_paginado(f"{API}/repos/{repo}/pulls", {"state": "all"})
    corte = _desde(since)
    linhas = []
    for pr in itens:
        if corte and pr.get("created_at", "") < corte:
            continue  # /pulls nao aceita "since"; filtramos por data de criacao
        linha = {
            "repo": repo,
            "numero": pr["number"],
            "titulo": _limpar(pr.get("title")),
            "autor": _login(pr.get("user")),
            "assoc_autor": pr.get("author_association", ""),
            "estado": pr.get("state", ""),
            "rascunho": pr.get("draft", False),
            "criado_em": pr.get("created_at", ""),
            "fechado_em": pr.get("closed_at", ""),
            "integrado_em": pr.get("merged_at", ""),
            "foi_integrado": bool(pr.get("merged_at")),
            "labels": _nomes(pr.get("labels")),
            "adicoes": "",
            "remocoes": "",
            "arquivos_alterados": "",
        }
        if detalhar:
            det = client._requisitar(f"{API}/repos/{repo}/pulls/{pr['number']}").json()
            linha["adicoes"] = det.get("additions", "")
            linha["remocoes"] = det.get("deletions", "")
            linha["arquivos_alterados"] = det.get("changed_files", "")
        linhas.append(linha)
    return linhas


def coletar_comentarios_issues(client, repo, since):
    print("  coletando comentarios de issues/PRs...")
    params = {}
    if since:
        params["since"] = _desde(since)
    itens = client.get_paginado(f"{API}/repos/{repo}/issues/comments", params)
    linhas = []
    for c in itens:
        numero = c.get("issue_url", "").rstrip("/").split("/")[-1]
        linhas.append({
            "repo": repo,
            "numero_issue": numero,  # vale para issue OU PR
            "autor": _login(c.get("user")),
            "assoc_autor": c.get("author_association", ""),
            "criado_em": c.get("created_at", ""),
            "tamanho_texto": len(c.get("body") or ""),
            "texto": _limpar(c.get("body")),
        })
    return linhas


def coletar_comentarios_revisao(client, repo, since):
    print("  coletando comentarios de revisao (linha a linha)...")
    params = {}
    if since:
        params["since"] = _desde(since)
    itens = client.get_paginado(f"{API}/repos/{repo}/pulls/comments", params)
    linhas = []
    for c in itens:
        numero = c.get("pull_request_url", "").rstrip("/").split("/")[-1]
        linhas.append({
            "repo": repo,
            "numero_pr": numero,
            "autor": _login(c.get("user")),
            "assoc_autor": c.get("author_association", ""),
            "criado_em": c.get("created_at", ""),
            "arquivo": c.get("path", ""),
            "linha": c.get("line", ""),
            "tamanho_texto": len(c.get("body") or ""),
            "texto": _limpar(c.get("body")),
        })
    return linhas


def coletar_revisoes(client, repo, prs):
    print(f"  coletando revisoes de {len(prs)} PRs (parte mais lenta)...")
    linhas = []
    for i, pr in enumerate(prs, 1):
        numero = pr["numero"]
        revs = client.get_paginado(f"{API}/repos/{repo}/pulls/{numero}/reviews")
        for r in revs:
            linhas.append({
                "repo": repo,
                "numero_pr": numero,
                "revisor": _login(r.get("user")),
                "assoc_revisor": r.get("author_association", ""),
                # veredito: APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED
                "veredito": r.get("state", ""),
                "enviado_em": r.get("submitted_at", ""),
            })
        if i % 25 == 0:
            print(f"    {i}/{len(prs)} PRs processados")
    return linhas


# ---------------------------------------------------------------------------
# SALVAR
# ---------------------------------------------------------------------------
def salvar_csv(caminho, linhas):
    if not linhas:
        print(f"  (sem dados para {caminho.name})")
        return
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        writer.writeheader()
        writer.writerows(linhas)
    print(f"  salvo: {caminho.name} ({len(linhas)} linhas)")


# ---------------------------------------------------------------------------
# PRINCIPAL
# ---------------------------------------------------------------------------
def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("AVISO: variavel GITHUB_TOKEN nao definida.")
        print("Sem token o limite e de apenas 60 requisicoes/hora.")
        print("Gere um token em https://github.com/settings/tokens e rode:")
        print("  export GITHUB_TOKEN=seu_token   (Linux/Mac)")
        print("  set GITHUB_TOKEN=seu_token      (Windows cmd)\n")

    client = GitHubClient(token)
    saida = Path(CONFIG["output_dir"])
    saida.mkdir(parents=True, exist_ok=True)
    since = CONFIG["since"]

    resumo = {}
    for repo in CONFIG["repos"]:
        print(f"\n=== {repo} ===")
        slug = repo.replace("/", "__")

        issues = coletar_issues(client, repo, since)
        salvar_csv(saida / f"{slug}__issues.csv", issues)

        prs = coletar_pulls(client, repo, since, CONFIG["detalhar_prs"])
        salvar_csv(saida / f"{slug}__pull_requests.csv", prs)

        coment_issues = coletar_comentarios_issues(client, repo, since)
        salvar_csv(saida / f"{slug}__comentarios_issues.csv", coment_issues)

        coment_rev = coletar_comentarios_revisao(client, repo, since)
        salvar_csv(saida / f"{slug}__comentarios_revisao.csv", coment_rev)

        revisoes = []
        if CONFIG["coletar_revisoes"]:
            revisoes = coletar_revisoes(client, repo, prs)
            salvar_csv(saida / f"{slug}__revisoes.csv", revisoes)

        resumo[repo] = {
            "issues": len(issues),
            "pull_requests": len(prs),
            "comentarios_issues": len(coment_issues),
            "comentarios_revisao": len(coment_rev),
            "revisoes": len(revisoes),
        }

    print("\n=== RESUMO ===")
    for repo, r in resumo.items():
        print(f"{repo}: " + ", ".join(f"{k}={v}" for k, v in r.items()))
    print(f"\nArquivos salvos em: {saida.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"\nErro HTTP: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuario.")
        sys.exit(1)