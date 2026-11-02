"""
Cliente minimo da API do GitHub (REST e GraphQL) usado pelo coletor (app.py)
e pela triagem de repositorios (triagem.py).

Trata autenticacao, paginacao, limites primario e secundario, timeout e
retentativa em falhas de rede/servidor.
"""

import os
import time
from datetime import date, datetime, timezone

import requests

API = "https://api.github.com"
GRAPHQL = f"{API}/graphql"

TIMEOUT = 60          # segundos por requisicao
MAX_TENTATIVAS = 6    # para erros 5xx, rede e limite secundario


class GitHubClient:
    def __init__(self, token):
        self.token = token
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

    def _enviar(self, metodo, url, **kwargs):
        """Envia a requisicao tratando limites, 5xx e falhas de rede."""
        for tentativa in range(1, MAX_TENTATIVAS + 1):
            try:
                resp = self.session.request(metodo, url, timeout=TIMEOUT, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as e:
                espera = 2 ** tentativa
                print(f"    [falha de rede: {e.__class__.__name__}] nova tentativa em {espera}s...")
                time.sleep(espera)
                continue

            if resp.status_code in (403, 429):
                if resp.headers.get("X-RateLimit-Remaining") == "0":
                    self._esperar_reset(resp)
                    continue
                retry = resp.headers.get("Retry-After")
                if retry or "secondary rate limit" in resp.text.lower():
                    # limite secundario: sem Retry-After, a doc pede >= 1 minuto
                    espera = int(retry) + 1 if retry else 60
                    print(f"    [limite secundario] aguardando {espera}s...")
                    time.sleep(espera)
                    continue
            if resp.status_code >= 500:
                espera = 2 ** tentativa
                print(f"    [erro {resp.status_code} do servidor] nova tentativa em {espera}s...")
                time.sleep(espera)
                continue

            resp.raise_for_status()
            self._esperar_reset(resp)
            return resp
        raise RuntimeError(f"Falhou apos {MAX_TENTATIVAS} tentativas: {url}")

    def get(self, url, params=None):
        return self._enviar("GET", url, params=params)

    def get_paginado(self, url, params=None, parar=None):
        """Percorre as paginas (cabecalho Link) e devolve a lista completa.

        `parar(pagina)` opcional: se devolver True, encerra apos essa pagina
        (usado para cortar listas ordenadas por data ao sair da janela).
        """
        params = dict(params or {})
        params.setdefault("per_page", 100)
        resultados = []
        while url:
            resp = self.get(url, params)
            dados = resp.json()
            pagina = dados if isinstance(dados, list) else [dados]
            resultados.extend(pagina)
            if parar and parar(pagina):
                break
            url = resp.links.get("next", {}).get("url")
            params = None  # a URL do "next" ja carrega os parametros
        return resultados

    def graphql(self, query, variaveis):
        """Executa uma consulta GraphQL (exige token)."""
        if not self.token:
            raise RuntimeError("A API GraphQL exige GITHUB_TOKEN.")
        for _ in range(MAX_TENTATIVAS):
            resp = self._enviar("POST", GRAPHQL, json={"query": query, "variables": variaveis})
            corpo = resp.json()
            erros = corpo.get("errors")
            if not erros:
                return corpo["data"]
            if any(e.get("type") == "RATE_LIMITED" for e in erros):
                print("    [limite GraphQL] aguardando 60s...")
                time.sleep(60)
                continue
            raise RuntimeError(f"Erro GraphQL: {erros}")
        raise RuntimeError("Limite GraphQL persistente.")

    def paginar_graphql(self, query, variaveis, caminho, parar=None):
        """Percorre uma conexao GraphQL paginada por cursor.

        `caminho` indica onde esta a conexao na resposta, ex.:
        ("repository", "pullRequests"). A consulta deve aceitar $cursor e
        devolver pageInfo { hasNextPage endCursor } e nodes.
        """
        variaveis = dict(variaveis, cursor=None)
        nos = []
        while True:
            dados = self.graphql(query, variaveis)
            conexao = dados
            for chave in caminho:
                conexao = conexao[chave]
            pagina = conexao["nodes"]
            nos.extend(pagina)
            if parar and parar(pagina):
                break
            info = conexao["pageInfo"]
            if not info["hasNextPage"]:
                break
            variaveis["cursor"] = info["endCursor"]
        return nos


# ---------------------------------------------------------------------------
# AUXILIARES COMUNS
# ---------------------------------------------------------------------------
def ler_token():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("AVISO: variavel GITHUB_TOKEN nao definida.")
        print("Sem token o limite e de 60 requisicoes/hora e a API GraphQL fica indisponivel.")
        print("Gere um token em https://github.com/settings/tokens e rode:")
        print('  $env:GITHUB_TOKEN="seu_token"   (Windows PowerShell)')
        print("  set GITHUB_TOKEN=seu_token      (Windows cmd)")
        print("  export GITHUB_TOKEN=seu_token   (Linux/Mac)\n")
    return token


def meses_antes(dia, meses):
    """Data `meses` meses antes de `dia` (ajusta o dia no fim do mes)."""
    total = dia.year * 12 + (dia.month - 1) - meses
    ano, mes = divmod(total, 12)
    mes += 1
    for d in (dia.day, 30, 29, 28):
        try:
            return date(ano, mes, d)
        except ValueError:
            continue


def agora_utc():
    return datetime.now(timezone.utc)


def iso(dia):
    """date -> 'AAAA-MM-DDT00:00:00Z' (formato usado pela API)."""
    return f"{dia.isoformat()}T00:00:00Z"


def login_rest(usuario):
    return (usuario or {}).get("login", "")


def tipo_rest(usuario):
    """User, Bot ou Organization (vazio para conta removida)."""
    return (usuario or {}).get("type", "")


def ator_graphql(ator):
    """Devolve (login, tipo) de um ator GraphQL.

    No GraphQL o login de bots vem sem o sufixo "[bot]" (ex.: "github-actions"),
    enquanto no REST vem "github-actions[bot]". Normalizamos para o formato REST
    para que as tabelas se cruzem.
    """
    if not ator:
        return "", ""
    login = ator.get("login", "")
    tipo = ator.get("__typename", "")
    if tipo == "Bot" and not login.endswith("[bot]"):
        login += "[bot]"
    return login, tipo
